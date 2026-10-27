"""Mining design geometry for the isolated CAD worker (Phase 4): haul-road alignments and road surfaces, and the
in-situ volumes of mining blocks per bench. Reuses the TIN tools of surface_worker.py (same folder).

Protocol as the other workers: one JSON request on stdin, JSON lines out (progress / log / result / error).

road            plan alignment fitted with circular curves, vertical alignment (keep / drape / grade / linear /
                profile) with parabolic vertical curves at the PVIs, crossfall and superelevation, edges, batters to the ground, a road surface (TIN) and its cut/fill.
block_volumes   material between an upper and a lower surface inside each block polygon and bench slab, exact
                (one composite triangulation with every block outline as a constraint; closed-form integrals).
geology_surfaces / idw_grid / block_geology   seam roof, floor and property surfaces from grids or drillhole
                intercepts, inverse-distance gridding, and seam volume, tonnes and tonnage-weighted qualities per block.
"""

import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I leaves the script's folder off sys.path

import surface_worker as sw  # noqa: E402
from surface_worker import Tin, emit, inside  # noqa: E402

# ---------------------------------------------------------------------------
# haul roads
# ---------------------------------------------------------------------------


def fit_alignment(points, radius, step):
	"""The plan alignment through a polyline's vertices with a circular curve of `radius` at every bend (a
	smaller one where the legs are too short for it, reported). Returns stations (chainage, x, y), the signed
	radius at each station (+: turning left, inf: straight) and notes."""
	p = np.asarray(points, float)[:, :2]
	keep = np.r_[True, np.linalg.norm(np.diff(p, axis=0), axis=1) > 1e-9]
	p = p[keep]
	if len(p) < 2:
		raise ValueError("The centreline needs at least two distinct points.")
	notes, short = [], []
	legs = np.diff(p, axis=0)
	lengths = np.linalg.norm(legs, axis=1)
	units = legs / lengths[:, None]
	pieces = []  # ("line", a, b) | ("arc", centre, start_angle, sweep, r)
	cursor = p[0]
	for i in range(1, len(p) - 1):
		d_in, d_out = units[i - 1], units[i]
		cross = d_in[0] * d_out[1] - d_in[1] * d_out[0]
		delta = math.atan2(cross, float(np.dot(d_in, d_out)))  # deflection, + = left
		if abs(delta) < 1e-9:
			continue
		if radius <= 0:  # no curves: the polyline's own bends
			pieces.append(("line", cursor, p[i]))
			cursor = p[i]
			continue
		t_len = radius * math.tan(abs(delta) / 2)
		available = min(lengths[i - 1], lengths[i]) / 2
		r = radius
		if t_len > available:
			r = available / math.tan(abs(delta) / 2)
			t_len = available
			short.append((i, r))
		a = p[i] - d_in * t_len
		b = p[i] + d_out * t_len
		left = np.array([-d_in[1], d_in[0]])
		centre = a + left * r * (1 if delta > 0 else -1)
		start = math.atan2(a[1] - centre[1], a[0] - centre[0])
		pieces.append(("line", cursor, a))
		pieces.append(("arc", centre, start, delta, r))
		cursor = b
	pieces.append(("line", cursor, p[-1]))
	if len(short) == 1:
		notes.append(f"Bend {short[0][0]}: the legs are too short for a {radius:g} m curve; {short[0][1]:.1f} m fitted.")
	elif short:
		worst = min(short, key=lambda x: x[1])
		notes.append(f"{len(short)} bends have legs too short for a {radius:g} m curve; smaller curves are fitted (the smallest {worst[1]:.1f} m, at bend {worst[0]}).")

	xs, ys, rs = [], [], []
	for piece in pieces:
		if piece[0] == "line":
			a, b = piece[1], piece[2]
			length = float(np.linalg.norm(b - a))
			n = max(1, int(math.ceil(length / step)))
			t = np.linspace(0, 1, n + 1)
			xs.append(a[0] + t * (b[0] - a[0]))
			ys.append(a[1] + t * (b[1] - a[1]))
			rs.append(np.full(n + 1, np.inf))
		else:
			_kind, centre, start, sweep, r = piece
			n = max(2, int(math.ceil(abs(sweep) * r / step)))
			ang = start + np.linspace(0, sweep, n + 1)
			xs.append(centre[0] + r * np.cos(ang))
			ys.append(centre[1] + r * np.sin(ang))
			rs.append(np.full(n + 1, r if sweep > 0 else -r))
	x, y, rad = np.concatenate(xs), np.concatenate(ys), np.concatenate(rs)
	# the shared ends of consecutive pieces: one station (the curve's radius wins)
	same = np.r_[False, np.hypot(np.diff(x), np.diff(y)) < 1e-9]
	for k in np.flatnonzero(same):
		if np.isinf(rad[k - 1]):
			rad[k - 1] = rad[k]
	x, y, rad = x[~same], y[~same], rad[~same]
	chainage = np.r_[0.0, np.cumsum(np.hypot(np.diff(x), np.diff(y)))]
	return chainage, x, y, rad, notes


def smooth_along(chainage, values, length):
	"""Moving average over `length` metres of chainage (superelevation run-off)."""
	if length <= 0 or len(values) < 3:
		return values
	grid = np.arange(0, chainage[-1] + 0.5, 0.5)
	on_grid = np.interp(grid, chainage, values)
	k = max(1, int(round(length / 0.5)))
	kernel = np.ones(k) / k
	padded = np.r_[np.full(k, on_grid[0]), on_grid, np.full(k, on_grid[-1])]
	smoothed = np.convolve(padded, kernel, mode="same")[k:-k]
	return np.interp(chainage, grid, smoothed)


def batter(ground, ex, ey, ez, ox, oy, cut_angle, fill_angle, reach=200.0, dt=0.5):
	"""Where batters from edge points (ex, ey, ez) going outwards (ox, oy) meet the ground: cut up at cut_angle where
	the ground is above the edge, fill down at fill_angle where it is below. Returns daylight x, y, z (the edge itself
	where the ground does not reach)."""
	t = np.arange(0, reach + dt, dt)
	g0 = ground.z_at(ex, ey)
	cut = g0 >= ez
	k = np.where(cut, math.tan(math.radians(cut_angle)), -math.tan(math.radians(fill_angle)))
	px = ex[:, None] + ox[:, None] * t[None, :]
	py = ey[:, None] + oy[:, None] * t[None, :]
	g = ground.z_at(px.ravel(), py.ravel()).reshape(px.shape)
	slope = ez[:, None] + k[:, None] * t[None, :]
	f = np.where(cut[:, None], g - slope, slope - g)  # > 0 until the batter meets the ground
	f = np.where(np.isfinite(f), f, -1.0)  # off the ground surface: stop there
	dx, dy, dz = ex.copy(), ey.copy(), ez.copy()
	rows, lo, hi = [], [], []
	for i in np.flatnonzero(np.isfinite(g0)):
		j = np.flatnonzero(f[i] <= 0)
		if len(j) and j[0] > 0:
			rows.append(i)
			lo.append(t[j[0] - 1])
			hi.append(t[j[0]])
	if not rows:
		return dx, dy, dz
	# the meeting point between the bracketing samples, by bisection (the ground is piecewise linear: a straight
	# interpolation between samples misses where the batter crosses a triangle edge)
	rows, lo, hi = np.asarray(rows), np.asarray(lo, float), np.asarray(hi, float)
	sign = np.where(cut[rows], 1.0, -1.0)
	for _ in range(40):
		mid = (lo + hi) / 2
		mx, my = ex[rows] + ox[rows] * mid, ey[rows] + oy[rows] * mid
		g = ground.z_at(mx, my)
		above = sign * (g - (ez[rows] + k[rows] * mid)) > 0
		above &= np.isfinite(g)
		lo, hi = np.where(above, mid, lo), np.where(above, hi, mid)
	s = (lo + hi) / 2
	dx[rows], dy[rows] = ex[rows] + ox[rows] * s, ey[rows] + oy[rows] * s
	gz = ground.z_at(dx[rows], dy[rows])
	dz[rows] = np.where(np.isfinite(gz), gz, ez[rows] + k[rows] * s)
	return dx, dy, dz


def vertical_curves(chainage, pv_ch, pv_z, length):
	"""Elevations along `chainage` on the grade lines through the PVIs (pv_ch, pv_z), with a symmetric parabolic
	vertical curve of `length` at each interior PVI (shortened to fit between its neighbours' tangents, reported).
	Returns z and the curves: {chainage, elevation, grade_in, grade_out (%), length, k (m per % of grade change), kind}."""
	pv_ch, pv_z = np.asarray(pv_ch, float), np.asarray(pv_z, float)
	z = np.interp(chainage, pv_ch, pv_z)
	curves = []
	if length <= 0 or len(pv_ch) < 3:
		return z, curves
	grades = np.diff(pv_z) / np.maximum(np.diff(pv_ch), 1e-9)
	room = np.diff(pv_ch)  # tangent lengths, shared by the curves at both of their ends
	for i in range(1, len(pv_ch) - 1):
		g1, g2 = grades[i - 1], grades[i]
		a = g2 - g1
		if abs(a) < 1e-6:
			continue
		half = min(length / 2, room[i - 1] / 2, room[i] / 2)
		if half <= 1e-6:
			continue
		bvc = pv_ch[i] - half
		sel = (chainage > bvc) & (chainage < pv_ch[i] + half)
		x = chainage[sel] - bvc
		z[sel] = pv_z[i] - g1 * half + g1 * x + a / (4 * half) * x * x
		curves.append(
			{
				"chainage": float(pv_ch[i]),
				"elevation": float(pv_z[i]),
				"grade_in": float(g1 * 100),
				"grade_out": float(g2 * 100),
				"length": float(2 * half),
				"k": float(2 * half / (abs(a) * 100)),
				"kind": "crest" if a < 0 else "sag",
				"shortened": bool(2 * half < length - 1e-6),
			}
		)
	return z, curves


def road(request, log):
	workspace = request["workspace"]
	o = request.get("options") or {}
	width = float(o.get("width") or 25)
	step = float(o.get("step") or 2.0)
	fit_radius = float(o.get("fit_radius") or 0)
	ground = Tin.read(inside(workspace, request["ground"])) if request.get("ground") else None
	centre = np.asarray(request["centreline"], float)
	if centre.shape[1] == 2:
		centre = np.c_[centre, np.zeros(len(centre))]
	emit(progress=5, log="Fitting the alignment…")
	chainage, x, y, rad, notes = fit_alignment(centre, fit_radius, step)
	length = float(chainage[-1])

	# vertical alignment
	mode = o.get("vertical") or "keep"
	if mode == "drape":
		if ground is None:
			raise ValueError("Choose the ground surface to drape the road on.")
		z = ground.z_at(x, y)
		if np.isnan(z).all():
			raise ValueError("The centreline is not over the ground surface.")
		good = np.isfinite(z)
		if not good.all():
			notes.append(f"{int((~good).sum())} stations are off the ground surface; their elevation is interpolated.")
			z = np.interp(chainage, chainage[good], z[good])
	elif mode == "grade":
		z = float(o["start_elevation"]) + float(o["gradient"]) / 100.0 * chainage
	elif mode == "profile":  # vertical intersection points (chainage, elevation)
		pvis = sorted([float(c), float(e)] for c, e in o.get("pvis") or [])
		if len(pvis) < 2:
			raise ValueError("Give at least two vertical intersection points (chainage, elevation).")
		pv = np.asarray(pvis)
		z = np.interp(chainage, pv[:, 0], pv[:, 1])
	elif mode == "linear":
		z0, z1 = float(o["start_elevation"]), float(o["end_elevation"])
		z = z0 + (z1 - z0) * chainage / max(length, 1e-9)
	else:  # keep the polyline's own elevations (by proportion of length)
		original = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(centre[:, :2], axis=0), axis=1))]
		z = np.interp(chainage * original[-1] / max(length, 1e-9), original, centre[:, 2])
	curves = []
	curve_length = float(o.get("vertical_curve_length") or 0)
	if curve_length > 0 and mode in ("keep", "profile"):
		if mode == "keep":  # the polyline's vertices are the PVIs
			pv_ch, pv_z = original * length / max(original[-1], 1e-9), centre[:, 2]
		else:
			pv_ch, pv_z = pv[:, 0], pv[:, 1]
		z, curves = vertical_curves(chainage, pv_ch, pv_z, curve_length)
		shortened = sum(1 for c in curves if c["shortened"])
		if shortened:
			notes.append(f"{shortened} vertical curves are shorter than {curve_length:g} m: their tangents are too short.")
	grade = np.r_[np.diff(z) / np.maximum(np.diff(chainage), 1e-9), 0.0] * 100
	if len(grade) > 1:
		grade[-1] = grade[-2]

	# cross slopes: crossfall on straights, superelevation towards the inside of curves (run-off smoothed)
	crossfall = float(o.get("crossfall") or 0) / 100
	emax = float(o.get("superelevation") or 0) / 100
	rmin = float(o.get("min_radius") or fit_radius or 1)
	curve = np.isfinite(rad)
	e = np.where(curve, np.minimum(emax, emax * rmin / np.maximum(np.abs(np.where(curve, rad, 1)), 1e-9)), 0.0)
	turn_left = curve & (rad > 0)
	s_left = np.where(curve & (emax > 0), np.where(turn_left, -e, e), -crossfall)
	s_right = np.where(curve & (emax > 0), np.where(turn_left, e, -e), -crossfall)
	runoff = float(o.get("runoff") or 20)
	s_left, s_right = smooth_along(chainage, s_left, runoff), smooth_along(chainage, s_right, runoff)

	# edges
	tx = np.gradient(x, chainage) if len(x) > 2 else np.full(len(x), x[-1] - x[0])
	ty = np.gradient(y, chainage) if len(y) > 2 else np.full(len(y), y[-1] - y[0])
	norm = np.hypot(tx, ty)
	tx, ty = tx / norm, ty / norm
	nx, ny = -ty, tx  # left
	half = width / 2
	lx, ly, lz = x + nx * half, y + ny * half, z + s_left * half
	rx, ry, rz = x - nx * half, y - ny * half, z + s_right * half
	emit(progress=35, log=f"{len(x)} stations over {length:.1f} m.")

	lines = {"centre": np.c_[x, y, z], "left_edge": np.c_[lx, ly, lz], "right_edge": np.c_[rx, ry, rz]}
	boundary_left, boundary_right = lines["left_edge"], lines["right_edge"]
	if ground is not None:
		cut_angle, fill_angle = float(o.get("cut_batter") or 45), float(o.get("fill_batter") or 34)
		dlx, dly, dlz = batter(ground, lx, ly, lz, nx, ny, cut_angle, fill_angle)
		drx, dry, drz = batter(ground, rx, ry, rz, -nx, -ny, cut_angle, fill_angle)
		lines["left_daylight"], lines["right_daylight"] = np.c_[dlx, dly, dlz], np.c_[drx, dry, drz]
		boundary_left, boundary_right = lines["left_daylight"], lines["right_daylight"]
	emit(progress=55, log="Building the road surface…")
	loop = np.r_[boundary_left, boundary_right[::-1]]
	data = {
		"points": [],
		"breaklines": [v.tolist() for v in lines.values()],
		"boundary": loop[:, :2].tolist(),
		"holes": [],
	}
	path = os.path.join(workspace, "inputs", "road_build.json")
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w") as f:
		json.dump(data, f)
	built = sw.build({"workspace": workspace, "input": path, "name": request.get("name") or "road"}, log)

	result = {
		"tin": built["tin"],
		"stats": built["stats"],
		"length": length,
		"lines": {k: v.tolist() for k, v in lines.items()},
		"stations": np.c_[chainage, x, y, z, grade, np.where(np.isfinite(rad), rad, 0.0), s_left * 100, s_right * 100].tolist(),
		"max_grade": float(np.max(np.abs(grade))) if len(grade) else 0.0,
		"min_radius": float(np.min(np.abs(rad[np.isfinite(rad)]))) if np.isfinite(rad).any() else None,
		"notes": notes,
		"vertical_curves": curves,
		"min_k": min((c["k"] for c in curves), default=None),
	}
	if ground is not None:
		emit(progress=75, log="Cut and fill…")
		v = sw.volume({"workspace": workspace, "base": request["ground"], "comparison": built["tin"]}, log)
		result["volume"] = v
	emit(progress=100)
	return result


# ---------------------------------------------------------------------------
# mining blocks
# ---------------------------------------------------------------------------


def positive_part(q, values, d):
	"""The parts of triangles q (n, 3, 2) where the linear function d (n, 3) is >= 0, with every array in values
	(name -> (n, 3)) interpolated to the new corners. Exact: triangles are cut along d = 0."""
	pos = d >= 0
	whole = pos.all(axis=1)
	mixed = pos.any(axis=1) & ~whole
	out_q = [q[whole]]
	out_v = {k: [v[whole]] for k, v in values.items()}
	for i in np.flatnonzero(mixed):
		poly, vals = [], []
		for k in range(3):
			a, b = k, (k + 1) % 3
			if d[i, a] >= 0:
				poly.append(q[i, a])
				vals.append({n: v[i, a] for n, v in values.items()})
			if (d[i, a] >= 0) != (d[i, b] >= 0):
				t = d[i, a] / (d[i, a] - d[i, b])
				poly.append(q[i, a] + t * (q[i, b] - q[i, a]))
				vals.append({n: v[i, a] + t * (v[i, b] - v[i, a]) for n, v in values.items()})
		for k in range(1, len(poly) - 1):  # fan
			out_q.append(np.array([[poly[0], poly[k], poly[k + 1]]]))
			for n in values:
				out_v[n].append(np.array([[vals[0][n], vals[k][n], vals[k + 1][n]]]))
	return np.concatenate(out_q), {k: np.concatenate(v) for k, v in out_v.items()}


def clipped_integral(f, area, a, b):
	"""∫ clip(f, a, b) over triangles with f linear (corner values), exactly: a·A + ∫(f−a)⁺ − ∫(f−b)⁺."""
	above_a = sw.split_volume(f - a, area)[0]
	above_b = sw.split_volume(f - b, area)[0]
	return a * area + above_a - above_b


def block_volumes(request, log):
	"""Material between the upper and lower surfaces (where upper is above lower) in every cell polygon and slab
	[bottom, top]. Returns {cell key: [volume per slab]} and the covered plan area per cell."""
	workspace = request["workspace"]
	upper = Tin.read(inside(workspace, request["upper"]))
	lower = Tin.read(inside(workspace, request["lower"]))
	cells = request["cells"]
	slabs = [(float(a), float(b)) for a, b in request["slabs"]]
	from matplotlib.path import Path

	paths = [Path(np.asarray(c["polygon"], float)[:, :2]) for c in cells]
	boxes = np.array([[p.vertices[:, 0].min(), p.vertices[:, 1].min(), p.vertices[:, 0].max(), p.vertices[:, 1].max()] for p in paths])
	volumes = np.zeros((len(cells), len(slabs)))
	areas = np.zeros(len(cells))
	loops = [np.asarray(c["polygon"], float)[:, :2] for c in cells]
	emit(progress=5, log=f"{len(cells)} block outlines × {len(slabs)} benches.")
	for u_tin, l_tin, xy, tri, ta, tb, cen in sw.composite_blocks(upper, lower, loops):
		if not len(tri):
			continue
		owner = np.full(len(tri), -1)
		for k, path in enumerate(paths):
			b = boxes[k]
			cand = np.flatnonzero((owner < 0) & (cen[:, 0] >= b[0]) & (cen[:, 0] <= b[2]) & (cen[:, 1] >= b[1]) & (cen[:, 1] <= b[3]))
			if len(cand):
				owner[cand[path.contains_points(cen[cand])]] = k
		keep = owner >= 0
		if not keep.any():
			continue
		tri, ta, tb, owner = tri[keep], ta[keep], tb[keep], owner[keep]
		q = xy[tri]
		zu = np.stack([sw.plane_z(u_tin.xyz, u_tin.tri, ta, q[:, k, 0], q[:, k, 1]) for k in range(3)], axis=1)
		zl = np.stack([sw.plane_z(l_tin.xyz, l_tin.tri, tb, q[:, k, 0], q[:, k, 1]) for k in range(3)], axis=1)
		own = np.repeat(owner[:, None], 3, axis=1).astype(float)
		q, vals = positive_part(q, {"u": zu, "l": zl, "own": own}, zu - zl)  # only where there is material
		if not len(q):
			continue
		cell = vals["own"][:, 0].astype(int)
		area = 0.5 * np.abs((q[:, 1, 0] - q[:, 0, 0]) * (q[:, 2, 1] - q[:, 0, 1]) - (q[:, 2, 0] - q[:, 0, 0]) * (q[:, 1, 1] - q[:, 0, 1]))
		np.add.at(areas, cell, area)
		for s, (a, b) in enumerate(slabs):
			v = clipped_integral(vals["u"], area, a, b) - clipped_integral(vals["l"], area, a, b)
			np.add.at(volumes[:, s], cell, v)
	emit(progress=100)
	return {
		"volumes": {c["key"]: volumes[k].tolist() for k, c in enumerate(cells)},
		"areas": {c["key"]: float(areas[k]) for k, c in enumerate(cells)},
		"method": "Material between the upper and lower surfaces, where the upper is above: both surfaces and every block"
		" outline are overlaid in one constrained Delaunay triangulation, triangles are cut exactly where the surfaces"
		" cross, and each bench slab is integrated in closed form (∫clip(z, bottom, top)).",
	}


# ---------------------------------------------------------------------------
# geology (Phase 6): seam and property surfaces, gridding, block geology
# ---------------------------------------------------------------------------


def grid_spacing(xy):
	"""Typical distance between neighbouring points (median nearest-neighbour distance)."""
	from scipy.spatial import cKDTree

	if len(xy) < 2:
		return 0.0
	d, _ = cKDTree(xy).query(xy, k=2)
	return float(np.median(d[:, 1]))


def geology_surfaces(request, log):
	"""A TIN per point set (seam roof / floor / topography elevations, or a property such as thickness or ash as Z),
	trimmed of long outer edges so it does not bridge gaps: max_edge "auto" = 2.5 × the points' spacing."""
	workspace = request["workspace"]
	out = []
	items = request["surfaces"]
	for n, item in enumerate(items):
		source = inside(workspace, item["points"])
		if source.endswith(".json"):
			with open(source) as fh:
				xyz = np.asarray(json.load(fh), float).reshape(-1, 3)
			source = os.path.join(workspace, "inputs", f"geology_{n}.npy")
			np.save(source, xyz)
		else:
			xyz = np.load(source)
		if len(xyz) < 3:
			raise ValueError(f"{item['key']}: fewer than three points.")
		spacing = grid_spacing(xyz[:, :2])
		max_edge = item.get("max_edge")
		if max_edge == "auto":
			max_edge = 2.5 * spacing if spacing > 0 else None
		path = os.path.join(workspace, "inputs", f"geology_{n}.json")
		with open(path, "w") as f:
			json.dump({"points_file": source, "max_edge": max_edge}, f)
		built = sw.build({"workspace": workspace, "input": path, "name": f"geology_{n}"}, log)
		out.append({"key": item["key"], "tin": built["tin"], "stats": built["stats"], "points": int(len(xyz)), "spacing": spacing, "max_edge": max_edge})
		emit(progress=int(100 * (n + 1) / len(items)), log=f"{item['key']}: {len(xyz)} points, spacing {spacing:.2f} m.")
	return {"surfaces": out}


def idw_grid(request, log):
	"""Inverse-distance-weighted grid of point values (x, y, v): `cell` spacing over the points' extent (plus
	`margin`), the `neighbours` nearest within `radius`, weights 1/d^power; nodes with no data in reach are left out.
	Returns the grid nodes as points (x, y, v) in a .npy file."""
	from scipy.spatial import cKDTree

	workspace = request["workspace"]
	pts = np.asarray(request["points"], float)
	cell = float(request["cell"])
	power = float(request.get("power") or 2)
	radius = float(request.get("radius") or np.inf)
	k = int(min(request.get("neighbours") or 12, len(pts)))
	margin = float(request.get("margin") or 0)
	if cell <= 0 or len(pts) < 3:
		raise ValueError("Inverse distance needs at least three points and a cell size.")
	lo, hi = pts[:, :2].min(axis=0) - margin, pts[:, :2].max(axis=0) + margin
	xs = np.arange(lo[0], hi[0] + cell * 0.5, cell)
	ys = np.arange(lo[1], hi[1] + cell * 0.5, cell)
	if len(xs) * len(ys) > 4_000_000:
		raise ValueError(f"{len(xs) * len(ys)} grid nodes: use a larger cell.")
	gx, gy = np.meshgrid(xs, ys, indexing="ij")
	q = np.c_[gx.ravel(), gy.ravel()]
	d, i = cKDTree(pts[:, :2]).query(q, k=k, distance_upper_bound=radius)
	d, i = d.reshape(len(q), k), i.reshape(len(q), k)
	ok = np.isfinite(d)
	exact = (d < 1e-9) & ok
	w = np.where(ok, 1.0 / np.maximum(d, 1e-12) ** power, 0.0)
	v = np.where(ok, pts[np.minimum(i, len(pts) - 1), 2], 0.0)
	wsum = w.sum(axis=1)
	val = np.where(wsum > 0, (w * v).sum(axis=1) / np.where(wsum > 0, wsum, 1), np.nan)
	hit = exact.any(axis=1)
	val[hit] = v[hit, np.argmax(exact[hit], axis=1)]
	keep = np.isfinite(val)
	out = os.path.join(workspace, "outputs", request.get("name", "idw") + ".npy")
	os.makedirs(os.path.dirname(out), exist_ok=True)
	np.save(out, np.c_[q[keep], val[keep]])
	return {"points": out, "nodes": int(keep.sum()), "cell": cell}


def composite_many(tins, loops):
	"""Composite triangulation of several TINs and loops: every edge of each is a constraint, so each composite
	triangle lies inside one triangle of every TIN it overlaps. Returns xy, tri, [triangle index per TIN, -1 off it]."""
	parts, edges, start = [], [], 0
	for t in tins:
		parts.append(t.xyz[:, :2])
		edges.append(sw.edge_list(t.tri) + start)
		start += len(t.xyz)
	for loop in loops:
		loop = np.asarray(loop, float)[:, :2]
		parts.append(loop)
		edges.append(sw.loop_edges(start, len(loop)))
		start += len(loop)
	pts = np.concatenate(parts)
	merged, inv, _ = sw.merge_points(np.c_[pts, np.zeros(len(pts))])
	e = np.sort(inv[np.concatenate(edges)], axis=1)
	e = sw.unique_rows(e[e[:, 0] != e[:, 1]])
	xy, tri, _pieces = sw.triangulate(merged[:, :2], e)
	cen = xy[tri].mean(axis=1)
	return xy, tri, cen, [t.locate(cen[:, 0], cen[:, 1]) for t in tins]


def product_integral(area, f, g):
	"""∫ f·g over triangles with f, g linear (corner values): A/12 (Σ fᵢgᵢ + Σfᵢ Σgᵢ)."""
	return area / 12.0 * ((f * g).sum(axis=1) + f.sum(axis=1) * g.sum(axis=1))


def clipped_product(q, f, g, a, b):
	"""∫ clip(f, a, b)·g over triangles q (f, g linear), exactly: a∫g + ∫(f−a)⁺g − ∫(f−b)⁺g, each positive part
	taken on the triangles cut along f = level (one side only, so nothing is counted twice)."""

	def tri_area(t):
		return 0.5 * np.abs((t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 2, 0] - t[:, 0, 0]) * (t[:, 1, 1] - t[:, 0, 1]))

	total = a * tri_area(q) * g.mean(axis=1)

	def above(level):
		pq, v = positive_part(q, {"f": f, "g": g, "own": np.repeat(np.arange(len(q))[:, None], 3, axis=1).astype(float)}, f - level)
		out = np.zeros(len(q))
		if len(pq):
			np.add.at(out, v["own"][:, 0].astype(int), product_integral(tri_area(pq), v["f"] - level, v["g"]))
		return out

	return total + above(a) - above(b)


def block_geology(request, log):
	"""Seam material in every block outline and bench slab, exactly: the roof, the floor, every property surface
	(thickness, ash, CV, density, …) and the block outlines are overlaid in one constrained triangulation, so all
	of them are linear on each triangle. Per block and slab: the seam volume ∫t (t = clip(roof) − clip(floor)),
	∫t·density (tonnes) and ∫t·q for every property q — tonnage-weighted means follow by division. Each property is
	counted only where it is defined (its own coverage)."""
	workspace = request["workspace"]
	roof = Tin.read(inside(workspace, request["roof"]))
	floor = Tin.read(inside(workspace, request["floor"]))
	names = list((request.get("properties") or {}).keys())
	props = [Tin.read(inside(workspace, request["properties"][n])) for n in names]
	cells = request["cells"]
	slabs = [(float(a), float(b)) for a, b in request["slabs"]]
	density_key = request.get("density")
	default_density = float(request.get("default_density") or 0)
	from matplotlib.path import Path

	loops = [np.asarray(c["polygon"], float)[:, :2] for c in cells]
	total = sum(len(t.tri) for t in [roof, floor, *props])
	if total > 3_000_000:
		raise ValueError(f"{total} triangles in the geology surfaces: too many for one block-geology run.")
	emit(progress=5, log=f"Overlaying {len(props) + 2} surfaces and {len(cells)} block outlines…")
	xy, tri, cen, idx = composite_many([roof, floor, *props], loops)
	owner = np.full(len(tri), -1)
	for k, loop in enumerate(loops):
		b0, b1 = loop.min(axis=0), loop.max(axis=0)
		cand = np.flatnonzero((owner < 0) & (cen[:, 0] >= b0[0]) & (cen[:, 0] <= b1[0]) & (cen[:, 1] >= b0[1]) & (cen[:, 1] <= b1[1]))
		if len(cand):
			owner[cand[Path(loop).contains_points(cen[cand])]] = k
	keep = (owner >= 0) & (idx[0] >= 0) & (idx[1] >= 0)
	tri, owner = tri[keep], owner[keep]
	idx = [i[keep] for i in idx]
	q = xy[tri]

	def corner_values(t, ti):
		return np.stack([sw.plane_z(t.xyz, t.tri, np.maximum(ti, 0), q[:, k, 0], q[:, k, 1]) for k in range(3)], axis=1)

	vals = {"r": corner_values(roof, idx[0]), "f": corner_values(floor, idx[1]), "own": np.repeat(owner[:, None], 3, axis=1).astype(float)}
	for n, (name, t) in enumerate(zip(names, props)):
		vals[f"p{n}"] = corner_values(t, idx[n + 2])
		vals[f"c{n}"] = np.repeat((idx[n + 2] >= 0)[:, None], 3, axis=1).astype(float)  # 1 where the property is defined
	emit(progress=40)
	q, vals = positive_part(q, vals, vals["r"] - vals["f"])  # only where the seam is (roof above floor)
	cell_of = vals["own"][:, 0].astype(int) if len(q) else np.zeros(0, int)
	ones = np.ones((len(q), 3))
	out = []
	for s, (a, b) in enumerate(slabs):
		# ∫ t·g with t = clip(roof, a, b) − clip(floor, a, b)
		def seam_integral(g):
			return clipped_product(q, vals["r"], g, a, b) - clipped_product(q, vals["f"], g, a, b)

		vol = seam_integral(ones) if len(q) else np.zeros(0)
		row = {"volume": np.bincount(cell_of, vol, minlength=len(cells))}
		for n, name in enumerate(names):
			cover = (vals[f"c{n}"][:, 0] > 0) if len(q) else np.zeros(0, bool)
			g = np.where(cover[:, None], vals[f"p{n}"], 0.0)
			row[f"tq:{name}"] = np.bincount(cell_of, seam_integral(g) if len(q) else np.zeros(0), minlength=len(cells))
			row[f"t:{name}"] = np.bincount(cell_of, np.where(cover, vol, 0.0), minlength=len(cells))
		out.append(row)
		emit(progress=40 + int(55 * (s + 1) / len(slabs)))
	result = {}
	for k, c in enumerate(cells):
		per = []
		for s in range(len(slabs)):
			r = out[s]
			v = float(r["volume"][k])
			entry = {"volume": v, "qualities": {}, "coverage": {}}
			for name in names:
				tv = float(r[f"t:{name}"][k])
				entry["coverage"][name] = tv / v if v > 0 else 0.0
				if tv > 0:
					entry["qualities"][name] = float(r[f"tq:{name}"][k]) / tv
			if density_key and density_key in names and v > 0:
				covered = float(r[f"t:{density_key}"][k])
				entry["tonnes"] = float(r[f"tq:{density_key}"][k]) + default_density * (v - covered)
			elif default_density and v > 0:
				entry["tonnes"] = default_density * v
			per.append(entry)
		result[c["key"]] = per
	emit(progress=100)
	return {
		"blocks": result,
		"method": "Roof, floor, property surfaces and block outlines in one constrained triangulation; per triangle all are"
		" linear, so the seam volume ∫(clip(roof) − clip(floor)) and the tonnage-weighted properties ∫t·q are integrated"
		" in closed form (A/12·(Σqᵢtᵢ + Σqᵢ·Σtᵢ)).",
	}


# ---------------------------------------------------------------------------
# pit-shell optimisation (Phase 6): regular block model, max closure, nested shells
# ---------------------------------------------------------------------------

INT_CAP = 2**31 - 1  # scipy's maximum_flow works in int32 (int64 capacities are silently truncated)


def max_closure(values, arcs):
	"""The closure (a set of nodes containing every predecessor of its members) of greatest total value: a minimum
	cut, source → positive nodes, negative nodes → sink, every arc (node → predecessor) uncuttable. values: float per
	node (float('-inf') = may not be mined); arcs: (n, 2) node → predecessor. Returns a boolean mask."""
	from scipy.sparse import csr_matrix
	from scipy.sparse.csgraph import breadth_first_order, maximum_flow

	v = np.asarray(values, float)
	n = len(v)
	if n == 0:
		return np.zeros(0, bool)
	forbidden = ~np.isfinite(v)
	finite = np.where(forbidden, 0.0, v)
	total = np.abs(finite).sum() + 1.0
	scale = 2**29 / total  # every cut fits in int32
	cap = np.rint(finite * scale).astype(np.int64)
	cap[forbidden] = -(2**30)  # never worth it
	s, t = n, n + 1
	pos, neg = np.flatnonzero(cap > 0), np.flatnonzero(cap < 0)
	arcs = np.asarray(arcs, np.int64).reshape(-1, 2)
	rows = np.concatenate([np.full(len(pos), s), neg, arcs[:, 0]])
	cols = np.concatenate([pos, np.full(len(neg), t), arcs[:, 1]])
	caps = np.concatenate([cap[pos], -cap[neg], np.full(len(arcs), INT_CAP, np.int64)])
	if not len(pos):
		return np.zeros(n, bool)
	graph = csr_matrix((caps.astype(np.int32), (rows, cols)), shape=(n + 2, n + 2))
	graph.sum_duplicates()
	flow = maximum_flow(graph, s, t, method="dinic").flow.tocsr()
	# residual graph: forward capacity − flow, plus reverse arcs carrying flow
	residual = (graph - flow).tocoo()
	keep = residual.data > 0
	reverse = flow.tocoo()
	back = reverse.data > 0
	res = csr_matrix((np.ones(keep.sum() + back.sum(), np.int8), (np.r_[residual.row[keep], reverse.col[back]], np.r_[residual.col[keep], reverse.row[back]])), shape=(n + 2, n + 2))
	reach = breadth_first_order(res, s, directed=True, return_predecessors=False)
	mask = np.zeros(n + 2, bool)
	mask[reach] = True
	return mask[:n]


def grid_edges(x0, y0, dx, dy, nx, ny):
	"""Every edge of a regular grid's cells, as points and index pairs (constraints for a composite triangulation)."""
	gx, gy = np.meshgrid(x0 + dx * np.arange(nx + 1), y0 + dy * np.arange(ny + 1), indexing="ij")
	pts = np.c_[gx.ravel(), gy.ravel()]
	idx = np.arange((nx + 1) * (ny + 1)).reshape(nx + 1, ny + 1)
	e = np.r_[np.c_[idx[:-1, :].ravel(), idx[1:, :].ravel()], np.c_[idx[:, :-1].ravel(), idx[:, 1:].ravel()]]
	return pts, e


def pit_shells(request, log):
	"""Nested pit shells on a regular block model (columns dx × dy, benches of `bench` metres from `top` down to
	`bottom`). Per cell, exactly: the in-situ volume below the ground and the coal tonnes of every seam (∫ thickness ×
	density); value = RF·price·recovery·t − processing·recovery·t − mining cost·volume. A cell needs the cells of the
	bench above within bench / tan(slope) of it. One maximum closure per revenue factor."""
	workspace = request["workspace"]
	g = request["grid"]
	x0, y0, dx, dy, nx, ny = float(g["x0"]), float(g["y0"]), float(g["dx"]), float(g["dy"]), int(g["nx"]), int(g["ny"])
	top, bench, nk = float(g["top"]), float(g["bench"]), int(g["benches"])
	if nx * ny * nk > 600_000:
		raise ValueError(f"{nx * ny * nk} blocks: use larger cells or fewer benches (at most 600 000).")
	ground = Tin.read(inside(workspace, request["ground"]))
	seams = [{"roof": Tin.read(inside(workspace, sm["roof"])), "floor": Tin.read(inside(workspace, sm["floor"])), "density": Tin.read(inside(workspace, sm["density"])) if sm.get("density") else None} for sm in request.get("seams") or []]
	default_density = float(request.get("default_density") or 1.5)
	tins = [ground]
	for sm in seams:
		tins += [sm["roof"], sm["floor"]] + ([sm["density"]] if sm["density"] is not None else [])
	emit(progress=5, log=f"Block model {nx} × {ny} × {nk} = {nx * ny * nk} blocks; overlaying {len(tins)} surfaces with the grid…")

	# composite of every surface and the grid lines: each triangle lies in one cell and one triangle of each surface
	gp, ge = grid_edges(x0, y0, dx, dy, nx, ny)
	parts, edges, start = [], [], 0
	for t in tins:
		parts.append(t.xyz[:, :2])
		edges.append(sw.edge_list(t.tri) + start)
		start += len(t.xyz)
	parts.append(gp)
	edges.append(ge + start)
	merged, inv, _ = sw.merge_points(np.c_[np.concatenate(parts), np.zeros(sum(len(p) for p in parts))])
	e = np.sort(inv[np.concatenate(edges)], axis=1)
	e = sw.unique_rows(e[e[:, 0] != e[:, 1]])
	xy, tri, _pieces = sw.triangulate(merged[:, :2], e)
	cen = xy[tri].mean(axis=1)
	ci = np.floor((cen[:, 0] - x0) / dx).astype(np.int64)
	cj = np.floor((cen[:, 1] - y0) / dy).astype(np.int64)
	inside_grid = (ci >= 0) & (ci < nx) & (cj >= 0) & (cj < ny)
	tg = ground.locate(cen[:, 0], cen[:, 1])
	keep = inside_grid & (tg >= 0)
	tri, ci, cj, cen = tri[keep], ci[keep], cj[keep], cen[keep]
	q = xy[tri]
	col = ci * ny + cj
	emit(progress=30, log=f"{len(tri)} composite triangles.")

	def corner(t, ti, qq):
		return np.stack([sw.plane_z(t.xyz, t.tri, np.maximum(ti, 0), qq[:, k, 0], qq[:, k, 1]) for k in range(3)], axis=1)

	def tri_area(qq):
		return 0.5 * np.abs((qq[:, 1, 0] - qq[:, 0, 0]) * (qq[:, 2, 1] - qq[:, 0, 1]) - (qq[:, 2, 0] - qq[:, 0, 0]) * (qq[:, 1, 1] - qq[:, 0, 1]))

	zg = corner(ground, tg[keep], q)
	area = tri_area(q)
	ncol = nx * ny
	volume = np.zeros((ncol, nk))
	tonnes = np.zeros((ncol, nk))
	coal_volume = np.zeros((ncol, nk))
	for k in range(nk):
		b = top - k * bench
		a = b - bench
		v = clipped_integral(zg, area, a, b) - a * area  # in-situ volume of the slab under the ground
		volume[:, k] = np.bincount(col, v, minlength=ncol)
	for n, sm in enumerate(seams):
		tr, tf = sm["roof"].locate(cen[:, 0], cen[:, 1]), sm["floor"].locate(cen[:, 0], cen[:, 1])
		ok = (tr >= 0) & (tf >= 0)
		if not ok.any():
			continue
		qq = q[ok]
		vals = {"r": corner(sm["roof"], tr[ok], qq), "f": corner(sm["floor"], tf[ok], qq), "own": np.repeat(col[ok][:, None], 3, axis=1).astype(float)}
		if sm["density"] is not None:
			td = sm["density"].locate(cen[ok, 0], cen[ok, 1])
			vals["d"] = np.where((td >= 0)[:, None], corner(sm["density"], td, qq), default_density)
		else:
			vals["d"] = np.full((len(qq), 3), default_density)
		pq, pv = positive_part(qq, vals, vals["r"] - vals["f"])
		if not len(pq):
			continue
		own = pv["own"][:, 0].astype(int)
		for k in range(nk):
			b = top - k * bench
			a = b - bench
			t = clipped_product(pq, pv["r"], pv["d"], a, b) - clipped_product(pq, pv["f"], pv["d"], a, b)
			tonnes[:, k] += np.bincount(own, t, minlength=ncol)
			one = np.ones_like(pv["d"])
			coal_volume[:, k] += np.bincount(own, clipped_product(pq, pv["r"], one, a, b) - clipped_product(pq, pv["f"], one, a, b), minlength=ncol)
		emit(progress=30 + int(30 * (n + 1) / max(len(seams), 1)))

	e_ = request["economics"]
	price, recovery = float(e_["price"]), float(e_.get("recovery", 100)) / 100
	mining, processing = float(e_["mining_cost"]), float(e_.get("processing_cost", 0))
	minable = np.ones(ncol, bool)
	if request.get("limit"):
		from matplotlib.path import Path

		cx = x0 + dx * (np.arange(nx)[:, None] + 0.5) + 0 * np.arange(ny)[None, :]
		cy = y0 + dy * (np.arange(ny)[None, :] + 0.5) + 0 * np.arange(nx)[:, None]
		minable = Path(np.asarray(request["limit"], float)[:, :2]).contains_points(np.c_[cx.ravel(), cy.ravel()])
	# nodes: cells with material (or under a cell with material); index = column · nk + bench (0 = top)
	node = np.arange(ncol * nk).reshape(ncol, nk)
	# precedence: the cells of the bench above within bench / tan(slope)
	reach = bench / math.tan(math.radians(float(request["slope"])))
	ri, rj = int(math.floor(reach / dx + 1e-9)), int(math.floor(reach / dy + 1e-9))
	offsets = [(a, b) for a in range(-ri, ri + 1) for b in range(-rj, rj + 1) if (a * dx) ** 2 + (b * dy) ** 2 <= reach**2 + 1e-9]
	arcs = []
	I, J = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
	I, J = I.ravel(), J.ravel()
	for a, b in offsets:
		ok = (I + a >= 0) & (I + a < nx) & (J + b >= 0) & (J + b < ny)
		src = (I * ny + J)[ok]
		dst = ((I + a) * ny + (J + b))[ok]
		for k in range(1, nk):
			arcs.append(np.c_[node[src, k], node[dst, k - 1]])
	arcs = np.concatenate(arcs) if arcs else np.zeros((0, 2), np.int64)
	emit(progress=65, log=f"{len(arcs)} slope arcs ({len(offsets)} per block: reach {reach:.1f} m at {request['slope']}°).")

	recovered = tonnes * recovery
	base_value = price * recovered - processing * recovered - mining * volume  # at RF = 1
	results, previous = [], np.zeros(ncol * nk, bool)
	factors = [float(f) for f in request["revenue_factors"]]
	for n, rf in enumerate(sorted(factors)):
		val = (rf * price * recovered - processing * recovered - mining * volume).ravel()
		val[~np.repeat(minable, nk)] = -np.inf
		mined = max_closure(val, arcs) | previous  # nested: a larger factor never mines less
		previous = mined
		m = mined.reshape(ncol, nk)
		coal = float(tonnes[m].sum())
		vol = float(volume[m].sum())
		waste = vol - float(coal_volume[m].sum())
		results.append(
			{
				"revenue_factor": rf,
				"blocks": int(m.sum()),
				"tonnes": coal,
				"recovered_tonnes": coal * recovery,
				"volume": vol,
				"waste_volume": waste,
				"strip_ratio": waste / coal if coal > 0 else None,
				"value": float(base_value[m].sum()),
				"value_at_rf": float(np.where(np.isfinite(val), val, 0).reshape(ncol, nk)[m].sum()),
				"depth": m.sum(axis=1).astype(int).tolist(),  # mined benches per column (contiguous from the top: each block needs the one above)
			}
		)
		emit(progress=65 + int(33 * (n + 1) / len(factors)), log=f"RF {rf:.2f}: {int(m.sum())} blocks, {coal:,.0f} t.")
	return {
		"grid": {"x0": x0, "y0": y0, "dx": dx, "dy": dy, "nx": nx, "ny": ny, "top": top, "bench": bench, "benches": nk},
		"shells": results,
		"totals": {"volume": float(volume.sum()), "tonnes": float(tonnes.sum())},
		"column_ground": [float(v) for v in np.bincount(col, (zg.mean(axis=1)) * area, minlength=ncol) / np.maximum(np.bincount(col, area, minlength=ncol), 1e-12)],
		"column_area": [float(v) for v in np.bincount(col, area, minlength=ncol)],
		"reach": reach,
		"method": "Regular block model; each block's in-situ volume and coal tonnes integrated exactly over the composite of the ground,"
		" the seam surfaces and the grid; block value = RF·price·recovery·t − processing·recovery·t − mining·volume; slope as"
		" precedence (the blocks of the bench above within bench / tan(slope)); the best closure of each revenue factor by"
		" minimum cut (Dinic), shells kept nested.",
	}


# ---------------------------------------------------------------------------
# post-mining surface
# ---------------------------------------------------------------------------


def merge(request, log):
	"""One surface from `inside` (e.g. a pit) within a loop (its daylight line) and `outside` (e.g. the ground) beyond
	it, exactly: every edge of both surfaces and the loop are constraints of one triangulation, so each new triangle
	lies inside one triangle of the surface it takes its elevations from."""
	workspace = request["workspace"]
	a = Tin.read(inside(workspace, request["inside"]))
	b = Tin.read(inside(workspace, request["outside"]))
	loop = np.asarray(request["loop"], float)[:, :2]
	parts = [a.xyz[:, :2], b.xyz[:, :2], loop]
	edges = [sw.edge_list(a.tri), sw.edge_list(b.tri) + len(a.xyz), sw.loop_edges(len(a.xyz) + len(b.xyz), len(loop))]
	merged, inv, _ = sw.merge_points(np.c_[np.concatenate(parts), np.zeros(sum(len(x) for x in parts))])
	e = np.sort(inv[np.concatenate(edges)], axis=1)
	e = sw.unique_rows(e[e[:, 0] != e[:, 1]])
	emit(progress=20, log=f"Overlaying {len(a.tri)} + {len(b.tri)} triangles and the {len(loop)}-point loop…")
	xy, tri, _pieces = sw.triangulate(merged[:, :2], e)
	cen = xy[tri].mean(axis=1)
	in_loop = sw.in_polygons(cen[:, 0], cen[:, 1], loop, [])
	ta, tb = a.locate(cen[:, 0], cen[:, 1]), b.locate(cen[:, 0], cen[:, 1])
	keep = np.where(in_loop, ta >= 0, tb >= 0)
	tri, in_loop, ta, tb = tri[keep], in_loop[keep], ta[keep], tb[keep]
	# vertex elevations from the surface of the triangles that use them (on the loop both surfaces agree)
	z = np.full(len(xy), np.nan)
	q = xy[tri]
	for k in range(3):
		za = sw.plane_z(a.xyz, a.tri, np.maximum(ta, 0), q[:, k, 0], q[:, k, 1])
		zb = sw.plane_z(b.xyz, b.tri, np.maximum(tb, 0), q[:, k, 0], q[:, k, 1])
		value = np.where(in_loop, za, zb)
		idx = tri[:, k]
		first = np.isnan(z[idx])
		z[idx[first]] = value[first]
		z[idx[in_loop]] = value[in_loop]  # the inside surface wins on the loop
	out = sw.compact(Tin(np.c_[xy, np.nan_to_num(z)], tri, np.zeros(len(xy), np.uint8)))
	emit(progress=80)
	return sw.finish_tin(out, {"workspace": workspace, "name": request.get("name") or "merged"}, log)


# ---------------------------------------------------------------------------
# drill holes (layout geometry only)
# ---------------------------------------------------------------------------


def drill_holes(request, log):
	"""Collar and toe of each hole: the collar on a surface (or at an elevation), the toe `subdrill` below the bench
	floor (a surface, or an elevation), along the hole's inclination (degrees from vertical) towards the dip
	direction (a plan unit vector). With a floor surface the toe is found by iteration (the toe moves as the floor
	under it changes)."""
	workspace = request["workspace"]
	xy = np.asarray(request["collars"], float).reshape(-1, 2)
	collar_tin = Tin.read(inside(workspace, request["collar_surface"])) if request.get("collar_surface") else None
	floor_tin = Tin.read(inside(workspace, request["floor_surface"])) if request.get("floor_surface") else None
	sub = float(request.get("subdrill") or 0)
	incl = math.radians(float(request.get("inclination") or 0))
	dip = np.asarray(request.get("dip") or [0, 0], float)
	n = np.linalg.norm(dip)
	dip = dip / n if n > 0 else np.zeros(2)
	zc = collar_tin.z_at(xy[:, 0], xy[:, 1]) if collar_tin is not None else np.full(len(xy), float(request["collar_elevation"]))
	if floor_tin is None:
		zf = np.full(len(xy), float(request["floor_elevation"]))
		toe_xy = xy + np.outer(np.maximum(zc - (zf - sub), 0) * math.tan(incl), dip)
	else:
		toe_xy = xy.copy()
		zf = floor_tin.z_at(toe_xy[:, 0], toe_xy[:, 1])
		for _ in range(6):  # the floor under the toe, then the toe from it
			toe_xy = xy + np.outer(np.maximum(zc - (zf - sub), 0) * math.tan(incl), dip)
			new = floor_tin.z_at(toe_xy[:, 0], toe_xy[:, 1])
			new = np.where(np.isfinite(new), new, zf)
			if np.allclose(new, zf, equal_nan=True, atol=1e-6):
				break
			zf = new
	toe_z = zf - sub
	depth = zc - toe_z
	length = depth / math.cos(incl)
	holes = []
	for k in range(len(xy)):
		problem = None
		if not np.isfinite(zc[k]):
			problem = "the collar is not over the collar surface"
		elif not np.isfinite(zf[k]):
			problem = "the toe is not over the floor surface"
		elif depth[k] <= 0:
			problem = "the collar is below the bench floor"
		holes.append(
			{
				"collar": [float(xy[k, 0]), float(xy[k, 1]), float(zc[k]) if np.isfinite(zc[k]) else None],
				"toe": [float(toe_xy[k, 0]), float(toe_xy[k, 1]), float(toe_z[k]) if np.isfinite(toe_z[k]) else None],
				"floor": float(zf[k]) if np.isfinite(zf[k]) else None,
				"length": float(length[k]) if np.isfinite(length[k]) else None,
				"problem": problem,
			}
		)
	bad = sum(1 for h in holes if h["problem"])
	if bad:
		log.append(f"{bad} of {len(holes)} holes cannot be placed.")
	return {"holes": holes}


COMMANDS = {"road": road, "block_volumes": block_volumes, "merge": merge, "drill_holes": drill_holes, "geology_surfaces": geology_surfaces, "idw_grid": idw_grid, "block_geology": block_geology, "pit_shells": pit_shells}


def main():
	log = []
	try:
		request = json.loads(sys.stdin.read() or "{}")
		command = COMMANDS.get(request.get("command"))
		if not command:
			raise ValueError(f"Unknown command {request.get('command')!r}.")
		sw.apply_limits(request)
		result = command(request, log)
		for line in log:
			emit(log=line)
		emit(result=result)
	except MemoryError:
		emit(error="The CAD worker ran out of its memory limit (CAD Settings → Worker Memory Limit).")
	except Exception as e:
		for line in log:
			emit(log=line)
		emit(error=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
	main()
