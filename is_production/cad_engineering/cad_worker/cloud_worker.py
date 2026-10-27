"""Point clouds for the isolated CAD worker (Phase 6): import, editing, display tiles, surfaces and comparison.

A cloud is kept as LAZ (laspy + lazrs): coordinates to the millimetre about a rounded offset, classification,
intensity, RGB when the source has it, and a "deviation" extra dimension after a comparison. Every edit writes a new
file (the server stores it under its SHA-256), so edits are undone like any other change.

Protocol as the other workers: one JSON request on stdin, JSON lines out (progress / log / result / error).

import     LAS / LAZ / E57 / XYZ / CSV / TXT → cloud (unit scale, axis mapping; E57: every scan in its pose, merged)
edit       one cloud through a list of operations: crop (outline, elevation), keep / drop classes, set class (in an
           outline, elevation band, from classes), remove noise (statistical outliers), decimate (voxel or random),
           classify ground (progressive morphological filter)
tiles      display tiles: per tile the points in random order, with nested coarser levels (prefixes)
surface    a TIN from (ground) points, thinned to the lowest point per cell
compare    deviation of every point from a TIN: statistics, histogram, and the cloud with a deviation dimension
export     LAS / LAZ / E57 / XYZ / CSV
"""

import json
import math
import os
import struct
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # -I leaves the script's folder off sys.path

import surface_worker as sw  # noqa: E402
from surface_worker import Tin, emit, inside  # noqa: E402

MAX_POINTS = 60_000_000
TILE_POINTS = 400_000  # points per display tile (level 0)
LEVEL_RATIO = 8  # each coarser level keeps 1 in 8
CLASS_NAMES = {0: "Never classified", 1: "Unclassified", 2: "Ground", 3: "Low vegetation", 4: "Medium vegetation", 5: "High vegetation", 6: "Building", 7: "Low point (noise)", 9: "Water", 17: "Bridge deck", 18: "High noise"}


# ---------------------------------------------------------------------------
# the cloud in memory and on disk
# ---------------------------------------------------------------------------


class Cloud:
	"""xyz (n, 3) float64, cls uint8, intensity uint16, rgb (n, 3) uint16 or None, deviation float32 or None."""

	def __init__(self, xyz, cls=None, intensity=None, rgb=None, deviation=None):
		n = len(xyz)
		self.xyz = np.asarray(xyz, np.float64).reshape(-1, 3)
		self.cls = np.zeros(n, np.uint8) if cls is None else np.asarray(cls, np.uint8)
		self.intensity = np.zeros(n, np.uint16) if intensity is None else np.asarray(intensity, np.uint16)
		self.rgb = None if rgb is None else np.asarray(rgb, np.uint16).reshape(-1, 3)
		self.deviation = None if deviation is None else np.asarray(deviation, np.float32)

	def __len__(self):
		return len(self.xyz)

	def subset(self, mask):
		return Cloud(self.xyz[mask], self.cls[mask], self.intensity[mask], None if self.rgb is None else self.rgb[mask], None if self.deviation is None else self.deviation[mask])

	@staticmethod
	def read(path):
		import laspy

		las = laspy.read(path)
		names = set(las.point_format.dimension_names)
		rgb = np.c_[las.red, las.green, las.blue] if {"red", "green", "blue"} <= names else None
		if rgb is not None and not rgb.any():
			rgb = None
		deviation = np.asarray(las["deviation"], np.float32) if "deviation" in names else None
		return Cloud(np.c_[las.x, las.y, las.z], np.asarray(las.classification, np.uint8), np.asarray(las.intensity, np.uint16), rgb, deviation)

	def write(self, path):
		import laspy

		header = laspy.LasHeader(point_format=3 if self.rgb is not None else 1, version="1.2")
		if len(self):
			lo = self.xyz.min(axis=0)
			header.offsets = np.floor(lo / 1000.0) * 1000.0
		header.scales = np.array([0.001, 0.001, 0.001])
		if self.deviation is not None:
			header.add_extra_dim(laspy.ExtraBytesParams(name="deviation", type=np.float32, description="Distance to a surface"))
		las = laspy.LasData(header)
		las.x, las.y, las.z = self.xyz[:, 0], self.xyz[:, 1], self.xyz[:, 2]
		las.classification = self.cls
		las.intensity = self.intensity
		if self.rgb is not None:
			las.red, las.green, las.blue = self.rgb[:, 0], self.rgb[:, 1], self.rgb[:, 2]
		if self.deviation is not None:
			las["deviation"] = self.deviation
		las.write(path)

	def stats(self):
		n = len(self)
		if not n:
			return {"count": 0, "bbox": [0, 0, 0, 0, 0, 0]}
		lo, hi = self.xyz.min(axis=0), self.xyz.max(axis=0)
		counts = np.bincount(self.cls, minlength=1)
		out = {
			"count": int(n),
			"bbox": [float(v) for v in (*lo, *hi)],
			"min_elevation": float(lo[2]),
			"max_elevation": float(hi[2]),
			"classes": {str(c): int(k) for c, k in enumerate(counts) if k},
			"has_rgb": self.rgb is not None,
			"intensity_range": [int(self.intensity.min()), int(self.intensity.max())],
		}
		area = float((hi[0] - lo[0]) * (hi[1] - lo[1]))
		out["density"] = n / area if area > 0 else None  # points per m² of the plan extent
		if self.deviation is not None:
			d = self.deviation[np.isfinite(self.deviation)]
			out["deviation_range"] = [float(d.min()), float(d.max())] if len(d) else None
		return out


def output(workspace, name):
	path = os.path.join(workspace, "outputs", name)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	return path


def finish(cloud, workspace, name, extra=None):
	if not len(cloud):
		raise ValueError("No points are left.")
	path = output(workspace, f"{name}.laz")
	cloud.write(path)
	return {"cloud": path, "stats": cloud.stats(), **(extra or {})}


# ---------------------------------------------------------------------------
# import / export
# ---------------------------------------------------------------------------


def read_text(path, columns):
	"""XYZ / CSV / TXT: x y z, optionally intensity and r g b (by column count) when the columns are not named."""
	xyz, _ids, _info = sw.read_points_text(path, columns)
	extra = None
	if not columns:
		delimiter = "," if path.lower().endswith(".csv") else None
		for skip in (0, 1):  # a header line, or none
			try:
				table = np.loadtxt(path, delimiter=delimiter, skiprows=skip, ndmin=2)
			except ValueError:
				continue
			extra = table if table.shape[0] == len(xyz) and table.shape[1] in (4, 6, 7) else None
			break
	if extra is None:
		return Cloud(xyz)
	if extra.shape[1] == 4:
		return Cloud(xyz, intensity=np.clip(extra[:, 3], 0, 65535))
	rgb = extra[:, -3:]
	scale = 257 if rgb.max() <= 255 else 1  # 8-bit colour to the LAS 16-bit range
	return Cloud(xyz, intensity=np.clip(extra[:, 3], 0, 65535) if extra.shape[1] == 7 else None, rgb=np.clip(rgb * scale, 0, 65535))


def read_e57(path):
	"""Every scan of an E57 file (in its pose: the scan's rotation and translation applied), merged; intensity
	(0–1 in E57) to the LAS 16-bit range, colour when the scans have it."""
	import pye57

	e = pye57.E57(path)
	parts = []
	for i in range(e.scan_count):
		d = e.read_scan(i, intensity=True, colors=True, ignore_missing_fields=True, transform=True)
		x = np.asarray(d["cartesianX"], float)
		ok = np.isfinite(x)
		xyz = np.c_[x, d["cartesianY"], d["cartesianZ"]][ok]
		inten = d.get("intensity")
		inten = None if inten is None or not len(inten) else np.clip(np.asarray(inten, float)[ok] * 65535, 0, 65535)
		rgb = None
		if d.get("colorRed") is not None and len(d["colorRed"]):
			rgb = np.c_[d["colorRed"], d["colorGreen"], d["colorBlue"]][ok].astype(float) * 257
		parts.append((xyz, inten, rgb))
		emit(log=f"E57 scan {i + 1}/{e.scan_count}: {len(xyz)} points.")
	if not parts:
		raise ValueError("The E57 file has no scans.")
	xyz = np.concatenate([p[0] for p in parts])
	inten = np.concatenate([p[1] if p[1] is not None else np.zeros(len(p[0])) for p in parts])
	rgb = np.concatenate([p[2] if p[2] is not None else np.zeros((len(p[0]), 3)) for p in parts]) if any(p[2] is not None for p in parts) else None
	return Cloud(xyz, intensity=inten, rgb=rgb)


def inspect_e57(request, log):
	"""Scans, points and extents of an E57 file from its headers (no points read)."""
	import pye57

	e = pye57.E57(inside(request["workspace"], request["input"]))
	scans, total, lo, hi, colour = [], 0, None, None, False
	for i in range(e.scan_count):
		h = e.get_header(i)
		n = int(h.point_count)
		total += n
		fields = set(h.point_fields)
		colour = colour or "colorRed" in fields
		scans.append({"name": h["name"].value() if h.node.isDefined("name") else f"Scan {i + 1}", "points": n})
		try:
			b = h.cartesianBounds
			box = np.array([[b["xMinimum"].value(), b["yMinimum"].value(), b["zMinimum"].value()], [b["xMaximum"].value(), b["yMaximum"].value(), b["zMaximum"].value()]])
			lo = box[0] if lo is None else np.minimum(lo, box[0])
			hi = box[1] if hi is None else np.maximum(hi, box[1])
		except Exception:  # bounds are optional in E57 (and are in the scan's own frame)
			pass
	return {"format": "E57", "scans": scans, "points": total, "has_rgb": colour, "extents": [float(v) for v in (*lo, *hi)] if lo is not None else None}


def write_e57(cloud, path):
	import pye57

	if os.path.exists(path):
		os.remove(path)
	e = pye57.E57(path, mode="w")
	data = {"cartesianX": cloud.xyz[:, 0], "cartesianY": cloud.xyz[:, 1], "cartesianZ": cloud.xyz[:, 2], "intensity": cloud.intensity.astype(float) / 65535}
	if cloud.rgb is not None:
		data.update({"colorRed": (cloud.rgb[:, 0] // 257).astype(np.uint8), "colorGreen": (cloud.rgb[:, 1] // 257).astype(np.uint8), "colorBlue": (cloud.rgb[:, 2] // 257).astype(np.uint8)})
	e.write_scan_raw(data, name="CAD Workstation export")
	e.close()


def import_cloud(request, log):
	workspace = request["workspace"]
	path = inside(workspace, request["input"])
	o = request.get("options") or {}
	if path.lower().endswith((".las", ".laz")):
		cloud = Cloud.read(path)
	elif path.lower().endswith(".e57"):
		cloud = read_e57(path)
	else:
		cloud = read_text(path, o.get("columns"))
	if len(cloud) > MAX_POINTS:
		raise ValueError(f"{len(cloud)} points: at most {MAX_POINTS} per cloud (decimate it first).")
	cloud.xyz *= float(o.get("unit_scale") or 1.0)
	cloud.xyz = sw.axis_map(cloud.xyz, o.get("axis_mapping") or "As in file")
	emit(progress=60, log=f"{len(cloud)} points read.")
	return finish(cloud, workspace, request.get("name") or "cloud")


def export_cloud(request, log):
	workspace = request["workspace"]
	cloud = Cloud.read(inside(workspace, request["cloud"]))
	fmt = (request.get("format") or "LAZ").upper()
	out = output(workspace, f"{request.get('name') or 'cloud'}.{fmt.lower()}")
	if fmt in ("LAS", "LAZ"):
		cloud.write(out)
	elif fmt == "E57":
		write_e57(cloud, out)
	else:
		cols = [cloud.xyz, cloud.intensity[:, None].astype(float), cloud.cls[:, None].astype(float)]
		header = "x,y,z,intensity,classification"
		if cloud.rgb is not None:
			cols.append((cloud.rgb // 257).astype(float))
			header += ",red,green,blue"
		table = np.hstack(cols)
		sep = "," if fmt == "CSV" else " "
		np.savetxt(out, table, fmt=["%.3f"] * 3 + ["%d"] * (table.shape[1] - 3), delimiter=sep, header=header.replace(",", sep) if fmt == "CSV" else "", comments="")
	return {"file": out, "points": int(len(cloud))}


# ---------------------------------------------------------------------------
# editing
# ---------------------------------------------------------------------------


def polygon_mask(xyz, polygon):
	from matplotlib.path import Path

	poly = np.asarray(polygon, float)[:, :2]
	lo, hi = poly.min(axis=0), poly.max(axis=0)
	mask = np.zeros(len(xyz), bool)
	cand = np.flatnonzero((xyz[:, 0] >= lo[0]) & (xyz[:, 0] <= hi[0]) & (xyz[:, 1] >= lo[1]) & (xyz[:, 1] <= hi[1]))
	if len(cand):
		mask[cand] = Path(poly).contains_points(xyz[cand, :2])
	return mask


def band_mask(xyz, zmin, zmax):
	z = xyz[:, 2]
	mask = np.ones(len(z), bool)
	if zmin is not None:
		mask &= z >= float(zmin)
	if zmax is not None:
		mask &= z <= float(zmax)
	return mask


def remove_noise(cloud, k=8, std=2.0):
	"""Statistical outlier removal: a point whose mean distance to its k nearest neighbours is more than `std`
	standard deviations above the cloud's mean of that distance is noise."""
	from scipy.spatial import cKDTree

	k = int(min(max(k, 1), len(cloud) - 1))
	d, _ = cKDTree(cloud.xyz).query(cloud.xyz, k=k + 1)
	mean = d[:, 1:].mean(axis=1)
	limit = mean.mean() + float(std) * mean.std()
	return mean <= limit


def voxel_keep(cloud, cell, keep="lowest"):
	"""One point per cube of side `cell` (lowest, highest, or the one nearest the cube's centre)."""
	key = np.floor((cloud.xyz - cloud.xyz.min(axis=0)) / cell).astype(np.int64)
	m = key.max(axis=0) + 1
	packed = (key[:, 0] * m[1] + key[:, 1]) * m[2] + key[:, 2]
	if keep == "highest":
		score = -cloud.xyz[:, 2]
	elif keep == "centre":
		score = np.linalg.norm(cloud.xyz - (np.floor((cloud.xyz - cloud.xyz.min(axis=0)) / cell) + 0.5) * cell - cloud.xyz.min(axis=0), axis=1)
	else:
		score = cloud.xyz[:, 2]
	order = np.lexsort((score, packed))
	first = np.r_[True, packed[order][1:] != packed[order][:-1]]
	mask = np.zeros(len(cloud), bool)
	mask[order[first]] = True
	return mask


def ground_grid(xyz, cell, max_window, slope, dh0, dh_max):
	"""Progressive morphological filter (Zhang et al. 2003) on the grid of lowest points: openings with growing
	windows; a cell rising more than the window's height threshold above its opening is not ground. Returns the
	ground grid (cells not flagged, the gaps filled by linear interpolation) and its geometry."""
	from scipy import ndimage
	from scipy.interpolate import griddata

	lo = xyz[:, :2].min(axis=0)
	ij = np.floor((xyz[:, :2] - lo) / cell).astype(np.int64)
	nx, ny = ij.max(axis=0) + 1
	if nx * ny > 25_000_000:
		raise ValueError("The ground grid would be too large: use a larger cell.")
	zmin = np.full((nx, ny), np.inf)
	np.minimum.at(zmin, (ij[:, 0], ij[:, 1]), xyz[:, 2])
	empty = ~np.isfinite(zmin)
	if empty.any():  # fill empty cells from the nearest filled one
		idx = ndimage.distance_transform_edt(empty, return_distances=False, return_indices=True)
		zmin = zmin[tuple(idx)]
	surface = zmin.copy()
	flagged = np.zeros_like(empty)
	w_prev, k = 1, 0
	while True:
		w = 2 * (2**k) + 1  # 3, 5, 9, 17, … cells
		if (w - 1) * cell > max_window:
			break
		dh = min(dh0 + slope * (w - w_prev) * cell, dh_max) if k else dh0
		opened = ndimage.grey_opening(surface, size=(w, w))
		flagged |= (surface - opened) > dh
		surface = opened
		w_prev, k = w, k + 1
	ground = ~flagged & ~empty
	gx, gy = np.meshgrid(lo[0] + (np.arange(nx) + 0.5) * cell, lo[1] + (np.arange(ny) + 0.5) * cell, indexing="ij")
	grid = zmin.copy()
	if (~ground).any() and ground.sum() >= 3:
		fill = griddata(np.c_[gx[ground], gy[ground]], zmin[ground], (gx[~ground], gy[~ground]), method="linear")
		near = griddata(np.c_[gx[ground], gy[ground]], zmin[ground], (gx[~ground], gy[~ground]), method="nearest")
		grid[~ground] = np.where(np.isfinite(fill), fill, near)
	return grid, lo, cell, gx, gy


def classify_ground(cloud, cell=1.0, max_window=40.0, slope=0.3, dh0=0.3, dh_max=3.0, tolerance=0.3):
	"""Ground (class 2) = points within `tolerance` of the filtered ground grid (bilinear between cell centres);
	never-classified and former ground points that are not ground become unclassified (1); points far below it, low
	noise (7). Other classes (buildings, vegetation set by hand) are kept."""
	from scipy.interpolate import RegularGridInterpolator

	grid, lo, cell, gx, gy = ground_grid(cloud.xyz, float(cell), float(max_window), float(slope), float(dh0), float(dh_max))
	xs, ys = gx[:, 0], gy[0, :]
	if len(xs) < 2 or len(ys) < 2:
		zg = np.full(len(cloud), grid.min())
	else:
		f = RegularGridInterpolator((xs, ys), grid, bounds_error=False, fill_value=None)
		zg = f(cloud.xyz[:, :2])
	dz = cloud.xyz[:, 2] - zg
	cls = cloud.cls.copy()
	ground = np.abs(dz) <= float(tolerance)
	cls[((cls == 2) | (cls == 0)) & ~ground] = 1  # processed, not ground (LAS: unclassified)
	cls[ground] = 2
	cls[dz < -max(3 * float(tolerance), 1.0)] = 7
	cloud.cls = cls
	return {"ground": int(ground.sum()), "low_noise": int((dz < -max(3 * float(tolerance), 1.0)).sum())}


def edit(request, log):
	workspace = request["workspace"]
	cloud = Cloud.read(inside(workspace, request["cloud"]))
	report = []
	ops = request.get("operations") or []
	for n, op in enumerate(ops):
		kind = op.get("op")
		before = len(cloud)
		info = {}
		if kind == "crop":
			mask = np.ones(len(cloud), bool)
			if op.get("polygon"):
				inside_poly = polygon_mask(cloud.xyz, op["polygon"])
				mask &= inside_poly if op.get("keep", "inside") == "inside" else ~inside_poly
			mask &= band_mask(cloud.xyz, op.get("zmin"), op.get("zmax"))
			cloud = cloud.subset(mask)
		elif kind == "classes":
			wanted = np.isin(cloud.cls, [int(c) for c in op.get("classes") or []])
			cloud = cloud.subset(wanted if op.get("keep", True) else ~wanted)
		elif kind == "set_class":
			mask = np.ones(len(cloud), bool)
			if op.get("polygon"):
				mask &= polygon_mask(cloud.xyz, op["polygon"])
			mask &= band_mask(cloud.xyz, op.get("zmin"), op.get("zmax"))
			if op.get("from") not in (None, [], ""):
				mask &= np.isin(cloud.cls, [int(c) for c in op["from"]])
			cloud.cls[mask] = int(op["to"])
			info["changed"] = int(mask.sum())
		elif kind == "noise":
			cloud = cloud.subset(remove_noise(cloud, int(op.get("k") or 8), float(op.get("std") or 2.0)))
		elif kind == "decimate":
			if op.get("mode") == "random":
				fraction = float(op.get("fraction") or 0.5)
				rng = np.random.default_rng(int(op.get("seed") or 1))
				cloud = cloud.subset(rng.random(len(cloud)) < fraction)
			else:
				cloud = cloud.subset(voxel_keep(cloud, float(op.get("cell") or 0.5), op.get("keep") or "lowest"))
		elif kind == "ground":
			info = classify_ground(cloud, **{k: op[k] for k in ("cell", "max_window", "slope", "dh0", "dh_max", "tolerance") if op.get(k) not in (None, "")})
		else:
			raise ValueError(f"Unknown point-cloud operation {kind!r}.")
		report.append({"op": kind, "before": before, "after": len(cloud), **info})
		emit(progress=int(90 * (n + 1) / max(len(ops), 1)), log=f"{kind}: {before} → {len(cloud)} points.")
	return finish(cloud, workspace, request.get("name") or "edited", {"report": report})


# ---------------------------------------------------------------------------
# display tiles
# ---------------------------------------------------------------------------


def write_points(path, origin, xyz, cls, intensity, rgb, deviation):
	"""Binary display tile "ISPPTS01": count, flags (1 class, 2 intensity, 4 rgb, 8 deviation), origin (float64 × 3),
	then float32 xyz relative to the origin, uint8 class, uint16 intensity, uint8 rgb, float32 deviation — each
	section padded to 4 bytes."""
	n = len(xyz)
	flags = 1 | 2 | (4 if rgb is not None else 0) | (8 if deviation is not None else 0)

	def pad(b):
		return b + b"\0" * (-len(b) % 4)

	parts = [b"ISPPTS01", struct.pack("<II", n, flags), struct.pack("<3d", *origin), (xyz - origin).astype("<f4").tobytes(), pad(cls.astype(np.uint8).tobytes()), pad(intensity.astype("<u2").tobytes())]
	if rgb is not None:
		parts.append(pad((rgb // 257).astype(np.uint8).tobytes()))
	if deviation is not None:
		parts.append(deviation.astype("<f4").tobytes())
	with open(path, "wb") as f:
		f.write(b"".join(parts))


def tiles(request, log):
	"""A grid of tiles of about TILE_POINTS points; in each the points in random order, written as nested levels:
	level 0 all, level 1 the first 1/8, level 2 the first 1/64 (each a prefix of the one before)."""
	workspace = request["workspace"]
	cloud = Cloud.read(inside(workspace, request["cloud"]))
	out_dir = inside(workspace, request["out_dir"])
	os.makedirs(out_dir, exist_ok=True)
	st = cloud.stats()
	x0, y0, _z0, x1, y1, _z1 = st["bbox"]
	n = max(1, int(math.ceil(math.sqrt(len(cloud) / TILE_POINTS))))
	sx, sy = max((x1 - x0) / n, 1e-9), max((y1 - y0) / n, 1e-9)
	ti = np.minimum(((cloud.xyz[:, 0] - x0) / sx).astype(int), n - 1)
	tj = np.minimum(((cloud.xyz[:, 1] - y0) / sy).astype(int), n - 1)
	rng = np.random.default_rng(7)
	order = np.lexsort((rng.random(len(cloud)), tj, ti))
	key = ti[order] * n + tj[order]
	bounds = np.searchsorted(key, np.arange(n * n + 1))
	index = {"version": 1, "kind": "point_cloud", "grid": [n, n], "bbox": st["bbox"], "count": st["count"], "tiles": []}
	for i in range(n):
		for j in range(n):
			sel = order[bounds[i * n + j] : bounds[i * n + j + 1]]
			if not len(sel):
				continue
			p = cloud.xyz[sel]
			origin = [float(round(v)) for v in p.mean(axis=0)]
			levels = []
			for level in range(3):
				count = len(sel) if level == 0 else len(sel) // (LEVEL_RATIO**level)
				if level and count < 2000:
					break
				s = sel[:count]
				name = f"t{i}_{j}_{level}.cadpts"
				write_points(os.path.join(out_dir, name), np.array(origin), cloud.xyz[s], cloud.cls[s], cloud.intensity[s], None if cloud.rgb is None else cloud.rgb[s], None if cloud.deviation is None else cloud.deviation[s])
				levels.append({"file": name, "points": int(count)})
			bx = [float(v) for v in (p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max(), p[:, 2].min(), p[:, 2].max())]
			index["tiles"].append({"i": i, "j": j, "bbox": bx, "levels": levels})
		emit(progress=int(100 * (i + 1) / n))
	with open(os.path.join(out_dir, "index.json"), "w") as f:
		json.dump(index, f)
	return {"index": os.path.join(out_dir, "index.json"), "tiles": len(index["tiles"])}


# ---------------------------------------------------------------------------
# surface and comparison
# ---------------------------------------------------------------------------


def surface(request, log):
	"""A TIN from the cloud's points of the chosen classes (all when none is chosen), thinned to the lowest point
	per `thin` cell, long outer edges trimmed at `max_edge`."""
	workspace = request["workspace"]
	cloud = Cloud.read(inside(workspace, request["cloud"]))
	classes = request.get("classes")
	if classes:
		cloud = cloud.subset(np.isin(cloud.cls, [int(c) for c in classes]))
	if len(cloud) < 3:
		raise ValueError("Fewer than three points of the chosen classes.")
	thin = float(request.get("thin") or 0)
	if thin > 0:
		key = np.floor(cloud.xyz[:, :2] / thin).astype(np.int64)
		order = np.lexsort((cloud.xyz[:, 2], key[:, 1], key[:, 0]))
		k = key[order]
		first = np.r_[True, (np.diff(k, axis=0) != 0).any(axis=1)]
		cloud = cloud.subset(order[first])
	points = output(workspace, "surface_points.npy")
	np.save(points, cloud.xyz)
	data = os.path.join(workspace, "inputs", "cloud_surface.json")
	os.makedirs(os.path.dirname(data), exist_ok=True)
	with open(data, "w") as f:
		json.dump({"points_file": points, "max_edge": request.get("max_edge") or None}, f)
	built = sw.build({"workspace": workspace, "input": data, "name": request.get("name") or "cloud_surface"}, log)
	built["points"] = int(len(cloud))
	return built


def compare(request, log):
	"""Every point's vertical distance to a surface (+ above, − below): statistics, a histogram and the cloud with a
	"deviation" dimension (NaN off the surface)."""
	workspace = request["workspace"]
	cloud = Cloud.read(inside(workspace, request["cloud"]))
	tin = Tin.read(inside(workspace, request["surface"]))
	zs = tin.z_at(cloud.xyz[:, 0], cloud.xyz[:, 1])
	dev = (cloud.xyz[:, 2] - zs).astype(np.float32)
	cloud.deviation = dev
	d = dev[np.isfinite(dev)].astype(np.float64)
	tol = float(request.get("tolerance") or 0.1)
	stats = {"compared": int(len(d)), "off_surface": int(len(dev) - len(d)), "tolerance": tol}
	if len(d):
		stats.update(
			{
				"mean": float(d.mean()),
				"std": float(d.std()),
				"rmse": float(np.sqrt((d * d).mean())),
				"min": float(d.min()),
				"max": float(d.max()),
				"p05": float(np.percentile(d, 5)),
				"p50": float(np.percentile(d, 50)),
				"p95": float(np.percentile(d, 95)),
				"within": float((np.abs(d) <= tol).mean() * 100),
				"above": float((d > tol).mean() * 100),
				"below": float((d < -tol).mean() * 100),
			}
		)
		# centred on zero, as wide as the tolerance and the bulk of the points call for; the tails are counted in the
		# end bins (beyond ± range)
		reach = max(4 * tol, 2 * max(abs(stats["p05"]), abs(stats["p95"])), 1e-6)
		edges = np.linspace(-reach, reach, 41)
		hist, edges = np.histogram(np.clip(d, -reach, reach), bins=edges)
		stats["histogram"] = {"edges": edges.tolist(), "counts": hist.tolist(), "below": int((d < -reach).sum()), "beyond": int((d > reach).sum()), "range": reach}
	return finish(cloud, workspace, request.get("name") or "compared", {"comparison": stats})


COMMANDS = {"import": import_cloud, "inspect_e57": inspect_e57, "edit": edit, "tiles": tiles, "surface": surface, "compare": compare, "export": export_cloud}


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
