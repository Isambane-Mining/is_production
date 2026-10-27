"""Mining design (Phase 4): design standards, open-pit bench generation, haul roads, mining blocks, design validation
and design exports.

Geometry of plan outlines (offsets of pit rings, block grids, distances checked by the validation) is done here with
Shapely, which the bench already has for the GIS module. Anything on a TIN (pit and road surfaces, daylight lines,
cut/fill, block volumes) runs in the isolated CAD worker (surface_worker.py / mining_worker.py) as a job.

Jobs (CAD Processing Job type *Mining Design*) only compute: the workstation then adds the results to the model as one
undoable edit — the same pattern as the Surface jobs — so nothing is written to the model until the user saves.

Design entities carry their meaning in CAD Entity.metadata, which the validation reads back:
  pit_design    {stage, kind: floor|toe|crest|daylight|ramp|surface|post_mining, bench, elevation, standard, run}
  haul_road     {name, width, role: centreline|edge|daylight, standard, min_k} (a ramp's centreline is one too)
  mining_block  {block_id, bench_bottom, bench_top, row, col, volume, material, sequence, direction, upper_ref, …}
  drill_hole    {pattern, hole_id, role: hole|collar, row, number, burden, spacing, diameter_mm, inclination, dip, …}

Drill-and-blast is limited to the geometric layout of holes and its validation (spec §12.4): no explosive loading.
"""

import json
import math
import os
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from is_production.cad_engineering.services import cad_job_service, cad_model_service, cad_revision_service, cad_surface_service, cad_worker_service
from is_production.cad_engineering.services import cad_storage_service as storage

STANDARD = "CAD Design Standard"
VALIDATION = "CAD Design Validation"
ENTITY = "CAD Entity"
OPERATIONS = {"pit", "stages", "road", "blocks", "subdivide", "drill", "reconcile", "geology", "block_geology", "pit_shells"}
MAX_HOLES = 20000
MAX_BENCHES = 200
MAX_BLOCKS = 20000
RULES = (
	"bench_height", "bench_reference_elevation", "face_angle", "maximum_slope_angle", "berm_width", "minimum_bench_width",
	"geotechnical_berm_width", "geotechnical_berm_every", "minimum_floor_width", "road_width", "minimum_road_width",
	"maximum_gradient", "minimum_turning_radius", "vertical_curve_length", "minimum_k_value", "normal_crossfall", "maximum_superelevation", "cut_batter_angle",
	"fill_batter_angle", "block_length", "block_width", "mining_direction", "design_tolerance", "angle_tolerance", "drill_burden", "drill_spacing",
	"drill_pattern", "drill_crest_standoff", "drill_hole_diameter", "drill_subdrill", "drill_max_inclination", "drill_spacing_tolerance",
)


# ---------------------------------------------------------------------------
# design standards
# ---------------------------------------------------------------------------


def standard_rules(name):
	doc = frappe.get_doc(STANDARD, name)
	return {"name": doc.name, "standard_code": doc.standard_code, "standard_name": doc.standard_name, "version": doc.version, "status": doc.status, **{f: doc.get(f) for f in RULES}}


def model_standard(model):
	name = frappe.db.get_value("CAD Model", model, "design_standard")
	return standard_rules(name) if name else None


def list_standards():
	return frappe.get_list(
		STANDARD,
		filters={"status": ["in", ["Active", "Draft"]]},
		fields=["name", "standard_code", "standard_name", "version", "status", "standard_type", "applicable_location"],
		order_by="standard_code asc, version desc",
		limit_page_length=500,
	)


def set_standard(model, standard):
	doc = frappe.get_doc("CAD Model", model)
	doc.check_permission("write")
	if standard:
		status = frappe.db.get_value(STANDARD, standard, "status")
		if status != "Active":
			frappe.throw(_("Only an Active design standard can be used ({0} is {1}).").format(standard, _(status or "missing")))
	doc.db_set("design_standard", standard or None)
	return model_standard(model)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def shapely():
	import shapely as sh
	from shapely import geometry

	return sh, geometry


def entity(model, entity_id):
	row = frappe.db.get_value(ENTITY, {"cad_model": model, "entity_id": entity_id}, ["entity_id", "entity_type", "geometry", "geometry_ref", "metadata"], as_dict=True)
	if not row:
		frappe.throw(_("{0} is not a saved entity of {1}. Save first.").format(entity_id, model))
	row.geometry = frappe.parse_json(row.geometry) or {}
	row.metadata = frappe.parse_json(row.metadata) or {}
	return row


def polygon_of(model, entity_id):
	"""A closed outline as a Shapely polygon and its elevation (first vertex)."""
	_sh, g = shapely()
	row = entity(model, entity_id)
	loop = cad_surface_service.loop_of(row.entity_type, row.geometry)
	z = 0.0
	if row.entity_type == "Polyline":
		z = flt(row.geometry["points"][0][2]) if len(row.geometry["points"][0]) > 2 else 0.0
	elif row.geometry.get("center"):
		z = flt(row.geometry["center"][2]) if len(row.geometry["center"]) > 2 else 0.0
	poly = g.Polygon(loop)
	if not poly.is_valid:
		poly = poly.buffer(0)
	if poly.is_empty or poly.area <= 0:
		frappe.throw(_("{0} does not enclose an area.").format(entity_id))
	if poly.geom_type == "MultiPolygon":
		poly = max(poly.geoms, key=lambda p: p.area)
	return poly, z


def ring_points(polygon, z):
	"""Exterior of a polygon as a counter-clockwise ring of [x, y, z] (no repeated closing point)."""
	from shapely.geometry.polygon import orient

	coords = list(orient(polygon, 1.0).exterior.coords)[:-1]
	return [[float(x), float(y), float(z)] for x, y in coords]


def surface_name(model, ref):
	row = frappe.db.get_value(ENTITY, {"cad_model": model, "geometry_ref": ref, "entity_type": "Surface"}, "geometry")
	if not row:
		frappe.throw(_("Surface {0} is not a saved surface of {1}.").format((ref or "")[:12], model))
	return (frappe.parse_json(row) or {}).get("name") or ref[:12]


def surface_stats(model, ref):
	return (cad_surface_service.sidecar(model, ref) or {}).get("stats") or {}


def direction_vector(model, degrees):
	"""Plan unit vector of an azimuth (degrees from north, clockwise) in model coordinates (westing/southing aware)."""
	a = math.radians(flt(degrees))
	doc = frappe.get_doc("CAD Model", model)
	flip = -1.0 if cad_model_service.plan_rotated(doc) else 1.0
	return (flip * math.sin(a), flip * math.cos(a))


def write_input(workspace, name, data):
	path = os.path.join(workspace, "inputs", name)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w") as f:
		json.dump(data, f)
	return path


def surface(job, request, log):
	return cad_job_service.run_worker(job, request, log, script=cad_worker_service.SURFACE_WORKER)


def mining(job, request, log):
	return cad_job_service.run_worker(job, request, log, script=cad_worker_service.MINING_WORKER)


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


def start(model, operation, params):
	if operation not in OPERATIONS:
		frappe.throw(_("Unknown mining design operation {0}.").format(operation))
	if not frappe.db.get_single_value("CAD Settings", "enable_mining_design_tools"):
		frappe.throw(_("The mining design tools are switched off (CAD Settings)."))
	params = frappe.parse_json(params) if isinstance(params, str) else params or {}
	return cad_job_service.start_job("Mining Design", model, cad_revision_service.working_revision(model), {"operation": operation, **params})


def run(job, parameters, workspace, log):
	operation = parameters["operation"]
	from is_production.cad_engineering.services.cad_geology_service import run_block_geology, run_geology
	from is_production.cad_engineering.services.cad_optimisation_service import run_pit_shells
	from is_production.cad_engineering.services.cad_planning_service import run_reconcile

	handlers = {"pit": run_pit, "stages": run_stages, "road": run_road, "blocks": run_blocks, "subdivide": run_subdivide, "drill": run_drill, "reconcile": run_reconcile, "geology": run_geology, "block_geology": run_block_geology, "pit_shells": run_pit_shells}
	result, message = handlers[operation](job, parameters, workspace, log)
	job.db_set("results", json.dumps(result))
	return message


# ---------------------------------------------------------------------------
# open pit: benches from a floor outline
# ---------------------------------------------------------------------------


def bench_rings(floor, z0, top, height, face_angle, berm, geotech_width=0, geotech_every=0, join="round"):
	"""Toe and crest rings of a pit from its floor outward and upward: each bench face rises `height` at
	`face_angle`, each berm is `berm` wide (a geotechnical berm every `geotech_every` benches). Stops at the first
	crest at or above `top`. Returns [(kind, bench, z, polygon)], the floor first.

	Every ring is the floor offset by the whole distance out to it (one offset, not an offset of the ring below):
	the same outline, but offsets of offsets multiply the vertices of rounded corners."""
	join_style = "mitre" if join == "mitre" else "round"

	def ring(distance):
		return floor.buffer(distance, join_style=join_style, mitre_limit=4.0, quad_segs=16) if distance else floor

	face = height / math.tan(math.radians(face_angle))
	rings = [("floor", 0, z0, floor)]
	out, z, k = 0.0, z0, 0
	while z < top - 1e-9:
		k += 1
		if k > MAX_BENCHES:
			frappe.throw(_("More than {0} benches: check the top elevation and the bench height.").format(MAX_BENCHES))
		out += face
		z += height
		rings.append(("crest", k, z, ring(out)))
		if z >= top - 1e-9:
			break
		out += geotech_width if geotech_every and geotech_width and k % cint(geotech_every) == 0 else berm
		rings.append(("toe", k + 1, z, ring(out)))
	return rings


def run_pit(job, p, workspace, log):
	result, _tins = build_pit(job, job.cad_model, p, workspace, log)
	s = result["summary"]
	message = _("Pit {0}: {1} benches, {2} m deep, overall slope {3}°.").format(result["stage"], s["benches"], round(s["depth"], 2), round(s["overall_slope_angle"], 1))
	if result.get("ramp"):
		message += " " + _("Ramp {0} m at {1} %.").format(round(result["ramp"]["length"], 1), round(result["ramp"]["max_grade"], 2))
	return result, message


def pit_rules(p, std):
	height = flt(p.get("bench_height") or std.get("bench_height"))
	face_angle = flt(p.get("face_angle") or std.get("face_angle"))
	berm = flt(p.get("berm_width") if p.get("berm_width") not in (None, "") else std.get("berm_width"))
	if height <= 0 or not 0 < face_angle < 90 or berm < 0:
		frappe.throw(_("Give a bench height, a face angle between 0° and 90° and a berm width (or choose a design standard)."))
	return height, face_angle, berm


def build_pit(job, model, p, workspace, log, tag=""):
	"""One pit (stage) from its floor outline: rings, pit surface, daylight, post-mining surface, volume and, when
	asked, an in-pit ramp. Returns (result, tins) — tins: the workspace TINs {pit, final} (final: the post-mining
	surface, or the pit itself without a ground surface; with the ramp in either)."""
	sh, g = shapely()
	std = model_standard(model) or {}
	height, face_angle, berm = pit_rules(p, std)
	stage = (p.get("stage") or "Stage 1").strip()[:40]
	floor, z0 = polygon_of(model, p["floor"])
	if p.get("floor_elevation") not in (None, ""):
		z0 = flt(p["floor_elevation"])
	if p.get("snap_to_benches"):
		ref = flt(std.get("bench_reference_elevation"))
		z0 = ref + round((z0 - ref) / height) * height
	if flt(p.get("floor_offset")):
		floor = floor.buffer(flt(p["floor_offset"]), join_style="round", quad_segs=8)
		if floor.is_empty:
			frappe.throw(_("{0}: shrinking the floor by {1} m leaves nothing.").format(stage, -flt(p["floor_offset"])))
		if floor.geom_type != "Polygon":
			floor = max(floor.geoms, key=lambda x: x.area)
	ground_ref = p.get("ground") or None
	ground_stats = surface_stats(model, ground_ref) if ground_ref else {}
	top = flt(p.get("top_elevation")) if p.get("top_elevation") not in (None, "") else None
	if top is None:
		if not ground_stats:
			frappe.throw(_("Give a top elevation or choose the ground surface."))
		top = flt(ground_stats.get("max_elevation"))
	if top <= z0:
		frappe.throw(_("{0}: the top elevation {1} is not above the floor at {2}.").format(stage, round(top, 3), round(z0, 3)))
	rings = bench_rings(floor, z0, top, height, face_angle, berm, flt(p.get("geotech_width") or std.get("geotechnical_berm_width")), cint(p.get("geotech_every") or std.get("geotechnical_berm_every")), p.get("corners") or "round")
	log.append(_("{0}: {1} benches from {2} to {3}.").format(stage, max(r[1] for r in rings), round(z0, 3), round(rings[-1][2], 3)))

	# the pit surface: every ring a breakline, the top crest the boundary
	closed = [ring_points(poly, z) for _k, _b, z, poly in rings]
	data = {"points": [], "breaklines": [pts + pts[:1] for pts in closed], "boundary": [q[:2] for q in closed[-1]], "holes": []}
	built = surface(job, {"command": "build", "workspace": workspace, "name": f"pit{tag}", "input": write_input(workspace, f"pit{tag}.json", data)}, log)
	source = {"operation": "pit", "stage": stage, "floor": p["floor"], "standard": std.get("name")}
	tins = {"pit": built["tin"], "final": built["tin"]}
	result = {
		"stage": stage,
		"standard": std.get("name"),
		"rings": [{"kind": kind, "bench": bench, "elevation": z, "points": ring_points(poly, z)} for kind, bench, z, poly in rings],
		"surface": {"geometry_ref": None, "stats": built["stats"], "source": source},
		"daylight": [],
		"post_mining": None,
		"benches": [],
	}
	# per bench: face (toe → crest) and berm (crest → next toe) widths, measured on the rings as built
	by = {(kind, bench): (z, poly) for kind, bench, z, poly in rings}
	floor_poly = rings[0][3]
	for k in range(1, max(r[1] for r in rings) + 1):
		toe = by.get(("toe", k)) or (by[("floor", 0)] if k == 1 else None)
		crest = by.get(("crest", k))
		nxt = by.get(("toe", k + 1))
		if not (toe and crest):
			continue
		face_w = crest[1].exterior.distance(toe[1].exterior)
		row = {"bench": k, "toe": toe[0], "crest": crest[0], "height": crest[0] - toe[0], "face_width": face_w, "face_angle": math.degrees(math.atan2(crest[0] - toe[0], face_w)) if face_w > 0 else 90.0}
		if nxt:
			row["berm_width"] = nxt[1].exterior.distance(crest[1].exterior)
		result["benches"].append(row)
	top_crest = rings[-1][3]
	run_ = top_crest.exterior.distance(floor_poly.exterior)
	depth = rings[-1][2] - z0
	result["summary"] = {
		"floor_elevation": z0,
		"top_elevation": rings[-1][2],
		"depth": depth,
		"benches": len(result["benches"]),
		"overall_slope_angle": math.degrees(math.atan2(depth, run_)) if run_ > 0 else 90.0,
		"floor_area": floor_poly.area,
		"footprint_area": top_crest.area,
	}

	ground, largest = None, None
	if ground_ref:
		ground = cad_surface_service.stage_tin(model, ground_ref, workspace, "ground")
		cut = surface(job, {"command": "intersect", "workspace": workspace, "base": ground, "comparison": built["tin"]}, log)
		result["daylight"] = cut["lines"]
		loops = [line["points"] for line in cut["lines"] if line.get("closed") and len(line["points"]) >= 3]
		if (p.get("post_mining", True) or p.get("ramp")) and loops:
			largest = max(loops, key=lambda pts: abs(g.Polygon([q[:2] for q in pts]).area))
			merged = mining(job, {"command": "merge", "workspace": workspace, "name": f"post_mining{tag}", "inside": built["tin"], "outside": ground, "loop": largest}, log)
			tins["final"] = merged["tin"]
			result["post_mining"] = {"stats": merged["stats"]}
		elif p.get("post_mining", True) or p.get("ramp"):
			log.append(_("The pit does not meet the ground in a closed line; no post-mining surface was made."))
	if p.get("ramp"):
		result["ramp"], tins["final"] = pit_ramp(job, p, workspace, log, rings, std, face_angle, tins["final"], tag, largest)
		if not result["post_mining"]:
			tins["pit"] = tins["final"]  # without the ground, the pit surface carries the ramp
			result["surface"]["stats"] = result["ramp"].pop("stats")
		else:
			result["post_mining"]["stats"] = result["ramp"].pop("stats")
		result["ramp"].pop("stats", None)
	if ground:
		vol = surface(job, {"command": "volume", "workspace": workspace, "base": ground, "comparison": tins["final"]}, log)
		result["volume"] = {k: vol[k] for k in ("cut", "fill", "net", "plan_area", "valid")}

	# store the surfaces
	result["surface"]["geometry_ref"] = cad_surface_service.store_tin(job, workspace, tins["pit"], result["surface"]["stats"], source, log=log)
	if result["post_mining"]:
		pm_source = {"operation": "post_mining", "stage": stage, "ground": ground_ref, "standard": std.get("name"), "ramp": bool(p.get("ramp"))}
		ref = cad_surface_service.store_tin(job, workspace, tins["final"], result["post_mining"]["stats"], pm_source, log=log)
		result["post_mining"].update({"geometry_ref": ref, "source": pm_source})
		if not p.get("post_mining", True) and not p.get("ramp"):
			result["post_mining"] = None
	return result, tins


# ---------------------------------------------------------------------------
# in-pit ramps
# ---------------------------------------------------------------------------


def interp(x, xs, ys):
	if x <= xs[0]:
		return ys[0]
	for (x0, y0), (x1, y1) in zip(zip(xs, ys), zip(xs[1:], ys[1:])):
		if x <= x1:
			return y0 + (y1 - y0) * (x - x0) / (x1 - x0) if x1 > x0 else y1
	return ys[-1]


def ramp_centreline(rings, width, gradient, start_direction, clockwise, step=2.0, z_start=None):
	"""The centreline of a ramp spiralling down the pit wall from the top crest to the floor at `gradient` (a
	fraction, along the horizontal chainage). At each elevation the ramp's inner edge is on the wall line — the floor
	edge, the middle of every berm and the top crest, joined — so the ramp is cut into the benches above that line and
	stands on rock left in place below it. `start_direction`: plan unit vector from the floor's centre to the entry;
	`z_start`: the entry elevation (where the pit meets the ground; the top crest by default)."""
	_sh, g = shapely()
	from shapely.geometry.polygon import orient

	floor, z0 = rings[0][3], rings[0][2]
	at = {}
	for kind, _bench, z, poly in rings[1:]:
		at.setdefault(z, {})[kind] = poly.exterior.distance(floor.exterior)
	zs, ds = [z0], [0.0]
	for z in sorted(at):
		d = at[z]
		zs.append(z)
		ds.append((d["crest"] + d["toe"]) / 2 if "toe" in d else d["crest"])
	half = width / 2

	def ring(z):
		return orient(floor.buffer(interp(z, zs, ds) + half, quad_segs=16), 1.0).exterior  # anticlockwise

	z = min(zs[-1], z_start) if z_start is not None else zs[-1]
	r = ring(z)
	c = floor.centroid
	t = r.project(g.Point(c.x + start_direction[0] * 1e6, c.y + start_direction[1] * 1e6))
	q = r.interpolate(t)
	sign = -1.0 if clockwise else 1.0
	points = [[q.x, q.y, z]]
	while z > z0 + 1e-6:
		ahead = r.interpolate((t + sign * step) % r.length)
		nz = z
		for _i in range(3):  # the elevation from the horizontal distance actually travelled (the ring shrinks)
			nz = max(z0, z - gradient * math.hypot(ahead.x - q.x, ahead.y - q.y))
			r2 = ring(nz)
			t2 = r2.project(ahead)
			ahead = r2.interpolate(t2)
		r, t, q, z = r2, t2, ahead, nz
		points.append([q.x, q.y, z])
		if len(points) > 50000:
			frappe.throw(_("The ramp would be longer than 100 km: check the gradient."))
	return points


def pit_ramp(job, p, workspace, log, rings, std, face_angle, base_tin, tag, daylight=None):
	"""Cut a ramp into the pit: a road on the spiral centreline with batters at the face angle onto `base_tin` (the
	post-mining surface, or the pit), merged into it exactly. Returns (ramp result, the merged TIN)."""
	_sh, g = shapely()
	width = flt(p.get("ramp_width")) or flt(std.get("road_width")) or 25
	gradient = flt(p.get("ramp_gradient")) or flt(std.get("maximum_gradient")) or 10
	if not 0 < gradient < 50:
		frappe.throw(_("Give a ramp gradient between 0 and 50 %."))
	direction = direction_vector(job.cad_model, p.get("ramp_bearing") or 0)
	z_start = None
	if daylight:  # enter where the daylight line is in the entry direction
		c = rings[0][3].centroid
		z_start = max(daylight, key=lambda q: (q[0] - c.x) * direction[0] + (q[1] - c.y) * direction[1])[2]
	centre = ramp_centreline(rings, width, gradient / 100, direction, (p.get("ramp_direction") or "clockwise") == "clockwise", z_start=z_start)
	crossfall = flt(p.get("ramp_crossfall")) if p.get("ramp_crossfall") not in (None, "") else flt(std.get("normal_crossfall"))
	cut_batter = flt(p.get("ramp_batter")) or face_angle
	options = {"width": width, "fit_radius": 0, "crossfall": crossfall, "superelevation": 0, "cut_batter": cut_batter, "fill_batter": cut_batter, "vertical": "keep", "step": 2.0}
	road = mining(job, {"command": "road", "workspace": workspace, "name": f"ramp{tag}", "centreline": centre, "options": options, "ground": base_tin}, log)
	lines = road["lines"]
	loop = [q[:2] for q in lines["left_daylight"] + lines["right_daylight"][::-1]]
	if not g.Polygon(loop).is_valid:
		frappe.throw(_("The ramp's laps (with their batters) overlap in plan: the pit is too small for a {0} m ramp at {1} % — use a wider floor, a steeper or narrower ramp.").format(width, gradient))
	merged = mining(job, {"command": "merge", "workspace": workspace, "name": f"ramp_pit{tag}", "inside": road["tin"], "outside": base_tin, "loop": loop}, log)
	laps = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(centre, centre[1:]))
	# measured on the surface as stored (the merge), not the bare ramp TIN: the two differ along the ramp's
	# outline, whose chords cross the benches between daylight points
	vol = surface(job, {"command": "volume", "workspace": workspace, "base": base_tin, "comparison": merged["tin"]}, log)
	ramp = {
		"width": width,
		"gradient": gradient,
		"length": road["length"],
		"max_grade": road["max_grade"],
		"start": centre[0],
		"end": centre[-1],
		"lines": lines,
		"extra_cut": vol.get("cut"),  # rock taken out of the wall for the ramp
		"left_in_place": vol.get("fill"),  # rock left under the ramp
		"plan_length": laps,
		"stats": merged["stats"],
	}
	log.append(_("Ramp: {0} m at {1} %, {2} m³ more cut, {3} m³ left in place.").format(round(road["length"], 1), round(road["max_grade"], 2), round(flt(vol.get("cut"))), round(flt(vol.get("fill")))))
	return ramp, merged["tin"]


# ---------------------------------------------------------------------------
# pit stages (pushbacks): a run of nested pits with incremental volumes
# ---------------------------------------------------------------------------

MAX_STAGES = 12


def run_stages(job, p, workspace, log):
	"""Several pits from one floor outline, each with its own floor elevation and floor offset (a deeper and/or wider
	pit each time), every one a full pit design (benches, ramp, post-mining surface). Between consecutive stages:
	the incremental volume (the earlier post-mining surface down to the later one), whether the later stage contains
	the earlier one, and the narrowest pushback at the surface."""
	_sh, g = shapely()
	model = job.cad_model
	stages = p.get("stages") or []
	if not 1 <= len(stages) <= MAX_STAGES:
		frappe.throw(_("Give 1 to {0} stages.").format(MAX_STAGES))
	if not p.get("ground"):
		frappe.throw(_("Choose the ground surface: stage volumes are measured from it."))
	std = model_standard(model) or {}
	min_width = flt(p.get("min_pushback_width")) if p.get("min_pushback_width") not in (None, "") else flt(std.get("minimum_floor_width"))
	common = {k: v for k, v in p.items() if k not in ("stages", "operation")}
	out, table, previous = [], [], None
	for i, s in enumerate(stages):
		q = {**common, **{k: v for k, v in s.items() if v not in (None, "")}, "post_mining": True}
		job.db_set("progress", round(100 * i / len(stages)), update_modified=False)
		r, tins = build_pit(job, model, q, workspace, log, tag=f"_{i + 1}")
		if not r["post_mining"]:
			frappe.throw(_("{0} does not meet the ground in a closed line.").format(r["stage"]))
		total = flt((r.get("volume") or {}).get("cut"))
		row = {"stage": r["stage"], "floor_elevation": r["summary"]["floor_elevation"], "depth": r["summary"]["depth"], "benches": r["summary"]["benches"], "volume": total, "increment": total, "undercut": 0.0, "pushback_width": None, "contains_previous": True}
		outline = max((line["points"] for line in r["daylight"] if line.get("closed")), key=lambda pts: abs(g.Polygon([x[:2] for x in pts]).area), default=None)
		poly = g.Polygon([x[:2] for x in outline]).buffer(0) if outline else None
		if previous:
			inc = surface(job, {"command": "volume", "workspace": workspace, "base": previous["tin"], "comparison": tins["final"]}, log)
			row["increment"], row["undercut"] = inc["cut"], inc["fill"]
			row["contains_previous"] = inc["fill"] <= max(1.0, 1e-6 * total)
			if poly is not None and previous["poly"] is not None:
				row["pushback_width"] = poly.exterior.distance(previous["poly"].exterior) if poly.contains(previous["poly"]) else 0.0
				row["pushback_ok"] = not min_width or row["pushback_width"] >= min_width - flt(std.get("design_tolerance"))
		previous = {"tin": tins["final"], "poly": poly}
		out.append(r)
		table.append(row)
	run = frappe.generate_hash(length=10)
	problems = [x["stage"] for x in table if not x["contains_previous"] or x.get("pushback_ok") is False]
	message = _("{0} stages, {1} m³ in all.").format(len(out), round(table[-1]["volume"]))
	if problems:
		message += " " + _("Check: {0}.").format(", ".join(problems))
	return {"run": run, "stages": out, "table": table, "min_pushback_width": min_width}, message


# ---------------------------------------------------------------------------
# haul roads
# ---------------------------------------------------------------------------


def centreline_points(model, p):
	if p.get("centreline_points"):
		return [[flt(c) for c in q][:3] for q in p["centreline_points"]]
	row = entity(model, p["centreline"])
	if row.entity_type == "Line":
		return [row.geometry["p0"], row.geometry["p1"]]
	if row.entity_type == "Polyline" and not row.geometry.get("closed"):
		return cad_surface_service.densify("Polyline", row.geometry)
	frappe.throw(_("The road centreline must be a line or an open polyline."))


def pvi_list(value):
	"""Vertical intersection points from [[chainage, elevation], …] or text "0:1500, 250:1480; 600 1452"."""
	if not value:
		return []
	if isinstance(value, str):
		value = [re.split(r"[:\s]+", item.strip()) for item in re.split(r"[,;\n]+", value) if item.strip()]
	try:
		return [[flt(c), flt(e)] for c, e in value]
	except (TypeError, ValueError):
		frappe.throw(_("Give the vertical intersection points as chainage:elevation pairs, e.g. 0:1500, 250:1480."))


def run_road(job, p, workspace, log):
	model = job.cad_model
	std = model_standard(model) or {}
	name = (p.get("name") or "Haul Road").strip()[:60]

	def rule(key, std_key):
		return flt(p[key]) if p.get(key) not in (None, "") else flt(std.get(std_key))

	options = {
		"width": rule("width", "road_width") or 25,
		"fit_radius": rule("fit_radius", "minimum_turning_radius"),
		"min_radius": flt(std.get("minimum_turning_radius")) or rule("fit_radius", "minimum_turning_radius"),
		"crossfall": rule("crossfall", "normal_crossfall"),
		"superelevation": rule("superelevation", "maximum_superelevation"),
		"cut_batter": rule("cut_batter", "cut_batter_angle") or 45,
		"fill_batter": rule("fill_batter", "fill_batter_angle") or 34,
		"vertical": p.get("vertical") or "keep",
		"start_elevation": p.get("start_elevation"),
		"end_elevation": p.get("end_elevation"),
		"gradient": p.get("gradient"),
		"step": flt(p.get("step")) or 2.0,
		"vertical_curve_length": rule("vertical_curve_length", "vertical_curve_length"),
		"pvis": pvi_list(p.get("pvis")),
	}
	request = {"command": "road", "workspace": workspace, "name": "road", "centreline": centreline_points(model, p), "options": options}
	ground_ref = p.get("ground") or None
	if ground_ref:
		request["ground"] = cad_surface_service.stage_tin(model, ground_ref, workspace, "ground")
	result = mining(job, request, log)
	source = {"operation": "road", "name": name, "centreline": p.get("centreline"), "standard": std.get("name"), "options": options}
	ref = cad_surface_service.store_tin(job, workspace, result.pop("tin"), result["stats"], source, log=log)
	result.update({"name": name, "standard": std.get("name"), "options": options, "surface": {"geometry_ref": ref, "stats": result["stats"], "source": source}})
	if ground_ref and result.get("volume"):
		names = {ground_ref: surface_name(model, ground_ref), ref: name}
		result["volume"]["record"] = cad_surface_service.record_volume(job, {}, [ground_ref, ref], names, {}, result["volume"])
	limit = flt(std.get("maximum_gradient"))
	message = _("{0}: {1} m, steepest {2} %").format(name, round(result["length"], 1), round(result["max_grade"], 2))
	if limit and result["max_grade"] > limit + 1e-9:
		message += " " + _("(above the standard's {0} %)").format(limit)
	if result.get("vertical_curves"):
		message += ", " + _("{0} vertical curves, K ≥ {1}").format(len(result["vertical_curves"]), round(result["min_k"], 1))
	return result, message


# ---------------------------------------------------------------------------
# mining blocks
# ---------------------------------------------------------------------------


def bench_slabs(lower_min, upper_max, height, ref):
	k0 = math.floor((lower_min - ref) / height)
	k1 = math.ceil((upper_max - ref) / height)
	if k1 - k0 > MAX_BENCHES:
		frappe.throw(_("More than {0} benches between the surfaces: check the bench height.").format(MAX_BENCHES))
	return [(ref + k * height, ref + (k + 1) * height) for k in range(k0, k1)]


def grid_cells(boundary, along, length, width, origin=None):
	"""Cells of a grid aligned with `along` (unit vector) over the boundary, clipped to it: (row, col, part, polygon).
	The grid lines are at whole multiples of the block size (the same for every bench), or start at `origin` (u, v)."""
	_sh, g = shapely()
	ux, uy = along
	vx, vy = -uy, ux
	coords = list(boundary.exterior.coords)
	us = [x * ux + y * uy for x, y in coords]
	vs = [x * vx + y * vy for x, y in coords]
	u0, v0 = origin or (math.floor(min(us) / length) * length, math.floor(min(vs) / width) * width)
	rows = max(1, int(math.ceil((max(us) - u0) / length - 1e-9)))
	cols = max(1, int(math.ceil((max(vs) - v0) / width - 1e-9)))
	if rows * cols > MAX_BLOCKS:
		frappe.throw(_("{0} blocks per bench: use bigger blocks.").format(rows * cols))
	cells = []
	for i in range(rows):
		for j in range(cols):
			a, b = u0 + i * length, v0 + j * width
			corners = [(a, b), (a + length, b), (a + length, b + width), (a, b + width)]
			rect = g.Polygon([(u * ux + v * vx, u * uy + v * vy) for u, v in corners])
			piece = rect.intersection(boundary)
			if piece.is_empty or piece.area < 1e-6:
				continue
			for n, part in enumerate(getattr(piece, "geoms", [piece])):
				if part.geom_type == "Polygon" and part.area > 1e-6:
					cells.append((i + 1, j + 1, n, part))
	return cells


def block_volume_job(job, workspace, log, model, upper_ref, lower_ref, cells, slabs):
	request = {
		"command": "block_volumes",
		"workspace": workspace,
		"upper": cad_surface_service.stage_tin(model, upper_ref, workspace, "upper"),
		"lower": cad_surface_service.stage_tin(model, lower_ref, workspace, "lower"),
		"cells": [{"key": key, "polygon": [list(c) for c in list(poly.exterior.coords)[:-1]]} for key, poly in cells],
		"slabs": [list(s) for s in slabs],
	}
	return mining(job, request, log)


def block_id(prefix, bottom, row, col, part):
	return f"{prefix}{int(round(bottom))}-{row:03d}-{col:03d}" + (f"-{part + 1}" if part else "")


def run_blocks(job, p, workspace, log):
	model = job.cad_model
	std = model_standard(model) or {}
	boundary, _z = polygon_of(model, p["boundary"])
	upper_ref, lower_ref = p["upper"], p["lower"]
	height = flt(p.get("bench_height") or std.get("bench_height"))
	ref = flt(p.get("bench_reference_elevation")) if p.get("bench_reference_elevation") not in (None, "") else flt(std.get("bench_reference_elevation"))
	length = flt(p.get("block_length") or std.get("block_length"))
	width = flt(p.get("block_width") or std.get("block_width"))
	direction = flt(p.get("direction")) if p.get("direction") not in (None, "") else flt(std.get("mining_direction"))
	if height <= 0 or length <= 0 or width <= 0:
		frappe.throw(_("Give a bench height and a block length and width (or choose a design standard)."))
	upper_stats, lower_stats = surface_stats(model, upper_ref), surface_stats(model, lower_ref)
	slabs = bench_slabs(flt(lower_stats.get("min_elevation")), flt(upper_stats.get("max_elevation")), height, ref)
	along = direction_vector(model, direction)
	cells = grid_cells(boundary, along, length, width)
	keyed = [(f"{row}_{col}_{part}", poly) for row, col, part, poly in cells]
	log.append(_("{0} block outlines × {1} benches.").format(len(keyed), len(slabs)))
	out = block_volume_job(job, workspace, log, model, upper_ref, lower_ref, keyed, slabs)
	min_volume = flt(p.get("min_volume")) if p.get("min_volume") not in (None, "") else 1.0
	prefix = (p.get("prefix") or "B").strip()[:10]
	blocks = []
	for (row, col, part, poly), (key, _poly) in zip(cells, keyed):
		for s, (bottom, top) in enumerate(slabs):
			v = out["volumes"][key][s]
			if v < min_volume:
				continue
			c = poly.centroid
			blocks.append(
				{
					"block_id": block_id(prefix, bottom, row, col, part),
					"bench_bottom": bottom,
					"bench_top": top,
					"row": row,
					"col": col,
					"volume": v,
					"plan_area": poly.area,
					"polygon": ring_points(poly, bottom),
					"along": c.x * along[0] + c.y * along[1],
					"across": -c.x * along[1] + c.y * along[0],
				}
			)
	blocks.sort(key=lambda b: (-b["bench_top"], b["along"], b["across"]))
	for n, b in enumerate(blocks, start=1):
		b["sequence"] = n
	common = {
		"upper_ref": upper_ref,
		"lower_ref": lower_ref,
		"upper": surface_name(model, upper_ref),
		"lower": surface_name(model, lower_ref),
		"direction": direction,
		"block_length": length,
		"block_width": width,
		"standard": std.get("name"),
	}
	total = sum(b["volume"] for b in blocks)
	result = {"blocks": blocks, "common": common, "method": out["method"], "total_volume": total, "benches": len({b["bench_bottom"] for b in blocks})}
	return result, _("{0} blocks on {1} benches, {2} m³ in situ.").format(len(blocks), result["benches"], round(total, 1))


def run_subdivide(job, p, workspace, log):
	"""Split blocks into n × m smaller blocks (along × across their mining direction), volumes recomputed."""
	_sh, g = shapely()
	model = job.cad_model
	n_along, n_across = max(1, cint(p.get("along") or 2)), max(1, cint(p.get("across") or 1))
	groups = {}
	for eid in p.get("blocks") or []:
		row = entity(model, eid)
		meta = row.metadata.get("mining_block") or {}
		if not meta or row.entity_type != "Polyline":
			frappe.throw(_("{0} is not a mining block.").format(eid))
		groups.setdefault((meta["upper_ref"], meta["lower_ref"]), []).append((row, meta))
	replacements = {}
	for (upper_ref, lower_ref), items in groups.items():
		cells, slabs, owners = [], [], []
		for row, meta in items:
			poly = g.Polygon([q[:2] for q in row.geometry["points"]])
			along = direction_vector(model, meta.get("direction") or 0)
			span_u = [x * along[0] + y * along[1] for x, y in poly.exterior.coords]
			span_v = [-x * along[1] + y * along[0] for x, y in poly.exterior.coords]
			length = (max(span_u) - min(span_u)) / n_along
			width = (max(span_v) - min(span_v)) / n_across
			pieces = grid_cells(poly, along, length, width, origin=(min(span_u), min(span_v)))
			slab = (meta["bench_bottom"], meta["bench_top"])
			if slab not in slabs:
				slabs.append(slab)
			for i, j, part, piece in pieces:
				cells.append((f"{row.entity_id}:{i}_{j}_{part}", piece))
				owners.append((row, meta, i, j, part, piece, slabs.index(slab)))
		out = block_volume_job(job, workspace, log, model, upper_ref, lower_ref, cells, slabs)
		for (key, _poly), (row, meta, i, j, part, piece, s) in zip(cells, owners):
			suffix = f".{i}{j}" + (f"-{part + 1}" if part else "")
			replacements.setdefault(row.entity_id, []).append(
				{
					**meta,
					"block_id": meta["block_id"] + suffix,
					"volume": out["volumes"][key][s],
					"plan_area": piece.area,
					"polygon": ring_points(piece, meta["bench_bottom"]),
					"parent": meta["block_id"],
				}
			)
	count = sum(len(v) for v in replacements.values())
	return {"replacements": replacements}, _("{0} blocks split into {1}.").format(len(replacements), count)


# ---------------------------------------------------------------------------
# drill patterns (layout geometry only)
# ---------------------------------------------------------------------------


def pattern_collars(region, row_dir, burden_dir, burden, spacing, staggered):
	"""Collars of a pattern filling `region`: rows along `row_dir`, `burden` apart in `burden_dir` (row 1 at the
	front of the region), holes `spacing` apart in a row, every other row shifted by half a spacing when staggered.
	Returns [(row, number, x, y)]."""
	_sh, g = shapely()
	rx, ry = row_dir
	bx, by = burden_dir
	coords = list(region.exterior.coords)
	us = [x * rx + y * ry for x, y in coords]
	vs = [x * bx + y * by for x, y in coords]
	rows = int(math.floor((max(vs) - min(vs)) / burden + 1e-9)) + 1
	per_row = int(math.floor((max(us) - min(us)) / spacing + 1e-9)) + 2
	if rows * per_row > MAX_HOLES * 4:
		frappe.throw(_("Too many holes for this area: use a larger burden or spacing."))
	inside = region.buffer(1e-6)
	out = []
	for i in range(rows):
		v = min(vs) + i * burden
		shift = spacing / 2 if staggered and i % 2 else 0.0
		n = 0
		for j in range(per_row):
			u = min(us) + shift + j * spacing
			x, y = u * rx + v * bx, u * ry + v * by
			if inside.contains(g.Point(x, y)):
				n += 1
				out.append((i + 1, n, x, y))
	if len(out) > MAX_HOLES:
		frappe.throw(_("{0} holes: at most {1} per pattern.").format(len(out), MAX_HOLES))
	return out


def run_drill(job, p, workspace, log):
	model = job.cad_model
	std = model_standard(model) or {}

	def rule(key, std_key, default=0.0):
		return flt(p[key]) if p.get(key) not in (None, "") else flt(std.get(std_key) or default)

	burden, spacing = rule("burden", "drill_burden"), rule("spacing", "drill_spacing")
	diameter, subdrill = rule("diameter", "drill_hole_diameter"), rule("subdrill", "drill_subdrill")
	standoff = rule("standoff", "drill_crest_standoff")
	inclination = rule("inclination", "", 0.0)
	staggered = (p.get("pattern_type") or std.get("drill_pattern") or "Staggered") == "Staggered"
	if burden <= 0 or spacing <= 0:
		frappe.throw(_("Give the burden and the spacing (or set them in the design standard)."))
	if not 0 <= inclination < 60:
		frappe.throw(_("The hole inclination must be between 0° and 60° from vertical."))
	name = (p.get("name") or "Pattern 1").strip()[:40]
	boundary, z_boundary = polygon_of(model, p["boundary"])
	region = boundary.buffer(-standoff) if standoff else boundary
	if region.is_empty:
		frappe.throw(_("Nothing is left of the blast boundary after the {0} m stand-off.").format(standoff))
	if region.geom_type == "MultiPolygon":
		region = max(region.geoms, key=lambda x: x.area)
	row_az = flt(p.get("row_direction"))
	row_dir = direction_vector(model, row_az)
	burden_dir = direction_vector(model, row_az + 90)  # to the right of the rows
	dip_az = flt(p["dip_direction"]) if p.get("dip_direction") not in (None, "") else row_az + 90
	collars = pattern_collars(region, row_dir, burden_dir, burden, spacing, staggered)
	if not collars:
		frappe.throw(_("No hole fits in the boundary with this burden and spacing."))
	request = {
		"command": "drill_holes",
		"workspace": workspace,
		"collars": [[x, y] for _r, _n, x, y in collars],
		"subdrill": subdrill,
		"inclination": inclination,
		"dip": list(direction_vector(model, dip_az)),
	}
	if p.get("collar_surface"):
		request["collar_surface"] = cad_surface_service.stage_tin(model, p["collar_surface"], workspace, "collar")
	else:
		request["collar_elevation"] = flt(p["collar_elevation"]) if p.get("collar_elevation") not in (None, "") else z_boundary
	if p.get("floor_surface"):
		request["floor_surface"] = cad_surface_service.stage_tin(model, p["floor_surface"], workspace, "floor")
	elif p.get("floor_elevation") not in (None, ""):
		request["floor_elevation"] = flt(p["floor_elevation"])
	else:
		frappe.throw(_("Give the bench floor elevation or a floor surface."))
	placed = mining(job, request, log)["holes"]
	prefix = (p.get("prefix") or name[:1] or "H").strip()[:6].upper()
	holes, problems = [], []
	for (row, number, _x, _y), h in zip(collars, placed):
		hole_id = f"{prefix}{row:02d}-{number:03d}"
		if h["problem"]:
			problems.append(f"{hole_id}: {h['problem']}")
			continue
		holes.append({"hole_id": hole_id, "row": row, "number": number, **{k: h[k] for k in ("collar", "toe", "floor", "length")}})
	if not holes:
		frappe.throw(_("No hole could be placed: {0}").format("; ".join(problems[:5])))
	design = {
		"pattern": name,
		"burden": burden,
		"spacing": spacing,
		"pattern_type": "Staggered" if staggered else "Square",
		"diameter_mm": diameter,
		"subdrill": subdrill,
		"inclination": inclination,
		"dip": dip_az % 360,
		"row_direction": row_az % 360,
		"standoff": standoff,
		"boundary": p["boundary"],
		"standard": std.get("name"),
	}
	lengths = [h["length"] for h in holes]
	floors = [h["floor"] for h in holes]
	collar_z = [h["collar"][2] for h in holes]
	result = {
		"holes": holes,
		"design": design,
		"problems": problems,
		"summary": {
			"holes": len(holes),
			"rows": len({h["row"] for h in holes}),
			"drilled_length": sum(lengths),
			"mean_length": sum(lengths) / len(lengths),
			"min_length": min(lengths),
			"max_length": max(lengths),
			"pattern_area": region.area,
			"mean_bench_height": sum(c - f for c, f in zip(collar_z, floors)) / len(holes),
		},
	}
	result["summary"]["rock_per_hole"] = burden * spacing * result["summary"]["mean_bench_height"]
	return result, _("{0}: {1} holes in {2} rows, {3} m drilled.").format(name, len(holes), result["summary"]["rows"], round(result["summary"]["drilled_length"], 1))


# ---------------------------------------------------------------------------
# design validation
# ---------------------------------------------------------------------------


def design_entities(model):
	rows = frappe.get_all(ENTITY, filters={"cad_model": model, "metadata": ["is", "set"]}, fields=["entity_id", "entity_type", "geometry", "metadata"])
	out = {"pit": [], "road": [], "block": [], "drill": []}
	for r in rows:
		meta = frappe.parse_json(r.metadata) or {}
		g = frappe.parse_json(r.geometry) or {}
		for key, bucket in (("pit_design", "pit"), ("haul_road", "road"), ("mining_block", "block"), ("drill_hole", "drill")):
			if meta.get(key):
				out[bucket].append({"id": r.entity_id, "type": r.entity_type, "geometry": g, "meta": meta[key]})
	return out


def validate(model):
	"""Check the model's pits, roads and blocks against its design standard; record a CAD Design Validation."""
	doc = frappe.get_doc("CAD Model", model)
	doc.check_permission("read")
	if not doc.design_standard:
		frappe.throw(_("Choose the model's design standard first (Mining → Design Standard)."))
	std = standard_rules(doc.design_standard)
	designs = design_entities(model)
	checks = []
	check_pits(designs["pit"], std, checks)
	check_roads(designs["road"], std, checks)
	check_blocks(designs["block"], std, checks)
	check_drill(model, designs["drill"], std, checks)
	if not checks:
		frappe.throw(_("There is nothing to validate: no saved pit, road, block or drill designs in {0}.").format(model))
	fails = sum(1 for c in checks if c["status"] == "fail")
	warns = sum(1 for c in checks if c["status"] == "warning")
	status = "Fail" if fails else "Warning" if warns else "Pass"
	stages = sorted({d["meta"].get("stage") for d in designs["pit"] if d["meta"].get("stage")})
	roads = sorted({d["meta"].get("name") for d in designs["road"] if d["meta"].get("name")})
	patterns = sorted({d["meta"].get("pattern") for d in designs["drill"] if d["meta"].get("pattern")})
	record = frappe.get_doc(
		{
			"doctype": VALIDATION,
			"cad_model": model,
			"revision": cad_revision_service.working_revision(model),
			"design_standard": std["name"],
			"standard_version": std["version"],
			"status": status,
			"checked_on": now_datetime(),
			"checks_run": len(checks),
			"failures": fails,
			"warnings": warns,
			"designs_checked": "; ".join(
				x
				for x in (
					_("Pits: {0}").format(", ".join(stages)) if stages else "",
					_("Roads: {0}").format(", ".join(roads)) if roads else "",
					_("Blocks: {0}").format(len(designs["block"])) if designs["block"] else "",
					_("Drill patterns: {0}").format(", ".join(patterns)) if patterns else "",
				)
				if x
			),
			"summary": _("{0} checks against {1}: {2} failed, {3} warnings.").format(len(checks), std["name"], fails, warns),
			"standard_snapshot": json.dumps(std, default=str),
			"results": json.dumps(checks, default=float),
		}
	)
	record.flags.ignore_permissions = True
	record.insert()
	doc.db_set("last_design_validation", record.name, update_modified=False)
	return {"name": record.name, "status": status, "summary": record.summary, "checks": checks, "standard": std}


def check(checks, category, name, status, value, limit, unit, message, ids, at=None):
	checks.append({"category": category, "check": name, "status": status, "value": value, "limit": limit, "unit": unit, "message": message, "entities": ids, "at": at})


def check_pits(items, std, checks):
	_sh, g = shapely()
	tol, atol = flt(std["design_tolerance"]), flt(std["angle_tolerance"])
	stages = {}
	for d in items:
		if d["type"] == "Polyline" and d["geometry"].get("closed") and d["meta"].get("kind") in ("floor", "toe", "crest"):
			stages.setdefault(d["meta"].get("stage") or "?", []).append(d)
	for stage, rings in stages.items():
		polys = []
		for d in rings:
			pts = d["geometry"]["points"]
			poly = g.Polygon([q[:2] for q in pts])
			z = flt(pts[0][2]) if len(pts[0]) > 2 else 0.0
			if not poly.is_valid:
				check(checks, "Pit", _("Outline is simple"), "fail", None, None, "", _("{0} {1} ring {2} crosses itself.").format(stage, d["meta"].get("kind"), d["id"]), [d["id"]], pts[0])
				poly = poly.buffer(0)
			polys.append((z, d["meta"].get("kind"), d["id"], poly, pts[0]))
		# upwards; at one elevation the crest of the bench below comes before the toe of the next bench (outside it)
		polys.sort(key=lambda x: (x[0], 1 if x[1] == "toe" else 0))
		floor = next((x for x in polys if x[1] == "floor"), None)
		# nesting: every ring inside the next one up
		for a, b in zip(polys, polys[1:]):
			if not b[3].buffer(tol).contains(a[3]):
				check(checks, "Pit", _("Rings nest"), "fail", None, None, "", _("{0}: ring {1} at {2} is not inside ring {3} at {4}.").format(stage, a[2], round(a[0], 2), b[2], round(b[0], 2)), [a[2], b[2]], a[4])
		# faces and berms
		for a, b in zip(polys, polys[1:]):
			gap = b[3].exterior.distance(a[3].exterior)
			if a[1] in ("floor", "toe") and b[1] == "crest":
				h = b[0] - a[0]
				ok_h = abs(h - flt(std["bench_height"])) <= tol
				check(checks, "Pit", _("Bench height"), "pass" if ok_h else "fail", h, std["bench_height"], "m", _("{0}: bench {1} → {2}").format(stage, round(a[0], 2), round(b[0], 2)), [a[2], b[2]], b[4])
				angle = math.degrees(math.atan2(h, gap)) if gap > 0 else 90.0
				ok_a = angle <= flt(std["face_angle"]) + atol
				check(checks, "Pit", _("Bench face angle (steepest)"), "pass" if ok_a else "fail", angle, std["face_angle"], "°", _("{0}: face at {1}").format(stage, round(a[0], 2)), [a[2], b[2]], b[4])
			elif a[1] == "crest" and b[1] in ("toe",) and abs(b[0] - a[0]) <= tol:
				minimum = flt(std["minimum_bench_width"])
				status = "fail" if gap < minimum - tol else "warning" if gap < flt(std["berm_width"]) - tol else "pass"
				check(checks, "Pit", _("Berm width (narrowest)"), status, gap, minimum, "m", _("{0}: berm at {1}").format(stage, round(a[0], 2)), [a[2], b[2]], a[4])
		if floor and len(polys) > 1:
			top = polys[-1]
			run_ = top[3].exterior.distance(floor[3].exterior)
			angle = math.degrees(math.atan2(top[0] - floor[0], run_)) if run_ > 0 else 90.0
			ok_s = angle <= flt(std["maximum_slope_angle"]) + atol
			check(checks, "Pit", _("Overall slope angle (steepest)"), "pass" if ok_s else "fail", angle, std["maximum_slope_angle"], "°", _("{0}: floor {1} to top crest {2}").format(stage, round(floor[0], 2), round(top[0], 2)), [floor[2], top[2]], top[4])
		if floor and flt(std["minimum_floor_width"]):
			w = flt(std["minimum_floor_width"])
			ok_f = not floor[3].buffer(-(w / 2 - tol)).is_empty
			check(checks, "Pit", _("Floor width"), "pass" if ok_f else "fail", None, w, "m", _("{0}: a {1} m wide floor fits").format(stage, w) if ok_f else _("{0}: the floor is narrower than {1} m").format(stage, w), [floor[2]], floor[4])


def circumradius(a, b, c):
	ab, bc, ca = math.dist(a, b), math.dist(b, c), math.dist(c, a)
	cross = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
	return math.inf if cross < 1e-12 else ab * bc * ca / (2 * cross)


def check_roads(items, std, checks):
	tol = flt(std["design_tolerance"])
	for d in items:
		meta = d["meta"]
		name = meta.get("name") or d["id"]
		if meta.get("role") == "centreline" and d["type"] == "Polyline":
			pts = d["geometry"]["points"]
			worst_g, at_g = 0.0, pts[0]
			for a, b in zip(pts, pts[1:]):
				run_ = math.dist(a[:2], b[:2])
				if run_ > 1e-6:
					grade = abs(b[2] - a[2]) / run_ * 100
					if grade > worst_g:
						worst_g, at_g = grade, b
			limit = flt(std["maximum_gradient"])
			check(checks, "Haul road", _("Gradient (steepest)"), "pass" if worst_g <= limit + 1e-6 else "fail", worst_g, limit, "%", name, [d["id"]], at_g)
			radii = [(circumradius(a[:2], b[:2], c[:2]), b) for a, b, c in zip(pts, pts[1:], pts[2:])]
			worst_r, at_r = min(radii, key=lambda x: x[0]) if radii else (math.inf, pts[0])
			limit_r = flt(std["minimum_turning_radius"])
			check(checks, "Haul road", _("Turning radius (tightest)"), "pass" if worst_r >= limit_r - tol else "fail", None if math.isinf(worst_r) else worst_r, limit_r, "m", name, [d["id"]], at_r)
			min_k = flt(std.get("minimum_k_value"))
			if min_k and meta.get("min_k") is not None:
				k = flt(meta["min_k"])
				check(checks, "Haul road", _("Vertical curve K (smallest)"), "pass" if k >= min_k - 1e-6 else "fail", k, min_k, "m/%", name, [d["id"]], pts[0])
			width = flt(meta.get("width"))
			check(checks, "Haul road", _("Road width"), "pass" if width >= flt(std["minimum_road_width"]) - tol else "fail", width, std["minimum_road_width"], "m", name, [d["id"]], pts[0])


def check_blocks(items, std, checks):
	if not items:
		return
	_sh, g = shapely()
	from shapely.strtree import STRtree

	tol = flt(std["design_tolerance"])
	by_bench = {}
	no_material, empty, bad = [], [], []
	for d in items:
		meta = d["meta"]
		poly = g.Polygon([q[:2] for q in d["geometry"]["points"]])
		if not poly.is_valid or not d["geometry"].get("closed"):
			bad.append(d["id"])
			continue
		by_bench.setdefault(round(flt(meta.get("bench_bottom")), 6), []).append((d, poly))
		if not meta.get("material"):
			no_material.append(d["id"])
		if flt(meta.get("volume")) <= 0:
			empty.append(d["id"])
	check(checks, "Mining blocks", _("Closed, simple outlines"), "fail" if bad else "pass", len(bad), 0, "", _("{0} invalid outlines").format(len(bad)) if bad else _("All {0} blocks").format(len(items)), bad[:50])
	overlaps = []
	for bench, rows in by_bench.items():
		polys = [p for _d, p in rows]
		tree = STRtree(polys)
		for i, poly in enumerate(polys):
			for j in tree.query(poly):
				if j > i and poly.intersection(polys[j]).area > max(tol * tol, 1e-6):
					overlaps.append((rows[i][0]["id"], rows[j][0]["id"]))
	check(checks, "Mining blocks", _("No overlaps within a bench"), "fail" if overlaps else "pass", len(overlaps), 0, "", _("{0} overlapping pairs").format(len(overlaps)) if overlaps else _("{0} benches").format(len(by_bench)), [x for pair in overlaps[:25] for x in pair])
	height, ref = flt(std["bench_height"]), flt(std["bench_reference_elevation"])
	off = [d["id"] for rows in by_bench.values() for d, _p in rows if height and abs(((flt(d["meta"].get("bench_bottom")) - ref) / height) - round((flt(d["meta"].get("bench_bottom")) - ref) / height)) * height > tol]
	check(checks, "Mining blocks", _("Benches on the standard's bench levels"), "warning" if off else "pass", len(off), 0, "", _("{0} blocks off the bench levels").format(len(off)) if off else _("All blocks"), off[:50])
	check(checks, "Mining blocks", _("Material classified"), "warning" if no_material else "pass", len(no_material), 0, "", _("{0} blocks without a material").format(len(no_material)) if no_material else _("All blocks"), no_material[:50])
	check(checks, "Mining blocks", _("Blocks hold material"), "warning" if empty else "pass", len(empty), 0, "", _("{0} blocks with no volume").format(len(empty)) if empty else _("All blocks"), empty[:50])


def check_drill(model, items, std, checks):
	"""Per pattern: hole inclination, collars inside the blast boundary, collar spacing, toes below the collars,
	burden/spacing against the standard. Read from the saved hole lines (moved holes are checked where they are)."""
	if not items:
		return
	_sh, g = shapely()
	from shapely.strtree import STRtree

	tol = flt(std["design_tolerance"])
	patterns = {}
	for d in items:
		if d["meta"].get("role") == "hole" and d["type"] == "Line":
			patterns.setdefault(d["meta"].get("pattern") or "?", []).append(d)
	for name, holes in patterns.items():
		meta = holes[0]["meta"]
		steep, upward, outside = [], [], []
		limit = flt(std.get("drill_max_inclination")) or 90
		boundary = None
		if meta.get("boundary") and frappe.db.exists(ENTITY, {"cad_model": model, "entity_id": meta["boundary"]}):
			boundary = polygon_of(model, meta["boundary"])[0].buffer(tol)
		collars = []
		for d in holes:
			a, b = d["geometry"]["p0"], d["geometry"]["p1"]
			collars.append((g.Point(a[0], a[1]), d["id"], a))
			dz = a[2] - b[2]
			if dz <= 0:
				upward.append(d["id"])
				continue
			angle = math.degrees(math.atan2(math.dist(a[:2], b[:2]), dz))
			if angle > limit + flt(std["angle_tolerance"]):
				steep.append(d["id"])
			if boundary is not None and not boundary.contains(collars[-1][0]):
				outside.append(d["id"])
		check(checks, "Drill pattern", _("Holes go down to their toes"), "fail" if upward else "pass", len(upward), 0, "", f"{name}: " + (_("{0} holes end above their collar").format(len(upward)) if upward else _("{0} holes").format(len(holes))), upward[:50])
		check(checks, "Drill pattern", _("Hole inclination"), "fail" if steep else "pass", len(steep), limit, "°", f"{name}: " + (_("{0} holes inclined more than the limit").format(len(steep)) if steep else _("all within {0}° of vertical").format(limit)), steep[:50])
		if boundary is not None:
			check(checks, "Drill pattern", _("Collars inside the blast boundary"), "fail" if outside else "pass", len(outside), 0, "", f"{name}: " + (_("{0} collars outside").format(len(outside)) if outside else _("all inside")), outside[:50])
		# collar spacing: nearest neighbours closer than the design burden/spacing less the tolerance
		design_min = min(flt(meta.get("burden")) or 0, flt(meta.get("spacing")) or 0)
		allowed = design_min * (1 - flt(std.get("drill_spacing_tolerance") or 10) / 100)
		points = [c[0] for c in collars]
		tree = STRtree(points)
		close, nearest = [], math.inf
		reach = max(flt(meta.get("burden")), flt(meta.get("spacing")), allowed, 1e-6) * 1.5  # far enough for the true nearest
		for i, pt in enumerate(points):
			for j in tree.query(pt.buffer(reach)):
				if j != i:
					dist = pt.distance(points[j])
					nearest = min(nearest, dist)
					if dist < allowed - 1e-9 and i < j:
						close.append((collars[i][1], collars[j][1]))
		check(checks, "Drill pattern", _("Collar spacing (closest)"), "warning" if close else "pass", None if math.isinf(nearest) else nearest, allowed, "m", f"{name}: " + (_("{0} pairs closer than {1} m").format(len(close), round(allowed, 2)) if close else _("no collars closer than {0} m").format(round(allowed, 2))), [x for pair in close[:25] for x in pair])
		for key, std_key, label in (("burden", "drill_burden", _("Burden")), ("spacing", "drill_spacing", _("Spacing")), ("diameter_mm", "drill_hole_diameter", _("Hole diameter"))):
			design, standard = flt(meta.get(key)), flt(std.get(std_key))
			if standard:
				check(checks, "Drill pattern", label, "pass" if abs(design - standard) <= max(tol, 1e-9) else "warning", design, standard, "mm" if key == "diameter_mm" else "m", f"{name}: " + (_("as the standard") if abs(design - standard) <= max(tol, 1e-9) else _("differs from the standard")), [holes[0]["id"]])


def export_drill_csv(model, content, out):
	"""The hole table for drill rigs: collar and toe coordinates, length, inclination, dip, diameter."""
	import csv

	rows = []
	for e in content["entities"]:
		meta = frappe.parse_json(e.get("metadata")) if isinstance(e.get("metadata"), str) else e.get("metadata") or {}
		h = meta.get("drill_hole")
		if not h or h.get("role") != "hole" or e["entity_type"] != "Line":
			continue
		g = frappe.parse_json(e.get("geometry")) if isinstance(e.get("geometry"), str) else e.get("geometry") or {}
		a, b = g["p0"], g["p1"]
		dz = a[2] - b[2]
		length = math.dist(a, b)
		incl = math.degrees(math.atan2(math.dist(a[:2], b[:2]), dz)) if dz > 0 else 0
		rows.append([h.get("pattern"), h.get("hole_id"), h.get("row"), h.get("number"), *[round(v, 3) for v in a], *[round(v, 3) for v in b], round(length, 3), round(incl, 2), round(flt(h.get("dip")), 2), h.get("diameter_mm"), h.get("subdrill"), h.get("burden"), h.get("spacing"), e["entity_id"]])
	if not rows:
		frappe.throw(_("There are no drill holes to export."))
	rows.sort(key=lambda r: (str(r[0]), r[2] or 0, r[3] or 0))
	with open(out, "w", newline="") as f:
		w = csv.writer(f)
		w.writerow(["pattern", "hole_id", "row", "number", "collar_x", "collar_y", "collar_z", "toe_x", "toe_y", "toe_z", "length_m", "inclination_deg", "dip_azimuth_deg", "diameter_mm", "subdrill_m", "burden_m", "spacing_m", "entity_id"])
		w.writerows(rows)
	return {"written": {"Hole": len(rows)}, "format": "Drill Holes CSV"}


# ---------------------------------------------------------------------------
# exports
# ---------------------------------------------------------------------------


def export_geojson(model, content, out):
	"""Design lines, rings and block outlines as GeoJSON in the model's coordinates (CRS named), with their design data."""
	crs = frappe.db.get_value("CAD Model", model, "crs")
	features = []
	for e in content["entities"]:
		g = frappe.parse_json(e.get("geometry")) if isinstance(e.get("geometry"), str) else e.get("geometry") or {}
		meta = frappe.parse_json(e.get("metadata")) if isinstance(e.get("metadata"), str) else e.get("metadata") or {}
		design = {k: v for k, v in meta.items() if k in ("pit_design", "haul_road", "mining_block", "drill_hole")}
		if not design:
			continue
		kind, data = next(iter(design.items()))
		props = {"entity_id": e["entity_id"], "layer": e.get("layer"), "design": kind, **{k: v for k, v in data.items() if not isinstance(v, (dict, list))}}
		if e["entity_type"] == "Polyline":
			pts = [list(map(float, q)) for q in g["points"]]
			if g.get("closed"):
				geometry = {"type": "Polygon", "coordinates": [pts + [pts[0]]]}
			else:
				geometry = {"type": "LineString", "coordinates": pts}
		elif e["entity_type"] == "Line":
			geometry = {"type": "LineString", "coordinates": [g["p0"], g["p1"]]}
		elif e["entity_type"] == "Point":
			geometry = {"type": "Point", "coordinates": g["p"]}
		else:
			continue
		features.append({"type": "Feature", "geometry": geometry, "properties": props})
	if not features:
		frappe.throw(_("There are no pit, road or block designs to export."))
	data = {"type": "FeatureCollection", "name": model, "features": features}
	if crs:
		data["crs"] = {"type": "name", "properties": {"name": crs}}
	with open(out, "w") as f:
		json.dump(data, f)
	return {"written": {"Feature": len(features)}, "format": "GeoJSON"}


def export_blocks_csv(model, content, out):
	import csv

	rows = []
	for e in content["entities"]:
		meta = frappe.parse_json(e.get("metadata")) if isinstance(e.get("metadata"), str) else e.get("metadata") or {}
		b = meta.get("mining_block")
		if not b:
			continue
		g = frappe.parse_json(e.get("geometry")) if isinstance(e.get("geometry"), str) else e.get("geometry") or {}
		pts = g.get("points") or []
		cx = sum(q[0] for q in pts) / len(pts) if pts else 0
		cy = sum(q[1] for q in pts) / len(pts) if pts else 0
		wkt = "POLYGON((" + ", ".join(f"{q[0]:.3f} {q[1]:.3f}" for q in pts + pts[:1]) + "))" if pts else ""
		rows.append([b.get("block_id"), b.get("sequence"), b.get("bench_bottom"), b.get("bench_top"), b.get("material") or "", round(flt(b.get("volume")), 3), round(flt(b.get("plan_area")), 3), round(cx, 3), round(cy, 3), b.get("upper"), b.get("lower"), e["entity_id"], wkt])
	if not rows:
		frappe.throw(_("There are no mining blocks to export."))
	rows.sort(key=lambda r: (r[1] or 0))
	with open(out, "w", newline="") as f:
		w = csv.writer(f)
		w.writerow(["block_id", "sequence", "bench_bottom", "bench_top", "material", "volume_m3", "plan_area_m2", "centroid_x", "centroid_y", "upper_surface", "lower_surface", "entity_id", "polygon_wkt"])
		w.writerows(rows)
	return {"written": {"Block": len(rows)}, "format": "Mining Blocks CSV"}
