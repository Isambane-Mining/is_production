"""CAD worker: Open CASCADE geometry for the CAD Engineering Workstation.

Runs under the isolated CAD worker Python (``/opt/cad-worker/venv/bin/python -I geometry_worker.py``),
never inside the bench: it does not import Frappe and never sees database credentials. The bench passes a
bounded JSON request on stdin; every path in it is inside the job's private workspace, and the worker only
writes into ``workspace``. It prints JSON lines on stdout: ``{"progress": n}``, ``{"log": "..."}`` and
finally ``{"result": {...}}`` or ``{"error": "..."}``.

Commands
--------
``health``    versions of OCCT, the binding and the optional libraries; a small boolean self-test.
``build``     make solids (box, cylinder, sphere, cone, extrude, or move/rotate/scale/mirror a stored one) → OCCT B-Rep
              file, validation, bounding box, volume/area and a display mesh.
``validate``  check entities (curves, points, text, solids from their B-Rep files) and measure them.
``tessellate`` display meshes for existing B-Rep files.
``offset``    planar offset of polylines (lines and arcs) with OCCT's BRepOffsetAPI_MakeOffset.
``section``   curves where a plane cuts solids.
``step_read`` / ``step_write``  STEP (ISO 10303) exchange of exact solids.
``build`` also makes Booleans (union / subtract / intersect of stored solids), revolve, loft and sweep.

Geometry is in model coordinates (double precision). Display meshes are float32 relative to their own
rounded centre (stored in the mesh header as float64) so large survey coordinates stay exact on screen;
they are never authoritative.

This file uses OCP / Open CASCADE Technology (LGPL-2.1 with the OCCT exception); see the app README.
"""

import hashlib
import json
import math
import os
import resource
import struct
import sys
import time

MESH_MAGIC = b"ISPCADM1"
MAX_ITEMS = 5000
MAX_POLYLINE_POINTS = 200000
LENGTH_TOLERANCE = 1e-10  # curve lengths
INTEGRATION_EPS = 1e-9  # relative accuracy of area and volume integration


def emit(**message):
	sys.stdout.write(json.dumps(message) + "\n")
	sys.stdout.flush()


def apply_limits(request):
	"""Lower CPU priority, cap memory and CPU time before any geometry is touched."""
	nice = int(request.get("nice") or 0)
	if nice > 0:
		os.nice(min(nice, 19))
	memory = int(request.get("memory_limit_mb") or 0)
	if memory > 0:
		limit = memory * 1024 * 1024
		resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
	cpu = int(request.get("cpu_seconds") or 0)
	if cpu > 0:
		resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 5))


def inside(workspace, path):
	"""Refuse any path outside the job workspace (the bench builds them, but never trust them)."""
	real = os.path.realpath(path)
	root = os.path.realpath(workspace)
	if real != root and not real.startswith(root + os.sep):
		raise ValueError(f"Path outside the job workspace: {path}")
	return real


# ---------------------------------------------------------------------------
# OCCT helpers
# ---------------------------------------------------------------------------


def occ():
	"""Import OCP lazily so ``health`` can report a missing binding instead of crashing."""
	from OCP import BRepAlgoAPI, BRepBndLib, BRepBuilderAPI, BRepCheck, BRepGProp, BRepMesh, BRepPrimAPI, BRepTools
	from OCP.BRep import BRep_Builder, BRep_Tool
	from OCP.BRepAdaptor import BRepAdaptor_Curve
	from OCP.Bnd import Bnd_Box
	from OCP.GC import GC_MakeArcOfCircle, GC_MakeSegment
	from OCP.GCPnts import GCPnts_QuasiUniformDeflection
	from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt, gp_Vec
	from OCP.GProp import GProp_GProps
	from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SOLID, TopAbs_WIRE
	from OCP.TopExp import TopExp_Explorer
	from OCP.TopLoc import TopLoc_Location
	from OCP.TopoDS import TopoDS, TopoDS_Shape

	return {k: v for k, v in locals().items()}


def point(o, xyz):
	x, y, z = (list(xyz) + [0.0, 0.0, 0.0])[:3]
	for v in (x, y, z):
		if not math.isfinite(float(v)):
			raise ValueError("Coordinate is not a finite number.")
	return o["gp_Pnt"](float(x), float(y), float(z))


def direction(o, xyz, default=(0.0, 0.0, 1.0)):
	vec = [float(v) for v in (xyz or default)][:3]
	if math.sqrt(sum(v * v for v in vec)) < 1e-12:
		raise ValueError("Direction has zero length.")
	return o["gp_Dir"](*vec)


def bbox(o, shape):
	box = o["Bnd_Box"]()
	o["BRepBndLib"].BRepBndLib.AddOptimal_s(shape, box, False, False)
	if box.IsVoid():
		return None
	x0, y0, z0, x1, y1, z1 = box.Get()
	return [x0, y0, z0, x1, y1, z1]


def check(o, shape):
	analyzer = o["BRepCheck"].BRepCheck_Analyzer(shape)
	return bool(analyzer.IsValid())


def measures(o, shape):
	"""Volume (solids), area (faces) or length (curves), by adaptive integration to INTEGRATION_EPS."""
	result = {}
	props = o["GProp_GProps"]()
	if o["TopExp_Explorer"](shape, o["TopAbs_SOLID"]).More():
		o["BRepGProp"].BRepGProp.VolumeProperties_s(shape, props, INTEGRATION_EPS, False, False)
		result["volume"] = props.Mass()
		props = o["GProp_GProps"]()
	if o["TopExp_Explorer"](shape, o["TopAbs_FACE"]).More():
		o["BRepGProp"].BRepGProp.SurfaceProperties_s(shape, props, INTEGRATION_EPS, False)
		result["area"] = props.Mass()
	else:
		result["length"] = length_of(o, shape)
	return result


def write_brep(o, shape, workspace, name):
	path = inside(workspace, os.path.join(workspace, "outputs", f"{name}.brep"))
	os.makedirs(os.path.dirname(path), exist_ok=True)
	if not o["BRepTools"].BRepTools.Write_s(shape, path):
		raise RuntimeError("The B-Rep file could not be written.")
	with open(path, "rb") as f:
		digest = hashlib.sha256(f.read()).hexdigest()
	return path, digest


def read_brep(o, workspace, path):
	shape = o["TopoDS_Shape"]()
	builder = o["BRep_Builder"]()
	if not o["BRepTools"].BRepTools.Read_s(shape, inside(workspace, path), builder):
		raise RuntimeError("The B-Rep file could not be read.")
	return shape


def mesh_origin(box):
	"""Each mesh is stored relative to its own rounded centre, so float32 keeps sub-millimetre precision."""
	if not box:
		return [0.0, 0.0, 0.0]
	return [float(round((box[i] + box[i + 3]) / 2)) for i in range(3)]


def deflection_for(box):
	if not box:
		return 0.01
	diagonal = math.dist(box[:3], box[3:])
	return min(max(diagonal * 0.002, 0.001), 5.0)


def tessellate(o, shape, origin, path, deflection):
	"""Triangles (+ feature edges) relative to ``origin`` in a small binary file for the browser."""
	o["BRepMesh"].BRepMesh_IncrementalMesh(shape, deflection, False, 0.35, True)
	ox, oy, oz = origin
	positions, indices, edges = [], [], []
	explorer = o["TopExp_Explorer"](shape, o["TopAbs_FACE"])
	while explorer.More():
		face = o["TopoDS"].Face_s(explorer.Current())
		location = o["TopLoc_Location"]()
		triangulation = o["BRep_Tool"].Triangulation_s(face, location)
		if triangulation is not None:
			transform = location.Transformation()
			base = len(positions) // 3
			for i in range(1, triangulation.NbNodes() + 1):
				p = triangulation.Node(i).Transformed(transform)
				positions.extend((p.X() - ox, p.Y() - oy, p.Z() - oz))
			reversed_face = face.Orientation() == o["TopAbs_REVERSED"]
			for i in range(1, triangulation.NbTriangles() + 1):
				a, b, c = triangulation.Triangle(i).Get()
				if reversed_face:
					b, c = c, b
				indices.extend((base + a - 1, base + b - 1, base + c - 1))
		explorer.Next()

	explorer = o["TopExp_Explorer"](shape, o["TopAbs_EDGE"])
	while explorer.More():
		edge = o["TopoDS"].Edge_s(explorer.Current())
		try:
			curve = o["BRepAdaptor_Curve"](edge)
			sampler = o["GCPnts_QuasiUniformDeflection"](curve, deflection)
			if sampler.IsDone():
				previous = None
				for i in range(1, sampler.NbPoints() + 1):
					p = sampler.Value(i)
					current = (p.X() - ox, p.Y() - oy, p.Z() - oz)
					if previous:
						edges.extend(previous + current)
					previous = current
		except Exception:  # a degenerate edge (e.g. a cone apex) has no curve to sample
			pass
		explorer.Next()

	header = struct.pack("<8sIII3d", MESH_MAGIC, len(positions) // 3, len(indices), len(edges) // 3, ox, oy, oz)
	with open(path, "wb") as f:  # callers pass a path already checked with inside()
		f.write(header)
		f.write(struct.pack(f"<{len(positions)}f", *positions))
		f.write(struct.pack(f"<{len(indices)}I", *indices))
		f.write(struct.pack(f"<{len(edges)}f", *edges))
	return {"vertices": len(positions) // 3, "triangles": len(indices) // 3, "deflection": deflection}


# ---------------------------------------------------------------------------
# solids
# ---------------------------------------------------------------------------


def positive(spec, key):
	value = float(spec.get(key) or 0)
	if not math.isfinite(value) or value <= 0:
		raise ValueError(f"{key} must be greater than zero.")
	return value


def tapered_prism(o, face, wire, vector, inset):
	"""A prism whose far end is the profile offset inwards by `inset` (outwards when negative): a ruled loft between
	the profile and the offset profile moved along the vector (sharp corners stay sharp)."""
	from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
	from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset, BRepOffsetAPI_ThruSections
	from OCP.GeomAbs import GeomAbs_Intersection
	from OCP.gp import gp_Trsf, gp_Vec

	maker = BRepOffsetAPI_MakeOffset(face, GeomAbs_Intersection)
	maker.Perform(-inset)
	if not maker.IsDone():
		raise ValueError("The taper is too steep for this profile (the far end vanishes).")
	explorer = o["TopExp_Explorer"](maker.Shape(), o["TopAbs_WIRE"])
	if not explorer.More():
		raise ValueError("The taper is too steep for this profile (the far end vanishes).")
	top = o["TopoDS"].Wire_s(explorer.Current())
	move = gp_Trsf()
	move.SetTranslation(gp_Vec(*vector))
	top = o["TopoDS"].Wire_s(BRepBuilderAPI_Transform(top, move, True).Shape())
	loft = BRepOffsetAPI_ThruSections(True, True)
	loft.AddWire(wire)
	loft.AddWire(top)
	loft.CheckCompatibility(True)
	loft.Build()
	if not loft.IsDone():
		raise ValueError("Open CASCADE could not taper this profile.")
	return loft.Shape()


def make_solid(o, spec):
	op = spec.get("op")
	prim = o["BRepPrimAPI"]
	if op == "box":
		dx, dy, dz = (float(v) for v in spec["size"])
		if min(dx, dy, dz) <= 0:
			raise ValueError("Box sizes must be greater than zero.")
		return prim.BRepPrimAPI_MakeBox(point(o, spec["origin"]), dx, dy, dz).Shape()
	if op == "cylinder":
		axis = o["gp_Ax2"](point(o, spec["base"]), direction(o, spec.get("axis")))
		return prim.BRepPrimAPI_MakeCylinder(axis, positive(spec, "radius"), positive(spec, "height")).Shape()
	if op == "cone":
		axis = o["gp_Ax2"](point(o, spec["base"]), direction(o, spec.get("axis")))
		r1, r2 = float(spec.get("radius1") or 0), float(spec.get("radius2") or 0)
		if r1 < 0 or r2 < 0 or (r1 == 0 and r2 == 0):
			raise ValueError("A cone needs at least one radius greater than zero.")
		return prim.BRepPrimAPI_MakeCone(axis, r1, r2, positive(spec, "height")).Shape()
	if op == "sphere":
		return prim.BRepPrimAPI_MakeSphere(point(o, spec["center"]), positive(spec, "radius")).Shape()
	if op == "extrude":
		# a closed profile (straight and arc segments; a circle is two half-circle arcs) pushed along a vector,
		# optionally tapered: +taper° leans the sides inwards (narrower at the far end), -taper° outwards
		profile = spec.get("profile") or []
		if len(profile) < 2:
			raise ValueError("An extrusion profile needs at least two points (with arcs) or three.")
		if len(profile) > MAX_POLYLINE_POINTS:
			raise ValueError("The extrusion profile has too many points.")
		face, wire = closed_face(o, profile, spec.get("bulges"))
		vx, vy, vz = (float(v) for v in spec["vector"])
		length = math.sqrt(vx * vx + vy * vy + vz * vz)
		if length < 1e-9:
			raise ValueError("The extrusion distance is zero.")
		taper = float(spec.get("taper") or 0)
		if abs(taper) < 1e-9:
			return prim.BRepPrimAPI_MakePrism(face, o["gp_Vec"](vx, vy, vz)).Shape()
		if not abs(taper) < 89:
			raise ValueError("The taper angle must be between -89° and 89°.")
		return tapered_prism(o, face, wire, (vx, vy, vz), length * math.tan(math.radians(taper)))
	raise ValueError(f"Unknown solid operation {op!r}.")


def transformed(o, workspace, spec):
	"""A stored solid moved/rotated/scaled/mirrored (the source B-Rep is staged in the workspace by the bench).

	``matrix``: 12 numbers, rows of a 3×4 similarity matrix [R·s | t]; or ``translate``: [dx, dy, dz]."""
	from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
	from OCP.gp import gp_Trsf

	shape = read_brep(o, workspace, spec["brep"])
	trsf = gp_Trsf()
	if spec.get("matrix"):
		m = [float(v) for v in spec["matrix"]]
		if len(m) != 12 or not all(math.isfinite(v) for v in m):
			raise ValueError("The transformation matrix needs 12 finite numbers.")
		trsf.SetValues(*m)  # OCCT refuses anything that is not a similarity (shear, non-uniform scale)
	else:
		dx, dy, dz = (float(v) for v in spec.get("translate") or (0, 0, 0))
		trsf.SetTranslation(o["gp_Vec"](dx, dy, dz))
	return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def closed_face(o, profile, bulges=None):
	"""A planar face bounded by a closed polyline (straight or bulged segments)."""
	wire = polyline_wire(o, profile, bulges, True)
	face = o["BRepBuilderAPI"].BRepBuilderAPI_MakeFace(wire, True)
	if not face.IsDone():
		raise ValueError("The profile is not a planar closed curve.")
	return face.Face(), wire


def chosen_edges(o, shape, which):
	"""Edges of a solid: all, vertical (straight and along Z), top (at the highest Z) or bottom (at the lowest)."""
	from OCP.BRep import BRep_Tool
	from OCP.TopExp import TopExp
	from OCP.TopTools import TopTools_IndexedMapOfShape

	edges = TopTools_IndexedMapOfShape()
	TopExp.MapShapes_s(shape, o["TopAbs_EDGE"], edges)
	box = bbox(o, shape)
	zmin, zmax = box[2], box[5]
	tol = max(1e-6, 1e-7 * max(box[3] - box[0], box[4] - box[1], zmax - zmin))
	out = []
	for i in range(1, edges.Extent() + 1):
		edge = o["TopoDS"].Edge_s(edges.FindKey(i))
		curve = o["BRepAdaptor_Curve"](edge)
		a, b = curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())
		m = curve.Value((curve.FirstParameter() + curve.LastParameter()) / 2)
		zs = (a.Z(), b.Z(), m.Z())
		if which == "vertical":
			length = a.Distance(b)
			ok = length > tol and abs(a.X() - b.X()) < tol and abs(a.Y() - b.Y()) < tol and abs(m.X() - a.X()) < tol and abs(m.Y() - a.Y()) < tol
		elif which == "top":
			ok = all(abs(z - zmax) < tol for z in zs)
		elif which == "bottom":
			ok = all(abs(z - zmin) < tol for z in zs)
		else:
			ok = True
		if ok and not BRep_Tool.Degenerated_s(edge):
			out.append(edge)
	if not out:
		raise ValueError(f"The solid has no {which} edges.")
	return out


def dressed(o, shape, spec):
	"""Fillet or chamfer chosen edges of a solid, or hollow it into a shell (optionally open at the top)."""
	op = spec["op"]
	if op in ("fillet", "chamfer"):
		size = float(spec.get("radius") or spec.get("distance") or 0)
		if not size > 0:
			raise ValueError("The fillet radius / chamfer distance must be greater than zero.")
		from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet

		maker = BRepFilletAPI_MakeFillet(shape) if op == "fillet" else BRepFilletAPI_MakeChamfer(shape)
		for edge in chosen_edges(o, shape, spec.get("edges") or "all"):
			maker.Add(size, edge)
		maker.Build()
		if not maker.IsDone():
			raise ValueError(f"Open CASCADE could not {op} these edges with {size:g} (too large for the solid?).")
		return maker.Shape()
	# shell
	from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
	from OCP.TopTools import TopTools_ListOfShape

	thickness = float(spec.get("thickness") or 0)
	if not thickness > 0:
		raise ValueError("The wall thickness must be greater than zero.")
	if spec.get("open") != "top":
		# closed: the solid less its inward offset (a hollow with walls `thickness` thick all round)
		from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
		from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffsetShape

		inner = BRepOffsetAPI_MakeOffsetShape()
		inner.PerformByJoin(shape, -thickness, 1e-6)
		if not inner.IsDone():
			raise ValueError("Open CASCADE could not offset the solid inwards by that wall thickness.")
		cut = BRepAlgoAPI_Cut(shape, inner.Shape())
		if not cut.IsDone():
			raise ValueError("Open CASCADE could not hollow the solid.")
		return cut.Shape()
	remove = TopTools_ListOfShape()
	if spec.get("open") == "top":
		box = bbox(o, shape)
		tol = max(1e-6, 1e-7 * (box[5] - box[2] + 1))
		explorer = o["TopExp_Explorer"](shape, o["TopAbs_FACE"])
		while explorer.More():
			face = o["TopoDS"].Face_s(explorer.Current())
			fb = bbox(o, face)
			if abs(fb[2] - box[5]) < tol and abs(fb[5] - box[5]) < tol:
				remove.Append(face)
			explorer.Next()
		if remove.Extent() == 0:
			raise ValueError("The solid has no flat top face to open.")
	maker = BRepOffsetAPI_MakeThickSolid()
	maker.MakeThickSolidByJoin(shape, remove, -thickness, 1e-6)
	maker.Build()
	if not maker.IsDone():
		raise ValueError("Open CASCADE could not shell the solid with that wall thickness.")
	return maker.Shape()


def make_shape(o, workspace, spec):
	"""Primitives, moves of stored solids, Booleans, revolve, loft and sweep (Phase 3)."""
	op = spec.get("op")
	if op == "transform":
		return transformed(o, workspace, spec)
	if op in ("fillet", "chamfer", "shell"):
		return dressed(o, read_brep(o, workspace, spec["brep"]), spec)
	if op == "boolean":
		from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse

		shapes = [read_brep(o, workspace, p) for p in spec.get("breps") or []]
		if len(shapes) < 2:
			raise ValueError("A Boolean needs at least two solids.")
		kind = spec.get("kind")
		Algo = {"union": BRepAlgoAPI_Fuse, "subtract": BRepAlgoAPI_Cut, "intersect": BRepAlgoAPI_Common}.get(kind)
		if not Algo:
			raise ValueError(f"Unknown Boolean {kind!r}.")
		result = shapes[0]
		for other in shapes[1:]:
			algo = Algo(result, other)
			if not algo.IsDone():
				raise ValueError(f"Open CASCADE could not {kind} these solids.")
			result = algo.Shape()
		if not o["TopExp_Explorer"](result, o["TopAbs_SOLID"]).More():
			raise ValueError("The result is empty (the solids do not overlap).")
		return unify(result)
	if op == "revolve":
		from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol
		from OCP.gp import gp_Ax1

		face, _wire = closed_face(o, spec["profile"], spec.get("bulges"))
		angle = math.radians(float(spec.get("angle") or 360))
		if not 0 < abs(angle) <= 2 * math.pi + 1e-12:
			raise ValueError("The revolve angle must be between 0 and 360 degrees.")
		axis = gp_Ax1(point(o, spec["axis_point"]), direction(o, spec["axis_direction"]))
		maker = BRepPrimAPI_MakeRevol(face, axis, angle, True)
		if not maker.IsDone():
			raise ValueError("Open CASCADE could not revolve the profile (does the axis cut through it?).")
		return maker.Shape()
	if op == "loft":
		from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections

		profiles = spec.get("profiles") or []
		if len(profiles) < 2:
			raise ValueError("A loft needs at least two closed profiles.")
		maker = BRepOffsetAPI_ThruSections(True, bool(spec.get("ruled")))
		for prof in profiles:
			maker.AddWire(polyline_wire(o, prof["points"], prof.get("bulges"), True))
		maker.CheckCompatibility(True)
		maker.Build()
		if not maker.IsDone():
			raise ValueError("Open CASCADE could not loft through these profiles.")
		return maker.Shape()
	if op == "sweep":
		from OCP.BRepBuilderAPI import BRepBuilderAPI_TransitionMode
		from OCP.BRepOffsetAPI import BRepOffsetAPI_MakePipeShell

		_face, profile = closed_face(o, spec["profile"], spec.get("profile_bulges"))
		path = polyline_wire(o, spec["path"], spec.get("path_bulges"), False)
		maker = BRepOffsetAPI_MakePipeShell(path)
		maker.SetTransitionMode(BRepBuilderAPI_TransitionMode.BRepBuilderAPI_RoundCorner)
		maker.Add(profile, False, False)
		maker.Build()
		if not maker.IsDone():
			raise ValueError("Open CASCADE could not sweep the profile along the path.")
		maker.MakeSolid()
		return maker.Shape()
	return make_solid(o, spec)


def unify(shape):
	"""Merge coplanar faces left by Booleans (cleaner B-Rep, same geometry)."""
	from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

	try:
		u = ShapeUpgrade_UnifySameDomain(shape, True, True, False)
		u.Build()
		return u.Shape()
	except Exception:
		return shape


def build(request):
	o = occ()
	workspace = request["workspace"]
	items = request.get("items") or []
	if len(items) > MAX_ITEMS:
		raise ValueError("Too many items in one request.")
	results = []
	for n, item in enumerate(items):
		key = str(item.get("key") or n)
		try:
			shape = make_shape(o, workspace, item)
			valid = check(o, shape)
			box = bbox(o, shape)
			entry = {"key": key, "valid": valid, "bbox": box, **measures(o, shape)}
			if not valid:
				entry["message"] = "OCCT topology check failed."
			else:
				path, digest = write_brep(o, shape, workspace, f"item_{n}")
				mesh_path = inside(workspace, os.path.join(workspace, "outputs", f"item_{n}.cadmesh"))
				entry.update({"brep": path, "checksum": digest, "mesh": mesh_path})
				entry["mesh_info"] = tessellate(o, shape, mesh_origin(box), mesh_path, deflection_for(box))
		except Exception as e:
			entry = {"key": key, "valid": False, "message": str(e)}
		results.append(entry)
		emit(progress=int((n + 1) * 100 / max(len(items), 1)))
	return {"items": results}


# ---------------------------------------------------------------------------
# validation of stored entities
# ---------------------------------------------------------------------------


def bulge_mid(p0, p1, b):
	"""The point halfway along a bulged segment (DXF bulge: tan(sweep/4), positive = counter-clockwise)."""
	dx, dy = p1[0] - p0[0], p1[1] - p0[1]
	c = math.hypot(dx, dy)
	if c < 1e-12:
		raise ValueError("A bulged segment has zero length.")
	return [(p0[0] + p1[0]) / 2 + b * c / 2 * dy / c, (p0[1] + p1[1]) / 2 - b * c / 2 * dx / c, p0[2]]


def polyline_wire(o, points, bulges=None, closed=False):
	"""A wire of straight and circular-arc edges (bulges) through the vertices."""
	api = o["BRepBuilderAPI"]
	if len(points) < 2:
		raise ValueError("A polyline needs at least two points.")
	if len(points) > MAX_POLYLINE_POINTS:
		raise ValueError("The polyline has too many points.")
	pts = [[float(v) for v in (list(p) + [0.0])[:3]] for p in points]
	n = len(pts)
	count = n if closed else n - 1
	wire = api.BRepBuilderAPI_MakeWire()
	vertices = [point(o, p) for p in pts]
	for i in range(count):
		a, b = vertices[i], vertices[(i + 1) % n]
		if a.Distance(b) < 1e-9:
			raise ValueError(f"The polyline has two identical consecutive points (segment {i + 1}).")
		bulge = bulges[i] if bulges else 0.0
		if bulge:
			if abs(pts[i][2] - pts[(i + 1) % n][2]) > 1e-9:
				raise ValueError(f"Arc segment {i + 1} is not horizontal.")
			curve = o["GC_MakeArcOfCircle"](a, point(o, bulge_mid(pts[i], pts[(i + 1) % n], bulge)), b).Value()
		else:
			curve = o["GC_MakeSegment"](a, b).Value()
		wire.Add(api.BRepBuilderAPI_MakeEdge(curve, a, b).Edge())
		if not wire.IsDone():
			raise ValueError(f"Segment {i + 1} does not connect.")
	return wire.Wire()


def spline_curve(g):
	from OCP.Geom import Geom_BSplineCurve
	from OCP.gp import gp_Pnt
	from OCP.TColgp import TColgp_Array1OfPnt
	from OCP.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal

	degree = int(g["degree"])
	control = g["control_points"]
	flat = [float(k) for k in g["knots"]]
	if len(flat) != len(control) + degree + 1:
		raise ValueError("The spline's knot count does not match its control points.")
	distinct, mults = [], []
	for k in flat:
		if distinct and abs(k - distinct[-1]) <= 1e-12 * max(1.0, abs(k)):
			mults[-1] += 1
		else:
			distinct.append(k)
			mults.append(1)
	poles = TColgp_Array1OfPnt(1, len(control))
	for i, p in enumerate(control):
		poles.SetValue(i + 1, gp_Pnt(*[float(v) for v in (list(p) + [0.0])[:3]]))
	knots = TColStd_Array1OfReal(1, len(distinct))
	multiplicities = TColStd_Array1OfInteger(1, len(distinct))
	for i, (k, m) in enumerate(zip(distinct, mults)):
		knots.SetValue(i + 1, k)
		multiplicities.SetValue(i + 1, m)
	if g.get("weights"):
		weights = TColStd_Array1OfReal(1, len(control))
		for i, w in enumerate(g["weights"]):
			weights.SetValue(i + 1, float(w))
		return Geom_BSplineCurve(poles, weights, knots, multiplicities, degree, False)
	return Geom_BSplineCurve(poles, knots, multiplicities, degree, False)


def curve_shape(o, entity_type, g):
	"""An OCCT edge/wire for a curve entity; raises ValueError for degenerate geometry."""
	api = o["BRepBuilderAPI"]
	if entity_type == "Line":
		p0, p1 = point(o, g["p0"]), point(o, g["p1"])
		if p0.Distance(p1) < 1e-9:
			raise ValueError("The line has zero length.")
		return api.BRepBuilderAPI_MakeEdge(o["GC_MakeSegment"](p0, p1).Value()).Edge()
	if entity_type == "Polyline":
		return polyline_wire(o, g.get("points") or [], g.get("bulges"), g.get("closed"))
	if entity_type in ("Circle", "Arc"):
		axis = o["gp_Ax2"](point(o, g["center"]), direction(o, g.get("normal")))
		radius = float(g.get("radius") or 0)
		if not radius > 0:
			raise ValueError("The radius must be greater than zero.")
		circle = o["gp_Circ"](axis, radius)
		if entity_type == "Circle":
			return api.BRepBuilderAPI_MakeEdge(circle).Edge()
		a0, a1 = float(g["start_angle"]), float(g["end_angle"])
		if abs(a1 - a0) < 1e-12:
			raise ValueError("The arc has zero sweep.")
		return api.BRepBuilderAPI_MakeEdge(o["GC_MakeArcOfCircle"](circle, a0, a1, True).Value()).Edge()
	if entity_type == "Ellipse":
		from OCP.GC import GC_MakeArcOfEllipse
		from OCP.gp import gp_Elips

		major = [float(v) for v in g["major_axis"]]
		length = math.sqrt(sum(v * v for v in major))
		ratio = float(g["ratio"])
		if length < 1e-12 or not 0 < ratio <= 1:
			raise ValueError("The ellipse axes are degenerate.")
		axis = o["gp_Ax2"](point(o, g["center"]), direction(o, g.get("normal")), direction(o, major))
		ellipse = gp_Elips(axis, length, length * ratio)
		t0, t1 = float(g.get("start_param", 0.0)), float(g.get("end_param", 2 * math.pi))
		if abs((t1 - t0) % (2 * math.pi)) < 1e-12:
			return api.BRepBuilderAPI_MakeEdge(ellipse).Edge()
		return api.BRepBuilderAPI_MakeEdge(GC_MakeArcOfEllipse(ellipse, t0, t1, True).Value()).Edge()
	if entity_type == "Spline":
		return api.BRepBuilderAPI_MakeEdge(spline_curve(g)).Edge()
	raise ValueError(f"No curve for {entity_type}.")


def face_area(o, wire):
	face = o["BRepBuilderAPI"].BRepBuilderAPI_MakeFace(wire, True)
	if not face.IsDone():
		return None
	props = o["GProp_GProps"]()
	o["BRepGProp"].BRepGProp.SurfaceProperties_s(face.Face(), props, INTEGRATION_EPS, False)
	return abs(props.Mass())


def length_of(o, shape):
	"""Curve length by adaptive integration per edge. (BRepGProp.LinearProperties uses a fixed Gauss rule
	and is about 0.1 % off on ellipses; GCPnts_AbscissaPoint converges to the tolerance.)"""
	from OCP.GCPnts import GCPnts_AbscissaPoint

	total = 0.0
	explorer = o["TopExp_Explorer"](shape, o["TopAbs_EDGE"])
	while explorer.More():
		edge = o["TopoDS"].Edge_s(explorer.Current())
		total += GCPnts_AbscissaPoint.Length_s(o["BRepAdaptor_Curve"](edge), LENGTH_TOLERANCE)
		explorer.Next()
	return total


def dimension_value(g):
	"""Same definition as cad_geometry.dimension_measurement (distance, or degrees for angular)."""
	p1, p2, kind = g["p1"], g["p2"], g["kind"]
	if kind == "aligned":
		return math.dist(p1, p2)
	if kind == "linear":
		a = float(g.get("angle", 0.0))
		return abs((p2[0] - p1[0]) * math.cos(a) + (p2[1] - p1[1]) * math.sin(a))
	c = g["center"]
	if kind == "radius":
		return math.dist(c, p1)
	if kind == "diameter":
		return 2 * math.dist(c, p1)
	a1 = math.atan2(p1[1] - c[1], p1[0] - c[0])
	a2 = math.atan2(p2[1] - c[1], p2[0] - c[0])
	return math.degrees((a2 - a1) % (2 * math.pi))


SIMPLE_TYPES = {"Construction Line", "Block Reference"}


def validate_entity(o, workspace, entity):
	entity_type, g = entity["entity_type"], entity.get("geometry") or {}
	result = {"entity_id": entity["entity_id"], "entity_version": entity.get("entity_version")}

	def done(status="Valid", message=None, measures=None, box=None):
		out = {**result, "status": status, "message": message, "measures": measures or {}}
		if box:
			out["bbox"] = box
		return out

	if entity_type == "Point":
		point(o, g["p"])
		return done()
	if entity_type in ("Text", "Multiline Text"):
		if not float(g.get("height") or 0) > 0:
			raise ValueError("Text height must be greater than zero.")
		point(o, g["position"])
		return done() if str(g.get("text") or "").strip() else done("Warning", "The text is empty.")
	if entity_type in SIMPLE_TYPES:
		return done()  # checked in the bench (block exists, direction non-zero)
	if entity_type == "Dimension":
		value = dimension_value(g)
		return done("Valid" if value > 0 else "Invalid", None if value > 0 else "The dimension measures nothing.", {"length": value})
	if entity_type == "Hatch":
		areas = []
		for n, loop in enumerate(g.get("loops") or []):
			wire = polyline_wire(o, loop["points"], loop.get("bulges"), True)
			area = face_area(o, wire)
			if area is None:
				return done("Invalid", f"Boundary {n + 1} is not a planar closed loop.")
			areas.append(area)
		if not areas:
			raise ValueError("The hatch has no boundary.")
		# even-odd: the first loop is the outer boundary, the others are holes inside it
		net = areas[0] - sum(areas[1:])
		return done("Valid" if net > 0 else "Warning", None if net > 0 else "The holes are larger than the boundary.", {"area": max(net, 0.0)})
	if entity_type in ("Line", "Polyline", "Circle", "Arc", "Ellipse", "Spline"):
		shape = curve_shape(o, entity_type, g)
		found = {"length": length_of(o, shape)}
		status, message = "Valid", None
		if entity_type == "Circle":
			found["area"] = math.pi * float(g["radius"]) ** 2
		elif entity_type == "Ellipse" and abs((float(g.get("end_param", 2 * math.pi)) - float(g.get("start_param", 0))) % (2 * math.pi)) < 1e-12:
			major = math.sqrt(sum(float(v) ** 2 for v in g["major_axis"]))
			found["area"] = math.pi * major * major * float(g["ratio"])
		elif entity_type == "Polyline" and g.get("closed"):
			area = face_area(o, shape)
			if area is None:
				status, message = "Warning", "Closed polyline is not planar or crosses itself; no area."
			else:
				found["area"] = area
		if not check(o, shape):
			status, message = "Invalid", "OCCT topology check failed."
		return done(status, message, found, bbox(o, shape))
	if entity.get("brep"):
		shape = read_brep(o, workspace, entity["brep"])
		valid = check(o, shape)
		return done("Valid" if valid else "Invalid", None if valid else "OCCT topology check failed.", measures(o, shape), bbox(o, shape))
	return done("Not Checked", f"{entity_type} is not validated yet.")


# ---------------------------------------------------------------------------
# curve operations
# ---------------------------------------------------------------------------


def wire_to_polyline(o, wire):
	"""Vertices and bulges of a wire made of lines and circular arcs; other edges are sampled."""
	from OCP.BRepTools import BRepTools_WireExplorer
	from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line

	points, bulges = [], []
	explorer = BRepTools_WireExplorer(wire)
	while explorer.More():
		edge = explorer.Current()
		curve = o["BRepAdaptor_Curve"](edge)
		first, last = curve.FirstParameter(), curve.LastParameter()
		reversed_edge = edge.Orientation() == o["TopAbs_REVERSED"]
		start = curve.Value(last if reversed_edge else first)
		end = curve.Value(first if reversed_edge else last)
		p0 = [start.X(), start.Y(), start.Z()]
		kind = curve.GetType()
		if kind == GeomAbs_Line:
			points.append(p0)
			bulges.append(0.0)
		elif kind == GeomAbs_Circle and abs(start.Z() - end.Z()) < 1e-9:
			mid = curve.Value((first + last) / 2)
			p1 = [end.X(), end.Y(), end.Z()]
			chord = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
			mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
			sagitta = math.hypot(mid.X() - mx, mid.Y() - my)
			side = (p1[0] - p0[0]) * (mid.Y() - p0[1]) - (p1[1] - p0[1]) * (mid.X() - p0[0])
			points.append(p0)
			bulges.append((2 * sagitta / chord) * (1 if side < 0 else -1) if chord > 1e-12 else 0.0)
		else:  # anything else (rare in planar offsets): sampled into short straight segments
			sampler = o["GCPnts_QuasiUniformDeflection"](curve, 1e-3)
			values = [sampler.Value(i) for i in range(1, sampler.NbPoints() + 1)] if sampler.IsDone() else [start]
			if reversed_edge:
				values.reverse()
			for v in values[:-1]:
				points.append([v.X(), v.Y(), v.Z()])
				bulges.append(0.0)
		last_point = [end.X(), end.Y(), end.Z()]
		explorer.Next()
	closed = bool(points) and math.dist(points[0], last_point) < 1e-7
	if not closed:
		points.append(last_point)
	return {"points": points, "bulges": bulges if any(bulges) else None, "closed": closed}


def offset(request):
	"""Planar (horizontal) offset of polylines with OCCT: +distance is outward for closed curves and to the
	right of the direction of travel for open ones. Returns every resulting polyline (an offset can split)."""
	from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeOffset
	from OCP.GeomAbs import GeomAbs_Arc, GeomAbs_Intersection

	o = occ()
	out = []
	items = request.get("items") or []
	for n, item in enumerate(items):
		key = str(item.get("key") or n)
		try:
			points = item["points"]
			if max(p[2] for p in points) - min(p[2] for p in points) > 1e-9:
				raise ValueError("Only horizontal polylines (one elevation) can be offset by the CAD worker.")
			distance = float(item["distance"])
			if not math.isfinite(distance) or abs(distance) < 1e-12:
				raise ValueError("The offset distance must not be zero.")
			wire = polyline_wire(o, points, item.get("bulges"), item.get("closed"))
			join = GeomAbs_Arc if item.get("join") == "round" else GeomAbs_Intersection
			maker = BRepOffsetAPI_MakeOffset(wire, join, not item.get("closed"))
			maker.Perform(distance)
			if not maker.IsDone():
				raise ValueError("Open CASCADE could not offset this curve by that distance.")
			results = []
			explorer = o["TopExp_Explorer"](maker.Shape(), o["TopAbs_WIRE"])
			while explorer.More():
				results.append(wire_to_polyline(o, o["TopoDS"].Wire_s(explorer.Current())))
				explorer.Next()
			if not results:
				raise ValueError("The offset collapsed (distance too large for this curve).")
			out.append({"key": key, "polylines": results})
		except Exception as e:
			out.append({"key": key, "error": str(e)})
		emit(progress=int((n + 1) * 100 / max(len(items), 1)))
	return {"items": out}


def validate(request):
	o = occ()
	workspace = request["workspace"]
	with open(inside(workspace, request["input"])) as f:
		entities = json.load(f)
	results = []
	for n, entity in enumerate(entities):
		try:
			results.append(validate_entity(o, workspace, entity))
		except Exception as e:
			results.append({"entity_id": entity.get("entity_id"), "entity_version": entity.get("entity_version"), "status": "Invalid", "message": str(e), "measures": {}})
		if n % 50 == 0 or n == len(entities) - 1:
			emit(progress=int((n + 1) * 100 / max(len(entities), 1)))
	return {"entities": results}


def tessellate_files(request):
	o = occ()
	workspace = request["workspace"]
	results = []
	for n, item in enumerate(request.get("items") or []):
		try:
			shape = read_brep(o, workspace, item["brep"])
			out = inside(workspace, item["out"])
			box = bbox(o, shape)
			results.append({"key": item["key"], "mesh": out, "mesh_info": tessellate(o, shape, mesh_origin(box), out, deflection_for(box))})
		except Exception as e:
			results.append({"key": item.get("key"), "error": str(e)})
		emit(progress=int((n + 1) * 100 / max(len(request.get("items") or []), 1)))
	return {"items": results}


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


def package_version(name):
	try:
		from importlib.metadata import version

		return version(name)
	except Exception:
		return None


def health(request):
	report = {"python": sys.version.split()[0], "executable": sys.executable}
	started = time.monotonic()
	try:
		o = occ()
	except Exception as e:
		report["occt"] = {"ok": False, "message": f"Cannot import the OCP binding: {e}"}
		return report
	binding = package_version("cadquery-ocp")
	report["occt"] = {
		"ok": True,
		"binding": f"cadquery-ocp {binding}" if binding else "OCP",
		# cadquery-ocp X.Y.Z.n wraps OCCT X.Y.Z
		"version": ".".join(binding.split(".")[:3]) if binding else None,
		"import_seconds": round(time.monotonic() - started, 2),
	}
	try:
		box = make_solid(o, {"op": "box", "origin": [2500000.0, -2900000.0, 1500.0], "size": [100, 50, 20]})
		hole = make_solid(o, {"op": "cylinder", "base": [2500050.0, -2899975.0, 1490.0], "radius": 10, "height": 40})
		cut = o["BRepAlgoAPI"].BRepAlgoAPI_Cut(box, hole).Shape()
		volume = measures(o, cut)["volume"]
		expected = 100 * 50 * 20 - math.pi * 10 * 10 * 20
		report["self_test"] = {"ok": check(o, cut) and abs(volume - expected) < 1e-6, "volume": volume, "expected": expected}
	except Exception as e:
		report["self_test"] = {"ok": False, "message": str(e)}
	report["libraries"] = {name: package_version(name) for name in ("ezdxf", "numpy", "scipy", "vtk", "pythoncdt", "matplotlib", "laspy", "lazrs")}
	workspace = request.get("workspace")
	if workspace:
		try:
			probe = inside(workspace, os.path.join(workspace, "probe.txt"))
			with open(probe, "w") as f:
				f.write("ok")
			os.remove(probe)
			report["workspace"] = {"ok": True}
		except Exception as e:
			report["workspace"] = {"ok": False, "message": str(e)}
	return report


def section(request):
	"""Curves where a plane cuts stored solids: [{key, polylines: [[x, y, z], …]}] (edges sampled finely)."""
	from OCP.BRepAlgoAPI import BRepAlgoAPI_Section
	from OCP.gp import gp_Pln

	o = occ()
	workspace = request["workspace"]
	plane = gp_Pln(point(o, request["origin"]), direction(o, request["normal"]))
	out = []
	for item in request.get("items") or []:
		shape = read_brep(o, workspace, item["brep"])
		algo = BRepAlgoAPI_Section(shape, plane, True)
		lines = []
		explorer = o["TopExp_Explorer"](algo.Shape(), o["TopAbs_EDGE"])
		while explorer.More():
			edge = o["TopoDS"].Edge_s(explorer.Current())
			curve = o["BRepAdaptor_Curve"](edge)
			sampler = o["GCPnts_QuasiUniformDeflection"](curve, max(deflection_for(bbox(o, shape)) / 4, 1e-4))
			if sampler.IsDone() and sampler.NbPoints() > 1:
				lines.append([[sampler.Value(i).X(), sampler.Value(i).Y(), sampler.Value(i).Z()] for i in range(1, sampler.NbPoints() + 1)])
			explorer.Next()
		out.append({"key": item["key"], "polylines": lines})
	return {"items": out}


def step_read(request):
	"""Solids of a STEP file, each as a B-Rep + display mesh (like build)."""
	from OCP.IFSelect import IFSelect_RetDone
	from OCP.STEPControl import STEPControl_Reader

	o = occ()
	workspace = request["workspace"]
	reader = STEPControl_Reader()
	if reader.ReadFile(inside(workspace, request["input"])) != IFSelect_RetDone:
		raise ValueError("The STEP file could not be read.")
	reader.TransferRoots()
	shape = reader.OneShape()
	scale = float(request.get("unit_scale") or 1.0)
	if scale != 1.0:
		from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
		from OCP.gp import gp_Trsf

		t = gp_Trsf()
		t.SetScale(o["gp_Pnt"](0, 0, 0), scale)
		shape = BRepBuilderAPI_Transform(shape, t, True).Shape()
	results = []
	explorer = o["TopExp_Explorer"](shape, o["TopAbs_SOLID"])
	n = 0
	while explorer.More():
		solid = explorer.Current()
		valid = check(o, solid)
		box = bbox(o, solid)
		entry = {"key": str(n), "valid": valid, "bbox": box, **measures(o, solid)}
		path, digest = write_brep(o, solid, workspace, f"step_{n}")
		mesh_path = inside(workspace, os.path.join(workspace, "outputs", f"step_{n}.cadmesh"))
		entry.update({"brep": path, "checksum": digest, "mesh": mesh_path, "mesh_info": tessellate(o, solid, mesh_origin(box), mesh_path, deflection_for(box))})
		results.append(entry)
		n += 1
		explorer.Next()
		emit(progress=min(99, n))
	if not results:
		raise ValueError("The STEP file has no solids.")
	return {"items": results}


def step_write(request):
	from OCP.BRep import BRep_Builder
	from OCP.IFSelect import IFSelect_RetDone
	from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
	from OCP.TopoDS import TopoDS_Compound

	o = occ()
	workspace = request["workspace"]
	builder = BRep_Builder()
	compound = TopoDS_Compound()
	builder.MakeCompound(compound)
	for path in request["breps"]:
		builder.Add(compound, read_brep(o, workspace, path))
	writer = STEPControl_Writer()
	writer.Transfer(compound, STEPControl_AsIs)
	out = inside(workspace, request["out"])
	if writer.Write(out) != IFSelect_RetDone:
		raise ValueError("The STEP file could not be written.")
	return {"out": out, "solids": len(request["breps"])}


COMMANDS = {
	"health": health,
	"build": build,
	"validate": validate,
	"tessellate": tessellate_files,
	"offset": offset,
	"section": section,
	"step_read": step_read,
	"step_write": step_write,
}


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
