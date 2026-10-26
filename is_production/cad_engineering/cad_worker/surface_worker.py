"""CAD worker: surfaces (TINs) and surface analysis for the CAD Engineering Workstation.

Runs under the isolated CAD worker Python (``/opt/cad-worker/venv/bin/python -I surface_worker.py``), never
inside the bench, with the same rules as geometry_worker.py: a bounded JSON request on stdin, files only inside
the job workspace, JSON lines out (``progress`` / ``log`` / ``result`` / ``error``).

The authoritative surface is a TIN file (``.tin``)::

    "ISPTIN01", uint32 vertex count, uint32 triangle count, uint32 flags,
    float64 vertices (x, y, z) × n, uint32 triangles (a, b, c) × m, uint8 vertex flags × n
    vertex flags: 1 = breakline vertex, 2 = boundary vertex (kept by smoothing and repairs)

Triangles are counter-clockwise in plan. Coordinates are model coordinates (double precision).

Commands
--------
``build``      TIN from points and breaklines (constrained Delaunay, CDT), optional boundary and holes,
               optional maximum outer edge length; reports merged/conflicting points.
``tiles``      display tiles: per tile the full triangles plus two resampled levels (view-driven loading).
``validate``   degenerate, flipped and duplicate triangles, non-manifold edges, spikes, holes.
``repair``     remove degenerate/duplicate triangles, reorient flipped ones, remove spike vertices.
``clip``       keep the part inside (or outside) boundary polygons; boundary points get the surface's Z.
``smooth``     Laplacian smoothing of elevations (breakline and boundary vertices fixed).
``contours``   contour lines at an interval (marching triangles, chained into polylines).
``profile``    exact long-section along an alignment for one or more surfaces (every TIN edge crossing).
``sections``   cross-sections at an interval along an alignment.
``volume``     exact cut/fill between two TINs (or a TIN and an elevation) inside an optional boundary.
``intersect``  the line where two surfaces meet.
``analyse``    slope / aspect / elevation statistics.
``import``     LandXML surfaces and points, CSV/XYZ points, STL/OBJ meshes, LAS/LAZ point clouds.
``export``     LandXML, STL, OBJ, CSV/XYZ.

Libraries: NumPy (BSD), CDT via PythonCDT (MPL-2.0), matplotlib's triangle finder (matplotlib licence, BSD-style),
laspy (BSD) with lazrs (MIT) for LAZ.
"""

import json
import math
import os
import resource
import struct
import sys
import xml.etree.ElementTree as ET

import numpy as np

MAGIC = b"ISPTIN01"
MESH_MAGIC = b"ISPCADM1"
MERGE_TOLERANCE = 1e-6  # points closer than this in plan are one point
FLAG_BREAKLINE, FLAG_BOUNDARY = 1, 2
TILE_TRIANGLES = 60000
COMPOSITE_BLOCK_TRIANGLES = 250_000
GRID_LOCATOR_TRIANGLES = 500_000  # larger TINs locate points with GridLocator (memory)  # larger surfaces are overlaid block by block (memory)
LEVELS = (96, 24)  # resampled grid cells per tile side for the coarser display levels
MAX_POINTS = 20_000_000


def emit(**message):
	sys.stdout.write(json.dumps(message) + "\n")
	sys.stdout.flush()


def apply_limits(request):
	nice = int(request.get("nice") or 0)
	if nice > 0:
		os.nice(min(nice, 19))
	memory = int(request.get("memory_limit_mb") or 0)
	if memory > 0:
		resource.setrlimit(resource.RLIMIT_AS, (memory * 1024 * 1024,) * 2)
	cpu = int(request.get("cpu_seconds") or 0)
	if cpu > 0:
		resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))


def inside(workspace, path):
	real, root = os.path.realpath(path), os.path.realpath(workspace)
	if real != root and not real.startswith(root + os.sep):
		raise ValueError(f"Path outside the job workspace: {path}")
	return real


# ---------------------------------------------------------------------------
# TIN files
# ---------------------------------------------------------------------------


class Tin:
	def __init__(self, xyz, tri, flags=None):
		self.xyz = np.ascontiguousarray(xyz, dtype=np.float64)
		self.tri = np.ascontiguousarray(tri, dtype=np.int64)
		self.flags = np.zeros(len(self.xyz), dtype=np.uint8) if flags is None else np.ascontiguousarray(flags, dtype=np.uint8)
		self._finder = None

	def write(self, path):
		with open(path, "wb") as f:
			f.write(struct.pack("<8sIII", MAGIC, len(self.xyz), len(self.tri), 0))
			f.write(self.xyz.astype("<f8").tobytes())
			f.write(self.tri.astype("<u4").tobytes())
			f.write(self.flags.tobytes())

	@staticmethod
	def read(path):
		with open(path, "rb") as f:
			data = f.read()
		magic, nv, nt, _ = struct.unpack_from("<8sIII", data, 0)
		if magic != MAGIC:
			raise ValueError("Not a CAD surface (TIN) file.")
		o = 20
		xyz = np.frombuffer(data, "<f8", nv * 3, o).reshape(nv, 3).copy()
		o += nv * 24
		tri = np.frombuffer(data, "<u4", nt * 3, o).reshape(nt, 3).astype(np.int64)
		o += nt * 12
		flags = np.frombuffer(data, np.uint8, nv, o).copy() if len(data) >= o + nv else None
		if nt and (tri.max() >= nv):
			raise ValueError("The TIN refers to vertices it does not have.")
		return Tin(xyz, tri, flags)

	def finder(self):
		if self._finder is None:
			if len(self.tri) > GRID_LOCATOR_TRIANGLES:
				self._finder = GridLocator(self.xyz, self.tri)  # the trapezoid map needs gigabytes at this size
			else:
				from matplotlib.tri import Triangulation, TrapezoidMapTriFinder

				try:
					self._finder = TrapezoidMapTriFinder(Triangulation(self.xyz[:, 0], self.xyz[:, 1], self.tri))
				except RuntimeError:  # "Triangulation is invalid": slivers or zero-area triangles; the grid locator copes
					self._finder = GridLocator(self.xyz, self.tri)
		return self._finder

	def locate(self, x, y):
		return self.finder()(np.asarray(x, float), np.asarray(y, float))

	def z_at(self, x, y, triangles=None):
		"""Elevation at plan points (NaN outside the surface)."""
		x, y = np.asarray(x, float), np.asarray(y, float)
		t = self.locate(x, y) if triangles is None else triangles
		z = np.full(x.shape, np.nan)
		ok = t >= 0
		z[ok] = plane_z(self.xyz, self.tri, t[ok], x[ok], y[ok])
		return z

	def stats(self):
		p = self.xyz[self.tri] if len(self.tri) else np.zeros((0, 3, 3))
		cross = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0]) if len(p) else np.zeros((0, 3))
		used = used_vertices(self.tri, len(self.xyz))
		xyz = self.xyz[used] if len(used) else self.xyz
		box = [float(v) for v in np.r_[xyz.min(0), xyz.max(0)]] if len(xyz) else [0.0] * 6
		return {
			"vertex_count": int(len(used)),
			"triangle_count": int(len(self.tri)),
			"bbox": box,
			"min_elevation": box[2],
			"max_elevation": box[5],
			"plan_area": float(0.5 * np.abs(cross[:, 2]).sum()),
			"surface_area": float(0.5 * np.linalg.norm(cross, axis=1).sum()),
		}


class GridLocator:
	"""Which triangle contains each plan point (-1: none), for TINs of millions of triangles: triangles are listed
	in the cells of a uniform grid that their extent covers, and a point is tested only against its cell's
	triangles. Memory grows with the triangles, not with their arrangement."""

	CHUNK = 500_000

	def __init__(self, xyz, tri):
		self.xyz, self.tri = xyz, tri
		p = xyz[tri][:, :, :2]
		mn, mx = p.min(axis=1), p.max(axis=1)
		del p
		self.origin = mn.min(axis=0)
		span = np.maximum(mx.max(axis=0) - self.origin, 1e-9)
		self.size = max(math.sqrt(span[0] * span[1] / max(len(tri), 1)) * 1.5, 1e-9)  # ~2 triangles per cell
		self.shape = np.maximum(np.ceil(span / self.size).astype(np.int64), 1)
		i0, j0 = self.cell(mn[:, 0], mn[:, 1])
		i1, j1 = self.cell(mx[:, 0], mx[:, 1])
		ni, nj = i1 - i0 + 1, j1 - j0 + 1
		count = ni * nj
		t = np.repeat(np.arange(len(tri)), count)
		k = np.arange(len(t)) - np.repeat(np.cumsum(count) - count, count)
		cells = (i0[t] + k // nj[t]) * self.shape[1] + (j0[t] + k % nj[t])
		order = np.argsort(cells, kind="stable")
		self.members = t[order]
		self.starts = np.searchsorted(cells[order], np.arange(self.shape[0] * self.shape[1] + 1))

	def cell(self, x, y):
		i = np.clip(((x - self.origin[0]) / self.size).astype(np.int64), 0, self.shape[0] - 1)
		j = np.clip(((y - self.origin[1]) / self.size).astype(np.int64), 0, self.shape[1] - 1)
		return i, j

	def __call__(self, x, y):
		x, y = np.ravel(x), np.ravel(y)
		out = np.full(len(x), -1, np.int64)
		for s in range(0, len(x), self.CHUNK):
			out[s : s + self.CHUNK] = self.locate_chunk(x[s : s + self.CHUNK], y[s : s + self.CHUNK])
		return out

	def locate_chunk(self, x, y):
		result = np.full(len(x), -1, np.int64)
		ok = np.isfinite(x) & np.isfinite(y)
		i, j = self.cell(np.where(ok, x, self.origin[0]), np.where(ok, y, self.origin[1]))
		c = i * self.shape[1] + j
		a, b = self.starts[c], self.starts[c + 1]
		count = np.where(ok, b - a, 0)
		q = np.repeat(np.arange(len(x)), count)  # one row per (point, candidate triangle)
		if not len(q):
			return result
		k = np.arange(len(q)) - np.repeat(np.cumsum(count) - count, count)
		t = self.members[a[q] + k]
		p = self.xyz[self.tri[t]]
		px, py = x[q] - p[:, 2, 0], y[q] - p[:, 2, 1]
		ax, ay = p[:, 0, 0] - p[:, 2, 0], p[:, 0, 1] - p[:, 2, 1]
		bx, by = p[:, 1, 0] - p[:, 2, 0], p[:, 1, 1] - p[:, 2, 1]
		den = ax * by - ay * bx
		safe = np.where(den == 0, 1, den)
		l1 = (px * by - py * bx) / safe
		l2 = (ax * py - ay * px) / safe
		tol = -1e-12
		hit = (den != 0) & (l1 >= tol) & (l2 >= tol) & (1 - l1 - l2 >= tol)
		result[q[hit][::-1]] = t[hit][::-1]  # the first containing triangle wins
		return result


def plane_z(xyz, tri, t, x, y):
	"""Elevation of the plane of triangles t at plan points (x, y)."""
	p = xyz[tri[t]]
	a, b, c = p[:, 0], p[:, 1], p[:, 2]
	n = np.cross(b - a, c - a)
	return a[:, 2] - (n[:, 0] * (x - a[:, 0]) + n[:, 1] * (y - a[:, 1])) / n[:, 2]


def merge_points(xyz, tolerance=MERGE_TOLERANCE):
	"""Points closer than tolerance in plan become one (the first one's Z). Returns (points, index map, conflicts)."""
	key = np.round(xyz[:, :2] / tolerance).astype(np.int64)
	_uniq, first, inv = unique_rows(key, return_index=True, return_inverse=True)
	inv = inv.ravel()
	merged = xyz[first]
	dz = np.abs(xyz[:, 2] - merged[inv, 2])
	return merged, inv, int(np.sum(dz > 1e-6))


def unique_rows(a, return_index=False, return_inverse=False, return_counts=False):
	"""np.unique(a, axis=0) for 2-D arrays, without its slow structured-dtype sort (several × faster on millions of
	rows). Rows come out sorted; return_index gives the first occurrence of each."""
	a = np.asarray(a)
	if a.ndim == 2 and a.shape[1] == 2 and a.dtype.kind in "iu" and len(a) and a.min() >= 0 and a.max() < 2**31:
		key = a[:, 0].astype(np.int64) << 32 | a[:, 1].astype(np.int64)  # one int64 per row
		order = np.argsort(key, kind="stable")
		s = key[order]
		flag = np.r_[True, s[1:] != s[:-1]]
	else:
		order = np.lexsort(a.T[::-1]) if len(a) else np.zeros(0, np.int64)
		s = a[order]
		flag = np.r_[True, (s[1:] != s[:-1]).any(axis=1)] if len(a) else np.zeros(0, bool)
	out = [a[order[flag]]]
	if return_index:
		out.append(order[flag])
	if return_inverse:
		inverse = np.empty(len(a), np.int64)
		inverse[order] = np.cumsum(flag) - 1
		out.append(inverse)
	if return_counts:
		starts = np.flatnonzero(flag)
		out.append(np.diff(np.r_[starts, len(a)]))
	return tuple(out) if len(out) > 1 else out[0]


def used_vertices(tri, n):
	"""Sorted indices of the vertices the triangles use (as np.unique(tri), much faster)."""
	return np.flatnonzero(np.bincount(np.asarray(tri).ravel(), minlength=n)) if len(tri) else np.zeros(0, np.int64)


def edge_table(tri):
	"""The edges of a TIN, worked out once: unique (sorted) edges, how many triangles use each, and the outline
	edges (used once) in their triangle's direction."""
	directed = np.r_[tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]
	unique, inverse, counts = unique_rows(np.sort(directed, axis=1), return_inverse=True, return_counts=True)
	return {"unique": unique, "counts": counts, "outline": directed[counts[inverse] == 1]}


def edge_list(tri):
	e = np.sort(np.r_[tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]], axis=1)
	return unique_rows(e)


def triangulate(xy, edges=None):
	"""Constrained Delaunay of plan points (+ constraint edges, intersections resolved). Returns
	(vertices (n', 2) — inputs first, then added intersection points —, triangles, piece→original edge map)."""
	import pythoncdt as cdt

	# vertices closer than this to a constraint edge split it there (otherwise near-touching crossings fail)
	t = cdt.Triangulation(cdt.VertexInsertionOrder.AUTO, cdt.IntersectingConstraintEdges.TRY_RESOLVE, MERGE_TOLERANCE)
	t.insert_vertices(np.ascontiguousarray(xy, dtype=np.float64))
	if edges is not None and len(edges):
		t.insert_edges(np.ascontiguousarray(edges, dtype=np.uint32))
	t.erase_super_triangle()
	v = t.vertices_array()
	tri = t.triangles_array()["vertices"].astype(np.int64)
	pieces = {}
	if len(v) > len(xy):
		for piece, originals in t.piece_to_originals.items():
			pieces[(piece.v1, piece.v2)] = [(o.v1, o.v2) for o in originals]
	return np.c_[v["x"], v["y"]], tri, pieces


def orient_ccw(xyz, tri):
	p = xyz[tri]
	a = (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1])
	tri = tri.copy()
	flip = a < 0
	tri[flip] = tri[flip][:, [0, 2, 1]]
	return tri, int(flip.sum())


def outer_edges(tri):
	"""Edges used by exactly one triangle (the outline, including holes), with the owning triangle."""
	e = np.r_[tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]]
	owner = np.r_[np.arange(len(tri)), np.arange(len(tri)), np.arange(len(tri))]
	key = np.sort(e, axis=1)
	_u, inv, counts = unique_rows(key, return_inverse=True, return_counts=True)
	single = counts[inv.ravel()] == 1
	return e[single], owner[single]


# ---------------------------------------------------------------------------
# polygons
# ---------------------------------------------------------------------------


def polygon_path(points):
	from matplotlib.path import Path

	return Path(np.asarray(points, float)[:, :2])


def in_polygons(x, y, outer, holes):
	q = np.c_[x, y]
	inside_ = polygon_path(outer).contains_points(q) if outer is not None else np.ones(len(q), bool)
	for h in holes or []:
		inside_ &= ~polygon_path(h).contains_points(q)
	return inside_


def loop_edges(start, count):
	idx = np.arange(start, start + count)
	return np.c_[idx, np.r_[idx[1:], idx[0]]]


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def build(request, log):
	"""TIN from points + breaklines; boundary/holes clip it; max_edge trims long outer triangles."""
	workspace = request["workspace"]
	with open(inside(workspace, request["input"])) as f:
		data = json.load(f)
	points = np.asarray(data.get("points") or np.zeros((0, 3)), float).reshape(-1, 3)
	breaklines = [np.asarray(b, float) for b in data.get("breaklines") or [] if len(b) >= 2]
	if data.get("points_file"):
		points = np.r_[points, np.load(inside(workspace, data["points_file"]))[:, :3]]
	for path in data.get("tins") or []:  # other surfaces as sources: their vertices
		points = np.r_[points, Tin.read(inside(workspace, path)).xyz]
	all_pts = [points] + breaklines
	xyz = np.concatenate([p.reshape(-1, 3) for p in all_pts]) if all_pts else np.zeros((0, 3))
	if len(xyz) > MAX_POINTS:
		raise ValueError(f"{len(xyz)} points: at most {MAX_POINTS} per surface.")
	if not np.all(np.isfinite(xyz)):
		raise ValueError("Some points are not finite numbers.")
	if len(xyz) < 3:
		raise ValueError("A surface needs at least three points.")
	emit(progress=5, log=f"{len(points)} points, {len(breaklines)} breaklines.")

	# breakline edges in input numbering
	edges, flags_in = [], np.zeros(len(xyz), np.uint8)
	start = len(points)
	for b in breaklines:
		idx = np.arange(start, start + len(b))
		edges.append(np.c_[idx[:-1], idx[1:]])
		flags_in[idx] |= FLAG_BREAKLINE
		start += len(b)
	edges = np.concatenate(edges) if edges else np.zeros((0, 2), np.int64)

	merged, inv, conflicts = merge_points(xyz)
	if conflicts:
		log.append(f"{conflicts} points share a plan position with a different elevation; the first one is used.")
	flags = np.zeros(len(merged), np.uint8)
	np.bitwise_or.at(flags, inv, flags_in)
	e = np.sort(inv[edges], axis=1) if len(edges) else edges
	e = unique_rows(e[e[:, 0] != e[:, 1]]) if len(e) else e
	if np.ptp(merged[:, 0]) == 0 or np.ptp(merged[:, 1]) == 0:
		raise ValueError("The points lie on a line; no surface can be made.")
	emit(progress=20, log=f"Triangulating {len(merged)} points, {len(e)} breakline edges…")
	xy, tri, pieces = triangulate(merged[:, :2], e)
	z = np.r_[merged[:, 2], np.zeros(len(xy) - len(merged))]
	fl = np.r_[flags, np.full(len(xy) - len(merged), FLAG_BREAKLINE, np.uint8)]
	if len(xy) > len(merged):
		crossing = resolve_intersection_z(xy, z, pieces, len(merged))
		if crossing:
			log.append(f"{crossing} breaklines cross at different elevations; the crossing uses their average.")
	tin = Tin(np.c_[xy, z], tri, fl)
	emit(progress=60)

	boundary = data.get("boundary")
	holes = data.get("holes") or []
	if boundary or holes:
		tin = clip_tin(tin, boundary, holes, keep="inside")
		log.append("Clipped to the boundary.")
	if data.get("max_edge"):
		removed = trim_long_edges(tin, float(data["max_edge"]))
		log.append(f"{removed} outer triangles with an edge longer than {data['max_edge']} removed.")
	tin = compact(tin)
	if not len(tin.tri):
		raise ValueError("No triangles are left (boundary or maximum edge length too tight).")
	return finish_tin(tin, request, log)


def resolve_intersection_z(xy, z, pieces, n_input):
	"""Z of vertices added where breaklines cross: interpolated along each original edge."""
	conflicts = 0
	values = {}
	for (a, b), originals in pieces.items():
		for v in (a, b):
			if v < n_input:
				continue
			for p, q in originals:
				if p >= n_input or q >= n_input:
					continue
				d = math.dist(xy[p], xy[q])
				t = math.dist(xy[p], xy[v]) / d if d else 0.0
				values.setdefault(v, []).append(z[p] + (z[q] - z[p]) * t)
	for v, zs in values.items():
		zs = list(dict.fromkeys(round(x, 9) for x in zs))
		if max(zs) - min(zs) > 1e-6:
			conflicts += 1
		z[v] = sum(zs) / len(zs)
	return conflicts


def compact(tin):
	used = used_vertices(tin.tri, len(tin.xyz))
	remap = np.full(len(tin.xyz), -1, np.int64)
	remap[used] = np.arange(len(used))
	return Tin(tin.xyz[used], remap[tin.tri], tin.flags[used])


def trim_long_edges(tin, max_edge):
	"""Peel outer triangles whose longest edge exceeds max_edge (concave outlines for survey data)."""
	removed = 0
	tri = tin.tri
	for _ in range(10000):
		edges, owner = outer_edges(tri)
		p = tin.xyz[tri][:, :, :2]
		longest = np.max(np.linalg.norm(p - np.roll(p, -1, axis=1), axis=2), axis=1)
		drop = np.unique(owner[longest[owner] > max_edge])
		if not len(drop):
			break
		keep = np.ones(len(tri), bool)
		keep[drop] = False
		tri = tri[keep]
		removed += len(drop)
	tin.tri = tri
	return removed


def clip_tin(tin, boundary, holes, keep="inside"):
	"""Exact clip: boundary/hole edges become constraints, new points take the surface's Z."""
	loops = ([np.asarray(boundary, float)[:, :2]] if boundary else []) + [np.asarray(h, float)[:, :2] for h in holes]
	extra = np.concatenate(loops) if loops else np.zeros((0, 2))
	n = len(tin.xyz)
	pts = np.r_[tin.xyz[:, :2], extra]
	edges = [edge_list(tin.tri)]
	start = n
	for loop in loops:
		edges.append(loop_edges(start, len(loop)))
		start += len(loop)
	merged, inv, _ = merge_points(np.c_[pts, np.zeros(len(pts))])
	e = np.sort(inv[np.concatenate(edges)], axis=1)
	e = unique_rows(e[e[:, 0] != e[:, 1]])
	xy, tri, _pieces = triangulate(merged[:, :2], e)
	zv = tin.z_at(xy[:, 0], xy[:, 1])  # new points (boundary, crossings) take the surface's Z
	zv[inv[:n]] = tin.xyz[:, 2]  # original vertices keep their exact Z
	fl = np.zeros(len(xy), np.uint8)
	np.bitwise_or.at(fl, inv[:n], tin.flags)
	cen = xy[tri].mean(axis=1)
	inside_surface = tin.locate(cen[:, 0], cen[:, 1]) >= 0
	inside_loops = in_polygons(cen[:, 0], cen[:, 1], loops[0] if boundary else None, loops[1:] if boundary else loops)
	keep_mask = inside_surface & (inside_loops if keep == "inside" else ~inside_loops)
	keep_mask &= np.isfinite(zv[tri]).all(axis=1)
	out = compact(Tin(np.c_[xy, zv], tri[keep_mask], fl))
	# vertices on the new outline are boundary vertices
	edges_out, _owner = outer_edges(out.tri)
	out.flags[np.unique(edges_out)] |= FLAG_BOUNDARY
	return out


def finish_tin(tin, request, log, extra=None):
	workspace = request["workspace"]
	tin.tri, flipped = orient_ccw(tin.xyz, tin.tri)
	out = inside(workspace, os.path.join(workspace, "outputs", request.get("name", "surface") + ".tin"))
	os.makedirs(os.path.dirname(out), exist_ok=True)
	tin.write(out)
	stats = tin.stats()
	stats["validation"] = validate_tin(tin)
	emit(progress=100)
	return {"tin": out, "stats": stats, **(extra or {})}


# ---------------------------------------------------------------------------
# display tiles
# ---------------------------------------------------------------------------


def write_mesh(path, origin, positions, index):
	with open(path, "wb") as f:
		f.write(struct.pack("<8sIII3d", MESH_MAGIC, len(positions), index.size, 0, *origin))
		f.write(np.ascontiguousarray(positions, "<f4").tobytes())
		f.write(np.ascontiguousarray(index, "<u4").tobytes())


def tiles(request, log):
	"""Tile pyramid for view-driven display: full triangles per tile + resampled grids (LEVELS)."""
	workspace = request["workspace"]
	tin = Tin.read(inside(workspace, request["tin"]))
	out_dir = inside(workspace, request["out_dir"])
	os.makedirs(out_dir, exist_ok=True)
	stats = tin.stats()
	x0, y0, _z0, x1, y1, _z1 = stats["bbox"]
	n = max(1, int(math.ceil(math.sqrt(len(tin.tri) / TILE_TRIANGLES))))
	sx, sy = max((x1 - x0) / n, 1e-9), max((y1 - y0) / n, 1e-9)
	cen = tin.xyz[tin.tri].mean(axis=1)
	ti = np.minimum(((cen[:, 0] - x0) / sx).astype(int), n - 1)
	tj = np.minimum(((cen[:, 1] - y0) / sy).astype(int), n - 1)
	index = {"version": 1, "grid": [n, n], "bbox": stats["bbox"], "triangles": len(tin.tri), "tiles": []}
	order = np.lexsort((tj, ti))
	bounds = np.searchsorted(ti[order] * n + tj[order], np.arange(n * n + 1))
	del cen, ti, tj

	def around(i, j):
		"""This tile's triangles and its neighbours' (a grid node near the edge may fall in either)."""
		ks = [a * n + b for a in range(max(i - 1, 0), min(i + 2, n)) for b in range(max(j - 1, 0), min(j + 2, n))]
		return tin.tri[np.concatenate([order[bounds[k] : bounds[k + 1]] for k in ks])]

	for i in range(n):
		for j in range(n):
			k = i * n + j
			sel = order[bounds[k] : bounds[k + 1]]
			if not len(sel):
				continue
			tri = tin.tri[sel]
			used, local = np.unique(tri, return_inverse=True)
			p = tin.xyz[used]
			origin = [float(round(v)) for v in p.mean(axis=0)]
			name = f"t{i}_{j}"
			write_mesh(os.path.join(out_dir, f"{name}_0.cadmesh"), origin, p - origin, local.reshape(-1, 3))
			bx = [x0 + i * sx, y0 + j * sy, x0 + (i + 1) * sx, y0 + (j + 1) * sy]
			levels = [{"file": f"{name}_0.cadmesh", "triangles": int(len(tri))}]
			near = None
			for level, cells in enumerate(LEVELS, start=1):
				if len(tri) <= cells * cells * 2:
					break
				near = around(i, j) if near is None else near
				gp, gi = resample(tin, bx, cells, near)
				if not len(gi):
					break
				write_mesh(os.path.join(out_dir, f"{name}_{level}.cadmesh"), origin, gp - origin, gi)
				levels.append({"file": f"{name}_{level}.cadmesh", "triangles": int(len(gi))})
			index["tiles"].append({"i": i, "j": j, "bbox": [*bx, float(p[:, 2].min()), float(p[:, 2].max())], "levels": levels})
		emit(progress=int(100 * (i + 1) / n))
	with open(os.path.join(out_dir, "index.json"), "w") as f:
		json.dump(index, f)
	return {"index": os.path.join(out_dir, "index.json"), "tiles": len(index["tiles"])}


def grid_z(xyz, tri, xs, ys):
	"""Z of the triangles at the nodes of a regular grid (NaN off the surface), by rasterising each triangle over
	the nodes in its bounding box: no point locator, so it scales to millions of triangles."""
	gz = np.full((len(xs), len(ys)), np.nan)
	if not len(tri):
		return gz
	dx, dy = xs[1] - xs[0], ys[1] - ys[0]
	p = xyz[tri]
	px, py = p[:, :, 0] - xs[0], p[:, :, 1] - ys[0]  # local coordinates: no cancellation at survey eastings
	i0 = np.clip(np.ceil(px.min(1) / dx - 1e-9), 0, len(xs)).astype(np.int64)
	i1 = np.clip(np.floor(px.max(1) / dx + 1e-9), -1, len(xs) - 1).astype(np.int64)
	j0 = np.clip(np.ceil(py.min(1) / dy - 1e-9), 0, len(ys)).astype(np.int64)
	j1 = np.clip(np.floor(py.max(1) / dy + 1e-9), -1, len(ys) - 1).astype(np.int64)
	ni, nj = np.maximum(i1 - i0 + 1, 0), np.maximum(j1 - j0 + 1, 0)
	count = ni * nj
	t = np.repeat(np.arange(len(tri)), count)  # one row per (triangle, candidate node)
	if not len(t):
		return gz
	k = np.arange(len(t)) - np.repeat(np.cumsum(count) - count, count)
	gi = i0[t] + k // nj[t]
	gj = j0[t] + k % nj[t]
	x, y = gi * dx, gj * dy
	ax, ay, bx, by, cx, cy = px[t, 0], py[t, 0], px[t, 1], py[t, 1], px[t, 2], py[t, 2]
	den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
	safe = np.where(den == 0, 1, den)
	l1 = ((by - cy) * (x - cx) + (cx - bx) * (y - cy)) / safe
	l2 = ((cy - ay) * (x - cx) + (ax - cx) * (y - cy)) / safe
	l3 = 1 - l1 - l2
	tol = -1e-9
	inside = (den != 0) & (l1 >= tol) & (l2 >= tol) & (l3 >= tol)
	z = l1 * p[t, 0, 2] + l2 * p[t, 1, 2] + l3 * p[t, 2, 2]
	gz[gi[inside], gj[inside]] = z[inside]
	return gz


def resample(tin, box, cells, tri=None):
	"""A regular grid over box with the surface's Z (cells inside the surface only); tri: the triangles near box."""
	xs = np.linspace(box[0], box[2], cells + 1)
	ys = np.linspace(box[1], box[3], cells + 1)
	gx, gy = np.meshgrid(xs, ys, indexing="ij")
	gz = grid_z(tin.xyz, tin.tri if tri is None else tri, xs, ys)
	ok = np.isfinite(gz)
	idx = np.arange((cells + 1) ** 2).reshape(cells + 1, cells + 1)
	a, b, c, d = idx[:-1, :-1], idx[1:, :-1], idx[1:, 1:], idx[:-1, 1:]
	quad_ok = ok[:-1, :-1] & ok[1:, :-1] & ok[1:, 1:] & ok[:-1, 1:]
	tris = np.r_[np.c_[a[quad_ok], b[quad_ok], c[quad_ok]], np.c_[a[quad_ok], c[quad_ok], d[quad_ok]]]
	used, local = np.unique(tris, return_inverse=True)
	pts = np.c_[gx.ravel()[used], gy.ravel()[used], gz.ravel()[used]]
	return pts, local.reshape(-1, 3)


# ---------------------------------------------------------------------------
# validation and repair
# ---------------------------------------------------------------------------


def validate_tin(tin, spike_factor=8.0):
	"""Problems that make a TIN unreliable for volumes, with up to 200 examples each."""
	xyz, tri = tin.xyz, tin.tri
	report = {}
	if not len(tri):
		return {"ok": False, "message": "No triangles."}
	p = xyz[tri]
	plan2 = (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1])
	scale = max(np.ptp(xyz[:, 0]), np.ptp(xyz[:, 1]), 1.0)
	degenerate = np.where(np.abs(plan2) < 1e-12 * scale * scale)[0]
	flipped = np.where(plan2 < 0)[0]
	key = np.sort(tri, axis=1)
	_u, first, counts = unique_rows(key, return_index=True, return_counts=True)
	duplicate_tris = int((counts - 1).sum())
	edges = edge_table(tri)
	nonmanifold = int((edges["counts"] > 2).sum())
	used = used_vertices(tri, len(xyz))
	_m, _inv, dup_vertices = merge_points(xyz[used])
	dup_plan = len(used) - len(_m)
	spikes = find_spikes(tin, spike_factor, edges)
	loops = count_outline_loops(tri, edges["outline"])
	report.update(
		{
			"degenerate_triangles": int(len(degenerate)),
			"flipped_triangles": int(len(flipped)),
			"duplicate_triangles": duplicate_tris,
			"non_manifold_edges": nonmanifold,
			"duplicate_plan_points": int(dup_plan),
			"spikes": spikes[:200],
			"spike_count": len(spikes),
			"outline_loops": loops,
			"examples": {"degenerate": degenerate[:200].tolist(), "flipped": flipped[:200].tolist()},
		}
	)
	report["ok"] = not (len(degenerate) or len(flipped) or duplicate_tris or nonmanifold or dup_plan)
	return report


def neighbours(tri, n, unique_edges=None):
	"""CSR adjacency of vertices (from triangle edges, or the unique edges of edge_table)."""
	if unique_edges is None:
		unique_edges = edge_table(tri)["unique"]
	e = unique_rows(np.r_[unique_edges, unique_edges[:, ::-1]])  # sorted by first vertex, then second
	starts = np.searchsorted(e[:, 0], np.arange(n + 1))
	return starts, e[:, 1]


def find_spikes(tin, factor, edges=None):
	"""Vertices whose Z departs from the median of their neighbours by more than factor × the typical departure."""
	n = len(tin.xyz)
	edges = edges or edge_table(tin.tri)
	starts, nb = neighbours(tin.tri, n, edges["unique"])
	counts = np.diff(starts)
	z = tin.xyz[:, 2]
	# median of the neighbours (one spike does not drag its neighbours' reference up): sort by owner, then Z,
	# with one float key (owner + Z scaled into [0, 0.5)) — far faster than a two-key lexsort on millions of edges
	owner = np.repeat(np.arange(n), counts)
	zr = max(float(np.ptp(z)), 1e-12)
	order = np.argsort(owner + (z[nb] - z.min()) / zr * 0.5)
	sorted_z = z[nb][order]
	mid_lo = starts[:-1] + (counts - 1) // 2
	mid_hi = starts[:-1] + counts // 2
	median = np.where(counts > 0, (sorted_z[np.minimum(mid_lo, len(sorted_z) - 1)] + sorted_z[np.minimum(mid_hi, len(sorted_z) - 1)]) / 2, z) if len(nb) else z.copy()
	dev = z - median
	interior = (counts >= 3) & ((tin.flags & (FLAG_BREAKLINE | FLAG_BOUNDARY)) == 0)
	# outline vertices (imported surfaces carry no boundary flags) see only half a neighbourhood, so on a curved
	# surface they depart further from their neighbours' median: they are held to a looser threshold
	on_outline = np.zeros(n, bool)
	on_outline[edges["outline"].ravel()] = True
	inner = interior & ~on_outline
	if not inner.any():
		return []
	typical = np.median(np.abs(dev[inner])) or 1e-9
	threshold = np.where(on_outline, 4, 1) * max(factor * typical, 1e-6)
	idx = np.where(interior & (np.abs(dev) > threshold))[0]
	idx = idx[np.argsort(-np.abs(dev[idx]))]
	# a spike drags its neighbours' median: a neighbour of a much larger spike is not one itself
	accepted = []
	flagged = set()
	for i in idx[:5000]:
		near = nb[starts[i] : starts[i + 1]]
		if any(int(k) in flagged and abs(dev[k]) > 4 * abs(dev[i]) for k in near):
			continue
		accepted.append(i)
		flagged.add(int(i))
	idx = np.array(accepted, dtype=int)
	return [{"vertex": int(i), "x": float(tin.xyz[i, 0]), "y": float(tin.xyz[i, 1]), "z": float(z[i]), "departure": float(dev[i])} for i in idx]


def count_outline_loops(tri, edges=None):
	if edges is None:
		edges, _owner = outer_edges(tri)
	if not len(edges):
		return 0
	nxt = {int(a): int(b) for a, b in edges}
	seen, loops = set(), 0
	for start in nxt:
		if start in seen:
			continue
		loops += 1
		v = start
		while v in nxt and v not in seen:
			seen.add(v)
			v = nxt[v]
	return loops


def repair(request, log):
	workspace = request["workspace"]
	tin = Tin.read(inside(workspace, request["tin"]))
	options = request.get("options") or {}
	tri = tin.tri
	p = tin.xyz[tri]
	plan2 = (p[:, 1, 0] - p[:, 0, 0]) * (p[:, 2, 1] - p[:, 0, 1]) - (p[:, 2, 0] - p[:, 0, 0]) * (p[:, 1, 1] - p[:, 0, 1])
	scale = max(np.ptp(tin.xyz[:, 0]), np.ptp(tin.xyz[:, 1]), 1.0)
	keep = np.abs(plan2) >= 1e-12 * scale * scale
	log.append(f"Removed {int((~keep).sum())} degenerate triangles.")
	tri = tri[keep]
	_u, first = unique_rows(np.sort(tri, axis=1), return_index=True)
	log.append(f"Removed {len(tri) - len(first)} duplicate triangles.")
	tri = tri[np.sort(first)]
	tri, flipped = orient_ccw(tin.xyz, tri)
	log.append(f"Reoriented {flipped} flipped triangles.")
	tin = compact(Tin(tin.xyz, tri, tin.flags))
	if options.get("remove_spikes"):
		spikes = find_spikes(tin, float(options.get("spike_factor") or 8.0))
		drop = np.array([s["vertex"] for s in spikes], np.int64)
		if len(drop):
			tin = remove_vertices(tin, drop)
			log.append(f"Removed {len(drop)} spike vertices and re-triangulated around them.")
	return finish_tin(tin, request, log)


def remove_vertices(tin, drop):
	"""Remove vertices and re-triangulate: remaining TIN edges stay as constraints, the outline is kept."""
	keep = np.ones(len(tin.xyz), bool)
	keep[drop] = False
	remap = np.full(len(tin.xyz), -1, np.int64)
	remap[keep] = np.arange(keep.sum())
	e = edge_list(tin.tri)
	e = remap[e[(keep[e[:, 0]]) & (keep[e[:, 1]])]]
	xy, tri, _ = triangulate(tin.xyz[keep][:, :2], e)
	cen = xy[tri].mean(axis=1)
	inside_ = tin.locate(cen[:, 0], cen[:, 1]) >= 0
	return compact(Tin(tin.xyz[keep], tri[inside_], tin.flags[keep]))


def clip(request, log):
	workspace = request["workspace"]
	tin = Tin.read(inside(workspace, request["tin"]))
	out = clip_tin(tin, request.get("boundary"), request.get("holes") or [], request.get("keep") or "inside")
	if not len(out.tri):
		raise ValueError("Nothing of the surface is left.")
	return finish_tin(out, request, log)


def smooth(request, log):
	workspace = request["workspace"]
	tin = Tin.read(inside(workspace, request["tin"]))
	iterations = min(max(int(request.get("iterations") or 3), 1), 200)
	weight = min(max(float(request.get("weight") or 0.5), 0.0), 1.0)
	n = len(tin.xyz)
	starts, nb = neighbours(tin.tri, n)
	counts = np.diff(starts)
	edges, _o = outer_edges(tin.tri)
	fixed = (tin.flags & (FLAG_BREAKLINE | FLAG_BOUNDARY)) > 0
	fixed[np.unique(edges)] = True
	z = tin.xyz[:, 2].copy()
	for _ in range(iterations):
		mean = np.add.reduceat(z[nb], starts[:-1]) / np.maximum(counts, 1)
		z = np.where(fixed | (counts == 0), z, (1 - weight) * z + weight * mean)
	tin.xyz[:, 2] = z
	log.append(f"{iterations} smoothing passes (weight {weight}); {int(fixed.sum())} breakline/boundary vertices kept.")
	return finish_tin(tin, request, log)


# ---------------------------------------------------------------------------
# contours, profiles, sections
# ---------------------------------------------------------------------------


def contour_segments(xyz, tri, level):
	"""Marching triangles at one level: segments as (edge key a, edge key b, point a, point b)."""
	z = xyz[:, 2]
	zt = z[tri]
	above = zt >= level  # a vertex exactly on the level counts as above (sides only; positions use the true Z)
	cnt = above.sum(axis=1)
	sel = np.where((cnt == 1) | (cnt == 2))[0]
	segs = []
	for t in sel:
		v = tri[t]
		zz = zt[t]
		up = above[t]
		crossings = []
		for a, b in ((0, 1), (1, 2), (2, 0)):
			if up[a] != up[b]:
				s = (level - zz[a]) / (zz[b] - zz[a])
				pa, pb = xyz[v[a]], xyz[v[b]]
				key = (min(v[a], v[b]), max(v[a], v[b]))
				crossings.append((key, (pa[0] + s * (pb[0] - pa[0]), pa[1] + s * (pb[1] - pa[1]), level)))
		if len(crossings) == 2:
			segs.append(crossings)
	return segs


def chain(segs):
	"""Join segments that share edge keys into polylines."""
	adj = {}
	for (ka, pa), (kb, pb) in segs:
		adj.setdefault(ka, []).append((kb, pa, pb))
		adj.setdefault(kb, []).append((ka, pb, pa))
	points_of = {}
	for (ka, pa), (kb, pb) in segs:
		points_of[ka] = pa
		points_of[kb] = pb
	used = set()
	lines = []

	def walk(start):
		line = [start]
		cur = start
		while True:
			nxt = None
			for k, _p, _q in adj.get(cur, []):
				edge = (min(cur, k), max(cur, k))
				if edge not in used:
					nxt = k
					used.add(edge)
					break
			if nxt is None:
				return line
			line.append(nxt)
			cur = nxt
			if cur == start:
				return line

	# open lines start at keys with one segment, then the closed loops
	for key, items in adj.items():
		if len(items) == 1 and any((min(key, k), max(key, k)) not in used for k, _a, _b in items):
			lines.append(walk(key))
	for key in adj:
		if any((min(key, k), max(key, k)) not in used for k, _a, _b in adj[key]):
			lines.append(walk(key))
	out = []
	for keys in lines:
		closed = len(keys) > 2 and keys[0] == keys[-1]
		pts = []
		for k in keys[:-1] if closed else keys:
			# a level through a vertex is reached from several edges: one point there
			if not pts or math.dist(points_of[k][:2], pts[-1][:2]) > 1e-9:
				pts.append(points_of[k])
		if closed and len(pts) > 1 and math.dist(pts[0][:2], pts[-1][:2]) <= 1e-9:
			pts.pop()
		closed = closed and len(pts) >= 3
		if len(pts) >= 2:
			out.append({"points": [[float(c) for c in p] for p in pts], "closed": bool(closed)})
	return out


def contours(request, log):
	workspace = request["workspace"]
	tin = Tin.read(inside(workspace, request["tin"]))
	interval = float(request["interval"])
	if not interval > 0:
		raise ValueError("The contour interval must be greater than zero.")
	major = float(request.get("major") or 0)
	base = float(request.get("base") or 0)
	zmin, zmax = tin.xyz[:, 2].min(), tin.xyz[:, 2].max()
	first = math.ceil((zmin - base) / interval)
	last = math.floor((zmax - base) / interval)
	if last - first > 5000:
		raise ValueError("More than 5000 contour levels: use a larger interval.")
	out = []
	for n, k in enumerate(range(first, last + 1)):
		level = base + k * interval
		for line in chain(contour_segments(tin.xyz, tin.tri, level)):
			line["z"] = level
			line["major"] = bool(major and abs(((level - base) / major) - round((level - base) / major)) < 1e-9)
			out.append(line)
		emit(progress=int(100 * (n + 1) / max(last - first + 1, 1)))
	log.append(f"{len(out)} contour lines at {last - first + 1} levels.")
	return {"contours": out}


def section_line(tin, edges, p0, p1):
	"""Exact profile of a TIN along the segment p0→p1: [(t, x, y, z)] at every edge crossing, plus ends."""
	x0, y0, x1, y1 = p0[0], p0[1], p1[0], p1[1]
	d = np.array([x1 - x0, y1 - y0])
	a = tin.xyz[edges[:, 0]]
	b = tin.xyz[edges[:, 1]]
	box = (np.minimum(a[:, 0], b[:, 0]) <= max(x0, x1)) & (np.maximum(a[:, 0], b[:, 0]) >= min(x0, x1)) & (np.minimum(a[:, 1], b[:, 1]) <= max(y0, y1)) & (np.maximum(a[:, 1], b[:, 1]) >= min(y0, y1))
	a, b = a[box], b[box]
	e = b[:, :2] - a[:, :2]
	den = d[0] * e[:, 1] - d[1] * e[:, 0]
	ok = np.abs(den) > 1e-15
	w = a[:, :2] - np.array([x0, y0])
	t = np.where(ok, (w[:, 0] * e[:, 1] - w[:, 1] * e[:, 0]) / np.where(ok, den, 1), -1)
	u = np.where(ok, (w[:, 0] * d[1] - w[:, 1] * d[0]) / np.where(ok, den, 1), -1)
	hit = ok & (t >= 0) & (t <= 1) & (u >= 0) & (u <= 1)
	t, u, a, b = t[hit], u[hit], a[hit], b[hit]
	z = a[:, 2] + u * (b[:, 2] - a[:, 2])
	ends = np.array([0.0, 1.0])
	ez = tin.z_at(np.array([x0, x1]), np.array([y0, y1]))
	t = np.r_[t, ends[np.isfinite(ez)]]
	z = np.r_[z, ez[np.isfinite(ez)]]
	order = np.argsort(t, kind="stable")
	t, z = t[order], z[order]
	if not len(t):  # the segment does not cross the surface
		return t, z
	keep = np.r_[True, np.diff(t) > 1e-12]
	return t[keep], z[keep]


def crossed_by(tin, p0, p1):
	"""The triangles the segment p0→p1 touches, as a small TIN of their own: crossings, ends and gaps along the
	segment are the same as on the whole surface, without a point locator over millions of triangles."""
	if not hasattr(tin, "_tri_box"):
		p = tin.xyz[tin.tri][:, :, :2]
		tin._tri_box = (p.min(axis=1), p.max(axis=1))
	mn, mx = tin._tri_box
	x0, y0, x1, y1 = p0[0], p0[1], p1[0], p1[1]
	cand = np.flatnonzero((mx[:, 0] >= min(x0, x1)) & (mn[:, 0] <= max(x0, x1)) & (mx[:, 1] >= min(y0, y1)) & (mn[:, 1] <= max(y0, y1)))
	q = tin.xyz[tin.tri[cand]][:, :, :2]
	side = (x1 - x0) * (q[:, :, 1] - y0) - (y1 - y0) * (q[:, :, 0] - x0)  # which side of the line each corner is
	cand = cand[(side.min(axis=1) <= 0) & (side.max(axis=1) >= 0)]
	return compact(Tin(tin.xyz, tin.tri[cand], tin.flags))


def profile_of(tin, alignment):
	"""Chainage/elevation runs of one surface along a polyline (gaps where the alignment leaves the surface)."""
	whole = tin
	runs, current = [], []
	chainage = 0.0
	for k in range(len(alignment) - 1):
		p0, p1 = alignment[k], alignment[k + 1]
		length = math.dist(p0[:2], p1[:2])
		if length == 0:
			continue
		tin = crossed_by(whole, p0, p1)
		t, z = section_line(tin, edge_list(tin.tri), p0, p1) if len(tin.tri) else (np.zeros(0), np.zeros(0))
		if current and (not len(t) or t[0] > 1e-12):  # the segment starts off the surface: the run ends
			runs.append(current)
			current = []
		# a gap: the midpoint between two samples is off the surface
		tm = (t[1:] + t[:-1]) / 2
		gap = np.r_[False, ~np.isfinite(tin.z_at(p0[0] + tm * (p1[0] - p0[0]), p0[1] + tm * (p1[1] - p0[1])))] if len(t) > 1 else np.zeros(len(t), bool)
		for i in range(len(t)):
			if current and gap[i]:
				runs.append(current)
				current = []
			x = p0[0] + t[i] * (p1[0] - p0[0])
			y = p0[1] + t[i] * (p1[1] - p0[1])
			c = chainage + t[i] * length
			if not current or c - current[-1][0] > 1e-9:
				current.append([float(c), float(x), float(y), float(z[i])])
		chainage += length
	if current:
		runs.append(current)
	return runs, chainage


def load_tins(request):
	workspace = request["workspace"]
	return [(s["key"], Tin.read(inside(workspace, s["tin"]))) for s in request["surfaces"]]


def profile(request, log):
	alignment = [list(map(float, p)) for p in request["alignment"]]
	out, length = {}, 0.0
	for key, tin in load_tins(request):
		runs, length = profile_of(tin, alignment)
		out[key] = runs
		log.append(f"{key}: {sum(len(r) for r in runs)} profile points.")
	return {"length": length, "profiles": out}


def sections(request, log):
	alignment = [np.asarray(p, float) for p in request["alignment"]]
	interval = float(request["interval"])
	left, right = float(request.get("left") or 50), float(request.get("right") or 50)
	if not interval > 0:
		raise ValueError("The section interval must be greater than zero.")
	tins = load_tins(request)
	stations = []
	lengths = [math.dist(alignment[i][:2], alignment[i + 1][:2]) for i in range(len(alignment) - 1)]
	total = sum(lengths)
	if total / interval > 2000:
		raise ValueError("More than 2000 sections: use a larger interval.")
	c = float(request.get("start") or 0.0)
	while c <= total + 1e-9:
		acc, k = 0.0, 0
		while k < len(lengths) - 1 and acc + lengths[k] < c:
			acc += lengths[k]
			k += 1
		p0, p1 = alignment[k], alignment[k + 1]
		t = (c - acc) / lengths[k] if lengths[k] else 0
		centre = p0 + t * (p1 - p0)
		d = (p1 - p0)[:2] / (lengths[k] or 1)
		n = np.array([-d[1], d[0]])  # left normal
		a = np.r_[centre[:2] + n * left, 0]
		b = np.r_[centre[:2] - n * right, 0]
		cut = {}
		for key, tin in tins:
			runs, _ = profile_of(tin, [a, b])
			cut[key] = [[[r[0] - left, r[3]] for r in run] for run in runs]  # offset (left negative … right positive), z
		stations.append({"chainage": float(c), "centre": [float(v) for v in centre[:2]], "direction": [float(v) for v in d], "surfaces": cut})
		c += interval
	log.append(f"{len(stations)} sections.")
	return {"sections": stations, "length": total, "left": left, "right": right}


# ---------------------------------------------------------------------------
# volumes and intersections
# ---------------------------------------------------------------------------


def composite(a, b=None, loops=None):
	"""Composite triangulation of TIN a (and TIN b, and boundary loops): all their edges are constraints,
	so every composite triangle lies inside one triangle of each input. Returns xy, triangles, ta, tb."""
	parts, edges, start = [a.xyz[:, :2]], [edge_list(a.tri)], len(a.xyz)
	if b is not None:
		parts.append(b.xyz[:, :2])
		edges.append(edge_list(b.tri) + start)
		start += len(b.xyz)
	for loop in loops or []:
		loop = np.asarray(loop, float)[:, :2]
		parts.append(loop)
		edges.append(loop_edges(start, len(loop)))
		start += len(loop)
	pts = np.concatenate(parts)
	merged, inv, _ = merge_points(np.c_[pts, np.zeros(len(pts))])
	e = np.sort(inv[np.concatenate(edges)], axis=1)
	e = unique_rows(e[e[:, 0] != e[:, 1]])
	xy, tri, _pieces = triangulate(merged[:, :2], e)
	cen = xy[tri].mean(axis=1)
	ta = a.locate(cen[:, 0], cen[:, 1])
	tb = b.locate(cen[:, 0], cen[:, 1]) if b is not None else np.zeros(len(tri), np.int64)
	keep = (ta >= 0) & (tb >= 0)
	return xy, tri[keep], ta[keep], tb[keep], cen[keep]


def composite_blocks(a, b=None, loops=None):
	"""composite() over a grid of blocks, so surfaces of millions of triangles fit in memory. Each block takes the
	triangles of a (and b) whose extent touches it, with the block's rectangle as one more constraint loop, and
	keeps the composite triangles inside the rectangle: every composite triangle still lies inside one triangle of
	each surface, and the blocks tile the plane, so sums over the blocks are exact. Yields (a_block, b_block, xy,
	tri, ta, tb, cen) with ta/tb indexing the block's own TINs."""
	n = max(1, int(math.ceil(math.sqrt(max(len(a.tri), len(b.tri) if b is not None else 0) / COMPOSITE_BLOCK_TRIANGLES))))
	if n == 1:
		yield (a, b, *composite(a, b, loops))
		return
	tins = [t for t in (a, b) if t is not None]
	boxes = []
	for t in tins:
		p = t.xyz[t.tri][:, :, :2]
		boxes.append((p.min(axis=1), p.max(axis=1)))
		del p
	lo = np.max([bx[0].min(axis=0) for bx in boxes], axis=0)  # only where both surfaces are
	hi = np.min([bx[1].max(axis=0) for bx in boxes], axis=0)
	if np.any(hi <= lo):
		return
	edges_x, edges_y = np.linspace(lo[0], hi[0], n + 1), np.linspace(lo[1], hi[1], n + 1)
	for i in range(n):
		for j in range(n):
			x0, x1, y0, y1 = edges_x[i], edges_x[i + 1], edges_y[j], edges_y[j + 1]
			local = []
			for t, (mn, mx) in zip(tins, boxes):
				sel = np.flatnonzero((mx[:, 0] >= x0) & (mn[:, 0] <= x1) & (mx[:, 1] >= y0) & (mn[:, 1] <= y1))
				local.append(compact(Tin(t.xyz, t.tri[sel], t.flags)) if len(sel) else None)
			if any(t is None for t in local):
				continue
			la, lb = local[0], local[1] if b is not None else None
			rect = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
			xy, tri, ta, tb, cen = composite(la, lb, [rect] + list(loops or []))
			inside = (cen[:, 0] > x0) & (cen[:, 0] < x1) & (cen[:, 1] > y0) & (cen[:, 1] < y1)
			yield la, lb, xy, tri[inside], ta[inside], tb[inside], cen[inside]
			emit(progress=5 + int(55 * (i * n + j + 1) / (n * n)))


def split_volume(d, area):
	"""Exact ∫max(d,0) and ∫max(-d,0) per triangle for d linear (values at the corners), plus the areas
	where d > 0 and d < 0."""
	s = np.sort(d, axis=1)
	s0, s1, s2 = s.T
	total = area * (s0 + s1 + s2) / 3
	pos, neg = np.zeros(len(d)), np.zeros(len(d))
	pos_area, neg_area = np.zeros(len(d)), np.zeros(len(d))
	allp, alln = s0 >= 0, s2 <= 0
	pos[allp], pos_area[allp & (s2 > 0)] = total[allp], area[allp & (s2 > 0)]
	neg[alln], neg_area[alln & (s0 < 0)] = -total[alln], area[alln & (s0 < 0)]
	mix = ~(allp | alln)
	with np.errstate(divide="ignore", invalid="ignore"):
		one_neg = mix & (s1 >= 0)  # s0 < 0 <= s1 <= s2
		two_neg = mix & (s1 < 0)  # s0 <= s1 < 0 < s2
		# the negative corner region of a one-negative triangle is similar with ratio r = s0²/((s1-s0)(s2-s0))
		r1 = np.where(one_neg, s0 * s0 / ((s1 - s0) * (s2 - s0)), 0)
		neg[one_neg] = area[one_neg] * r1[one_neg] * (-s0[one_neg]) / 3
		neg_area[one_neg] = area[one_neg] * r1[one_neg]
		pos[one_neg] = total[one_neg] + neg[one_neg]
		pos_area[one_neg] = area[one_neg] - neg_area[one_neg]
		r2 = np.where(two_neg, s2 * s2 / ((s2 - s0) * (s2 - s1)), 0)
		pos[two_neg] = area[two_neg] * r2[two_neg] * s2[two_neg] / 3
		pos_area[two_neg] = area[two_neg] * r2[two_neg]
		neg[two_neg] = pos[two_neg] - total[two_neg]
		neg_area[two_neg] = area[two_neg] - pos_area[two_neg]
	return pos, neg, pos_area, neg_area


def volume(request, log):
	"""Cut/fill between a base TIN and a comparison TIN (or an elevation), inside an optional boundary.
	cut = material above the comparison (base higher), fill = space below it (comparison higher)."""
	workspace = request["workspace"]
	base = Tin.read(inside(workspace, request["base"]))
	comp = Tin.read(inside(workspace, request["comparison"])) if request.get("comparison") else None
	elevation = request.get("elevation")
	boundary = request.get("boundary")
	holes = request.get("holes") or []
	if comp is None and elevation is None:
		raise ValueError("Compare with a surface or an elevation.")
	emit(progress=5, log="Building the composite triangulation…")
	loops = ([boundary] if boundary else []) + holes
	sums = dict.fromkeys(("cut", "fill", "cut_area", "fill_area", "plan_area", "net_direct", "level_area"), 0.0)
	count, max_depth, max_height = 0, -np.inf, -np.inf
	cut_ok = fill_ok = True
	blocks = 0
	for b_tin, c_tin, xy, tri, ta, tb, cen in composite_blocks(base, comp, loops):
		blocks += 1
		if boundary or holes:
			keep = in_polygons(cen[:, 0], cen[:, 1], boundary, holes)
			tri, ta, tb = tri[keep], ta[keep], tb[keep]
		if not len(tri):
			continue
		q = xy[tri]
		zb = np.stack([plane_z(b_tin.xyz, b_tin.tri, ta, q[:, k, 0], q[:, k, 1]) for k in range(3)], axis=1)
		zc = np.stack([plane_z(c_tin.xyz, c_tin.tri, tb, q[:, k, 0], q[:, k, 1]) for k in range(3)], axis=1) if comp is not None else np.full_like(zb, float(elevation))
		d = zc - zb  # > 0: comparison above base = fill
		area = 0.5 * np.abs((q[:, 1, 0] - q[:, 0, 0]) * (q[:, 2, 1] - q[:, 0, 1]) - (q[:, 2, 0] - q[:, 0, 0]) * (q[:, 1, 1] - q[:, 0, 1]))
		fill, cut, fill_area, cut_area = split_volume(d, area)
		sums["cut"] += float(cut.sum())
		sums["fill"] += float(fill.sum())
		sums["cut_area"] += float(cut_area.sum())
		sums["fill_area"] += float(fill_area.sum())
		sums["plan_area"] += float(area.sum())
		sums["net_direct"] += float((area * d.sum(axis=1) / 3).sum())
		sums["level_area"] += float(area[(np.abs(d) < 1e-12).all(axis=1)].sum())
		count += len(tri)
		max_depth, max_height = max(max_depth, float(np.max(-d))), max(max_height, float(np.max(d)))
		cut_ok = cut_ok and bool((cut >= -1e-9 * area * (np.abs(d).max(axis=1) + 1)).all())
		fill_ok = fill_ok and bool((fill >= -1e-9 * area * (np.abs(d).max(axis=1) + 1)).all())
	emit(progress=60, log=f"{count} composite triangles in {blocks} block(s).")
	net_direct = sums["net_direct"]
	result = {
		"cut": sums["cut"],
		"fill": sums["fill"],
		"net": sums["fill"] - sums["cut"],
		"cut_area": sums["cut_area"],
		"fill_area": sums["fill_area"],
		"plan_area": sums["plan_area"],
		"composite_triangles": int(count),
		"blocks": blocks,
		"base_triangles": int(len(base.tri)),
		"comparison_triangles": int(len(comp.tri)) if comp is not None else 0,
		"max_depth": max_depth if count else 0.0,
		"max_height": max_height if count else 0.0,
	}
	checks = {
		"net_matches_direct_integral": abs(result["net"] - net_direct) <= 1e-9 * max(1.0, abs(net_direct), result["cut"] + result["fill"]),
		"net_direct": net_direct,
		"areas_add_up": abs(result["cut_area"] + result["fill_area"] - result["plan_area"]) <= 1e-6 * max(result["plan_area"], 1.0) + sums["level_area"],
		"cut_not_negative": cut_ok,
		"fill_not_negative": fill_ok,
	}
	result["validation"] = checks
	result["valid"] = all(v for k, v in checks.items() if isinstance(v, bool))
	result["method"] = (
		"Exact TIN-to-TIN volume: the two surfaces"
		+ (" and the boundary" if loops else "")
		+ " are overlaid in one constrained Delaunay triangulation in which every edge of both surfaces is a constraint"
		" (crossings resolved). Each composite triangle therefore lies inside one triangle of each surface, so the"
		" elevation difference is linear over it; cut and fill are integrated in closed form, splitting triangles"
		" exactly where the difference changes sign. Only the area covered by both surfaces is measured."
		+ (f" Large surfaces are overlaid in {blocks} rectangular blocks whose edges are also constraints, so the sum stays exact." if blocks > 1 else "")
		if comp is not None
		else "Exact TIN-to-plane volume: the surface"
		+ (" and the boundary" if loops else "")
		+ f" are triangulated together; on each triangle the difference to elevation {elevation} is linear and is"
		" integrated in closed form, splitting triangles exactly where it changes sign."
	)
	emit(progress=100)
	return result


def intersect(request, log):
	"""Where two surfaces have the same elevation: the zero contour of their difference."""
	workspace = request["workspace"]
	a = Tin.read(inside(workspace, request["base"]))
	b = Tin.read(inside(workspace, request["comparison"]))
	xy, tri, ta, tb, _cen = composite(a, b)
	used, local = np.unique(tri, return_inverse=True)
	local = local.reshape(-1, 3)
	pts = xy[used]
	# Z of each composite vertex: from the first triangle that uses it (continuous surfaces agree)
	owner = np.full(len(used), -1)
	owner[local.ravel()] = np.repeat(np.arange(len(tri)), 3)
	za = plane_z(a.xyz, a.tri, ta[owner], pts[:, 0], pts[:, 1])
	zb = plane_z(b.xyz, b.tri, tb[owner], pts[:, 0], pts[:, 1])
	diff = np.c_[pts, zb - za]
	lines = chain(contour_segments(diff, local, 0.0))
	for line in lines:
		p = np.asarray(line["points"])
		line["points"] = np.c_[p[:, :2], a.z_at(p[:, 0], p[:, 1])].tolist()
	log.append(f"{len(lines)} intersection line(s).")
	return {"lines": lines}


def analyse(request, log):
	workspace = request["workspace"]
	tin = Tin.read(inside(workspace, request["tin"]))
	p = tin.xyz[tin.tri]
	n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
	plan = 0.5 * np.abs(n[:, 2])
	slope = np.degrees(np.arctan2(np.hypot(n[:, 0], n[:, 1]), np.abs(n[:, 2])))
	aspect = (np.degrees(np.arctan2(-n[:, 0], -n[:, 1])) + 360) % 360
	classes = [0, 2, 5, 10, 15, 20, 30, 45, 60, 90]
	hist = [{"from": classes[i], "to": classes[i + 1], "plan_area": float(plan[(slope >= classes[i]) & (slope < classes[i + 1])].sum())} for i in range(len(classes) - 1)]
	zs = tin.xyz[:, 2]
	band = float(request.get("elevation_band") or max((zs.max() - zs.min()) / 10, 1e-9))
	zc = p[:, :, 2].mean(axis=1)
	bands = []
	b0 = math.floor(zs.min() / band) * band
	while b0 < zs.max():
		m = (zc >= b0) & (zc < b0 + band)
		bands.append({"from": float(b0), "to": float(b0 + band), "plan_area": float(plan[m].sum())})
		b0 += band
	w = plan / max(plan.sum(), 1e-12)
	return {
		"slope": {"mean": float((slope * w).sum()), "max": float(slope.max()), "classes": hist},
		"aspect": {"mean": float(np.degrees(np.arctan2((np.sin(np.radians(aspect)) * w).sum(), (np.cos(np.radians(aspect)) * w).sum())) % 360)},
		"elevation": {"min": float(zs.min()), "max": float(zs.max()), "bands": bands},
		"stats": tin.stats(),
	}


# ---------------------------------------------------------------------------
# import / export
# ---------------------------------------------------------------------------


def axis_map(xyz, mapping):
	if mapping == "Negate X and Y":
		xyz[:, 0] *= -1
		xyz[:, 1] *= -1
	elif mapping == "Swap X and Y":
		xyz[:, [0, 1]] = xyz[:, [1, 0]]
	return xyz


def read_points_text(path, columns):
	"""CSV / XYZ / TXT points. columns: e.g. "X Y Z", "Y X Z", "ID X Y Z", "ID Y X Z" (auto when blank)."""
	import csv

	with open(path, newline="", errors="replace") as f:
		sample = f.read(65536)
		f.seek(0)
		try:
			dialect = csv.Sniffer().sniff(sample, delimiters=",; \t")
			reader = csv.reader(f, dialect)
		except csv.Error:
			reader = (line.split() for line in f)
		rows, skipped = [], 0
		for row in reader:
			row = [c.strip() for c in row if c.strip() != ""]
			if len(row) < 3:
				skipped += 1
				continue
			rows.append(row)
	if not rows:
		raise ValueError("No point rows found.")

	def numeric(v):
		try:
			float(v)
			return True
		except ValueError:
			return False

	header = not all(numeric(v) for v in rows[0][:3]) and not (len(rows[0]) > 3 and all(numeric(v) for v in rows[0][1:4]))
	if header:
		rows = rows[1:]
	order = (columns or "").split()
	if not order:
		order = ["ID", "X", "Y", "Z"] if len(rows[0]) >= 4 and not numeric(rows[0][0]) else ["X", "Y", "Z"]
	ix, iy, iz = order.index("X"), order.index("Y"), order.index("Z")
	pts, ids, bad = [], [], 0
	for row in rows:
		try:
			pts.append((float(row[ix]), float(row[iy]), float(row[iz])))
			ids.append(row[order.index("ID")] if "ID" in order else None)
		except (ValueError, IndexError):
			bad += 1
	return np.asarray(pts, float), ids, {"header": header, "columns": " ".join(order), "skipped_rows": skipped + bad}


def read_landxml(path):
	"""Surfaces (TIN: Pnts + Faces) and CgPoints of a LandXML file. LandXML points are 'northing easting elevation'."""
	surfaces, points = [], []
	current = None
	for event, el in ET.iterparse(path, events=("start", "end")):
		tag = el.tag.rsplit("}", 1)[-1]
		if event == "start" and tag == "Surface":
			current = {"name": el.get("name") or f"Surface {len(surfaces) + 1}", "ids": {}, "pts": [], "faces": []}
		elif event == "end" and tag == "P" and current is not None:
			n, e, z = (float(v) for v in el.text.split()[:3])
			current["ids"][el.get("id")] = len(current["pts"])
			current["pts"].append((e, n, z))
			el.clear()
		elif event == "end" and tag == "F" and current is not None:
			if el.get("i") != "1":  # i="1": invisible (outside) face
				current["faces"].append(el.text.split()[:3])
			el.clear()
		elif event == "end" and tag == "Surface" and current is not None:
			ids = current["ids"]
			tri = [[ids[a], ids[b], ids[c]] for a, b, c in current["faces"] if a in ids and b in ids and c in ids]
			surfaces.append({"name": current["name"], "xyz": np.asarray(current["pts"], float), "tri": np.asarray(tri, np.int64).reshape(-1, 3)})
			current = None
			el.clear()
		elif event == "end" and tag == "CgPoint" and el.text:
			vals = el.text.split()
			if len(vals) >= 2:
				n, e = float(vals[0]), float(vals[1])
				z = float(vals[2]) if len(vals) > 2 else 0.0
				points.append((el.get("name") or el.get("pntRef") or "", e, n, z))
			el.clear()
	return surfaces, points


def read_stl(path):
	with open(path, "rb") as f:
		data = f.read()
	ascii_ = data[:5].lower() == b"solid" and b"facet" in data[:2000]
	if not ascii_ and len(data) >= 84:
		(count,) = struct.unpack_from("<I", data, 80)
		if 84 + count * 50 == len(data):
			rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=count, offset=84)
			tris = rec["v"].astype(np.float64)
			return dedupe_triangles(tris)
	verts = []
	for line in data.decode(errors="replace").splitlines():
		line = line.strip()
		if line.startswith("vertex"):
			verts.append([float(v) for v in line.split()[1:4]])
	tris = np.asarray(verts, float).reshape(-1, 3, 3)
	return dedupe_triangles(tris)


def dedupe_triangles(tris):
	flat = tris.reshape(-1, 3)
	uniq, inv = unique_rows(flat, return_inverse=True)
	return uniq, inv.reshape(-1, 3)


def read_obj(path):
	verts, faces = [], []
	with open(path, errors="replace") as f:
		for line in f:
			parts = line.split()
			if not parts:
				continue
			if parts[0] == "v":
				verts.append([float(v) for v in parts[1:4]])
			elif parts[0] == "f":
				idx = [int(p.split("/")[0]) for p in parts[1:]]
				idx = [i - 1 if i > 0 else len(verts) + i for i in idx]
				for k in range(1, len(idx) - 1):  # fan for polygons
					faces.append([idx[0], idx[k], idx[k + 1]])
	return np.asarray(verts, float), np.asarray(faces, np.int64).reshape(-1, 3)


def read_las(path, options):
	import laspy

	with laspy.open(path) as reader:
		header = reader.header
		las = reader.read()
	xyz = np.c_[las.x, las.y, las.z].astype(np.float64)
	info = {"version": f"{header.version.major}.{header.version.minor}", "point_format": int(header.point_format.id), "points": int(len(xyz))}
	if options.get("ground_only") and "classification" in las.point_format.dimension_names:
		mask = np.asarray(las.classification) == 2
		info["ground_points"] = int(mask.sum())
		xyz = xyz[mask]
	cell = float(options.get("thin") or 0)
	if cell > 0 and len(xyz):
		key = np.floor(xyz[:, :2] / cell).astype(np.int64)
		order = np.lexsort((xyz[:, 2], key[:, 1], key[:, 0]))  # lowest point per cell
		k = key[order]
		first = np.r_[True, (np.diff(k, axis=0) != 0).any(axis=1)]
		xyz = xyz[order][first]
		info["thinned_to"] = int(len(xyz))
	return xyz, info


def is_two_and_half_d(xyz, tri):
	"""A mesh can be a surface when no two triangles overlap in plan: all face the same way up."""
	if not len(tri):
		return False
	p = xyz[tri]
	nz = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])[:, 2]
	return bool((nz > 0).all() or (nz < 0).all())


def import_file(request, log):
	"""Read a file into surfaces / meshes / points (in the workspace) and report what it holds."""
	workspace = request["workspace"]
	path = inside(workspace, request["input"])
	fmt = request["format"]
	options = request.get("options") or {}
	scale = float(options.get("unit_scale") or 1.0)
	mapping = options.get("axis_mapping") or "As in file"
	out_dir = inside(workspace, os.path.join(workspace, "outputs"))
	os.makedirs(out_dir, exist_ok=True)
	report = {"format": fmt, "surfaces": [], "meshes": [], "points": 0, "warnings": []}
	surfaces, meshes, points = [], [], None

	def prepare(xyz):
		xyz = np.asarray(xyz, float) * scale
		return axis_map(xyz, mapping)

	if fmt == "LandXML":
		found, pts = read_landxml(path)
		for s in found:
			surfaces.append((s["name"], prepare(s["xyz"]), s["tri"]))
		if pts:
			points = (prepare([p[1:] for p in pts]), [p[0] for p in pts])
	elif fmt in ("CSV", "XYZ"):
		xyz, ids, info = read_points_text(path, options.get("columns"))
		report.update(info)
		points = (prepare(xyz), ids)
	elif fmt in ("LAS", "LAZ"):
		xyz, info = read_las(path, options)
		report.update(info)
		points = (prepare(xyz), None)
	elif fmt in ("STL", "OBJ"):
		xyz, tri = read_stl(path) if fmt == "STL" else read_obj(path)
		xyz = prepare(xyz)
		name = os.path.splitext(os.path.basename(request.get("file_name") or "mesh"))[0]
		if is_two_and_half_d(xyz, tri) and not options.get("as_mesh"):
			surfaces.append((name, xyz, tri))
		else:
			meshes.append((name, xyz, tri))
	else:
		raise ValueError(f"Unknown format {fmt}.")

	for n, (name, xyz, tri) in enumerate(surfaces):
		if mapping == "Swap X and Y" and len(tri):
			tri = tri[:, [0, 2, 1]]
		tin = compact(Tin(xyz, tri))
		tin.tri, flipped = orient_ccw(tin.xyz, tin.tri)
		file = os.path.join(out_dir, f"surface_{n}.tin")
		tin.write(file)
		stats = tin.stats()
		stats["validation"] = validate_tin(tin)
		report["surfaces"].append({"name": name, "tin": file, "stats": stats, "reoriented": flipped})
	for n, (name, xyz, tri) in enumerate(meshes):
		mesh = Tin(xyz, tri)
		file = os.path.join(out_dir, f"mesh_{n}.tin")
		mesh.write(file)
		stats = mesh.stats()
		stats.update(mesh_checks(mesh))
		report["meshes"].append({"name": name, "tin": file, "stats": stats})
	if points is not None:
		xyz, ids = points
		if not len(xyz):
			raise ValueError("No points found.")
		file = os.path.join(out_dir, "points.npy")
		np.save(file, xyz)
		box = np.r_[xyz.min(0), xyz.max(0)]
		report.update({"points": int(len(xyz)), "points_file": file, "points_bbox": box.tolist(), "point_ids": ids[:200000] if ids and any(ids) else None})
		merged, _inv, conflicts = merge_points(xyz)
		report["duplicate_points"] = int(len(xyz) - len(merged))
		report["conflicting_points"] = conflicts
	emit(progress=100)
	return report


def mesh_checks(mesh):
	"""Closed-ness and enclosed volume of a general mesh (signed tetrahedra; meaningful when closed)."""
	e = np.sort(np.r_[mesh.tri[:, [0, 1]], mesh.tri[:, [1, 2]], mesh.tri[:, [2, 0]]], axis=1)
	_u, counts = unique_rows(e, return_counts=True)
	closed = bool((counts == 2).all())
	p = mesh.xyz[mesh.tri]
	vol = float(np.einsum("ij,ij->i", p[:, 0], np.cross(p[:, 1], p[:, 2])).sum() / 6) if closed else None
	return {"closed": closed, "open_edges": int((counts == 1).sum()), "non_manifold_edges": int((counts > 2).sum()), "volume": abs(vol) if vol is not None else None}


def export_file(request, log):
	workspace = request["workspace"]
	fmt = request["format"]
	out = inside(workspace, request["out"])
	mapping = request.get("axis_mapping") or "As in file"
	items = [(s["name"], Tin.read(inside(workspace, s["tin"]))) for s in request["surfaces"]]
	for _name, tin in items:
		axis_map(tin.xyz, mapping)
		if mapping == "Swap X and Y":
			tin.tri = tin.tri[:, [0, 2, 1]]
	if fmt == "LandXML":
		root = ET.Element("LandXML", {"xmlns": "http://www.landxml.org/schema/LandXML-1.2", "version": "1.2", "date": request.get("date", ""), "time": ""})
		units = ET.SubElement(root, "Units")
		ET.SubElement(units, "Metric", {"linearUnit": "meter", "areaUnit": "squareMeter", "volumeUnit": "cubicMeter", "angularUnit": "decimal degrees"})
		ET.SubElement(root, "Application", {"name": "is_production CAD Engineering Workstation"})
		group = ET.SubElement(root, "Surfaces")
		for name, tin in items:
			s = ET.SubElement(group, "Surface", {"name": name})
			d = ET.SubElement(s, "Definition", {"surfType": "TIN"})
			pnts = ET.SubElement(d, "Pnts")
			for i, (x, y, z) in enumerate(tin.xyz.tolist(), start=1):  # Python floats: repr is exact and plain
				ET.SubElement(pnts, "P", {"id": str(i)}).text = f"{y!r} {x!r} {z!r}"
			faces = ET.SubElement(d, "Faces")
			for a, b, c in tin.tri.tolist():
				ET.SubElement(faces, "F").text = f"{a + 1} {b + 1} {c + 1}"
		ET.ElementTree(root).write(out, encoding="utf-8", xml_declaration=True)
	elif fmt == "STL":
		with open(out, "wb") as f:
			total = sum(len(t.tri) for _n, t in items)
			f.write(b"is_production CAD Engineering Workstation".ljust(80, b" "))
			f.write(struct.pack("<I", total))
			for _name, tin in items:
				p = tin.xyz[tin.tri]
				n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
				n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
				rec = np.zeros(len(p), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]))
				rec["n"], rec["v"] = n, p
				f.write(rec.tobytes())
		log.append("STL stores single-precision coordinates: survey coordinates lose precision (about 0.25 m at 2 860 000). Prefer LandXML or OBJ.")
	elif fmt == "OBJ":
		with open(out, "w") as f:
			f.write("# is_production CAD Engineering Workstation\n")
			base = 0
			for name, tin in items:
				f.write(f"o {name}\n")
				for x, y, z in tin.xyz.tolist():
					f.write(f"v {x!r} {y!r} {z!r}\n")
				for a, b, c in tin.tri.tolist():
					f.write(f"f {a + 1 + base} {b + 1 + base} {c + 1 + base}\n")
				base += len(tin.xyz)
	elif fmt in ("CSV", "XYZ"):
		sep = "," if fmt == "CSV" else " "
		with open(out, "w") as f:
			if fmt == "CSV":
				f.write("x,y,z\n")
			for _name, tin in items:
				for x, y, z in tin.xyz.tolist():
					f.write(f"{x!r}{sep}{y!r}{sep}{z!r}\n")
	else:
		raise ValueError(f"Unknown format {fmt}.")
	return {"out": out, "bytes": os.path.getsize(out)}


COMMANDS = {
	"build": build,
	"tiles": tiles,
	"validate": lambda r, log: {"validation": validate_tin(Tin.read(inside(r["workspace"], r["tin"])), float(r.get("spike_factor") or 8.0)), "stats": Tin.read(inside(r["workspace"], r["tin"])).stats()},
	"repair": repair,
	"clip": clip,
	"smooth": smooth,
	"contours": contours,
	"profile": profile,
	"sections": sections,
	"volume": volume,
	"intersect": intersect,
	"analyse": analyse,
	"import": import_file,
	"export": export_file,
}


def main():
	log = []
	try:
		request = json.loads(sys.stdin.read() or "{}")
		command = COMMANDS.get(request.get("command"))
		if not command:
			raise ValueError(f"Unknown command {request.get('command')!r}.")
		apply_limits(request)
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
