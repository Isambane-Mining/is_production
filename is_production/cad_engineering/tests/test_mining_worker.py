"""Exercise mining_worker.py (Phase 4) against closed-form answers.

Run with the CAD worker Python:  /opt/cad-worker/venv/bin/python -I test_mining_worker.py ../cad_worker <empty folder>
"""
import json, math, os, subprocess, sys
import numpy as np

HERE, WS = sys.argv[1], sys.argv[2]
sys.path.insert(0, HERE)
import surface_worker as sw  # noqa: E402

os.makedirs(WS + "/outputs", exist_ok=True)
os.makedirs(WS + "/inputs", exist_ok=True)
failures = 0


def ok(cond, text):
	global failures
	print(("ok   " if cond else "FAIL ") + text)
	failures += 0 if cond else 1


def run(script, req):
	req["workspace"] = WS
	p = subprocess.run([sys.executable, "-I", os.path.join(HERE, script)], input=json.dumps(req), capture_output=True, text=True)
	lines = [json.loads(line) for line in p.stdout.splitlines() if line.strip()]
	res = [line for line in lines if "result" in line or "error" in line][-1]
	if "error" in res:
		raise SystemExit(f"{script} {req['command']}: {res['error']}\n{p.stderr}")
	return res["result"]


def plane(name, z_of, x0=0.0, y0=0.0, size=100.0, n=11):
	pts = [[x0 + i * size / (n - 1), y0 + j * size / (n - 1), z_of(x0 + i * size / (n - 1), y0 + j * size / (n - 1))] for i in range(n) for j in range(n)]
	with open(f"{WS}/inputs/{name}.json", "w") as f:
		json.dump({"points": pts}, f)
	return run("surface_worker.py", {"command": "build", "name": name, "input": f"{WS}/inputs/{name}.json"})["tin"]


# 1. blocks: upper z = 100, lower z = 60 + 0.4x on a 100 m square; four quadrant blocks, two 20 m benches
upper = plane("upper", lambda x, y: 100.0)
lower = plane("lower", lambda x, y: 60 + 0.4 * x)
quads = [{"key": f"Q{i}{j}", "polygon": [[50 * i, 50 * j], [50 * i + 50, 50 * j], [50 * i + 50, 50 * j + 50], [50 * i, 50 * j + 50]]} for i in range(2) for j in range(2)]
r = run("mining_worker.py", {"command": "block_volumes", "upper": upper, "lower": lower, "cells": quads, "slabs": [[60, 80], [80, 100]]})
v = r["volumes"]
expect = {"Q00": [25000, 50000], "Q01": [25000, 50000], "Q10": [0, 25000], "Q11": [0, 25000]}
err = max(abs(v[k][s] - expect[k][s]) for k in expect for s in range(2))
ok(err < 1e-6, f"block volumes per bench slab: max error {err:.2e} (e.g. Q00 {v['Q00']})")

# 2. the lower surface rises above the upper one (no material there): lower = 60 + 0.5x crosses z = 100 at x = 80
lower2 = plane("lower2", lambda x, y: 60 + 0.5 * x)
r = run("mining_worker.py", {"command": "block_volumes", "upper": upper, "lower": lower2, "cells": [{"key": "ALL", "polygon": [[0, 0], [100, 0], [100, 100], [0, 100]]}], "slabs": [[0, 200]]})
ok(abs(r["volumes"]["ALL"][0] - 160000) < 1e-6 and abs(r["areas"]["ALL"] - 8000) < 1e-6, f"material only where the upper surface is above: {r['volumes']['ALL'][0]:.6f} (160000), area {r['areas']['ALL']:.3f} (8000)")

# 3. blocks across several composite blocks (large-surface path) give the same answer, in process
sw.emit = lambda **k: None
import mining_worker as mw  # noqa: E402

mw.emit = sw.emit
sw.COMPOSITE_BLOCK_TRIANGLES = 40
r2 = mw.block_volumes({"workspace": WS, "upper": upper, "lower": lower, "cells": quads, "slabs": [[60, 80], [80, 100]]}, [])
err2 = max(abs(r2["volumes"][k][s] - expect[k][s]) for k in expect for s in range(2))
ok(err2 < 1e-6, f"block volumes with the composite split into blocks: max error {err2:.2e}")
sw.COMPOSITE_BLOCK_TRIANGLES = 250_000

# 4. road on fill: flat ground z = 100, a straight 200 m road rising at 5 % from 100, 20 m wide, 45° fill batters.
#    The batters are planes, so the fill is exact: ∫ (20h + h²) dc with h = 0.05c = 20000 + 6666.667
ground = plane("ground", lambda x, y: 100.0, x0=-50, y0=-100, size=300, n=13)
r = run("mining_worker.py", {"command": "road", "ground": ground, "centreline": [[0, 0, 100], [200, 0, 100]], "options": {"width": 20, "vertical": "grade", "start_elevation": 100, "gradient": 5, "crossfall": 0, "superelevation": 0, "fill_batter": 45, "cut_batter": 45}})
vol = r["volume"]
ok(abs(vol["fill"] - 26666.6667) < 0.01 and vol["cut"] < 1e-6 and vol["valid"], f"road fill {vol['fill']:.4f} (26666.667), cut {vol['cut']:.6f}")
ok(abs(r["max_grade"] - 5) < 1e-9 and abs(r["length"] - 200) < 1e-9, f"road grade {r['max_grade']:.6f} %, length {r['length']}")
ld = np.asarray(r["lines"]["left_daylight"])
ok(abs(ld[-1][1] - (10 + 10)) < 1e-3 and abs(ld[-1][2] - 100) < 1e-6, f"daylight at the end: offset {ld[-1][1]:.4f} (20), z {ld[-1][2]:.4f}")

# 5. horizontal alignment: an L with a 40 m curve: length 2·60 + 40·π/2, minimum radius 40
r = run("mining_worker.py", {"command": "road", "centreline": [[0, 0, 50], [100, 0, 50], [100, 100, 50]], "options": {"width": 10, "fit_radius": 40, "step": 1, "vertical": "keep", "crossfall": 2, "superelevation": 4, "min_radius": 40}})
ok(abs(r["length"] - (120 + 20 * math.pi)) < 0.05 and abs(r["min_radius"] - 40) < 1e-9, f"fitted alignment length {r['length']:.3f} ({120 + 20 * math.pi:.3f}), radius {r['min_radius']}")
st = np.asarray(r["stations"])
mid = st[np.argmin(np.abs(st[:, 0] - r["length"] / 2))]
ok(abs(mid[6] + 4) < 1e-6 and abs(mid[7] - 4) < 1e-6, f"superelevation on the curve: left {mid[6]:.3f} % (inside, -4), right {mid[7]:.3f} % (+4)")
r = run("mining_worker.py", {"command": "road", "centreline": [[0, 0, 50], [30, 0, 50], [30, 30, 50]], "options": {"width": 10, "fit_radius": 40, "vertical": "keep"}})
ok(r["notes"] and r["min_radius"] < 40, f"short legs: a smaller curve is fitted and reported ({r['min_radius']:.2f} m)")

# 6. no curve radius: the polyline's own bends are kept (an L of 100 + 100 m)
r = run("mining_worker.py", {"command": "road", "centreline": [[0, 0, 50], [100, 0, 40], [100, 100, 50]], "options": {"width": 10, "fit_radius": 0, "vertical": "keep"}})
st = np.asarray(r["stations"])
corner = st[np.argmin(np.abs(st[:, 0] - 100))]
ok(abs(r["length"] - 200) < 1e-9 and abs(corner[1] - 100) < 1e-9 and abs(corner[2]) < 1e-9 and abs(corner[3] - 40) < 1e-9, f"radius 0 keeps the bend: length {r['length']}, corner at ({corner[1]:.3f}, {corner[2]:.3f}, {corner[3]:.3f})")

# 7. vertical curves at the vertices: -10 % then +10 %, a 40 m sag curve: z at the PVI = 40 + A·L/8 = 41, K = 2
r = run("mining_worker.py", {"command": "road", "centreline": [[0, 0, 50], [100, 0, 40], [200, 0, 50]], "options": {"width": 10, "vertical": "keep", "vertical_curve_length": 40, "step": 1}})
(c,) = r["vertical_curves"]
st = np.asarray(r["stations"])
z = dict(zip(np.round(st[:, 0], 6), st[:, 3]))
ok(c["kind"] == "sag" and abs(c["k"] - 2) < 1e-9 and abs(z[100.0] - 41) < 1e-9 and abs(z[80.0] - 42) < 1e-9 and abs(z[120.0] - 42) < 1e-9, f"sag curve: K {c['k']}, z at PVI {z[100.0]:.6f} (41), ends {z[80.0]:.6f} / {z[120.0]:.6f} (42)")
r = run("mining_worker.py", {"command": "road", "centreline": [[0, 0, 0], [200, 0, 0]], "options": {"width": 10, "vertical": "profile", "pvis": [[0, 100], [30, 103], [200, 86]], "vertical_curve_length": 100, "step": 1}})
(c,) = r["vertical_curves"]
ok(c["shortened"] and abs(c["length"] - 30) < 1e-9 and r["notes"], f"a curve longer than its tangents allow is shortened to {c['length']} m and reported")

print("FAILED" if failures else "all passed")
sys.exit(1 if failures else 0)
