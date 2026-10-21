"""CAD worker: DXF exchange (ezdxf) for the CAD Engineering Workstation.

Runs under the isolated CAD worker Python (``/opt/cad-worker/venv/bin/python -I dxf_worker.py``), never
inside the bench, with the same rules as geometry_worker.py: bounded JSON request on stdin, files only
inside the job workspace, JSON lines out (``progress`` / ``log`` / ``result`` / ``error``). DXF files are
parsed as data only (ezdxf's recover mode); nothing embedded in them is executed.

Commands
--------
``read``   convert a DXF's model space (and the blocks it uses) into the CAD Engineering entity format
           (see services/cad_geometry.py). With ``out`` it writes the entities as JSON lines; it always
           returns the validation report (version, units, layers, counts, unsupported/invalid/duplicate
           entities, extents, elevation range). Options: unit scale, axis mapping, reprojection.
``write``  write layers, blocks and entities to a new DXF (R12 … R2018), solids as MESH from their display
           meshes (approximations, reported).

Coordinate handling: DXF object coordinate systems (OCS, e.g. mirrored arcs with extrusion 0,0,-1) are
resolved to world coordinates; arcs keep their exact definition. A unit scale and the axis mappings are
similarities and keep every curve exact. A reprojection (``source_crs`` → ``target_crs``, pyproj) is not:
curved entities are then densified into polylines and blocks are exploded — both are reported.

This file uses ezdxf (MIT) and pyproj (MIT).
"""

import hashlib
import json
import math
import os
import resource
import struct
import sys

TAU = 2 * math.pi
INSUNITS = {
	0: ("Unitless", None),
	1: ("Inches", 0.0254),
	2: ("Feet", 0.3048),
	4: ("Millimetres", 0.001),
	5: ("Centimetres", 0.01),
	6: ("Metres", 1.0),
	7: ("Kilometres", 1000.0),
	10: ("Yards", 0.9144),
	21: ("US Survey Feet", 1200.0 / 3937.0),
}
UNIT_CODES = {"Metres": 6, "Millimetres": 4, "Kilometres": 7, "Feet": 2, "US Survey Feet": 21}
LINETYPES_IN = {
	"CONTINUOUS": "Continuous",
	"DASHED": "Dashed",
	"DASHED2": "Dashed",
	"DASHEDX2": "Dashed",
	"DOT": "Dotted",
	"DOT2": "Dotted",
	"DOTX2": "Dotted",
	"DASHDOT": "Dash Dot",
	"DASHDOT2": "Dash Dot",
	"CENTER": "Centre",
	"CENTER2": "Centre",
	"CENTERX2": "Centre",
	"HIDDEN": "Hidden",
	"HIDDEN2": "Hidden",
	"PHANTOM": "Phantom",
	"PHANTOM2": "Phantom",
}
LINETYPES_OUT = {"Continuous": "CONTINUOUS", "Dashed": "DASHED", "Dotted": "DOT", "Dash Dot": "DASHDOT", "Centre": "CENTER", "Hidden": "HIDDEN", "Phantom": "PHANTOM"}
LINEWEIGHTS = [0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60, 70, 80, 90, 100, 106, 120, 140, 158, 200, 211]
PATTERNS_IN = {"ANSI31": "ANSI31", "ANSI37": "ANSI37", "LINE": "LINES", "NET": "GRID", "DOTS": "DOTS"}
PATTERNS_OUT = {"ANSI31": "ANSI31", "ANSI37": "ANSI37", "LINES": "LINE", "GRID": "NET", "DOTS": "DOTS"}
UNSUPPORTED = {
	"3DSOLID": "ACIS solids cannot be read (Phase 3: meshes and solids)",
	"BODY": "ACIS bodies cannot be read",
	"REGION": "ACIS regions cannot be read",
	"SURFACE": "ACIS surfaces cannot be read",
	"MESH": "meshes arrive with Phase 3",
	"3DFACE": "3D faces arrive with Phase 3 (surfaces)",
	"IMAGE": "raster images are not imported",
	"WIPEOUT": "wipeouts are not imported",
	"OLE2FRAME": "embedded OLE objects are never imported",
	"VIEWPORT": "paper-space viewports are not imported",
	"POINTCLOUD": "point clouds arrive with Phase 6",
}
MAX_EXPLODE_DEPTH = 8
DUPLICATE_DECIMALS = 6


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
# small vector helpers
# ---------------------------------------------------------------------------


def v3(p):
	return [float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0]


def plane_basis(n):
	"""OCCT gp_Ax2 X/Y directions (same as services/cad_geometry.plane_basis)."""
	a, b, c = n
	aa, ab, ac = abs(a), abs(b), abs(c)
	if ab <= aa and ab <= ac:
		x = [-c, 0.0, a] if aa > ac else [c, 0.0, -a]
	elif aa <= ab and aa <= ac:
		x = [0.0, -c, b] if ab > ac else [0.0, c, -b]
	else:
		x = [-b, a, 0.0] if aa > ab else [b, -a, 0.0]
	length = math.sqrt(sum(v * v for v in x))
	x = [v / length for v in x]
	return x, [b * x[2] - c * x[1], c * x[0] - a * x[2], a * x[1] - b * x[0]]


def unit(v):
	length = math.sqrt(sum(c * c for c in v))
	return [c / length for c in v] if length > 1e-15 else None


def is_up(normal, tolerance=1e-9):
	return abs(normal[0]) < tolerance and abs(normal[1]) < tolerance and normal[2] > 0


def is_down(normal, tolerance=1e-9):
	return abs(normal[0]) < tolerance and abs(normal[1]) < tolerance and normal[2] < 0


def angle_in_plane(center, p, normal):
	ux, uy = plane_basis(normal)
	d = [p[k] - center[k] for k in range(3)]
	return math.atan2(sum(d[k] * uy[k] for k in range(3)), sum(d[k] * ux[k] for k in range(3)))


def bulge_from_arc(p0, p1, center, ccw):
	"""DXF bulge of an arc from p0 to p1 about center (sweep measured in the travel direction)."""
	a0 = math.atan2(p0[1] - center[1], p0[0] - center[0])
	a1 = math.atan2(p1[1] - center[1], p1[0] - center[0])
	sweep = (a1 - a0) % TAU if ccw else -((a0 - a1) % TAU)
	if abs(sweep) < 1e-15:
		sweep = TAU if ccw else -TAU
	return math.tan(sweep / 4)


# ---------------------------------------------------------------------------
# coordinate mapping (units, axes, CRS)
# ---------------------------------------------------------------------------


class Mapper:
	"""Source WCS → model coordinates: scale, then axis mapping, then (optionally) reprojection."""

	def __init__(self, options):
		self.scale = float(options.get("unit_scale") or 1.0)
		self.axes = options.get("axis_mapping") or "As in file"
		self.transformer = None
		if options.get("source_crs") and options.get("target_crs") and options["source_crs"] != options["target_crs"]:
			from pyproj import Transformer

			self.transformer = Transformer.from_crs(options["source_crs"], options["target_crs"], always_xy=True)
		self.mirrored = self.axes == "Swap X and Y"
		self.exact = self.transformer is None

	def point(self, p):
		x, y, z = (v * self.scale for v in v3(p))
		if self.axes == "Negate X and Y":
			x, y = -x, -y
		elif self.axes == "Swap X and Y":
			x, y = y, x
		if self.transformer:
			x, y, z = self.transformer.transform(x, y, z)
		return [x, y, z]

	def vector(self, v):
		"""A direction/size vector under the similarity part (no translation, no reprojection)."""
		x, y, z = (c * self.scale for c in v3(v))
		if self.axes == "Negate X and Y":
			return [-x, -y, z]
		if self.axes == "Swap X and Y":
			return [y, x, z]
		return [x, y, z]

	def angle(self, a):
		"""A plan angle (counter-clockwise from +X) after the axis mapping."""
		if self.axes == "Negate X and Y":
			return a + math.pi
		if self.axes == "Swap X and Y":
			return math.pi / 2 - a
		return a

	def rotation_at(self, p, a):
		"""Plan rotation at p including the local grid rotation of a reprojection."""
		if not self.transformer:
			return self.angle(a)
		step = 1.0
		q = self.point([p[0] + step * math.cos(a), p[1] + step * math.sin(a), p[2] if len(p) > 2 else 0.0])
		r = self.point(p)
		return math.atan2(q[1] - r[1], q[0] - r[0])

	def length(self, value, p=None):
		if not self.transformer or p is None:
			return value * self.scale
		a, b = self.point(p), self.point([p[0] + value, p[1], p[2] if len(p) > 2 else 0.0])
		return math.dist(a[:2], b[:2])


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


class Reader:
	def __init__(self, doc, mapper, report):
		self.doc = doc
		self.map = mapper
		self.report = report
		self.blocks_used = set()
		self.block_defs = {}

	def warn(self, message):
		warnings = self.report["warnings"]
		if message not in warnings and len(warnings) < 200:
			warnings.append(message)

	def count(self, key, dxftype):
		bucket = self.report[key]
		bucket[dxftype] = bucket.get(dxftype, 0) + 1

	# -- styles ---------------------------------------------------------------

	def color(self, e):
		from ezdxf.colors import aci2rgb

		from ezdxf.colors import int2rgb

		if e.dxf.hasattr("true_color"):
			return "#%02x%02x%02x" % tuple(int2rgb(e.dxf.true_color))
		aci = e.dxf.get("color", 256)
		if aci in (0, 256) or aci is None:
			return None  # by block / by layer
		r, g, b = aci2rgb(aci)
		return "#%02x%02x%02x" % (r, g, b)

	def linetype(self, name):
		name = (name or "").upper()
		if name in ("", "BYLAYER", "BYBLOCK"):
			return None
		return LINETYPES_IN.get(name, "Dashed" if name != "CONTINUOUS" else "Continuous")

	def style(self, e):
		return {
			"layer": e.dxf.get("layer", "0"),
			"color": self.color(e),
			"line_type": self.linetype(e.dxf.get("linetype")),
			"source_id": e.dxf.get("handle"),
		}

	# -- conversion -----------------------------------------------------------

	def convert(self, e, depth=0, mapper=None, in_block=False):
		"""[(entity_type, geometry, style, metadata)] for one DXF entity (several if exploded)."""
		m = mapper or self.map
		t = e.dxftype()
		meta = {"dxf_type": t}
		try:
			if t == "POINT":
				return [("Point", {"p": m.point(e.dxf.location)}, meta)]
			if t == "LINE":
				return [("Line", {"p0": m.point(e.dxf.start), "p1": m.point(e.dxf.end)}, meta)]
			if t in ("CIRCLE", "ARC"):
				return self.circle_arc(e, m, meta)
			if t == "ELLIPSE":
				return self.ellipse(e, m, meta)
			if t == "SPLINE":
				return self.spline(e, m, meta)
			if t == "LWPOLYLINE":
				points = [(p[0], p[1], p[2]) for p in e.get_points("xyb")]
				return self.polyline_2d(e, points, float(e.dxf.get("elevation", 0.0)), bool(e.closed), m, meta)
			if t == "POLYLINE":
				if e.is_2d_polyline:
					points = [(v.dxf.location[0], v.dxf.location[1], v.dxf.get("bulge", 0.0)) for v in e.vertices]
					return self.polyline_2d(e, points, float(e.dxf.elevation[2]) if e.dxf.hasattr("elevation") else 0.0, bool(e.is_closed), m, meta)
				if e.is_3d_polyline:
					points = [m.point(v.dxf.location) for v in e.vertices]
					return self.polyline_points(points, bool(e.is_closed), meta)
				return self.unsupported(t, "polyface and polygon meshes arrive with Phase 3")
			if t in ("XLINE", "RAY"):
				return [("Construction Line", {"point": m.point(e.dxf.start), "direction": unit(m.vector(e.dxf.unit_vector)), "ray": t == "RAY"}, meta)]
			if t == "TEXT":
				return self.text(e, m, meta)
			if t == "MTEXT":
				return self.mtext(e, m, meta)
			if t == "INSERT":
				return self.insert(e, m, meta, depth, in_block)
			if t == "DIMENSION":
				return self.dimension(e, m, meta, depth)
			if t == "HATCH":
				return self.hatch(e, m, meta)
			if t in ("SOLID", "TRACE"):
				ocs = e.ocs()
				corners = [ocs.to_wcs(e.dxf.get(f"vtx{i}")) for i in (0, 1, 3, 2) if e.dxf.hasattr(f"vtx{i}")]
				loop = dedupe([m.point(c) for c in corners])
				if len(loop) < 3:
					return self.invalid(e, "degenerate 2D solid")
				return [("Hatch", {"loops": [{"points": loop}], "pattern": "SOLID", "scale": 1.0, "angle": 0.0}, {**meta, "converted_from": t})]
			if t in UNSUPPORTED:
				return self.unsupported(t, UNSUPPORTED[t])
			if depth < MAX_EXPLODE_DEPTH and hasattr(e, "virtual_entities"):
				return self.explode(e, m, depth, f"{t} exploded into simple entities")
			return self.unsupported(t, "not supported")
		except Exception as ex:  # one broken entity never stops the import
			return self.invalid(e, f"{type(ex).__name__}: {ex}")

	def sane(self, e, rows):
		"""Drop (and report) degenerate results: zero-length lines, coincident circle/arc data, empty dimensions."""
		out = []
		for entity_type, g, meta in rows:
			problem = None
			if entity_type == "Line" and math.dist(g["p0"], g["p1"]) < 1e-9:
				problem = "zero-length line"
			elif entity_type in ("Circle", "Arc") and not g["radius"] > 0:
				problem = "zero radius"
			elif entity_type == "Arc" and abs(g["end_angle"] - g["start_angle"]) < 1e-12:
				problem = "zero-sweep arc"
			elif entity_type == "Construction Line" and not g.get("direction"):
				problem = "construction line without direction"
			if problem:
				self.invalid(e, problem)
			else:
				out.append((entity_type, g, meta))
		return out

	def unsupported(self, t, why):
		self.count("unsupported", t)
		self.warn(f"{t}: {why}.")
		return []

	def invalid(self, e, message):
		self.report["invalid_count"] += 1
		if len(self.report["invalid"]) < 200:
			self.report["invalid"].append({"handle": e.dxf.get("handle"), "type": e.dxftype(), "message": message})
		return []

	def explode(self, e, m, depth, why):
		self.count("exploded", e.dxftype())
		self.warn(why)
		out = []
		for child in e.virtual_entities():
			for row in self.convert(child, depth + 1, m):
				row[2]["exploded_from"] = e.dxftype()
				row[2].setdefault("parent_handle", e.dxf.get("handle"))
				out.append(row)
		return out

	def densify(self, e, m, meta, why):
		"""A curved entity as a polyline (reprojection or non-planar OCS): reported, not exact."""
		from ezdxf import path as ezpath

		from ezdxf.math import BoundingBox

		p = ezpath.make_path(e)
		size = BoundingBox(p.control_vertices()).size.magnitude if p.control_vertices() else 1.0
		points = [m.point(v) for v in p.flattening(max(size * 1e-5, 1e-6))]
		self.count("densified", e.dxftype())
		self.warn(why)
		closed = len(points) > 2 and math.dist(points[0], points[-1]) < 1e-9
		return self.polyline_points(points[:-1] if closed else points, closed, {**meta, "densified": True})

	def polyline_points(self, points, closed, meta, bulges=None):
		"""A Polyline without zero-length segments. bulges[i] belongs to the segment from vertex i to i+1."""
		pts, bl = [], []
		for i, p in enumerate(points):
			b = bulges[i] if bulges and i < len(bulges) else 0.0
			if pts and math.dist(pts[-1], p) < 1e-9:
				bl[-1] = b  # the zero-length segment goes; the kept vertex takes this vertex's outgoing segment
				continue
			pts.append(p)
			bl.append(b)
		if closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9:
			pts.pop()  # an explicit closing vertex: its (zero-length) closing segment goes
			bl.pop()
		if len(pts) < 2:
			self.report["invalid_count"] += 1
			return []
		# a closed two-vertex polyline is a real shape only with an arc segment
		g = {"points": pts, "closed": bool(closed) and (len(pts) > 2 or any(bl[:2]))}
		segments = len(pts) if g["closed"] else len(pts) - 1
		bl = bl[:segments]
		if any(bl):
			g["bulges"] = bl
		if len(points) != len(pts):
			meta["removed_duplicate_vertices"] = len(points) - len(pts)
		return [("Polyline", g, meta)]

	def polyline_2d(self, e, points, elevation, closed, m, meta):
		normal = v3(e.dxf.get("extrusion", (0, 0, 1)))
		ocs = e.ocs()
		has_bulge = any(abs(b) > 1e-15 for _x, _y, b in points)
		if not (is_up(normal) or is_down(normal)) or (has_bulge and not m.exact):
			return self.densify(e, m, meta, "Curved polylines were densified (reprojection or tilted OCS).")
		wcs = [m.point(ocs.to_wcs((x, y, elevation))) for x, y, _b in points]
		sign = (1 if is_up(normal) else -1) * (-1 if m.mirrored else 1)
		bulges = [sign * b for _x, _y, b in points] if has_bulge else None
		return self.polyline_points(wcs, closed, meta, bulges)

	def circle_arc(self, e, m, meta):
		normal = unit(v3(e.dxf.get("extrusion", (0, 0, 1))))
		ocs = e.ocs()
		center_ocs = e.dxf.center
		r = float(e.dxf.radius)
		if r <= 0:
			return self.invalid(e, "radius is zero")
		if not m.exact:
			return self.densify(e, m, meta, "Circles and arcs were densified (reprojection).")
		center = m.point(ocs.to_wcs(center_ocs))
		n = m.vector(normal)
		n = unit([-c for c in n] if m.mirrored else n)  # a mirror reverses the turning direction
		if is_down(n):  # turning clockwise about +Z = counter-clockwise about -Z: store everything about +Z
			n = [0.0, 0.0, 1.0]
			flipped = True
		else:
			flipped = False
		if e.dxftype() == "CIRCLE":
			return [("Circle", {"center": center, "radius": r * m.scale, "normal": n}, meta)]
		a0, a1 = math.radians(e.dxf.start_angle), math.radians(e.dxf.end_angle)
		start = m.point(ocs.to_wcs((center_ocs[0] + r * math.cos(a0), center_ocs[1] + r * math.sin(a0), center_ocs[2])))
		end = m.point(ocs.to_wcs((center_ocs[0] + r * math.cos(a1), center_ocs[1] + r * math.sin(a1), center_ocs[2])))
		if m.mirrored != flipped:  # the turning direction reversed once: the arc now runs from end to start
			start, end = end, start
		return [("Arc", {"center": center, "radius": r * m.scale, "start_angle": angle_in_plane(center, start, n), "end_angle": angle_in_plane(center, end, n), "normal": n}, meta)]

	def ellipse(self, e, m, meta):
		if not m.exact:
			return self.densify(e, m, meta, "Ellipses were densified (reprojection).")
		normal = unit(m.vector(e.dxf.get("extrusion", (0, 0, 1))))
		t0, t1 = float(e.dxf.start_param), float(e.dxf.end_param)
		if m.mirrored:
			normal = [-c for c in normal]
			t0, t1 = -t1, -t0
		if is_down(normal):  # the same ellipse about +Z: the minor axis flips, so the parameters change sign
			normal, t0, t1 = [0.0, 0.0, 1.0], -t1, -t0
		return [("Ellipse", {"center": m.point(e.dxf.center), "major_axis": m.vector(e.dxf.major_axis), "ratio": float(e.dxf.ratio), "start_param": t0, "end_param": t1, "normal": normal}, meta)]

	def spline(self, e, m, meta):
		tool = e.construction_tool()
		control = [m.point(p) for p in tool.control_points]  # B-splines are affine-invariant: exact under similarity
		if not m.exact:
			self.warn("Splines were reprojected through their control points (approximation).")
		g = {"degree": int(tool.degree), "control_points": control, "knots": [float(k) for k in tool.knots()], "closed": bool(e.closed)}
		if tool.is_rational:
			g["weights"] = [float(w) for w in tool.weights()]
		if e.fit_point_count():
			g["fit_points"] = [m.point(p) for p in e.fit_points]
		return [("Spline", g, meta)]

	def text(self, e, m, meta):
		halign, valign = int(e.dxf.get("halign", 0)), int(e.dxf.get("valign", 0))
		h = {0: "left", 1: "center", 2: "right", 3: "left", 4: "center", 5: "left"}.get(halign, "left")
		v = {0: "baseline", 1: "bottom", 2: "middle", 3: "top"}.get(valign, "baseline")
		if halign == 4:
			v = "middle"
		if halign in (3, 5):
			self.warn("Aligned/fit TEXT is imported left-aligned at its first point.")
		ocs = e.ocs()
		anchor = e.dxf.insert if (halign, valign) == (0, 0) or halign in (3, 5) else e.dxf.get("align_point", e.dxf.insert)
		p = ocs.to_wcs(anchor)
		rot = math.radians(e.dxf.get("rotation", 0.0))
		direction = ocs.to_wcs((math.cos(rot), math.sin(rot), 0)) - ocs.to_wcs((0, 0, 0))
		rotation = m.rotation_at(v3(p), math.atan2(direction[1], direction[0]))
		return [("Text", {"position": m.point(p), "text": e.plain_text(), "height": m.length(float(e.dxf.get("height", 1.0)), v3(p)), "rotation": rotation, "width_factor": float(e.dxf.get("width", 1.0)) or 1.0, "halign": h, "valign": v}, meta)]

	def mtext(self, e, m, meta):
		p = v3(e.dxf.insert)
		return [
			(
				"Multiline Text",
				{
					"position": m.point(p),
					"text": e.plain_text(split=False),
					"height": m.length(float(e.dxf.get("char_height", 1.0)), p),
					"rotation": m.rotation_at(p, math.radians(e.get_rotation())),
					"width": m.length(float(e.dxf.get("width", 0.0)), p),
					"attachment": int(e.dxf.get("attachment_point", 1)),
					"line_spacing": float(e.dxf.get("line_spacing_factor", 1.0)) or 1.0,
				},
				meta,
			)
		]

	def insert(self, e, m, meta, depth, in_block):
		name = e.dxf.name
		matrix = e.matrix44()
		ux, uy, uz = matrix.ux, matrix.uy, matrix.uz
		vertical = abs(uz[0]) < 1e-9 and abs(uz[1]) < 1e-9 and abs(ux[2]) < 1e-9 and abs(uy[2]) < 1e-9
		shear = abs(ux[0] * uy[0] + ux[1] * uy[1]) > 1e-9 * (math.hypot(ux[0], ux[1]) * math.hypot(uy[0], uy[1]) or 1)
		layout = self.doc.blocks.get(name)
		if layout is None:
			return self.invalid(e, f"block {name} does not exist")
		if not m.exact or not vertical or shear or (e.dxf.get("column_count", 1) or 1) > 1 or (e.dxf.get("row_count", 1) or 1) > 1:
			out = self.explode(e, m, depth, "Some block references were exploded (reprojection, tilted, sheared or arrayed inserts).") if depth < MAX_EXPLODE_DEPTH else []
			return out
		# matrix = insert + R(θ)·diag(sx, sy) in the XY plane
		sx = math.hypot(ux[0], ux[1])
		theta = math.atan2(ux[1], ux[0])
		sy = (ux[0] * uy[1] - ux[1] * uy[0]) / sx
		sz = uz[2]
		# matrix44 maps block coordinates to WCS (base point included): the base point lands on the insert point
		insert = matrix.transform(layout.block.dxf.base_point)
		if m.axes == "Negate X and Y":
			theta += math.pi
		elif m.axes == "Swap X and Y":
			theta, sy = math.pi / 2 - theta, -sy
		self.blocks_used.add(name)
		out = [("Block Reference", {"block": name, "insert": m.point(insert), "scale": [sx * m.scale, sy * m.scale, sz * m.scale], "rotation": theta}, meta)]
		if not in_block:
			for attrib in e.attribs:
				if not attrib.is_invisible:
					for row in self.text(attrib, m, {"dxf_type": "ATTRIB", "tag": attrib.dxf.tag, "parent_handle": e.dxf.handle}):
						out.append(row)
		return out

	def dimension(self, e, m, meta, depth):
		kind = e.dimtype & 7
		if not m.exact or kind in (2, 6):
			return self.explode(e, m, depth, "Angular (2-line), ordinate and reprojected dimensions were exploded into lines and text.")
		try:
			override = e.override()
			scale = float(override.get("dimscale", 1.0) or 1.0)
			text_height = float(override.get("dimtxt", 2.5)) * scale
			arrow = float(override.get("dimasz", 2.5)) * scale
		except Exception:
			text_height = arrow = 2.5
		g = {"text_height": text_height * m.scale, "arrow_size": arrow * m.scale}
		d = e.dxf
		if kind == 0:
			g.update(kind="linear", p1=m.point(d.defpoint2), p2=m.point(d.defpoint3), location=m.point(d.defpoint), angle=m.angle(math.radians(d.get("angle", 0.0))))
		elif kind == 1:
			g.update(kind="aligned", p1=m.point(d.defpoint2), p2=m.point(d.defpoint3), location=m.point(d.defpoint))
		elif kind == 3:
			a, b = v3(d.defpoint), v3(d.defpoint4)
			g.update(kind="diameter", center=m.point([(a[k] + b[k]) / 2 for k in range(3)]), p1=m.point(b), p2=m.point(a), location=m.point(d.get("text_midpoint", b)))
		elif kind == 4:
			g.update(kind="radius", center=m.point(d.defpoint), p1=m.point(d.defpoint4), p2=m.point(d.defpoint4), location=m.point(d.get("text_midpoint", d.defpoint4)))
		elif kind == 5:
			p1, p2 = m.point(d.defpoint2), m.point(d.defpoint3)
			if m.mirrored:
				p1, p2 = p2, p1
			g.update(kind="angular", center=m.point(d.defpoint4), p1=p1, p2=p2, location=m.point(d.defpoint))
		text = d.get("text", "")
		if text and text not in ("<>", " "):
			g["text_override"] = text
		try:
			measured = e.get_measurement()
			meta["dxf_measurement"] = float(measured)  # ezdxf gives degrees for angular dimensions
		except Exception:
			pass
		return [("Dimension", g, meta)]

	def hatch(self, e, m, meta):
		from ezdxf.entities.boundary_paths import BoundaryPathType, EdgeType

		normal = v3(e.dxf.get("extrusion", (0, 0, 1)))
		if not (is_up(normal) or is_down(normal)):
			return self.unsupported("HATCH (tilted)", "tilted hatches are not imported")
		ocs = e.ocs()
		elevation = float(e.dxf.elevation[2]) if e.dxf.hasattr("elevation") else 0.0
		flip = (1 if is_up(normal) else -1) * (-1 if m.mirrored else 1)
		loops, densified = [], False
		paths = sorted(e.paths, key=lambda p: 0 if p.path_type_flags & 1 else 1)  # external boundary first
		for path in paths:
			points, bulges = [], []
			if path.type == BoundaryPathType.POLYLINE:
				for x, y, b in path.vertices:
					points.append((x, y))
					bulges.append(b)
			else:
				for edge in path.edges:
					if edge.type == EdgeType.LINE:
						points.append(tuple(edge.start)[:2])
						bulges.append(0.0)
					elif edge.type == EdgeType.ARC:
						a0, a1 = math.radians(edge.start_angle), math.radians(edge.end_angle)
						c, r = edge.center, edge.radius
						if edge.ccw:
							s, t = (c[0] + r * math.cos(a0), c[1] + r * math.sin(a0)), (c[0] + r * math.cos(a1), c[1] + r * math.sin(a1))
						else:  # clockwise arcs store mirrored angles
							s, t = (c[0] + r * math.cos(-a0), c[1] + r * math.sin(-a0)), (c[0] + r * math.cos(-a1), c[1] + r * math.sin(-a1))
						points.append(s)
						bulges.append(bulge_from_arc(s, t, c, edge.ccw))
					else:
						densified = True
						for p in edge_points(edge):
							points.append(p)
							bulges.append(0.0)
			if len(points) < 3 and not (len(points) == 2 and any(bulges)):
				continue
			wcs = [m.point(ocs.to_wcs((x, y, elevation))) for x, y in points]
			row = {"points": wcs}
			if any(bulges) and m.exact:
				row["bulges"] = [flip * b for b in bulges]
			elif any(bulges):
				densified = True
			loops.append(row)
		if not loops:
			return self.invalid(e, "hatch without a usable boundary")
		if densified:
			self.count("densified", "HATCH")
			self.warn("Hatch boundaries with ellipse/spline edges were densified.")
		name = (e.dxf.get("pattern_name") or "").upper()
		pattern = "SOLID" if e.dxf.get("solid_fill", 0) else PATTERNS_IN.get(name, "ANSI31")
		if pattern != "SOLID" and name not in PATTERNS_IN:
			meta["pattern_name"] = name
		g = {"loops": loops, "pattern": pattern, "scale": float(e.dxf.get("pattern_scale", 1.0)) * (m.scale if pattern != "SOLID" else 1.0), "angle": m.angle(math.radians(e.dxf.get("pattern_angle", 0.0))) % math.pi}
		return [("Hatch", g, meta)]


def edge_points(edge):
	"""Points of an ellipse or spline hatch edge (without the end point, which the next edge starts at)."""
	try:
		points = [tuple(p)[:2] for p in edge.construction_tool().flattening(1e-3)]
	except Exception:
		points = [tuple(edge.start_point)[:2]] if hasattr(edge, "start_point") else []
	return points[:-1] if len(points) > 1 else points


def dedupe(points):
	out = []
	for p in points:
		if not out or math.dist(out[-1], p) > 1e-9:
			out.append(p)
	if len(out) > 1 and math.dist(out[0], out[-1]) < 1e-9:
		out.pop()
	return out


def key_points(entity_type, g):
	"""A few points of the geometry for extents and elevation range in the report."""
	if entity_type == "Point":
		return [g["p"]]
	if entity_type == "Line":
		return [g["p0"], g["p1"]]
	if entity_type == "Polyline":
		return g["points"]
	if entity_type in ("Circle", "Arc"):
		c, r = g["center"], g["radius"]
		return [[c[0] - r, c[1] - r, c[2]], [c[0] + r, c[1] + r, c[2]]]
	if entity_type == "Ellipse":
		c, a = g["center"], math.sqrt(sum(v * v for v in g["major_axis"]))
		return [[c[0] - a, c[1] - a, c[2]], [c[0] + a, c[1] + a, c[2]]]
	if entity_type == "Spline":
		return g["control_points"]
	if entity_type in ("Text", "Multiline Text"):
		return [g["position"]]
	if entity_type == "Construction Line":
		return [g["point"]]
	if entity_type == "Dimension":
		return [g["p1"], g["p2"], g["location"]]
	if entity_type == "Hatch":
		return [p for loop in g["loops"] for p in loop["points"]]
	if entity_type == "Block Reference":
		return [g["insert"]]
	return []


def fingerprint(entity_type, g, layer):
	def rounded(value):
		if isinstance(value, float):
			return round(value, DUPLICATE_DECIMALS)
		if isinstance(value, list):
			return [rounded(v) for v in value]
		if isinstance(value, dict):
			return {k: rounded(v) for k, v in value.items()}
		return value

	return hashlib.sha1(json.dumps([entity_type, layer, rounded(g)], sort_keys=True).encode()).hexdigest()


def read(request):
	import ezdxf
	from ezdxf import recover
	from ezdxf.colors import aci2rgb

	workspace = request["workspace"]
	path = inside(workspace, request["input"])
	emit(progress=2, log="Reading DXF…")
	doc, auditor = recover.readfile(path)
	emit(progress=30, log=f"DXF {doc.dxfversion} read.")
	units_code = int(doc.header.get("$INSUNITS", 0) or 0)
	units_name, units_factor = INSUNITS.get(units_code, (f"Code {units_code}", None))
	report = {
		"dxf_version": doc.dxfversion,
		"release": ezdxf.const.acad_release.get(doc.dxfversion, doc.dxfversion),
		"encoding": doc.encoding,
		"units_code": units_code,
		"units_name": units_name,
		"units_to_metres": units_factor,
		"audit": {"errors": len(auditor.errors), "fixes": len(auditor.fixes)},
		"counts": {},
		"converted": {},
		"unsupported": {},
		"exploded": {},
		"densified": {},
		"invalid": [],
		"invalid_count": 0,
		"duplicates": 0,
		"warnings": [],
		"layers": [],
		"blocks": 0,
		"paperspace_entities": sum(len(layout) for layout in doc.layouts if not layout.is_modelspace),
		"geodata": None,
		"extents": None,
		"elevation_range": None,
	}
	if units_factor is None:
		report["warnings"].append("The drawing has no units ($INSUNITS); choose the source units before importing.")
	msp = doc.modelspace()
	try:
		geodata = msp.get_geodata()
		if geodata:
			report["geodata"] = {"coordinate_system": (geodata.coordinate_system_definition or "")[:2000]}
	except Exception:
		pass
	mapper = Mapper(request.get("options") or {})
	reader = Reader(doc, mapper, report)

	layer_counts = {}
	out = open(inside(workspace, request["out"]), "w") if request.get("out") else None
	seen = set()
	box = [math.inf] * 3 + [-math.inf] * 3
	entities = list(msp)
	total = max(len(entities), 1)

	def record(entity_type, g, meta, style):
		nonlocal box
		points = key_points(entity_type, g)
		for p in points:
			for k in range(3):
				box[k] = min(box[k], p[k])
				box[k + 3] = max(box[k + 3], p[k])
		fp = fingerprint(entity_type, g, style["layer"])
		if fp in seen:
			report["duplicates"] += 1
			meta["duplicate"] = True
		seen.add(fp)
		report["converted"][entity_type] = report["converted"].get(entity_type, 0) + 1
		layer_counts[style["layer"]] = layer_counts.get(style["layer"], 0) + 1
		if out:
			out.write(json.dumps({"entity_type": entity_type, "geometry": g, "metadata": meta, **style}) + "\n")

	for n, e in enumerate(entities):
		t = e.dxftype()
		report["counts"][t] = report["counts"].get(t, 0) + 1
		style = reader.style(e)
		for entity_type, g, meta in reader.sane(e, reader.convert(e)):
			child_style = dict(style)
			if meta.get("exploded_from") or meta.get("dxf_type") == "ATTRIB":
				child_style["source_id"] = f"{style['source_id']}.{report['converted'].get(entity_type, 0)}"
			record(entity_type, g, meta, child_style)
		if n % 2000 == 0:
			emit(progress=30 + int(60 * n / total))
	if out:
		out.close()

	# blocks used by the model space (and nested ones), in block coordinates
	blocks = []
	pending = list(reader.blocks_used)
	done = set()
	block_mapper = Mapper({"unit_scale": 1.0})  # block contents stay in block units; the reference scales them
	while pending:
		name = pending.pop()
		if name in done:
			continue
		done.add(name)
		layout = doc.blocks.get(name)
		block_reader = Reader(doc, block_mapper, report)
		contents = []
		for e in layout:
			if e.dxftype() in ("ATTDEF",):
				continue
			style = block_reader.style(e)
			for entity_type, g, meta in block_reader.convert(e, in_block=True):
				contents.append({"entity_type": entity_type, "geometry": g, "layer": style["layer"], "color": style["color"], "line_type": style["line_type"], "metadata": meta})
		for nested in block_reader.blocks_used:
			if nested not in done:
				pending.append(nested)
		blocks.append({"block_name": name, "base_point": v3(layout.block.dxf.base_point), "entities": contents, "source_id": layout.block.dxf.get("handle")})
	report["blocks"] = len(blocks)

	for layer in doc.layers:
		name = layer.dxf.name
		if layer.rgb:
			color = "#%02x%02x%02x" % tuple(layer.rgb)
		else:
			r, g, b = aci2rgb(abs(layer.color) if 0 < abs(layer.color) < 256 else 7)
			color = "#%02x%02x%02x" % (r, g, b)
		weight = layer.dxf.get("lineweight", -3)
		report["layers"].append(
			{
				"layer_name": name,
				"color": color,
				"line_type": reader.linetype(layer.dxf.get("linetype")) or "Continuous",
				"line_weight": weight / 100 if weight and weight > 0 else 0.25,
				"visible": 0 if layer.is_off() or layer.is_frozen() else 1,
				"locked": 1 if layer.is_locked() else 0,
				"entities": layer_counts.get(name, 0),
			}
		)
	for name in layer_counts:
		if name not in {layer["layer_name"] for layer in report["layers"]}:
			report["layers"].append({"layer_name": name, "color": "#e0e0e0", "line_type": "Continuous", "line_weight": 0.25, "visible": 1, "locked": 0, "entities": layer_counts[name]})
	if math.isfinite(box[0]):
		report["extents"] = box
		report["elevation_range"] = [box[2], box[5]]
	report["entity_count"] = sum(report["converted"].values())
	emit(progress=100)
	result = {"report": report}
	if request.get("blocks_out"):
		with open(inside(workspace, request["blocks_out"]), "w") as f:
			json.dump(blocks, f)
	return result


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def closest_lineweight(mm):
	value = int(round((mm or 0.25) * 100))
	return min(LINEWEIGHTS, key=lambda w: abs(w - value))


def rgb(color):
	color = (color or "").lstrip("#")
	if len(color) != 6:
		return None
	return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


class Writer:
	def __init__(self, doc, mapper, report, workspace):
		self.doc = doc
		self.map = mapper
		self.report = report
		self.workspace = workspace

	def attribs(self, row):
		a = {"layer": row.get("layer") or "0"}
		if row.get("line_type"):
			a["linetype"] = LINETYPES_OUT.get(row["line_type"], "CONTINUOUS")
		return a

	def style(self, entity, row):
		color = rgb(row.get("color"))
		if color:
			entity.rgb = color

	def add(self, layout, row):
		from ezdxf.enums import TextEntityAlignment
		from ezdxf.math import OCS

		t, g, m = row["entity_type"], row["geometry"], self.map
		a = self.attribs(row)
		count = self.report["written"]
		count[t] = count.get(t, 0) + 1
		created = []
		if t == "Point":
			created.append(layout.add_point(m.point(g["p"]), dxfattribs=a))
		elif t == "Line":
			created.append(layout.add_line(m.point(g["p0"]), m.point(g["p1"]), dxfattribs=a))
		elif t == "Polyline":
			points = [m.point(p) for p in g["points"]]
			bulges = g.get("bulges") or [0.0] * len(points)
			flat = max(p[2] for p in points) - min(p[2] for p in points) < 1e-9
			if flat:
				sign = -1 if m.mirrored else 1
				data = [(p[0], p[1], sign * (bulges[i] if i < len(bulges) else 0.0)) for i, p in enumerate(points)]
				e = layout.add_lwpolyline(data, format="xyb", close=bool(g.get("closed")), dxfattribs={**a, "elevation": points[0][2]})
			else:
				e = layout.add_polyline3d(points, close=bool(g.get("closed")), dxfattribs=a)
			created.append(e)
		elif t in ("Circle", "Arc"):
			normal = unit(m.vector(g.get("normal") or [0, 0, 1]))
			if m.mirrored:
				normal = [-c for c in normal]
			ocs = OCS(normal)
			center = m.point(g["center"])
			attribs = {**a, "extrusion": normal} if not is_up(normal) else a
			center_ocs = ocs.from_wcs(center)
			if t == "Circle":
				created.append(layout.add_circle(center_ocs, g["radius"] * m.scale, dxfattribs=attribs))
			else:
				ux, uy = plane_basis(g.get("normal") or [0, 0, 1])
				ends = []
				for angle in (g["start_angle"], g["end_angle"]):
					p = [g["center"][k] + g["radius"] * (math.cos(angle) * ux[k] + math.sin(angle) * uy[k]) for k in range(3)]
					q = ocs.from_wcs(m.point(p))
					ends.append(math.degrees(math.atan2(q[1] - center_ocs[1], q[0] - center_ocs[0])))
				if m.mirrored:
					ends.reverse()
				created.append(layout.add_arc(center_ocs, g["radius"] * m.scale, ends[0], ends[1], dxfattribs=attribs))
		elif t == "Ellipse":
			normal = unit(m.vector(g.get("normal") or [0, 0, 1]))
			t0, t1 = g.get("start_param", 0.0), g.get("end_param", TAU)
			if m.mirrored:
				normal, t0, t1 = [-c for c in normal], -t1, -t0
			created.append(layout.add_ellipse(m.point(g["center"]), m.vector(g["major_axis"]), g["ratio"], t0, t1, dxfattribs={**a, "extrusion": normal}))
		elif t == "Spline":
			control = [m.point(p) for p in g["control_points"]]
			if g.get("weights"):
				e = layout.add_rational_spline(control, g["weights"], degree=g["degree"], knots=g["knots"], dxfattribs=a)
			else:
				e = layout.add_open_spline(control, degree=g["degree"], knots=g["knots"], dxfattribs=a)
			if g.get("fit_points"):  # kept for editing; the control points stay authoritative
				e.fit_points = [m.point(p) for p in g["fit_points"]]
			if g.get("closed"):
				e.closed = True
			created.append(e)
		elif t == "Construction Line":
			add = layout.add_ray if g.get("ray") else layout.add_xline
			created.append(add(m.point(g["point"]), m.vector(g["direction"]), dxfattribs=a))
		elif t == "Text":
			align = {
				("left", "baseline"): "LEFT",
				("center", "baseline"): "CENTER",
				("right", "baseline"): "RIGHT",
			}.get((g.get("halign"), g.get("valign")))
			if not align:
				align = f"{(g.get('valign') or 'bottom').upper()}_{(g.get('halign') or 'left').upper()}"
			e = layout.add_text(g["text"], height=g["height"] * m.scale, rotation=math.degrees(m.angle(g.get("rotation", 0.0))), dxfattribs={**a, "width": g.get("width_factor", 1.0)})
			position = m.point(g["position"])
			e.set_placement(position[:2], align=getattr(TextEntityAlignment, align, TextEntityAlignment.LEFT))
			for key in ("insert", "align_point"):  # set_placement is 2D: keep the elevation
				if e.dxf.hasattr(key):
					v = e.dxf.get(key)
					e.dxf.set(key, (v.x, v.y, position[2]))
			created.append(e)
		elif t == "Multiline Text":
			created.append(
				layout.add_mtext(
					g["text"].replace("\n", "\\P"),
					dxfattribs={
						**a,
						"insert": m.point(g["position"]),
						"char_height": g["height"] * m.scale,
						"width": g.get("width", 0.0) * m.scale,
						"attachment_point": g.get("attachment", 1),
						"rotation": math.degrees(m.angle(g.get("rotation", 0.0))),
						"line_spacing_factor": g.get("line_spacing", 1.0),
					},
				)
			)
		elif t == "Dimension":
			created.append(self.dimension(layout, g, a))
		elif t == "Hatch":
			hatch = layout.add_hatch(dxfattribs=a)
			hatch.dxf.elevation = (0, 0, m.point(g["loops"][0]["points"][0])[2])
			sign = -1 if m.mirrored else 1
			for i, loop in enumerate(g["loops"]):
				bulges = loop.get("bulges") or [0.0] * len(loop["points"])
				hatch.paths.add_polyline_path([(*m.point(p)[:2], sign * bulges[k]) for k, p in enumerate(loop["points"])], is_closed=True, flags=1 if i == 0 else 0)
			if g.get("pattern", "SOLID") == "SOLID":
				hatch.set_solid_fill()
			else:
				hatch.set_pattern_fill(PATTERNS_OUT.get(g["pattern"], "ANSI31"), scale=g.get("scale", 1.0) * m.scale, angle=math.degrees(m.angle(g.get("angle", 0.0))))
			created.append(hatch)
		elif t == "Block Reference":
			sx, sy, sz = g["scale"]
			rotation = g["rotation"]
			if m.axes == "Negate X and Y":
				rotation += math.pi
			elif m.axes == "Swap X and Y":
				rotation, sy = math.pi / 2 - rotation, -sy
			created.append(layout.add_blockref(g["block"], m.point(g["insert"]), dxfattribs={**a, "xscale": sx * m.scale, "yscale": sy * m.scale, "zscale": sz * m.scale, "rotation": math.degrees(rotation)}))
		elif t == "Solid":
			created.append(self.mesh(layout, row, a))
		else:
			count[t] -= 1
			self.report["skipped"][t] = self.report["skipped"].get(t, 0) + 1
			return
		for e in created:
			if e is not None:
				self.style(e, row)

	def dimension(self, layout, g, a):
		m = self.map
		override = {"dimtxt": g["text_height"] * m.scale, "dimasz": g["arrow_size"] * m.scale}
		text = g.get("text_override") or "<>"
		p1, p2, location = (m.point(g[k])[:2] for k in ("p1", "p2", "location"))
		kind = g["kind"]
		if kind == "aligned":
			dx, dy = p2[0] - p1[0], p2[1] - p1[1]
			length = math.hypot(dx, dy)
			distance = ((location[0] - p1[0]) * -dy + (location[1] - p1[1]) * dx) / length
			dim = layout.add_aligned_dim(p1=p1, p2=p2, distance=distance, text=text, override=override, dxfattribs=a)
		elif kind == "linear":
			dim = layout.add_linear_dim(base=location, p1=p1, p2=p2, angle=math.degrees(m.angle(g.get("angle", 0.0))), text=text, override=override, dxfattribs=a)
		elif kind == "radius":
			dim = layout.add_radius_dim(center=m.point(g["center"])[:2], mpoint=p1, text=text, override=override, dxfattribs=a)
		elif kind == "diameter":
			dim = layout.add_diameter_dim(center=m.point(g["center"])[:2], mpoint=p1, text=text, override=override, dxfattribs=a)
		else:
			if m.mirrored:
				p1, p2 = p2, p1
			dim = layout.add_angular_dim_3p(base=location, center=m.point(g["center"])[:2], p1=p1, p2=p2, text=text, override=override, dxfattribs=a)
		dim.render()
		return dim.dimension

	def mesh(self, layout, row, a):
		path = row.get("mesh")
		if not path:
			self.report["skipped"]["Solid"] = self.report["skipped"].get("Solid", 0) + 1
			self.report["written"]["Solid"] -= 1
			return None
		with open(inside(self.workspace, path), "rb") as f:
			data = f.read()
		nv, ni, _ne = struct.unpack_from("<III", data, 8)
		origin = struct.unpack_from("<3d", data, 20)
		positions = struct.unpack_from(f"<{nv * 3}f", data, 44)
		index = struct.unpack_from(f"<{ni}I", data, 44 + nv * 12)
		mesh = layout.add_mesh(dxfattribs=a)
		with mesh.edit_data() as edit:
			edit.vertices = [self.map.point([positions[i] + origin[0], positions[i + 1] + origin[1], positions[i + 2] + origin[2]]) for i in range(0, len(positions), 3)]
			edit.faces = [index[i : i + 3] for i in range(0, len(index), 3)]
		self.report["approximated"] = self.report.get("approximated", 0) + 1
		return mesh


def write(request):
	import ezdxf

	workspace = request["workspace"]
	options = request.get("options") or {}
	version = options.get("version") or "R2018"
	doc = ezdxf.new(version, setup=True)
	units = options.get("units") or "Metres"
	doc.units = UNIT_CODES.get(units, 6)
	doc.header["$INSUNITS"] = UNIT_CODES.get(units, 6)
	mapper = Mapper({"axis_mapping": options.get("axis_mapping"), "unit_scale": options.get("unit_scale") or 1.0})
	report = {"written": {}, "skipped": {}, "version": version, "units": units}
	with open(inside(workspace, request["input"])) as f:
		content = json.load(f)
	for layer in content.get("layers") or []:
		name = layer["layer_name"]
		entry = doc.layers.get(name) if doc.layers.has_entry(name) else doc.layers.add(name)  # "0", "Defpoints" exist
		color = rgb(layer.get("color"))
		if color:
			entry.rgb = color
		entry.dxf.linetype = LINETYPES_OUT.get(layer.get("line_type") or "Continuous", "CONTINUOUS")
		entry.dxf.lineweight = closest_lineweight(layer.get("line_weight"))
		if not layer.get("visible", 1):
			entry.off()
		if layer.get("locked"):
			entry.lock()
	writer = Writer(doc, mapper, report, workspace)
	block_writer = Writer(doc, Mapper({}), report, workspace)  # block contents stay in block units
	for block in content.get("blocks") or []:
		layout = doc.blocks.new(name=block["block_name"], base_point=block.get("base_point") or (0, 0, 0))
		for row in block.get("entities") or []:
			block_writer.add(layout, row)
	msp = doc.modelspace()
	entities = content.get("entities") or []
	for n, row in enumerate(entities):
		writer.add(msp, row)
		if n % 2000 == 0:
			emit(progress=int(90 * n / max(len(entities), 1)))
	out = inside(workspace, request["out"])
	doc.saveas(out)
	emit(progress=100)
	report["bytes"] = os.path.getsize(out)
	return {"report": report, "out": out}


COMMANDS = {"read": read, "write": write}


def main():
	try:
		request = json.loads(sys.stdin.read() or "{}")
		command = COMMANDS.get(request.get("command"))
		if not command:
			raise ValueError(f"Unknown command {request.get('command')!r}.")
		apply_limits(request)
		emit(result=command(request))
	except MemoryError:
		emit(error="The CAD worker ran out of its memory limit (CAD Settings → Worker Memory Limit).")
	except Exception as e:
		emit(error=f"{type(e).__name__}: {e}")


if __name__ == "__main__":
	main()
