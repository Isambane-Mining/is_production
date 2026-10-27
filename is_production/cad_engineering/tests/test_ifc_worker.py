"""Exercise ifc_worker.py (Phase 6 IFC) with known answers: a georeferenced, rotated box, merging by class, and an
export → import round trip.

Run with the CAD worker Python:  /opt/cad-worker/venv/bin/python -I test_ifc_worker.py ../cad_worker <empty folder>
"""
import json, math, os, subprocess, sys
import numpy as np

HERE, WS = sys.argv[1], sys.argv[2]
os.makedirs(WS + "/inputs", exist_ok=True)
os.makedirs(WS + "/outputs", exist_ok=True)
sys.path.insert(0, HERE)
import surface_worker as sw  # noqa: E402

failures = 0


def ok(cond, text):
	global failures
	print(("ok   " if cond else "FAIL ") + text)
	failures += 0 if cond else 1


def run(req):
	req["workspace"] = WS
	p = subprocess.run([sys.executable, "-I", os.path.join(HERE, "ifc_worker.py")], input=json.dumps(req), capture_output=True, text=True)
	lines = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
	res = [line for line in lines if "result" in line or "error" in line][-1]
	if "error" in res:
		raise SystemExit(f"{req['command']}: {res['error']}\n{p.stderr}")
	return res["result"]


# a file with two boxes (10 × 5 × 3 m at the local origin and 20 m east), georeferenced at E −19 500, N 2 860 500,
# height 1 500, rotated 30°
import ifcopenshell  # noqa: E402
import ifcopenshell.api  # noqa: E402
import ifcopenshell.api.georeference  # noqa: E402

f = ifcopenshell.api.run("project.create_file", version="IFC4")
proj = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name="Test plant")
ifcopenshell.api.run("unit.assign_unit", f)
ctx = ifcopenshell.api.run("context.add_context", f, context_type="Model")
body = ifcopenshell.api.run("context.add_context", f, context_type="Model", context_identifier="Body", target_view="MODEL_VIEW", parent=ctx)
site = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcSite", name="Site")
ifcopenshell.api.run("aggregate.assign_object", f, relating_object=proj, products=[site])
box = np.array([[0, 0, 0], [10, 0, 0], [10, 5, 0], [0, 5, 0], [0, 0, 3], [10, 0, 3], [10, 5, 3], [0, 5, 3]], float)
faces = [[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7], [0, 1, 5], [0, 5, 4], [1, 2, 6], [1, 6, 5], [2, 3, 7], [2, 7, 6], [3, 0, 4], [3, 4, 7]]
elements = []
for k, dx in enumerate((0, 20)):
	el = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcBuildingElementProxy", name=f"Box {k + 1}")
	rep = ifcopenshell.api.run("geometry.add_mesh_representation", f, context=body, vertices=[(box + [dx, 0, 0]).tolist()], faces=[faces])
	ifcopenshell.api.run("geometry.assign_representation", f, product=el, representation=rep)
	elements.append(el)
ifcopenshell.api.run("spatial.assign_container", f, relating_structure=site, products=elements)
ifcopenshell.api.georeference.add_georeferencing(f)
c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
ifcopenshell.api.georeference.edit_georeferencing(f, projected_crs={"Name": "EPSG:2053"}, coordinate_operation={"Eastings": -19500.0, "Northings": 2860500.0, "OrthogonalHeight": 1500.0, "XAxisAbscissa": c, "XAxisOrdinate": s})
f.write(f"{WS}/inputs/plant.ifc")

# 1. inspect: two proxies, the georeference, extents in map coordinates
r = run({"command": "inspect", "input": f"{WS}/inputs/plant.ifc"})
corner = [-19500 + 30 * c - 0 * s, 2860500 + 30 * s, 1503]  # (30, 0, 3) local
ok(r["schema"] == "IFC4" and r["elements"] == 2 and r["by_class"] == {"IfcBuildingElementProxy": 2} and r["georeferencing"]["crs"] == "EPSG:2053", f"inspect: {r['schema']}, {r['by_class']}, CRS {r['georeferencing']['crs']}")
ok(abs(r["extents"][5] - 1503) < 1e-9 and abs(r["extents"][3] - corner[0]) < 1e-6, f"extents in map coordinates: max x {r['extents'][3]:.4f} ({corner[0]:.4f}), top {r['extents'][5]}")

# 2. import per element: two closed meshes of 150 m³, the first box's local corner (10, 5, 3) where the rotation puts it
r = run({"command": "import", "input": f"{WS}/inputs/plant.ifc", "options": {"group_by": "element"}})
ok(len(r["meshes"]) == 2 and all(abs(m["stats"].get("volume", 0) - 150) < 1e-6 for m in r["meshes"]), f"import: {len(r['meshes'])} meshes, volumes {[round(m['stats'].get('volume', 0), 6) for m in r['meshes']]}")
first = next(m for m in r["meshes"] if m["metadata"]["name"] == "Box 1")
t = sw.Tin.read(first["tin"])
want = np.array([-19500 + 10 * c - 5 * s, 2860500 + 10 * s + 5 * c, 1503])
ok(np.min(np.linalg.norm(t.xyz - want, axis=1)) < 1e-6 and first["metadata"]["ifc_class"] == "IfcBuildingElementProxy" and len(first["metadata"]["global_id"]) == 22, "rotated, offset corner and the element's GlobalId / class kept")
r = run({"command": "import", "input": f"{WS}/inputs/plant.ifc", "options": {"group_by": "class"}})
ok(len(r["meshes"]) == 1 and r["meshes"][0]["metadata"]["elements"] == 2, "merged per IFC class: one mesh of two elements")

# 3. export a TIN (as terrain) and a polyline, read it back: same coordinates through the map conversion
tin = sw.Tin(np.array([[-19500, 2860500, 1500], [-19400, 2860500, 1510], [-19400, 2860600, 1520], [-19500, 2860600, 1505.0]]), np.array([[0, 1, 2], [0, 2, 3]]))
tin.write(f"{WS}/inputs/ground.tin")
r = run({"command": "export", "out": f"{WS}/outputs/export.ifc", "origin": [-19450, 2860550, 1500], "crs": "EPSG:2053", "project": "Demo", "meshes": [{"name": "Ground", "kind": "surface", "tin": f"{WS}/inputs/ground.tin"}], "polylines": [{"name": "Limit", "points": [[-19500, 2860500, 1500], [-19400, 2860500, 1500], [-19400, 2860600, 1500]], "closed": True}]})
ok(r["written"] == {"surface": 1, "polyline": 1}, f"export: {r['written']}, {r['bytes']} bytes")
back = run({"command": "import", "input": f"{WS}/outputs/export.ifc", "options": {}})
bt = sw.Tin.read(back["meshes"][0]["tin"])
err = max(np.min(np.linalg.norm(bt.xyz - p, axis=1)) for p in tin.xyz)
g = ifcopenshell.open(f"{WS}/outputs/export.ifc")
ok(err < 1e-6 and g.by_type("IfcGeographicElement") and g.by_type("IfcAnnotation") and back["georeferencing"]["crs"] == "EPSG:2053", f"round trip: largest vertex error {err:.2e} m, terrain + annotation, CRS kept")

print("FAILED" if failures else "all passed")
sys.exit(1 if failures else 0)
