"""Pit-shell optimisation (Phase 6): nested pit shells on a regular block model valued from a CAD Geology Model.

The block model is a grid of columns (cell × cell) by benches (the standard's bench height and levels) from above the
ground down to the lowest seam floor. In the CAD worker (mining_worker.py pit_shells) every block's in-situ volume and
coal tonnes are integrated exactly over the composite of the ground, the seam surfaces and the grid; a block is worth
RF·price·recovery·t − processing·recovery·t − mining·volume; the slope is a precedence (a block needs the blocks of the
bench above within bench / tan(slope)); and the best closure of every revenue factor is a minimum cut — the classic
Lerchs–Grossmann result, solved as a maximum flow. Shells are kept nested. The selected shell becomes a surface and a
pit-limit outline in the model (added by the workstation as one undoable edit) and a CAD Pit Optimisation record keeps
the inputs, every shell and the method. Undiscounted: no schedule, no discounting.
"""

import json
import math
import os

import frappe
from frappe import _
from frappe.utils import cint, flt

from is_production.cad_engineering.services import cad_job_service, cad_revision_service, cad_surface_service, cad_worker_service
from is_production.cad_engineering.services.cad_mining_service import model_standard, polygon_of, shapely

OPTIMISATION = "CAD Pit Optimisation"
MAX_BLOCKS = 600_000


def mining(job, request, log):
	return cad_job_service.run_worker(job, request, log, script=cad_worker_service.MINING_WORKER)


def write_input(workspace, name, data):
	path = os.path.join(workspace, "inputs", name)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w") as f:
		json.dump(data, f)
	return path


def factors(p):
	lo, hi, step = flt(p.get("rf_from") or 0.5), flt(p.get("rf_to") or 1.5), flt(p.get("rf_step") or 0.1)
	if step <= 0 or hi < lo:
		frappe.throw(_("Give revenue factors from ≤ to and a step above 0."))
	n = int(math.floor((hi - lo) / step + 1e-9)) + 1
	if n > 40:
		frappe.throw(_("At most 40 revenue factors (use a larger step)."))
	out = sorted({round(lo + k * step, 6) for k in range(n)} | {round(flt(p.get("selected_rf") or 1.0), 6)})
	return out


def run_pit_shells(job, p, workspace, log):
	model = job.cad_model
	record = frappe.get_doc("CAD Geology Model", p["geology"])
	if record.cad_model != model:
		frappe.throw(_("{0} is not a geology model of {1}.").format(record.name, model))
	surfaces = json.loads(record.surfaces or "{}")
	std = model_standard(model) or {}
	ground_ref = p.get("ground") or (surfaces.get("topography") or {}).get("geometry_ref")
	if not ground_ref:
		frappe.throw(_("Choose the ground (topography) surface: the geology has none."))
	ground_meta = cad_surface_service.sidecar(model, ground_ref) or {}
	bench = flt(p.get("bench_height") or std.get("bench_height") or 10)
	cell = flt(p.get("cell_size") or 10)
	slope = flt(p.get("slope") or std.get("maximum_slope_angle") or 45)
	if bench <= 0 or cell <= 0 or not 0 < slope < 90:
		frappe.throw(_("Give a cell size, a bench height and an overall slope between 0° and 90°."))
	seams = [s for s in record.seams if not p.get("seams") or s.seam in p["seams"]]
	if not seams:
		frappe.throw(_("{0} has no seams.").format(record.name))
	staged = []
	floors, boxes = [], []
	for s in seams:
		item = {"roof": cad_surface_service.stage_tin(model, s.roof_ref, workspace, f"opt_{s.seam}_roof"), "floor": cad_surface_service.stage_tin(model, s.floor_ref, workspace, f"opt_{s.seam}_floor")}
		density = (surfaces.get(f"{s.seam}:Relative Density") or {}).get("geometry_ref")
		if density:
			item["density"] = cad_surface_service.stage_tin(model, density, workspace, f"opt_{s.seam}_rd")
		staged.append(item)
		roof_box = (cad_surface_service.sidecar(model, s.roof_ref) or {}).get("bbox")
		floor_box = (cad_surface_service.sidecar(model, s.floor_ref) or {}).get("bbox")
		if roof_box:
			boxes.append(roof_box)
		if floor_box:
			floors.append(floor_box[2])
	gbox = ground_meta.get("bbox")
	if not gbox or not boxes:
		frappe.throw(_("The ground or the seam surfaces are missing from model storage."))
	limit, limit_pts = None, None
	if p.get("limit"):
		_sh, g = shapely()
		limit, _z = polygon_of(model, p["limit"])
		lb = limit.bounds
		x0, y0, x1, y1 = max(lb[0], gbox[0]), max(lb[1], gbox[1]), min(lb[2], gbox[3]), min(lb[3], gbox[4])
		limit_pts = [list(c) for c in limit.exterior.coords][:-1]
	else:
		x0, y0 = max(min(b[0] for b in boxes), gbox[0]), max(min(b[1] for b in boxes), gbox[1])
		x1, y1 = min(max(b[3] for b in boxes), gbox[3]), min(max(b[4] for b in boxes), gbox[4])
	if x1 <= x0 or y1 <= y0:
		frappe.throw(_("The seams, the ground (and the limit) do not overlap."))
	x0, y0 = math.floor(x0 / cell) * cell, math.floor(y0 / cell) * cell
	nx, ny = int(math.ceil((x1 - x0) / cell - 1e-9)), int(math.ceil((y1 - y0) / cell - 1e-9))
	ref = flt(std.get("bench_reference_elevation"))
	top = ref + math.ceil((flt(gbox[5]) - ref) / bench - 1e-9) * bench
	bottom = flt(p["bottom_elevation"]) if p.get("bottom_elevation") not in (None, "") else ref + math.floor((min(floors) - ref) / bench + 1e-9) * bench
	nk = int(math.ceil((top - bottom) / bench - 1e-9))
	if nk < 1:
		frappe.throw(_("The bottom elevation {0} is not below the top {1}.").format(bottom, top))
	if nx * ny * nk > MAX_BLOCKS:
		frappe.throw(_("{0} blocks ({1} × {2} × {3}): use a larger cell (at most {4}).").format(nx * ny * nk, nx, ny, nk, MAX_BLOCKS))
	economics = {"price": flt(p.get("price")), "mining_cost": flt(p.get("mining_cost")), "processing_cost": flt(p.get("processing_cost")), "recovery": flt(p.get("recovery") if p.get("recovery") not in (None, "") else 100)}
	if economics["price"] <= 0:
		frappe.throw(_("Give the coal price per tonne."))
	rfs = factors(p)
	log.append(_("Block model {0} × {1} × {2} ({3} m cells, {4} m benches, {5} to {6}).").format(nx, ny, nk, cell, bench, round(top, 2), round(bottom, 2)))
	res = mining(
		job,
		{
			"command": "pit_shells",
			"workspace": workspace,
			"ground": cad_surface_service.stage_tin(model, ground_ref, workspace, "opt_ground"),
			"seams": staged,
			"default_density": flt(p.get("default_density") or 1.5),
			"grid": {"x0": x0, "y0": y0, "dx": cell, "dy": cell, "nx": nx, "ny": ny, "top": top, "bench": bench, "benches": nk},
			"economics": economics,
			"slope": slope,
			"limit": limit_pts,
			"revenue_factors": rfs,
		},
		log,
	)
	selected_rf = round(flt(p.get("selected_rf") or 1.0), 6)
	chosen = next(s for s in res["shells"] if abs(s["revenue_factor"] - selected_rf) < 1e-9)
	shell, outline = shell_geometry(job, workspace, log, res, chosen)
	shells = [{k: v for k, v in s.items() if k != "depth"} for s in res["shells"]]
	record_doc = frappe.get_doc(
		{
			"doctype": OPTIMISATION,
			"title": _("{0} · RF {1}").format(record.geo_project, selected_rf),
			"cad_model": model,
			"geology_model": record.name,
			"revision": cad_revision_service.working_revision(model),
			"processing_job": job.name,
			"cell_size": cell,
			"bench_height": bench,
			"slope_angle": slope,
			"top_elevation": top,
			"bottom_elevation": bottom,
			"blocks": nx * ny * nk,
			"price": economics["price"],
			"recovery": economics["recovery"],
			"mining_cost": economics["mining_cost"],
			"processing_cost": economics["processing_cost"],
			"default_density": flt(p.get("default_density") or 1.5),
			"selected_revenue_factor": selected_rf,
			"shell_tonnes": chosen["tonnes"],
			"shell_waste": chosen["waste_volume"],
			"strip_ratio": chosen["strip_ratio"] or 0,
			"shell_value": chosen["value"],
			"shell_surface_ref": shell["geometry_ref"] if shell else None,
			"shells": json.dumps(shells, default=float),
			"method": res["method"],
		}
	)
	record_doc.flags.ignore_permissions = True
	record_doc.insert()
	result = {
		"optimisation": record_doc.name,
		"geology": record.name,
		"grid": res["grid"],
		"reach": res["reach"],
		"shells": shells,
		"selected_rf": selected_rf,
		"selected": {k: v for k, v in chosen.items() if k != "depth"},
		"shell": shell,
		"outline": outline,
		"totals": res["totals"],
		"economics": economics,
		"method": res["method"],
	}
	message = _("{0}: shell at RF {1} — {2} t of coal, {3} bcm waste, strip ratio {4}, value {5} (undiscounted).").format(
		record_doc.name, selected_rf, round(chosen["tonnes"]), round(chosen["waste_volume"]), round(chosen["strip_ratio"], 2) if chosen["strip_ratio"] else "—", round(chosen["value"])
	)
	return result, message


def shell_geometry(job, workspace, log, res, chosen):
	"""The shell as a surface through the column centres (the shell bottom where mined, the ground elsewhere) and its
	pit limit: the outline of the mined columns."""
	gr = res["grid"]
	nx, ny, dx, dy = gr["nx"], gr["ny"], gr["dx"], gr["dy"]
	depth, area, ground = chosen["depth"], res["column_area"], res["column_ground"]
	if not any(depth):
		return None, []
	pts, boxes = [], []
	_sh, g = shapely()
	for i in range(nx):
		for j in range(ny):
			c = i * ny + j
			if area[c] <= 0:
				continue
			cx, cy = gr["x0"] + (i + 0.5) * dx, gr["y0"] + (j + 0.5) * dy
			z = ground[c]
			if depth[c]:
				z = min(z, gr["top"] - depth[c] * gr["bench"])
				boxes.append(g.box(cx - dx / 2, cy - dy / 2, cx + dx / 2, cy + dy / 2))
			pts.append([cx, cy, z])
	built = mining(job, {"command": "geology_surfaces", "workspace": workspace, "surfaces": [{"key": "shell", "points": write_input(workspace, "shell.json", pts), "max_edge": "auto"}]}, log)["surfaces"][0]
	source = {"operation": "pit_shell", "revenue_factor": chosen["revenue_factor"]}
	ref = cad_surface_service.store_tin(job, workspace, built["tin"], built["stats"], source, log=log)
	from shapely.ops import unary_union

	union = unary_union(boxes)
	polys = [union] if union.geom_type == "Polygon" else list(union.geoms)
	mined_ground = [ground[i * ny + j] for i in range(nx) for j in range(ny) if depth[i * ny + j]]
	z = sum(mined_ground) / len(mined_ground)
	outline = [[[x, y, z] for x, y in list(poly.exterior.coords)[:-1]] for poly in polys]
	return {"geometry_ref": ref, "stats": built["stats"], "source": source}, outline
