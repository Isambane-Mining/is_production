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

# 8. block geology: roof z = 100 + 0.01x, floor 2 m below, ash = 20 + 0.1x, density 1.5, on a 100 m square split in two
#    blocks; slab [99, 200] clips the floor at 99: t = 1 + 0.01x. Volume ∫t = 100·150 = 15 000 (west half
#    100·(50 + 12.5) = 6 250), ash weighted by t = 3833.33/150, tonnes = 1.5 · volume; the seam below 99 is 5 000.
def plane_tin(name, f, n=11):
	pts = [[100 * i / (n - 1), 100 * j / (n - 1), f(100 * i / (n - 1), 100 * j / (n - 1))] for i in range(n) for j in range(n)]
	with open(f"{WS}/inputs/{name}.json", "w") as fh:
		json.dump({"points": pts}, fh)
	return run("surface_worker.py", {"command": "build", "name": name, "input": f"{WS}/inputs/{name}.json"})["tin"]
roof = plane_tin("g_roof", lambda x, y: 100 + 0.01 * x)
floor = plane_tin("g_floor", lambda x, y: 98 + 0.01 * x)
ash = plane_tin("g_ash", lambda x, y: 20 + 0.1 * x, n=4)  # a coarser grid than the seam: its edges are constraints too
rd = plane_tin("g_rd", lambda x, y: 1.5, n=3)
cells = [{"key": "W", "polygon": [[0, 0], [50, 0], [50, 100], [0, 100]]}, {"key": "E", "polygon": [[50, 0], [100, 0], [100, 100], [50, 100]]}]
r = run("mining_worker.py", {"command": "block_geology", "roof": roof, "floor": floor, "properties": {"ash": ash, "rd": rd}, "density": "rd", "cells": cells, "slabs": [[99, 200], [0, 99]]})
w, e = r["blocks"]["W"], r["blocks"]["E"]
vol = w[0]["volume"] + e[0]["volume"]
ok(abs(vol - 15000) < 1e-6 and abs(w[0]["volume"] - 100 * (50 + 0.005 * 2500)) < 1e-6, f"seam volume above 99: {vol:.6f} (15000), west {w[0]['volume']:.6f} (6250)")
ash_all = (w[0]["qualities"]["ash"] * w[0]["volume"] + e[0]["qualities"]["ash"] * e[0]["volume"]) / vol
ok(abs(ash_all - 3833.3333333 / 150) < 1e-6, f"tonnage-weighted ash {ash_all:.6f} ({3833.3333333 / 150:.6f})")
ok(abs(w[0]["tonnes"] + e[0]["tonnes"] - 22500) < 1e-6 and abs(w[0]["coverage"]["ash"] - 1) < 1e-9, f"tonnes {w[0]['tonnes'] + e[0]['tonnes']:.6f} (22500), full ash coverage")
lower = w[1]["volume"] + e[1]["volume"]
ok(abs(lower + vol - 20000) < 1e-6, f"the rest of the seam is in the slab below 99: {lower:.6f} (5000)")

# 9. inverse distance: values at the data points are kept exactly, a constant field stays constant
r = run("mining_worker.py", {"command": "idw_grid", "points": [[0, 0, 5], [10, 0, 5], [0, 10, 5], [10, 10, 5]], "cell": 2.5, "power": 2})
g = np.load(r["points"])
ok(r["nodes"] == 25 and np.allclose(g[:, 2], 5), f"idw: {r['nodes']} nodes, constant 5")
r = run("mining_worker.py", {"command": "idw_grid", "points": [[0, 0, 1], [10, 0, 2], [0, 10, 3], [10, 10, 4]], "cell": 10})
g = np.load(r["points"])
ok(sorted(g[:, 2].tolist()) == [1, 2, 3, 4], "idw keeps the data values at the data points")

# 10. maximum closure (min cut) against brute force on random layered graphs
import itertools  # noqa: E402

sys.path.insert(0, HERE)
import mining_worker as mw  # noqa: E402

rng = np.random.default_rng(11)
worst = 0.0
for trial in range(40):
	n = 10
	layer = np.sort(rng.integers(0, 3, n))
	arcs = [(i, j) for i in range(n) for j in range(n) if layer[j] == layer[i] - 1 and rng.random() < 0.4]
	vals = rng.normal(0, 10, n)
	best = 0.0
	for bits in itertools.product([0, 1], repeat=n):
		if all(not bits[i] or bits[j] for i, j in arcs):
			best = max(best, float(np.dot(bits, vals)))
	got = mw.max_closure(vals, np.array(arcs).reshape(-1, 2))
	ok_closed = all(not got[i] or got[j] for i, j in arcs)
	worst = max(worst, abs(float(vals[got].sum()) - best) if ok_closed else 1e9)
ok(worst < 1e-6, f"max closure = brute force on 40 random graphs (worst difference {worst:.2e})")

# 11. pit shells on a 5 × 1 × 3 block model: flat ground 100, a 2 m seam 80–78 (density 1.5), 10 m cells and benches,
#     45° (the blocks above and beside), price 12 / t, mining 1 / bcm: brute force over all 2^15 closures
gnd = plane_tin("p_ground", lambda x, y: 100.0)
sroof = plane_tin("p_roof", lambda x, y: 80.0)
sfloor = plane_tin("p_floor", lambda x, y: 78.0)
req = {"command": "pit_shells", "ground": gnd, "seams": [{"roof": sroof, "floor": sfloor}], "default_density": 1.5, "grid": {"x0": 0, "y0": 0, "dx": 10, "dy": 10, "nx": 5, "ny": 1, "top": 100, "bench": 10, "benches": 3}, "economics": {"price": 12, "mining_cost": 1, "recovery": 100}, "slope": 45, "revenue_factors": [0.5, 1.0, 2.0]}
r = run("mining_worker.py", req)
ok(abs(r["totals"]["volume"] - 50 * 10 * 30) < 1e-6 and abs(r["totals"]["tonnes"] - 5 * 300) < 1e-6, f"block model volume {r['totals']['volume']:.6f} (15000), tonnes {r['totals']['tonnes']:.6f} (1500)")


def brute(rf, forbid=()):
	nodes = [(c, k) for c in range(5) for k in range(3)]
	value = {(c, k): (rf * 12 * 300 if k == 2 else 0) - 1000 for c, k in nodes}
	best = 0.0
	for bits in itertools.product([0, 1], repeat=15):
		m = {nd: b for nd, b in zip(nodes, bits)}
		if any(m[(c, k)] for c in forbid for k in range(3)):
			continue
		if all(not m[(c, k)] or all(m[(c2, k - 1)] for c2 in (c - 1, c, c + 1) if 0 <= c2 < 5) for c, k in nodes if k > 0):
			best = max(best, sum(value[nd] for nd in nodes if m[nd]))
	return best


diffs = [abs(s_["value_at_rf"] - brute(s_["revenue_factor"])) for s_ in r["shells"]]
full = r["shells"][-1]
ok(abs(full["waste_volume"] - (15000 - 1000)) < 1e-6 and abs(full["strip_ratio"] - 14000 / 1500) < 1e-9, f"waste {full['waste_volume']:.6f} (14000), strip ratio {full['strip_ratio']:.6f} bcm/t")
ok(max(diffs) < 1e-6 and [s_["blocks"] for s_ in r["shells"]] == sorted(s_["blocks"] for s_ in r["shells"]), f"shell values = brute force at RF 0.5/1/2 ({[round(s_['value_at_rf']) for s_ in r['shells']]}), nested {[s_['blocks'] for s_ in r['shells']]}")
r2 = run("mining_worker.py", {**req, "limit": [[10, -1], [51, -1], [51, 11], [10, 11]], "revenue_factors": [2.0]})
ok(abs(r2["shells"][0]["value_at_rf"] - brute(2.0, forbid=(0,))) < 1e-6 and r2["shells"][0]["depth"][0] == 0, f"mining limit: column 0 left, value {r2['shells'][0]['value_at_rf']:.0f} = {brute(2.0, forbid=(0,)):.0f}")

print("FAILED" if failures else "all passed")
sys.exit(1 if failures else 0)
