"""Exercise surface_worker.py commands against analytic answers.

Run with the CAD worker Python:  /opt/cad-worker/venv/bin/python -I test_surface_worker.py ../cad_worker/surface_worker.py <empty folder>
"""
import json, math, os, subprocess, sys, numpy as np
WK = sys.argv[1]; WS = sys.argv[2]
os.makedirs(WS + "/outputs", exist_ok=True)
def run(req):
    req["workspace"] = WS
    p = subprocess.run([sys.executable, "-I", WK], input=json.dumps(req), capture_output=True, text=True)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    res = [l for l in lines if "result" in l or "error" in l][-1]
    if "error" in res: raise SystemExit(f"{req['command']}: {res['error']}\n{p.stderr[-2000:]}")
    return res["result"], [l["log"] for l in lines if "log" in l]
def write(name, data):
    path = f"{WS}/{name}"; json.dump(data, open(path, "w")); return path
ok = lambda c, m: print(("ok  " if c else "FAIL"), m)
X0, Y0 = -19500.0, 2860500.0
# 1. cone z = 100 - r on a polar grid → contours are circles; volume vs plane z=0 = π r² h / 3 approx (faceted)
r = np.linspace(0, 50, 51); a = np.linspace(0, 2*math.pi, 181)[:-1]
pts = [[X0, Y0, 100.0]] + [[X0 + rr*math.cos(t), Y0 + rr*math.sin(t), 100 - rr] for rr in r[1:] for t in a]
res, log = run({"command": "build", "name": "cone", "input": write("cone.json", {"points": pts})})
cone = res["tin"]; st = res["stats"]
ok(st["triangle_count"] > 0 and abs(st["max_elevation"] - 100) < 1e-9 and st["validation"]["ok"], f"build cone: {st['triangle_count']} tris, z {st['min_elevation']}..{st['max_elevation']}, valid={st['validation']['ok']}")
res, _ = run({"command": "contours", "tin": cone, "interval": 10, "major": 50})
c = res["contours"]; c75 = [l for l in c if abs(l["z"] - 70) < 1e-9]
ok(len(c75) == 1 and c75[0]["closed"], f"contours: {len(c)} lines; z=70 closed={c75[0]['closed'] if c75 else None}, {len(c75[0]['points']) if c75 else 0} pts")
rad = [math.hypot(p[0]-X0, p[1]-Y0) for p in c75[0]["points"]]
ok(max(abs(v - 30) for v in rad) < 0.06, f"z=70 contour radius 30 ± {max(abs(v-30) for v in rad):.4f} (chordal error of the 2° facets)")
# 2. tilted plane z = x-X0 on a square with breaklines and boundary + hole; check exact volumes vs elevation 0
sq = [[X0, Y0, 0], [X0+100, Y0, 100], [X0+100, Y0+100, 100], [X0, Y0+100, 0]]
grid = [[X0+i, Y0+j, i] for i in range(0, 101, 5) for j in range(0, 101, 5)]
res, log = run({"command": "build", "name": "plane", "input": write("plane.json", {"points": grid, "breaklines": [[[X0+10, Y0+50, 10], [X0+90, Y0+50, 90]], [[X0+50, Y0+10, 50], [X0+50, Y0+90, 50]]], "boundary": [p[:2] for p in sq], "holes": [[[X0+40, Y0+40], [X0+60, Y0+40], [X0+60, Y0+60], [X0+40, Y0+60]]]})})
plane = res["tin"]
ok(abs(res["stats"]["plan_area"] - 9600) < 1e-6, f"build plane with hole: plan area {res['stats']['plan_area']} (9600)")
res, _ = run({"command": "volume", "base": plane, "elevation": 50})
# fill = ∫max(50-x,0), cut = ∫max(x-50,0) over square minus hole: square: 125000 each; hole (x 40..60): ∫|x-50| over 20x20 = 20*2*(10²/2)=2000 → each side 1000
ok(abs(res["fill"] - 124000) < 1e-6 and abs(res["cut"] - 124000) < 1e-6 and res["valid"], f"volume vs elevation 50: cut {res['cut']}, fill {res['fill']} (124000 each), valid {res['valid']}")
res, _ = run({"command": "volume", "base": plane, "elevation": 0, "boundary": [[X0, Y0], [X0+50, Y0], [X0+50, Y0+100], [X0, Y0+100]]})
# ∫ x over left half minus hole part (x 40..50, y 40..60): 100*50²/2 = 125000 - ∫_{40}^{50} x*20 dx = 20*(2500-1600)/2 = 9000 → 116000 cut (base above 0)
ok(abs(res["cut"] - 116000) < 1e-6 and res["fill"] < 1e-9, f"volume with boundary: cut {res['cut']} (116000)")
# 3. profile along y = Y0+25 across the plane: z must equal x - X0 exactly at all samples
res, _ = run({"command": "profile", "surfaces": [{"key": "plane", "tin": plane}], "alignment": [[X0-10, Y0+25, 0], [X0+110, Y0+25, 0]]})
pr = res["profiles"]["plane"]; flat = [p for run_ in pr for p in run_]
ok(len(pr) == 1 and max(abs(p[3] - (p[1] - X0)) for p in flat) < 1e-9 and abs(flat[0][0] - 10) < 1e-9, f"profile: {len(flat)} points, exact z, starts at chainage {flat[0][0]}")
res, _ = run({"command": "profile", "surfaces": [{"key": "plane", "tin": plane}], "alignment": [[X0-10, Y0+50, 0], [X0+110, Y0+50, 0]]})
ok(len(res["profiles"]["plane"]) == 2, f"profile through the hole: {len(res['profiles']['plane'])} runs (2)")
res, _ = run({"command": "contours", "tin": plane, "interval": 10})  # every level runs through grid vertices
cl = res["contours"]
dup = sum(1 for c in cl for a, b in zip(c["points"], c["points"][1:]) if math.dist(a[:2], b[:2]) <= 1e-9)
ok(cl and dup == 0 and all(len(c["points"]) >= 2 for c in cl), f"contours through vertices: {len(cl)} lines, {dup} repeated points")
res, _ = run({"command": "profile", "surfaces": [{"key": "plane", "tin": plane}], "alignment": [[X0-50, Y0-50, 0], [X0-20, Y0-50, 0]]})
ok(res["profiles"]["plane"] == [], "profile off the surface: no runs (no error)")
res, _ = run({"command": "profile", "surfaces": [{"key": "plane", "tin": plane}], "alignment": [[X0+20, Y0+25, 0], [X0-20, Y0+25, 0], [X0-20, Y0+75, 0], [X0+20, Y0+75, 0]]})
ok(len(res["profiles"]["plane"]) == 2, f"profile leaving the surface for a whole segment: {len(res['profiles']['plane'])} runs (2)")
res, _ = run({"command": "sections", "surfaces": [{"key": "plane", "tin": plane}], "alignment": [[X0+5, Y0+5, 0], [X0+5, Y0+95, 0]], "interval": 30, "left": 10, "right": 10})
ok(len(res["sections"]) == 4, f"sections: {len(res['sections'])} stations")
# 4. intersection of the cone with plane z = 80 → circle r=20
res, _ = run({"command": "build", "name": "flat80", "input": write("flat.json", {"points": [[X0-60, Y0-60, 80], [X0+60, Y0-60, 80], [X0+60, Y0+60, 80], [X0-60, Y0+60, 80]]})})
flat = res["tin"]
res, _ = run({"command": "intersect", "base": cone, "comparison": flat})
L = res["lines"]; rad = [math.hypot(p[0]-X0, p[1]-Y0) for p in L[0]["points"]] if L else [0]
ok(len(L) == 1 and L[0]["closed"] and max(abs(v - 20) for v in rad) < 0.02 and all(abs(p[2] - 80) < 1e-6 for p in L[0]["points"]), f"intersect cone/plane: {len(L)} line, radius 20 ± {max(abs(v-20) for v in rad):.4f}")
res, _ = run({"command": "volume", "base": cone, "comparison": flat})
ok(res["valid"] and res["cut"] > 0 and res["fill"] > 0, f"cone vs 80: cut {res['cut']:.2f} (cone cap ≈ π·20²·20/3 = {math.pi*400*20/3:.2f}), fill {res['fill']:.2f}")
# 5. spikes, repair, smooth, clip
spiky = [[X0+i, Y0+j, 0.0] for i in range(0, 50, 2) for j in range(0, 50, 2)]; spiky[300][2] = 40.0
res, _ = run({"command": "build", "name": "spiky", "input": write("spiky.json", {"points": spiky})})
ok(res["stats"]["validation"]["spike_count"] == 1, f"validate: spikes {res['stats']['validation']['spike_count']} (1)")
res, log = run({"command": "repair", "name": "fixed", "tin": res["tin"], "options": {"remove_spikes": True}})
ok(res["stats"]["max_elevation"] == 0 and res["stats"]["validation"]["spike_count"] == 0, f"repair: max z {res['stats']['max_elevation']}; {log}")
res, log = run({"command": "smooth", "name": "smooth", "tin": cone, "iterations": 5})
ok(res["stats"]["max_elevation"] < 100 and res["stats"]["validation"]["ok"], f"smooth: apex {res['stats']['max_elevation']:.3f} (<100)")
res, _ = run({"command": "clip", "name": "clipped", "tin": cone, "boundary": [[X0-10, Y0-10], [X0+10, Y0-10], [X0+10, Y0+10], [X0-10, Y0+10]]})
ok(abs(res["stats"]["plan_area"] - 400) < 1e-6, f"clip: plan area {res['stats']['plan_area']} (400)")
res, _ = run({"command": "analyse", "tin": plane})
ok(abs(res["slope"]["mean"] - 45) < 1e-6, f"analyse: mean slope {res['slope']['mean']} (45°)")
res, _ = run({"command": "tiles", "tin": cone, "out_dir": WS + "/outputs/tiles"})
ok(res["tiles"] >= 1 and os.path.exists(res["index"]), f"tiles: {res['tiles']}")
# 6. export / import round trips
for fmt, ext in (("LandXML", "xml"), ("OBJ", "obj"), ("STL", "stl"), ("CSV", "csv")):
    out = f"{WS}/outputs/export.{ext}"
    run({"command": "export", "format": fmt, "out": out, "surfaces": [{"name": "Plane", "tin": plane}]})
    res, _ = run({"command": "import", "format": fmt, "input": out, "file_name": f"export.{ext}"})
    if fmt == "CSV":
        ok(res["points"] > 0, f"CSV round trip: {res['points']} points, columns {res['columns']}, header {res['header']}")
    else:
        s = res["surfaces"][0]["stats"]
        tol = 1e-9 if fmt != "STL" else 0.5
        ok(abs(s["plan_area"] - 9600) < (1e-6 if fmt != "STL" else 1.0), f"{fmt} round trip: plan area {s['plan_area']} triangles {s['triangle_count']}")
import laspy
hdr = laspy.LasHeader(point_format=3, version="1.2"); hdr.offsets = [X0, Y0, 0]; hdr.scales = [0.001, 0.001, 0.001]
las = laspy.LasData(hdr); g = np.array(grid, float); las.x, las.y, las.z = g[:, 0], g[:, 1], g[:, 2]; las.classification = np.where(np.arange(len(g)) % 2 == 0, 2, 1)
for fmt, ext in (("LAS", "las"), ("LAZ", "laz")):
    las.write(f"{WS}/cloud.{ext}")
    res, _ = run({"command": "import", "format": fmt, "input": f"{WS}/cloud.{ext}", "options": {"ground_only": True}})
    ok(res["points"] == (len(g) + 1) // 2, f"{fmt}: {res['points']} ground points of {len(g)}")
# 6. the paths for very large surfaces, forced on these small ones (in process): block-by-block composite volumes and
# the grid point locator must give the same answers as the single composite and matplotlib's trapezoid map
sys.path.insert(0, os.path.dirname(os.path.abspath(WK)))
import surface_worker as sw
sw.emit = lambda **k: None
single = sw.volume({"workspace": WS, "base": cone, "comparison": flat}, [])
sw.COMPOSITE_BLOCK_TRIANGLES = 500
blocked = sw.volume({"workspace": WS, "base": cone, "comparison": flat}, [])
ok(blocked["blocks"] > 1 and blocked["valid"] and abs(blocked["cut"] - single["cut"]) < 1e-6 and abs(blocked["fill"] - single["fill"]) < 1e-6,
   f"volume in {blocked['blocks']} blocks: cut {blocked['cut']:.6f} = {single['cut']:.6f}, fill {blocked['fill']:.6f} = {single['fill']:.6f}")
ct = sw.Tin.read(cone)
rng = np.random.default_rng(7)
qx, qy = rng.uniform(X0 - 70, X0 + 70, 50000), rng.uniform(Y0 - 70, Y0 + 70, 50000)
ref = ct.z_at(qx, qy)
grid_z = ct.z_at(qx, qy, triangles=sw.GridLocator(ct.xyz, ct.tri)(qx, qy))
ok(np.array_equal(np.isnan(ref), np.isnan(grid_z)) and np.nanmax(np.abs(ref - grid_z)) < 1e-9, "grid locator = trapezoid map on 50 000 points")
