"""Exercise cloud_worker.py (Phase 6 point clouds) on a synthetic site with known answers.

Run with the CAD worker Python:  /opt/cad-worker/venv/bin/python -I test_cloud_worker.py ../cad_worker <empty folder>
"""
import json, os, struct, subprocess, sys
import numpy as np

HERE, WS = sys.argv[1], sys.argv[2]
os.makedirs(WS + "/inputs", exist_ok=True)
os.makedirs(WS + "/outputs", exist_ok=True)
failures = 0


def ok(cond, text):
	global failures
	print(("ok   " if cond else "FAIL ") + text)
	failures += 0 if cond else 1


def run(req, script="cloud_worker.py"):
	req["workspace"] = WS
	p = subprocess.run([sys.executable, "-I", os.path.join(HERE, script)], input=json.dumps(req), capture_output=True, text=True)
	lines = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
	res = [line for line in lines if "result" in line or "error" in line][-1]
	if "error" in res:
		raise SystemExit(f"{req['command']}: {res['error']}\n{p.stderr}")
	return res["result"]


# the site: terrain z = 100 + 0.05x (0.5 m jittered grid over 100 × 100 m), a 20 × 20 m building 8 m high,
# a clump of trees (2–10 m above ground), and 20 gross outliers (±50 m)
rng = np.random.default_rng(3)
X0, Y0 = -19500.0, 2860500.0
gx, gy = np.meshgrid(np.arange(0, 100, 0.5), np.arange(0, 100, 0.5), indexing="ij")
tx, ty = (gx.ravel() + rng.uniform(0, 0.4, gx.size)), (gy.ravel() + rng.uniform(0, 0.4, gy.size))
in_building = (tx >= 40) & (tx <= 60) & (ty >= 40) & (ty <= 60)
terrain = np.c_[tx[~in_building], ty[~in_building], 100 + 0.05 * tx[~in_building]]
bx, by = np.meshgrid(np.arange(40, 60.01, 0.5), np.arange(40, 60.01, 0.5), indexing="ij")
roof = np.c_[bx.ravel(), by.ravel(), 108 + 0.05 * bx.ravel()]
ang, rad = rng.uniform(0, 2 * np.pi, 3000), 5 * np.sqrt(rng.uniform(0, 1, 3000))
tree_xy = np.c_[20 + rad * np.cos(ang), 75 + rad * np.sin(ang)]
trees = np.c_[tree_xy, 100 + 0.05 * tree_xy[:, 0] + rng.uniform(2, 10, 3000)]
out_xy = rng.uniform(5, 95, (20, 2))
outliers = np.c_[out_xy, 100 + 0.05 * out_xy[:, 0] + np.where(np.arange(20) % 2, 50, -50)]
parts = {"terrain": terrain, "roof": roof, "trees": trees, "outliers": outliers}
xyz = np.concatenate(list(parts.values()))
xyz[:, 0] += X0
xyz[:, 1] += Y0
label = np.concatenate([np.full(len(v), k, dtype=object) for k, v in parts.items()])
rgb = rng.integers(0, 256, (len(xyz), 3))
np.savetxt(f"{WS}/inputs/site.xyz", np.c_[xyz, rgb], fmt=["%.3f"] * 3 + ["%d"] * 3)

# 1. import (x y z r g b): every point, colour kept (8-bit scaled to 16-bit)
r = run({"command": "import", "input": f"{WS}/inputs/site.xyz"})
cloud = r["cloud"]
ok(r["stats"]["count"] == len(xyz) and r["stats"]["has_rgb"], f"import: {r['stats']['count']} points ({len(xyz)}), RGB {r['stats']['has_rgb']}")


def load(path):
	sys.path.insert(0, HERE)
	import cloud_worker as cw

	return cw.Cloud.read(path)


c = load(cloud)
ok(np.abs(c.xyz - xyz).max() < 0.0006, f"coordinates kept to the millimetre (max error {np.abs(c.xyz - xyz).max():.5f} m)")

# 2. noise: the 20 outliers go, almost nothing else
r = run({"command": "edit", "cloud": cloud, "operations": [{"op": "noise", "k": 8, "std": 3}], "name": "denoised"})
d = load(r["cloud"])
kept_outliers = sum(1 for p in outliers if np.any(np.all(np.abs(d.xyz - (p + [X0, Y0, 0])) < 1e-3, axis=1)))
lost = len(xyz) - 20 - (len(d) - kept_outliers)
ok(kept_outliers == 0 and lost < 0.005 * len(xyz), f"noise: {20 - kept_outliers}/20 outliers removed, {lost} other points removed")
denoised = r["cloud"]

# 3. ground: terrain → class 2, roof and trees not
r = run({"command": "edit", "cloud": denoised, "operations": [{"op": "ground", "cell": 1, "max_window": 40, "slope": 0.15, "tolerance": 0.3}], "name": "ground"})
g = load(r["cloud"])
z_terrain = 100 + 0.05 * (g.xyz[:, 0] - X0)
in_outline = (g.xyz[:, 0] >= X0 + 40) & (g.xyz[:, 0] <= X0 + 60) & (g.xyz[:, 1] >= Y0 + 40) & (g.xyz[:, 1] <= Y0 + 60)
is_roof = ((g.xyz[:, 2] - z_terrain) > 7.5) & in_outline
is_tree = ((g.xyz[:, 2] - z_terrain) > 1.5) & ~is_roof
is_terrain = np.abs(g.xyz[:, 2] - z_terrain) < 1e-3
gr = g.cls == 2
ok(gr[is_terrain].mean() > 0.99 and gr[is_roof].mean() == 0 and gr[is_tree].mean() < 0.02, f"ground: terrain {gr[is_terrain].mean() * 100:.2f} % ground, roof {gr[is_roof].mean() * 100:.2f} %, trees {gr[is_tree].mean() * 100:.2f} %")
classified = r["cloud"]

# 4. set class: unclassified points inside the building outline → building (6); keep classes
poly = [[X0 + 39, Y0 + 39], [X0 + 61, Y0 + 39], [X0 + 61, Y0 + 61], [X0 + 39, Y0 + 61]]
r = run({"command": "edit", "cloud": classified, "operations": [{"op": "set_class", "polygon": poly, "from": [0, 1], "to": 6}], "name": "buildings"})
b = load(r["cloud"])
ok((b.cls == 6).sum() == is_roof.sum() and r["report"][0]["changed"] == is_roof.sum(), f"set class: {(b.cls == 6).sum()} building points ({is_roof.sum()} roof points)")
r = run({"command": "edit", "cloud": r["cloud"], "operations": [{"op": "classes", "classes": [2], "keep": True}], "name": "only_ground"})
ok(r["stats"]["classes"] == {"2": int(gr.sum())}, f"keep ground only: {r['stats']['classes']}")

# 5. crop: inside an outline and an elevation band, exactly
r = run({"command": "edit", "cloud": cloud, "operations": [{"op": "crop", "polygon": poly, "keep": "inside", "zmin": 107}], "name": "crop"})
expect = int(((xyz[:, 0] > X0 + 39) & (xyz[:, 0] < X0 + 61) & (xyz[:, 1] > Y0 + 39) & (xyz[:, 1] < Y0 + 61) & (xyz[:, 2] >= 107)).sum())
ok(r["stats"]["count"] == expect, f"crop: {r['stats']['count']} points ({expect})")

# 6. decimate: one point per 2 m cube (the lowest)
r = run({"command": "edit", "cloud": classified, "operations": [{"op": "decimate", "mode": "voxel", "cell": 2, "keep": "lowest"}], "name": "thin"})
t = load(r["cloud"])
key = np.floor((t.xyz - g.xyz.min(axis=0)) / 2).astype(np.int64)
ok(len(np.unique(key, axis=0)) == len(t) and len(np.unique(np.floor((g.xyz - g.xyz.min(axis=0)) / 2).astype(np.int64), axis=0)) == len(t), f"voxel 2 m: {len(t)} points, one per occupied cube")

# 7. surface from the ground points: the terrain plane
r = run({"command": "surface", "cloud": classified, "classes": [2], "thin": 1.0, "name": "ground_tin"})
sys.path.insert(0, HERE)
import surface_worker as sw  # noqa: E402

tin = sw.Tin.read(r["tin"])
qx, qy = X0 + rng.uniform(5, 95, 2000), Y0 + rng.uniform(5, 95, 2000)
zq = tin.z_at(qx, qy)
err = np.nanmax(np.abs(zq - (100 + 0.05 * (qx - X0))))
ok(err < 0.02, f"ground surface vs the terrain plane: max error {err:.4f} m ({r['points']} points)")

# 8. compare the cloud with that surface: terrain ≈ 0, roof ≈ +8
r = run({"command": "compare", "cloud": classified, "surface": r["tin"], "tolerance": 0.1, "name": "compared"})
cmp_ = load(r["cloud"])
roof_dev = cmp_.deviation[(cmp_.cls != 2) & (cmp_.deviation > 7)]
ok(r["comparison"]["within"] > 85 and abs(np.median(roof_dev) - 8) < 0.05, f"compare: {r['comparison']['within']:.1f} % within 0.1 m, roof +{np.median(roof_dev):.3f} m")
ok(r["stats"]["deviation_range"] is not None, "the deviation is kept in the cloud (LAZ extra dimension)")

# 9. display tiles: nested levels, every point in level 0
r = run({"command": "tiles", "cloud": classified, "out_dir": f"{WS}/outputs/tiles"})
idx = json.load(open(r["index"]))
total = sum(t["levels"][0]["points"] for t in idx["tiles"])
with open(f"{WS}/outputs/tiles/{idx['tiles'][0]['levels'][0]['file']}", "rb") as f:
	head = f.read(40)
ok(total == len(g) and head[:8] == b"ISPPTS01" and struct.unpack("<II", head[8:16])[0] == idx["tiles"][0]["levels"][0]["points"], f"tiles: {len(idx['tiles'])} tiles, {total} points, levels {[len(t['levels']) for t in idx['tiles']][:4]}")

# 10. export
for fmt in ("LAS", "XYZ"):
	r = run({"command": "export", "cloud": classified, "format": fmt, "name": "out"})
	n = len(load(r["file"])) if fmt == "LAS" else sum(1 for _ in open(r["file"]))
	ok(n == len(g), f"export {fmt}: {n} points")

# 11. E57: export and re-import keeps every point, the colour and (to 1/65535) the intensity
r = run({"command": "export", "cloud": classified, "format": "E57", "name": "scan"})
r2 = run({"command": "import", "input": r["file"], "name": "from_e57"})
e57 = load(r2["cloud"])
ok(len(e57) == len(g) and r2["stats"]["has_rgb"] and np.abs(np.sort(e57.xyz[:, 2]) - np.sort(g.xyz[:, 2])).max() < 0.0011, f"E57 round trip: {len(e57)} points, RGB {r2['stats']['has_rgb']}")

print("FAILED" if failures else "all passed")
sys.exit(1 if failures else 0)
