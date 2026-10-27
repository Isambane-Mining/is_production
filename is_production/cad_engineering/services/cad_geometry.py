"""Shape of the exact geometry stored on CAD Entity, checked in the bench (no OCCT here).

Every entity keeps its definition in model coordinates as plain JSON numbers (Python floats are IEEE
doubles and JSON round-trips them exactly). Angles are radians, counter-clockwise about ``normal``;
the plane's X direction follows OCCT's gp_Ax2 rule (``plane_basis``), the same in the browser and the worker.

    Point              {"p": [x, y, z]}
    Line               {"p0": [x, y, z], "p1": [x, y, z]}
    Polyline           {"points": [[x, y, z], ...], "closed": bool, "bulges": [b, ...]}
                       bulges (optional) per segment, DXF convention: tan(sweep / 4), positive = counter-clockwise
                       seen from +Z; a segment with a bulge must be horizontal (both ends at the same Z).
    Circle             {"center", "radius", "normal"}
    Arc                {"center", "radius", "start_angle", "end_angle", "normal"}
    Ellipse            {"center", "major_axis": [dx, dy, dz], "ratio", "start_param", "end_param", "normal"}
    Spline             {"degree", "control_points": [...], "knots": [...], "weights": [...]?, "fit_points": [...]?, "closed"}
    Construction Line  {"point", "direction": unit vector, "ray": bool}          (infinite, or half-infinite)
    Text               {"position", "text", "height", "rotation", "width_factor", "halign", "valign"}
    Multiline Text     {"position", "text" (lines separated by \\n), "height", "rotation", "width", "attachment" 1-9, "line_spacing"}
    Dimension          {"kind": aligned|linear|angular|radius|diameter, "p1", "p2", "location", "center"?, "angle"?,
                        "text_override"?, "text_height", "arrow_size"}
    Hatch              {"loops": [{"points", "bulges"?}], "pattern": SOLID|ANSI31|ANSI37|LINES|GRID, "scale", "angle"}
    Block Reference    {"block": name, "insert", "scale": [sx, sy, sz], "rotation"}
    Solid              {"source": {...}}  + geometry_ref (OCCT B-Rep file)

The bounding box worked out here only indexes the entity for view queries. Lengths, areas, volumes and
topology checks come from the CAD worker (Open CASCADE).
"""

import math

import frappe
from frappe import _

MAX_POINTS = 200000
TEXT_LIMIT = 20000
BLOB_TYPES = {"Solid", "Mesh", "Surface", "Point Cloud"}
SUPPORTED_TYPES = {
	"Point",
	"Line",
	"Polyline",
	"Circle",
	"Arc",
	"Ellipse",
	"Spline",
	"Construction Line",
	"Text",
	"Multiline Text",
	"Dimension",
	"Hatch",
	"Block Reference",
	"Solid",
	"Surface",
	"Mesh",
	"Point Cloud",
}
SURFACE_TYPES = {"Survey Surface", "Terrain Surface", "Geological Surface", "Coal Seam", "Pit Surface", "Design Surface", "As-Built Surface"}
BLOB_FORMATS = {"Solid": ("brep", "OCCT B-Rep"), "Surface": ("tin", "TIN"), "Mesh": ("tin", "TIN"), "Point Cloud": ("laz", "LAZ")}
HATCH_PATTERNS = {"SOLID", "ANSI31", "ANSI37", "LINES", "GRID", "DOTS"}
DIMENSION_KINDS = {"aligned", "linear", "angular", "radius", "diameter"}
HALIGN = {"left", "center", "right"}
VALIGN = {"baseline", "bottom", "middle", "top"}
TAU = 2 * math.pi


def coordinate(value, label):
	if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
		frappe.throw(_("{0} must be [x, y] or [x, y, z].").format(label))
	out = []
	for v in value:
		if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
			frappe.throw(_("{0} has a coordinate that is not a finite number.").format(label))
		out.append(float(v))
	if len(out) == 2:
		out.append(0.0)
	return out


def number(value, label, positive=False, minimum=None, maximum=None):
	if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
		frappe.throw(_("{0} must be a number.").format(label))
	if positive and value <= 0:
		frappe.throw(_("{0} must be greater than zero.").format(label))
	if minimum is not None and value < minimum or maximum is not None and value > maximum:
		frappe.throw(_("{0} is out of range.").format(label))
	return float(value)


def unit_vector(value, label):
	vec = coordinate(value if value is not None else [0, 0, 1], label)
	length = math.sqrt(sum(v * v for v in vec))
	if length < 1e-12:
		frappe.throw(_("{0} has zero length.").format(label))
	return [v / length for v in vec]


def text_value(value, label):
	text = str(value if value is not None else "")
	if len(text) > TEXT_LIMIT:
		frappe.throw(_("{0} may be at most {1} characters.").format(label, TEXT_LIMIT))
	return text


def points_list(value, label, minimum):
	if not isinstance(value, list) or len(value) < minimum:
		frappe.throw(_("{0} needs at least {1} points.").format(label, minimum))
	if len(value) > MAX_POINTS:
		frappe.throw(_("{0} may have at most {1} points.").format(label, MAX_POINTS))
	return [coordinate(p, _("{0} point {1}").format(label, i + 1)) for i, p in enumerate(value)]


def bulge_list(value, points, closed, label):
	"""Per-segment bulges, or None when every segment is straight."""
	segments = len(points) if closed else len(points) - 1
	if value in (None, []):
		return None
	if not isinstance(value, list) or len(value) != segments:
		frappe.throw(_("{0} needs one bulge per segment ({1}).").format(label, segments))
	out = [number(b, _("Bulge")) for b in value]
	for i, b in enumerate(out):
		if b and abs(points[i][2] - points[(i + 1) % len(points)][2]) > 1e-9:
			frappe.throw(_("{0}: an arc segment must be horizontal (segment {1}).").format(label, i + 1))
		if abs(b) > 1e6:
			frappe.throw(_("{0}: bulge {1} is too large.").format(label, i + 1))
	return out if any(out) else None


def normalise(entity_type, geometry, block_exists=None):
	"""Checked, normalised geometry (2D points get z = 0). Throws on anything malformed.

	``block_exists(name)`` checks Block Reference targets (None = not checked here)."""
	g = frappe.parse_json(geometry) if isinstance(geometry, str) else geometry
	if not isinstance(g, dict):
		frappe.throw(_("Geometry must be an object."))
	if entity_type not in SUPPORTED_TYPES:
		frappe.throw(_("{0} entities cannot be created yet.").format(entity_type))

	if entity_type == "Point":
		return {"p": coordinate(g.get("p"), _("Point"))}
	if entity_type == "Line":
		return {"p0": coordinate(g.get("p0"), _("Start point")), "p1": coordinate(g.get("p1"), _("End point"))}
	if entity_type == "Polyline":
		points = points_list(g.get("points"), _("Polyline"), 2)
		closed = bool(g.get("closed"))
		out = {"points": points, "closed": closed}
		bulges = bulge_list(g.get("bulges"), points, closed, _("Polyline"))
		if bulges:
			out["bulges"] = bulges
		if closed and g.get("fill"):
			out["fill"] = True  # drawn filled (a SOLID hatch in DXF)
		return out
	if entity_type == "Circle":
		out = {
			"center": coordinate(g.get("center"), _("Centre")),
			"radius": number(g.get("radius"), _("Radius"), positive=True),
			"normal": unit_vector(g.get("normal"), _("Normal")),
		}
		if g.get("fill"):
			out["fill"] = True
		return out
	if entity_type == "Arc":
		start, end = number(g.get("start_angle"), _("Start angle")), number(g.get("end_angle"), _("End angle"))
		if abs(end - start) < 1e-12:
			frappe.throw(_("The arc has zero sweep."))
		return {
			"center": coordinate(g.get("center"), _("Centre")),
			"radius": number(g.get("radius"), _("Radius"), positive=True),
			"start_angle": start,
			"end_angle": end,
			"normal": unit_vector(g.get("normal"), _("Normal")),
		}
	if entity_type == "Ellipse":
		normal = unit_vector(g.get("normal"), _("Normal"))
		major = coordinate(g.get("major_axis"), _("Major axis"))
		length = math.sqrt(sum(v * v for v in major))
		if length < 1e-12:
			frappe.throw(_("The major axis has zero length."))
		if abs(sum(a * b for a, b in zip(major, normal))) > 1e-6 * length:
			frappe.throw(_("The major axis must lie in the ellipse's plane."))
		start = number(g.get("start_param", 0.0), _("Start parameter"))
		end = number(g.get("end_param", TAU), _("End parameter"))
		out = {
			"center": coordinate(g.get("center"), _("Centre")),
			"major_axis": major,
			"ratio": number(g.get("ratio"), _("Axis ratio"), positive=True, maximum=1.0),
			"start_param": start,
			"end_param": end,
			"normal": normal,
		}
		if g.get("fill") and abs((end - start) % TAU) < 1e-9:
			out["fill"] = True
		return out
	if entity_type == "Spline":
		degree = int(number(g.get("degree", 3), _("Degree"), minimum=1, maximum=11))
		control = points_list(g.get("control_points"), _("Spline"), degree + 1)
		knots = [number(k, _("Knot")) for k in g.get("knots") or []]
		if len(knots) != len(control) + degree + 1:
			frappe.throw(_("A degree-{0} spline with {1} control points needs {2} knots.").format(degree, len(control), len(control) + degree + 1))
		if any(b < a for a, b in zip(knots, knots[1:])) or knots[-1] - knots[0] <= 0:
			frappe.throw(_("Spline knots must increase."))
		out = {"degree": degree, "control_points": control, "knots": knots, "closed": bool(g.get("closed"))}
		if g.get("weights"):
			weights = [number(w, _("Weight"), positive=True) for w in g["weights"]]
			if len(weights) != len(control):
				frappe.throw(_("A spline needs one weight per control point."))
			if any(abs(w - 1) > 1e-12 for w in weights):
				out["weights"] = weights
		if g.get("fit_points"):
			out["fit_points"] = points_list(g["fit_points"], _("Fit points"), 2)
		return out
	if entity_type == "Construction Line":
		return {"point": coordinate(g.get("point"), _("Point")), "direction": unit_vector(g.get("direction"), _("Direction")), "ray": bool(g.get("ray"))}
	if entity_type == "Text":
		halign, valign = g.get("halign") or "left", g.get("valign") or "baseline"
		if halign not in HALIGN or valign not in VALIGN:
			frappe.throw(_("Unknown text alignment."))
		return {
			"position": coordinate(g.get("position"), _("Text position")),
			"text": text_value(g.get("text"), _("Text")),
			"height": number(g.get("height", 1.0), _("Text height"), positive=True),
			"rotation": number(g.get("rotation", 0.0), _("Rotation")),
			"width_factor": number(g.get("width_factor", 1.0), _("Width factor"), positive=True),
			"halign": halign,
			"valign": valign,
		}
	if entity_type == "Multiline Text":
		attachment = int(number(g.get("attachment", 1), _("Attachment"), minimum=1, maximum=9))
		return {
			"position": coordinate(g.get("position"), _("Text position")),
			"text": text_value(g.get("text"), _("Text")),
			"height": number(g.get("height", 1.0), _("Text height"), positive=True),
			"rotation": number(g.get("rotation", 0.0), _("Rotation")),
			"width": number(g.get("width", 0.0), _("Width"), minimum=0),
			"attachment": attachment,
			"line_spacing": number(g.get("line_spacing", 1.0), _("Line spacing"), positive=True),
		}
	if entity_type == "Dimension":
		kind = g.get("kind")
		if kind not in DIMENSION_KINDS:
			frappe.throw(_("Unknown dimension type {0}.").format(kind))
		out = {
			"kind": kind,
			"p1": coordinate(g.get("p1"), _("First point")),
			"p2": coordinate(g.get("p2"), _("Second point")),
			"location": coordinate(g.get("location") or g.get("p2"), _("Dimension line location")),
			"text_height": number(g.get("text_height", 1.0), _("Text height"), positive=True),
			"arrow_size": number(g.get("arrow_size", g.get("text_height", 1.0)), _("Arrow size"), positive=True),
		}
		if kind in ("angular", "radius", "diameter"):
			out["center"] = coordinate(g.get("center"), _("Centre"))
		if kind == "linear":
			out["angle"] = number(g.get("angle", 0.0), _("Angle"))
		if g.get("text_override"):
			out["text_override"] = text_value(g["text_override"], _("Text"))
		if dimension_measurement(out) <= 0:
			frappe.throw(_("The dimension measures nothing."))
		return out
	if entity_type == "Hatch":
		loops = g.get("loops")
		if not isinstance(loops, list) or not loops or len(loops) > 10000:
			frappe.throw(_("A hatch needs at least one boundary loop."))
		out_loops = []
		for n, loop in enumerate(loops):
			points = points_list((loop or {}).get("points"), _("Hatch boundary {0}").format(n + 1), 2)
			row = {"points": points}
			bulges = bulge_list(loop.get("bulges"), points, True, _("Hatch boundary {0}").format(n + 1))
			if len(points) == 2 and not bulges:
				frappe.throw(_("Hatch boundary {0} encloses nothing.").format(n + 1))
			if bulges:
				row["bulges"] = bulges
			out_loops.append(row)
		pattern = (g.get("pattern") or "SOLID").upper()
		if pattern not in HATCH_PATTERNS:
			pattern = "ANSI31"  # unknown patterns are shown as ANSI31; the name is kept in metadata on import
		return {
			"loops": out_loops,
			"pattern": pattern,
			"scale": number(g.get("scale", 1.0), _("Pattern scale"), positive=True),
			"angle": number(g.get("angle", 0.0), _("Pattern angle")),
		}
	if entity_type == "Block Reference":
		name = str(g.get("block") or "")
		if not name or len(name) > 140:
			frappe.throw(_("A block reference needs its block name."))
		if block_exists and not block_exists(name):
			frappe.throw(_("Block {0} does not exist in this model.").format(name))
		scale = coordinate(g.get("scale") or [1, 1, 1], _("Scale"))
		if any(abs(s) < 1e-12 for s in scale):
			frappe.throw(_("Block scale cannot be zero."))
		return {"block": name, "insert": coordinate(g.get("insert"), _("Insertion point")), "scale": scale, "rotation": number(g.get("rotation", 0.0), _("Rotation"))}
	if entity_type in ("Surface", "Mesh", "Point Cloud"):
		# the triangles (points) are in the TIN (LAZ) file, geometry_ref; the geometry keeps the name and where it came from
		name = str(g.get("name") or g.get("surface_name") or "").strip()
		if not name or len(name) > 140:
			frappe.throw(_("A {0} needs a name.").format(_(entity_type)))
		out = {"name": name}
		if entity_type == "Surface":
			surface_type = g.get("surface_type") or "Design Surface"
			if surface_type not in SURFACE_TYPES:
				frappe.throw(_("Unknown surface type {0}.").format(surface_type))
			out["surface_type"] = surface_type
		if isinstance(g.get("source"), dict):
			out["source"] = g["source"]
		return out
	# Solid: the source parameters are kept for reference; the B-Rep file is the authoritative shape.
	source = g.get("source")
	if not isinstance(source, dict) or source.get("op") not in ("box", "cylinder", "cone", "sphere", "extrude", "transform", "boolean", "revolve", "loft", "sweep", "step"):
		frappe.throw(_("A solid needs its source operation."))
	return {"source": source}


# ---------------------------------------------------------------------------
# helpers shared with the importer and the tests
# ---------------------------------------------------------------------------


def plane_basis(normal):
	"""X and Y directions of the plane perpendicular to ``normal``, exactly as OCCT's gp_Ax2(P, V) picks
	them, so arc angles mean the same in the bench, the browser (geometry.js) and the worker."""
	a, b, c = normal
	aa, ab, ac = abs(a), abs(b), abs(c)
	if ab <= aa and ab <= ac:
		x = [-c, 0.0, a] if aa > ac else [c, 0.0, -a]
	elif aa <= ab and aa <= ac:
		x = [0.0, -c, b] if ab > ac else [0.0, c, -b]
	else:
		x = [-b, a, 0.0] if aa > ab else [b, -a, 0.0]
	length = math.sqrt(sum(v * v for v in x))
	x = [v / length for v in x]
	y = [b * x[2] - c * x[1], c * x[0] - a * x[2], a * x[1] - b * x[0]]
	return x, y


def cross(a, b):
	return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def sweep(a0, a1):
	"""Counter-clockwise sweep from a0 to a1 in (0, 2π]."""
	s = (a1 - a0) % TAU
	return s if s > 1e-15 else TAU


def arc_points(g, segments=64):
	center, radius, normal = g["center"], g["radius"], g.get("normal") or [0.0, 0.0, 1.0]
	a0 = g["start_angle"]
	s = sweep(a0, g["end_angle"])
	ux, uy = plane_basis(normal)
	return [
		[center[k] + math.cos(a0 + s * i / segments) * radius * ux[k] + math.sin(a0 + s * i / segments) * radius * uy[k] for k in range(3)]
		for i in range(segments + 1)
	]


def ellipse_points(g, segments=128):
	c, m, r = g["center"], g["major_axis"], g["ratio"]
	minor = [v * r for v in cross(g.get("normal") or [0, 0, 1], m)]
	t0 = g.get("start_param", 0.0)
	s = sweep(t0, g.get("end_param", TAU))
	return [[c[k] + math.cos(t0 + s * i / segments) * m[k] + math.sin(t0 + s * i / segments) * minor[k] for k in range(3)] for i in range(segments + 1)]


def bulge_arc(p0, p1, b):
	"""Centre and radius of a bulged segment (in XY, at p0's Z)."""
	dx, dy = p1[0] - p0[0], p1[1] - p0[1]
	c = math.hypot(dx, dy)
	rn = [dy / c, -dx / c]  # right-hand normal of the chord
	r = c * (1 + b * b) / (4 * abs(b))
	mid = [(p0[0] + p1[0]) / 2 + b * c / 2 * rn[0], (p0[1] + p1[1]) / 2 + b * c / 2 * rn[1]]
	sign = 1 if b > 0 else -1
	return [mid[0] - sign * r * rn[0], mid[1] - sign * r * rn[1], p0[2]], r


def polyline_points(points, bulges=None, closed=False, segments_per_arc=32):
	"""Vertices with each bulged segment replaced by points on its arc (for extents and drawing)."""
	n = len(points)
	count = n if closed else n - 1
	out = [points[0]]
	for i in range(count):
		p0, p1 = points[i], points[(i + 1) % n]
		b = bulges[i] if bulges else 0.0
		if b:
			center, radius = bulge_arc(p0, p1, b)
			a0 = math.atan2(p0[1] - center[1], p0[0] - center[0])
			total = 4 * math.atan(b)  # signed sweep
			for k in range(1, segments_per_arc):
				a = a0 + total * k / segments_per_arc
				out.append([center[0] + radius * math.cos(a), center[1] + radius * math.sin(a), p0[2]])
		out.append(p1)
	return out


def planar_arc_extent(center, radius, a0, sweep_angle):
	"""Exact box of a horizontal arc: its end points plus every axis extreme inside the sweep."""
	points = [[center[0] + radius * math.cos(a0 + t), center[1] + radius * math.sin(a0 + t), center[2]] for t in (0.0, sweep_angle)]
	for k in range(4):
		axis = k * math.pi / 2
		if (axis - a0) % TAU <= sweep_angle + 1e-15:
			points.append([center[0] + radius * math.cos(axis), center[1] + radius * math.sin(axis), center[2]])
	return bbox_of(points)


def polyline_extent(points, bulges, closed):
	boxes = [bbox_of(points)]
	n = len(points)
	for i, b in enumerate(bulges or []):
		if b:
			p0, p1 = points[i], points[(i + 1) % n]
			center, r = bulge_arc(p0, p1, b)
			a0 = math.atan2(p0[1] - center[1], p0[0] - center[0])
			total = 4 * math.atan(b)
			# a clockwise arc covers the same points as the counter-clockwise arc from its end
			start, s = (a0, total) if total > 0 else (a0 + total, -total)
			boxes.append(planar_arc_extent(center, r, start, s))
	return union(boxes)


def bbox_of(points):
	return [min(p[k] for p in points) for k in range(3)] + [max(p[k] for p in points) for k in range(3)]


def pad(box, amount):
	return [v - amount for v in box[:3]] + [v + amount for v in box[3:]]


def circle_extent(center, radius, normal):
	"""Exact axis-aligned extent of a full circle: radius × sqrt(1 - n_i²) per axis."""
	half = [radius * math.sqrt(max(0.0, 1.0 - n * n)) for n in normal]
	return [c - h for c, h in zip(center, half)] + [c + h for c, h in zip(center, half)]


def dimension_measurement(g):
	"""The value a dimension shows (distance in model units, or degrees for angular)."""
	p1, p2 = g["p1"], g["p2"]
	kind = g["kind"]
	if kind == "aligned":
		return math.dist(p1, p2)
	if kind == "linear":
		a = g.get("angle", 0.0)
		return abs((p2[0] - p1[0]) * math.cos(a) + (p2[1] - p1[1]) * math.sin(a))
	c = g["center"]
	if kind == "radius":
		return math.dist(c, p1)
	if kind == "diameter":
		return 2 * math.dist(c, p1)
	a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
	a2 = math.atan2(p2[1] - c[1], p2[0] - c[0])
	return math.degrees(sweep(a1, a2))


def block_transform(g):
	"""Matrix rows (2×3 in XY + Z offset/scale) of a Block Reference: insert + rotate(rotation) · scale(sx, sy)."""
	sx, sy, sz = g["scale"]
	cr, sr = math.cos(g["rotation"]), math.sin(g["rotation"])
	ix, iy, iz = g["insert"]
	return (cr * sx, -sr * sy, ix, sr * sx, cr * sy, iy, sz, iz)


def apply_block_transform(t, point, base):
	a, b, e, c, d, f, sz, iz = t
	x, y, z = point[0] - base[0], point[1] - base[1], point[2] - base[2]
	return [a * x + b * y + e, c * x + d * y + f, sz * z + iz]


def extent(entity_type, g, block_extent=None):
	"""[min_x, min_y, min_z, max_x, max_y, max_z] for indexing (padded where the curve is sampled), or None.

	``block_extent(name)`` returns (local bbox, base point) of a block, for Block References."""
	if entity_type == "Point":
		return bbox_of([g["p"]])
	if entity_type == "Line":
		return bbox_of([g["p0"], g["p1"]])
	if entity_type == "Polyline":
		return polyline_extent(g["points"], g.get("bulges"), g.get("closed"))
	if entity_type == "Circle":
		return circle_extent(g["center"], g["radius"], g["normal"])
	if entity_type == "Arc":
		s = sweep(g["start_angle"], g["end_angle"])
		if g["normal"] == [0.0, 0.0, 1.0]:
			return planar_arc_extent(g["center"], g["radius"], g["start_angle"], s)
		return pad(bbox_of(arc_points(g, 128)), g["radius"] * (1 - math.cos(s / 256)))
	if entity_type == "Ellipse":
		major = math.sqrt(sum(v * v for v in g["major_axis"]))
		return pad(bbox_of(ellipse_points(g, 256)), major * (1 - math.cos(TAU / 512)))
	if entity_type == "Spline":
		# a B-spline lies inside the convex hull of its control points
		return bbox_of(g["control_points"])
	if entity_type == "Construction Line":
		return bbox_of([g["point"]])  # infinite: indexed at its base point only
	if entity_type in ("Text", "Multiline Text"):
		p, h = g["position"], g["height"]
		lines = (g.get("text") or "").split("\n") if entity_type == "Multiline Text" else [g.get("text") or ""]
		width = g.get("width") or h * 0.6 * max(max((len(line) for line in lines), default=1), 1) * g.get("width_factor", 1.0)
		height = h * (1.3 + 1.67 * g.get("line_spacing", 1.0) * (len(lines) - 1))
		reach = math.hypot(width, height)
		return pad(bbox_of([p]), reach)  # covers every alignment and rotation
	if entity_type == "Dimension":
		points = [g["p1"], g["p2"], g["location"]] + ([g["center"]] if g.get("center") else [])
		return pad(bbox_of(points), g["text_height"] * 2)
	if entity_type == "Hatch":
		return union([polyline_extent(loop["points"], loop.get("bulges"), True) for loop in g["loops"]])
	if entity_type == "Block Reference":
		if not block_extent:
			return bbox_of([g["insert"]])
		found = block_extent(g["block"])
		if not found or not found[0]:
			return bbox_of([g["insert"]])
		box, base = found
		t = block_transform(g)
		corners = [[box[i], box[j], box[k]] for i in (0, 3) for j in (1, 4) for k in (2, 5)]
		return bbox_of([apply_block_transform(t, c, base) for c in corners])
	return None


def union(boxes):
	boxes = [b for b in boxes if b]
	if not boxes:
		return None
	return [min(b[k] for b in boxes) for k in range(3)] + [max(b[k] for b in boxes) for k in range(3, 6)]


def block_local_extent(entities, block_extent=None):
	"""Local bounding box of a block's entity list."""
	boxes = []
	for e in entities:
		try:
			boxes.append(extent(e["entity_type"], e["geometry"], block_extent))
		except (KeyError, TypeError, ValueError):
			continue
	return union(boxes)
