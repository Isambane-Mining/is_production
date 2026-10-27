"""Make the IFC and E57 test fixtures (run once with the CAD worker Python, which has IfcOpenShell and pye57):

    /opt/cad-worker/venv/bin/python -I make_sample_ifc_e57.py fixtures

sample_plant.ifc  IFC4, two 10 × 5 × 3 m boxes (IfcBuildingElementProxy "Box 1" at the local origin, "Box 2" 20 m
                  east), georeferenced at E −19 500, N 2 860 500, height 100 (EPSG:2053), not rotated
sample_scan.e57   two scans of a flat square at z = 100 (21 × 21 points, 1 m apart): scan 1 at the origin with colour,
                  scan 2 posed 30 m east (its translation) with intensity only
"""

import os
import sys

import numpy as np

out = sys.argv[1]
os.makedirs(out, exist_ok=True)

import ifcopenshell.api  # noqa: E402
import ifcopenshell.api.georeference  # noqa: E402

f = ifcopenshell.api.run("project.create_file", version="IFC4")
proj = ifcopenshell.api.run("root.create_entity", f, ifc_class="IfcProject", name="Sample plant")
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
ifcopenshell.api.georeference.edit_georeferencing(f, projected_crs={"Name": "EPSG:2053"}, coordinate_operation={"Eastings": -19500.0, "Northings": 2860500.0, "OrthogonalHeight": 100.0})
f.write(os.path.join(out, "sample_plant.ifc"))

import pye57  # noqa: E402

gx, gy = np.meshgrid(np.arange(21.0), np.arange(21.0), indexing="ij")
x, y = gx.ravel(), gy.ravel()
path = os.path.join(out, "sample_scan.e57")
if os.path.exists(path):
	os.remove(path)
e = pye57.E57(path, mode="w")
e.write_scan_raw({"cartesianX": x - 19500, "cartesianY": y + 2860500, "cartesianZ": np.full(len(x), 100.0), "intensity": np.full(len(x), 0.5), "colorRed": np.full(len(x), 200, np.uint8), "colorGreen": np.full(len(x), 100, np.uint8), "colorBlue": np.full(len(x), 50, np.uint8)}, name="Scan 1")
e.write_scan_raw({"cartesianX": x, "cartesianY": y, "cartesianZ": np.full(len(x), 100.0), "intensity": np.full(len(x), 0.25)}, name="Scan 2", translation=np.array([-19470.0, 2860500.0, 0.0]))
e.close()
print("fixtures written to", out)
