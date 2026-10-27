"""Geological modelling (Phase 6): seam surfaces from a Geo Project's model grids or drillholes, geology checks, and the
geology of mining blocks (seam volume, tonnes and tonnage-weighted qualities).

Sources (Geo Planning, read only):
  model grids   Geo Model Points, one point set per variable. Variables are named "<code> - <description>" where the
                code is a seam code plus a kind suffix (S1USR = seam S1U roof, S1UAS = its ash, …); seams are grouped
                from these codes and the user confirms them.
  drillholes    Geo Drillhole collars (easting, northing, RL, azimuth, dip) with Geo Material Interval seams (roof =
                shallowest from-depth, floor = deepest to-depth of a seam in a hole) and Geo Quality Interval values
                (averaged over the seam by length). Straight holes are desurveyed from their azimuth and dip.

Geo data of south-orientated (Lo) projects is often stored as easting/northing-style values (x = −westing,
y = −southing): "Negate X and Y" maps it onto the westing/southing axes of an EPSG:2053 model; it is suggested when
the model is south-orientated and the data's y values are negative.

Surfaces are built in the CAD worker (mining_worker.py geology_surfaces / idw_grid / block_geology): seam roofs and
floors and the topography become Surface entities (added by the workstation as one undoable edit); property surfaces
(thickness, ash, CV, density, …: the value as Z) are stored like any TIN but only referenced by the CAD Geology Model
record, which keeps the source, method, seam totals and checks.
"""

import json
import math
import os
import re

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from is_production.cad_engineering.services import cad_job_service, cad_model_service, cad_revision_service, cad_surface_service, cad_worker_service

GEOLOGY = "CAD Geology Model"
ENTITY = "CAD Entity"
# kind suffixes of Geo variable codes, longest first when matching
KINDS = {
	"DAF": "Dry Ash Free",
	"SR": "Roof",
	"SF": "Floor",
	"ST": "Thickness",
	"AS": "Ash",
	"CV": "CV",
	"RD": "Relative Density",
	"FC": "Fixed Carbon",
	"IM": "Internal Moisture",
	"TS": "Total Sulphur",
	"VM": "Volatile Matter",
	"IB": "Interburden",
	"OB": "Overburden",
}
STRUCTURE = {"SR", "SF", "ST", "IB", "OB"}  # not seam qualities
TOPOGRAPHY = re.compile(r"^(topo|topography|ground|surface)\b", re.I)
MAX_HOLE_POINTS = 200000


def mining(job, request, log):
	return cad_job_service.run_worker(job, request, log, script=cad_worker_service.MINING_WORKER)


def surface(job, request, log):
	return cad_job_service.run_worker(job, request, log, script=cad_worker_service.SURFACE_WORKER)


def write_input(workspace, name, data):
	path = os.path.join(workspace, "inputs", name)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w") as f:
		json.dump(data, f)
	return path


# ---------------------------------------------------------------------------
# what a Geo Project holds
# ---------------------------------------------------------------------------


def parse_variable(name):
	"""(seam code, kind suffix, description) of a Geo variable name such as "S1USR - 1 Seam Upper Roof"."""
	code, _sep, desc = (name or "").partition(" - ")
	code, desc = code.strip(), desc.strip()
	for suffix in sorted(KINDS, key=len, reverse=True):
		if code.upper().endswith(suffix) and len(code) > len(suffix):
			return code[: -len(suffix)], suffix, desc
	return None, None, desc


def seam_label(desc):
	return re.sub(r"\s*(roof|floor)\s*$", "", (desc or "").strip(), flags=re.I).strip() or desc


def sources(model, geo_project):
	"""Variables of the project's model grids grouped into seams, the drillholes' seams, and the axis mapping to
	suggest for this model."""
	if not frappe.db.exists("Geo Project", geo_project):
		frappe.throw(_("Geo Project {0} does not exist.").format(geo_project))
	frappe.get_doc("Geo Project", geo_project).check_permission("read")
	rows = frappe.db.sql(
		"""select variable_name, count(*) n, min(x) x0, max(x) x1, min(y) y0, max(y) y1, min(z) z0, max(z) z1
		from `tabGeo Model Points` where geo_project=%s and variable_name is not null group by variable_name order by variable_name""",
		geo_project,
		as_dict=True,
	)
	seams, others = {}, []
	for r in rows:
		code, kind, desc = parse_variable(r.variable_name)
		var = {"variable": r.variable_name, "points": r.n, "min": r.z0, "max": r.z1, "kind": KINDS.get(kind) if kind else None}
		if code:
			s = seams.setdefault(code, {"key": code, "label": None, "roof": None, "floor": None, "thickness": None, "density": None, "qualities": {}})
			if kind == "SR":
				s["roof"], s["label"] = r.variable_name, seam_label(desc)
			elif kind == "SF":
				s["floor"] = r.variable_name
				s["label"] = s["label"] or seam_label(desc)
			elif kind == "ST":
				s["thickness"] = r.variable_name
			elif kind == "RD":
				s["density"] = r.variable_name
			elif kind not in STRUCTURE:
				s["qualities"][KINDS[kind]] = r.variable_name
			else:
				others.append(var)
		else:
			others.append(var)
	complete = [s for s in seams.values() if s["roof"] and s["floor"]]
	for s in complete:  # S1A inside S1: a split or ply of another seam (counting both would count its coal twice)
		s["part_of"] = next((o["key"] for o in complete if o is not s and s["key"].startswith(o["key"])), None)
	for s in seams.values():
		if not (s["roof"] and s["floor"]):
			others.extend({"variable": v, "kind": None} for v in [s["roof"], s["floor"], s["thickness"], s["density"], *s["qualities"].values()] if v)
	topography = next((v["variable"] for v in others if TOPOGRAPHY.match(v["variable"] or "")), None)
	ys = [r.y0 for r in rows] + [r.y1 for r in rows]
	holes = drillhole_seams(geo_project)
	if holes["holes"]:
		ys += [holes["northing"]]
	doc = frappe.get_doc("CAD Model", model)
	negate = bool(ys) and cad_model_service.plan_rotated(doc) and sorted(ys)[len(ys) // 2] < 0
	return {
		"geo_project": geo_project,
		"seams": sorted(complete, key=lambda s: s["key"]),
		"others": others,
		"topography": topography,
		"variables": len(rows),
		"drillholes": holes,
		"axis": "Negate X and Y" if negate else "As stored",
	}


def drillhole_seams(geo_project):
	n = frappe.db.count("Geo Drillhole", {"geo_project": geo_project})
	if not n:
		return {"holes": 0, "seams": [], "qualities": []}
	seams = frappe.db.sql(
		"""select i.seam, count(distinct d.name) holes from `tabGeo Material Interval` i join `tabGeo Drillhole` d on d.name = i.parent
		where d.geo_project=%s and ifnull(i.seam, '') != '' group by i.seam order by i.seam""",
		geo_project,
		as_dict=True,
	)
	qualities = frappe.db.sql(
		"""select distinct q.variable_name from `tabGeo Quality Interval` q join `tabGeo Drillhole` d on d.name = q.parent
		where d.geo_project=%s order by 1""",
		geo_project,
		pluck=True,
	)
	northing = frappe.db.sql("select avg(northing) from `tabGeo Drillhole` where geo_project=%s", geo_project)[0][0]
	return {"holes": n, "seams": seams, "qualities": qualities, "northing": flt(northing)}


def grid_points(geo_project, variable, negate):
	"""The variable's points (the latest version), mapped to model axes. One indexed read of the variable's rows."""
	rows = frappe.db.sql(
		"select x, y, z, ifnull(version_tag, ''), creation from `tabGeo Model Points` where variable_name=%s and geo_project=%s",
		(variable, geo_project),
	)
	if not rows:
		frappe.throw(_("{0} has no points in {1}.").format(variable, geo_project))
	latest = max(rows, key=lambda r: (r[4] is not None, r[4] or 0))[3]  # bulk-loaded rows may have no creation time
	s = -1.0 if negate else 1.0
	return [[s * flt(x), s * flt(y), flt(z)] for x, y, z, version, _c in rows if version == latest and x is not None and y is not None and z is not None]


# ---------------------------------------------------------------------------
# drillholes: desurvey and seam intercepts
# ---------------------------------------------------------------------------


def hole_point(collar, depth, azimuth, dip):
	"""A point `depth` metres down a straight hole. Dip from horizontal, negative (or positive) both meaning down;
	0 or empty: vertical. Azimuth from north, clockwise, in the data's own (easting/northing) axes."""
	inc = abs(flt(dip)) or 90.0
	a, i = math.radians(flt(azimuth)), math.radians(inc)
	h = depth * math.cos(i)
	return [collar[0] + h * math.sin(a), collar[1] + h * math.cos(a), collar[2] - depth * math.sin(i)]


def drillhole_intercepts(geo_project, negate, by_ply=False):
	"""Per hole: collar, end and seam intercepts {seam: (roof xyz, floor xyz, from, to, qualities, density)}."""
	holes = frappe.get_all("Geo Drillhole", filters={"geo_project": geo_project}, fields=["name", "hole_id", "easting", "northing", "rl_m", "end_depth_m", "azimuth", "dip"])
	intervals = frappe.get_all("Geo Material Interval", filters={"parenttype": "Geo Drillhole", "parent": ["in", [h.name for h in holes] or [""]]}, fields=["parent", "from_depth", "to_depth", "seam", "ply", "density"])
	quality = frappe.get_all("Geo Quality Interval", filters={"parenttype": "Geo Drillhole", "parent": ["in", [h.name for h in holes] or [""]]}, fields=["parent", "from_depth", "to_depth", "variable_name", "value"])
	by_hole, q_by_hole = {}, {}
	for r in intervals:
		by_hole.setdefault(r.parent, []).append(r)
	for r in quality:
		q_by_hole.setdefault(r.parent, []).append(r)
	s = -1.0 if negate else 1.0
	out, skipped = [], []
	for h in holes:
		if h.easting is None or h.northing is None or h.rl_m is None:
			skipped.append(h.hole_id)
			continue
		collar = [flt(h.easting), flt(h.northing), flt(h.rl_m)]
		rows = by_hole.get(h.name) or []
		end = max([flt(h.end_depth_m)] + [flt(r.to_depth) for r in rows])

		def at(depth, collar=collar, h=h):
			p = hole_point(collar, depth, h.azimuth, h.dip)
			return [s * p[0], s * p[1], p[2]]

		seams = {}
		for r in rows:
			if not r.seam:
				continue
			key = f"{r.seam}{r.ply or ''}" if by_ply else r.seam
			e = seams.setdefault(key, {"from": flt(r.from_depth), "to": flt(r.to_depth), "density": [], "rows": []})
			e["from"], e["to"] = min(e["from"], flt(r.from_depth)), max(e["to"], flt(r.to_depth))
			if flt(r.density):
				e["density"].append((flt(r.to_depth) - flt(r.from_depth), flt(r.density)))
		for key, e in seams.items():
			e["roof"], e["floor"] = at(e["from"]), at(e["to"])
			# qualities: length-weighted over the part of each quality interval inside the seam
			acc = {}
			for q in q_by_hole.get(h.name) or []:
				overlap = min(flt(q.to_depth), e["to"]) - max(flt(q.from_depth), e["from"])
				if overlap > 1e-9 and q.value is not None:
					w = acc.setdefault(q.variable_name, [0.0, 0.0])
					w[0] += overlap * flt(q.value)
					w[1] += overlap
			e["qualities"] = {k: v[0] / v[1] for k, v in acc.items() if v[1] > 0}
			if e["density"]:
				e["qualities"]["Relative Density"] = sum(a * b for a, b in e["density"]) / sum(a for a, _b in e["density"])
			e.pop("rows")
			e.pop("density")
		out.append({"hole_id": h.hole_id, "collar": at(0), "end": at(end), "seams": seams})
	return out, skipped


# ---------------------------------------------------------------------------
# the geology job
# ---------------------------------------------------------------------------


def run_geology(job, p, workspace, log):
	model = job.cad_model
	project = p.get("geo_project")
	info = sources(model, project)
	negate = (p.get("axis") or info["axis"]) == "Negate X and Y"
	source = p.get("source") or "Model Grids"
	chosen = set(p.get("seams") or [])
	items, meta = [], {}  # worker inputs and what each surface is

	def add(key, points, role, seam=None, variable=None, label=None, max_edge="auto"):
		items.append({"key": key, "points": write_input(workspace, f"geo_{len(items)}.json", points), "max_edge": max_edge})
		meta[key] = {"role": role, "seam": seam, "variable": variable, "label": label}

	holes, skipped = [], []
	if source == "Model Grids":
		seams = [s for s in info["seams"] if not chosen or s["key"] in chosen]
		if not seams:
			frappe.throw(_("{0} has no seams with both a roof and a floor grid.").format(project))
		for s in seams:
			add(f"{s['key']}:roof", grid_points(project, s["roof"], negate), "roof", s["key"], s["roof"], s["label"])
			add(f"{s['key']}:floor", grid_points(project, s["floor"], negate), "floor", s["key"], s["floor"], s["label"])
			props = {"Relative Density": s["density"], **s["qualities"]} if p.get("qualities", True) else {}
			if s["thickness"]:
				props["Thickness"] = s["thickness"]
			for kind, variable in props.items():
				if variable:
					add(f"{s['key']}:{kind}", grid_points(project, variable, negate), "property", s["key"], variable, kind)
		topo = p.get("topography") if p.get("topography") is not None else info["topography"]
		if topo:
			add("topography", grid_points(project, topo, negate), "topography", None, topo, _("Topography"))
		method = _("Model grids of {0}: each variable's points triangulated (long outer edges trimmed at 2.5 × the grid spacing).").format(project)
	else:
		holes, skipped = drillhole_intercepts(project, negate, bool(p.get("by_ply")))
		if skipped:
			log.append(_("{0} holes have no collar coordinates and are left out: {1}").format(len(skipped), ", ".join(skipped[:20])))
		names = sorted({k for h in holes for k in h["seams"]})
		seams_used = [k for k in names if not chosen or k in chosen]
		if not seams_used:
			frappe.throw(_("No drillhole in {0} has seam intervals.").format(project))
		interp = p.get("interpolation") or "tin"
		seams = []
		for k in seams_used:
			hits = [h["seams"][k] for h in holes if k in h["seams"]]
			if len(hits) < 3:
				log.append(_("Seam {0}: only {1} holes, at least 3 are needed; left out.").format(k, len(hits)))
				continue
			roof = [e["roof"] for e in hits]
			floor = [e["floor"] for e in hits]
			props = {}
			for e in hits:
				for q, v in e["qualities"].items():
					props.setdefault(q, []).append([e["roof"][0], e["roof"][1], v])
			if interp == "idw":
				cell = flt(p.get("cell")) or 10.0
				grid = {"cell": cell, "power": flt(p.get("power")) or 2, "neighbours": int(p.get("neighbours") or 12), "radius": flt(p.get("radius")) or None, "margin": flt(p.get("margin"))}
				r = mining(job, {"command": "idw_grid", "workspace": workspace, "name": f"idw_{k}_roof", "points": roof, **grid}, log)
				t = mining(job, {"command": "idw_grid", "workspace": workspace, "name": f"idw_{k}_thick", "points": [[a[0], a[1], a[2] - b[2]] for a, b in zip(roof, floor)], **grid}, log)
				items.append({"key": f"{k}:roof", "points": r["points"], "max_edge": "auto"})
				items.append({"key": f"{k}:floor", "points": write_floor_points(workspace, k, r["points"], t["points"]), "max_edge": "auto"})
				meta[f"{k}:roof"] = {"role": "roof", "seam": k, "variable": "drillholes", "label": k}
				meta[f"{k}:floor"] = {"role": "floor", "seam": k, "variable": "drillholes", "label": k}
				for q, pts in props.items():
					if len(pts) >= 3:
						r = mining(job, {"command": "idw_grid", "workspace": workspace, "name": f"idw_{k}_{len(items)}", "points": pts, **grid}, log)
						items.append({"key": f"{k}:{q}", "points": r["points"], "max_edge": "auto"})
						meta[f"{k}:{q}"] = {"role": "property", "seam": k, "variable": q, "label": q}
			else:
				edge = flt(p.get("max_edge")) or None
				add(f"{k}:roof", roof, "roof", k, "drillholes", k, edge)
				add(f"{k}:floor", floor, "floor", k, "drillholes", k, edge)
				for q, pts in props.items():
					if len(pts) >= 3:
						add(f"{k}:{q}", pts, "property", k, q, q, edge)
			seams.append({"key": k, "label": k, "roof": "drillholes", "floor": "drillholes"})
		if p.get("collars_as_topography") and len(holes) >= 3:
			add("topography", [h["collar"] for h in holes], "topography", None, "collars", _("Topography (collars)"), flt(p.get("max_edge")) or None)
		if interp == "idw":
			method = _("Drillholes of {0}: seam roof and thickness by inverse distance (cell {1} m, power {2}); floor = roof − thickness, so it is never above the roof.").format(project, flt(p.get("cell")) or 10, flt(p.get("power")) or 2)
		else:
			method = _("Drillholes of {0}: seam roof and floor intercepts triangulated (linear between holes; a hole with both gives floor ≤ roof on the same triangles).").format(project)
	if not items:
		frappe.throw(_("Nothing to model."))

	built = mining(job, {"command": "geology_surfaces", "workspace": workspace, "surfaces": items}, log)["surfaces"]
	tins, surfaces = {}, {}
	for b in built:
		m = meta[b["key"]]
		src = {"operation": "geology", "geo_project": project, "source": source, "key": b["key"], "variable": m["variable"]}
		ref = cad_surface_service.store_tin(job, workspace, b["tin"], b["stats"], src, log=log)
		tins[b["key"]] = b["tin"]
		surfaces[b["key"]] = {**m, "geometry_ref": ref, "points": b["points"], "spacing": b["spacing"], "stats": b["stats"], "source": src}
	checks, seam_rows = geology_checks(job, workspace, log, seams, tins, surfaces)
	status = "Fail" if any(c["status"] == "fail" for c in checks) else "Warning" if any(c["status"] == "warning" for c in checks) else "Pass"
	for old in frappe.get_all(GEOLOGY, filters={"cad_model": model, "geo_project": project, "source": source, "status": "Current"}, pluck="name"):
		frappe.db.set_value(GEOLOGY, old, "status", "Superseded", update_modified=False)
	record = frappe.get_doc(
		{
			"doctype": GEOLOGY,
			"title": f"{project} · {source}",
			"cad_model": model,
			"geo_project": project,
			"source": source,
			"status": "Current",
			"checks_status": status,
			"revision": cad_revision_service.working_revision(model),
			"processing_job": job.name,
			"axis_mapping": "Negate X and Y" if negate else "As stored",
			"method": method,
			"summary": _("{0} seams; {1} m³, {2} t in all.").format(len(seam_rows), round(sum(flt(r["volume"]) for r in seam_rows)), round(sum(flt(r.get("tonnes")) for r in seam_rows))),
			"seams": seam_rows,
			"checks": json.dumps(checks, default=float),
			"surfaces": json.dumps({k: {x: v[x] for x in ("role", "seam", "variable", "label", "geometry_ref", "points", "spacing")} for k, v in surfaces.items()}, default=float),
		}
	)
	record.flags.ignore_permissions = True
	record.insert()
	result = {
		"geology": record.name,
		"geo_project": project,
		"source": source,
		"axis": record.axis_mapping,
		"method": method,
		"checks_status": status,
		"checks": checks,
		"seams": seam_rows,
		"surfaces": [{"key": k, **{x: v[x] for x in ("role", "seam", "variable", "label", "geometry_ref", "stats", "source")}} for k, v in surfaces.items() if v["role"] != "property"],
		"properties": sorted(k for k, v in surfaces.items() if v["role"] == "property"),
		"holes": [{"hole_id": h["hole_id"], "collar": h["collar"], "end": h["end"], "seams": {k: [e["roof"], e["floor"]] for k, e in h["seams"].items()}} for h in holes],
	}
	message = _("Geology {0}: {1} seams, checks {2}.").format(record.name, len(seam_rows), _(status))
	return result, message


def write_floor_points(workspace, key, roof_file, thick_file):
	"""Floor = roof − thickness at the same grid nodes (the worker's .npy outputs), as a JSON point file."""
	import numpy as np  # the bench has numpy (pandas / shapely)

	r = np.load(cad_job_service.checked_output(workspace, roof_file))
	t = np.load(cad_job_service.checked_output(workspace, thick_file))
	n = min(len(r), len(t))
	floor = np.c_[r[:n, :2], r[:n, 2] - np.maximum(t[:n, 2], 0)]
	return write_input(workspace, f"floor_{key}.json", floor.tolist())


def geology_checks(job, workspace, log, seams, tins, surfaces):
	"""Per seam: floor above roof (area), seam volume, tonnes and qualities (one block over the whole seam); between
	seams in order of elevation: a lower seam's roof above the upper seam's floor (crossing); seam above topography."""
	checks, rows = [], []
	order = []
	for s in seams:
		k = s["key"]
		if f"{k}:roof" not in tins or f"{k}:floor" not in tins:
			continue
		v = surface(job, {"command": "volume", "workspace": workspace, "base": tins[f"{k}:roof"], "comparison": tins[f"{k}:floor"]}, log)
		bbox = surfaces[f"{k}:roof"]["stats"]["bbox"]
		props = {kind.split(":", 1)[1]: tins[kind] for kind in tins if kind.startswith(f"{k}:") and surfaces[kind]["role"] == "property" and not kind.endswith(":Thickness")}
		whole = [[bbox[0] - 1, bbox[1] - 1], [bbox[3] + 1, bbox[1] - 1], [bbox[3] + 1, bbox[4] + 1], [bbox[0] - 1, bbox[4] + 1]]
		g = mining(job, {"command": "block_geology", "workspace": workspace, "roof": tins[f"{k}:roof"], "floor": tins[f"{k}:floor"], "properties": props, "density": "Relative Density" if "Relative Density" in props else None, "default_density": 0, "cells": [{"key": "all", "polygon": whole}], "slabs": [[-1e7, 1e7]]}, log)["blocks"]["all"][0]
		area = flt(v["cut_area"])
		thick = surfaces.get(f"{k}:Thickness")
		row = {
			"seam": k,
			"seam_label": s.get("label") or k,
			"roof_variable": s.get("roof"),
			"floor_variable": s.get("floor"),
			"plan_area": area,
			"volume": g["volume"],
			"tonnes": g.get("tonnes") or 0,
			"mean_thickness": g["volume"] / area if area > 0 else 0,
			"max_thickness": flt(thick["stats"]["bbox"][5]) if thick else 0,
			"mean_density": g["qualities"].get("Relative Density") or 0,
			"qualities": json.dumps({q: round(val, 4) for q, val in g["qualities"].items()}),
			"roof_ref": surfaces[f"{k}:roof"]["geometry_ref"],
			"floor_ref": surfaces[f"{k}:floor"]["geometry_ref"],
		}
		rows.append(row)
		order.append((flt(surfaces[f"{k}:roof"]["stats"]["bbox"][5]), k))
		# a floor above the roof is the modeller's "seam absent" (negative thickness): not counted, but worth knowing
		status = "pass" if flt(v["fill_area"]) <= 1e-6 else "warning"
		checks.append({"check": _("Floor below roof"), "seam": k, "status": status, "value": v["fill_area"], "unit": "m²", "message": _("{0}: the floor is above the roof over {1} m² (negative thickness: the seam is taken as absent there).").format(k, round(flt(v["fill_area"]), 1)) if status != "pass" else _("{0}: the floor is never above the roof.").format(k)})
		if not row["tonnes"]:
			checks.append({"check": _("Density"), "seam": k, "status": "warning", "value": None, "unit": "", "message": _("{0}: no relative density: no tonnes (block geology uses its default density).").format(k)})
	# seams in order of elevation (highest roof first): the next seam's roof must not rise above this seam's floor
	order.sort(reverse=True)
	for (_z1, upper), (_z2, lower) in zip(order, order[1:]):
		v = surface(job, {"command": "volume", "workspace": workspace, "base": tins[f"{upper}:floor"], "comparison": tins[f"{lower}:roof"]}, log)
		crossing = flt(v["fill_area"])
		checks.append({"check": _("Seams do not cross"), "seam": f"{upper} / {lower}", "status": "pass" if crossing <= 1e-6 else "warning", "value": crossing, "unit": "m²", "message": _("{0} roof rises above the {1} floor over {2} m² (merged or split seams?).").format(lower, upper, round(crossing, 1)) if crossing > 1e-6 else _("{0} stays below {1}.").format(lower, upper)})
	if "topography" in tins:
		for _z, k in order:
			v = surface(job, {"command": "volume", "workspace": workspace, "base": tins["topography"], "comparison": tins[f"{k}:roof"]}, log)
			out = flt(v["fill_area"])
			checks.append({"check": _("Seam below topography"), "seam": k, "status": "pass" if out <= 1e-6 else "warning", "value": out, "unit": "m²", "message": _("{0} is above the topography over {1} m² (outcrop, or a surface mismatch).").format(k, round(out, 1)) if out > 1e-6 else _("{0} is below the topography everywhere.").format(k)})
	return checks, rows


# ---------------------------------------------------------------------------
# block geology
# ---------------------------------------------------------------------------


def run_block_geology(job, p, workspace, log):
	"""Seam volume, tonnes and tonnage-weighted qualities of every mining block in its bench slab, waste and strip
	ratio, and a material from the rule (coal where the seams fill at least `min_coal` % of the block)."""
	model = job.cad_model
	record = frappe.get_doc(GEOLOGY, p["geology"])
	if record.cad_model != model:
		frappe.throw(_("{0} is not a geology model of {1}.").format(record.name, model))
	surfaces = json.loads(record.surfaces or "{}")
	rows = frappe.get_all(ENTITY, filters={"cad_model": model, "metadata": ["like", '%"mining_block"%']}, fields=["entity_id", "geometry", "metadata"])
	wanted = set(p.get("blocks") or [])
	blocks = []
	for r in rows:
		meta = (frappe.parse_json(r.metadata) or {}).get("mining_block")
		if not meta or (wanted and r.entity_id not in wanted):
			continue
		pts = (frappe.parse_json(r.geometry) or {}).get("points") or []
		if len(pts) >= 3:
			blocks.append({"id": r.entity_id, "meta": meta, "polygon": [q[:2] for q in pts]})
	if not blocks:
		frappe.throw(_("Select mining blocks (or the model has none)."))
	slabs = sorted({(flt(b["meta"].get("bench_bottom")), flt(b["meta"].get("bench_top"))) for b in blocks})
	slab_index = {s: n for n, s in enumerate(slabs)}
	seams = [s for s in record.seams if not p.get("seams") or s.seam in p["seams"]]
	default_density = flt(p.get("default_density")) or 1.5
	per_block = {b["id"]: {} for b in blocks}
	for s in seams:
		staged = {"roof": cad_surface_service.stage_tin(model, s.roof_ref, workspace, f"bg_{s.seam}_roof"), "floor": cad_surface_service.stage_tin(model, s.floor_ref, workspace, f"bg_{s.seam}_floor")}
		props = {}
		for key, v in surfaces.items():
			if v["role"] == "property" and v["seam"] == s.seam and v["label"] != "Thickness":
				props[v["label"]] = cad_surface_service.stage_tin(model, v["geometry_ref"], workspace, f"bg_{len(props)}_{s.seam}")
		res = mining(
			job,
			{
				"command": "block_geology",
				"workspace": workspace,
				"roof": staged["roof"],
				"floor": staged["floor"],
				"properties": props,
				"density": "Relative Density" if "Relative Density" in props else None,
				"default_density": default_density,
				"cells": [{"key": b["id"], "polygon": b["polygon"]} for b in blocks],
				"slabs": [list(x) for x in slabs],
			},
			log,
		)["blocks"]
		for b in blocks:
			e = res[b["id"]][slab_index[(flt(b["meta"].get("bench_bottom")), flt(b["meta"].get("bench_top")))]]
			if e["volume"] > 1e-9:
				per_block[b["id"]][s.seam] = {"label": s.seam_label, "volume": e["volume"], "tonnes": e.get("tonnes") or default_density * e["volume"], "qualities": {q: round(v, 4) for q, v in e["qualities"].items()}}
	min_coal = flt(p.get("min_coal")) if p.get("min_coal") not in (None, "") else 50.0
	naming = p.get("material_by") or "coal"
	coal_name, waste_name = p.get("coal_material") or "Coal", p.get("waste_material") or "Overburden"
	updates, totals, by_seam = {}, {"block_volume": 0.0, "coal_volume": 0.0, "coal_tonnes": 0.0, "waste_volume": 0.0}, {}
	for b in blocks:
		seam_data = per_block[b["id"]]
		volume = flt(b["meta"].get("volume"))
		coal_v = sum(x["volume"] for x in seam_data.values())
		coal_t = sum(x["tonnes"] for x in seam_data.values())
		waste = max(volume - coal_v, 0.0)
		fraction = coal_v / volume * 100 if volume > 0 else 0.0
		if fraction >= min_coal - 1e-9 and seam_data:  # a block exactly at the threshold counts
			main = max(seam_data.items(), key=lambda kv: kv[1]["volume"])
			material = main[1]["label"] if naming == "seam" else coal_name
		else:
			material = waste_name
		updates[b["id"]] = {
			"material": material,
			"geology": {
				"model": record.name,
				"seams": seam_data,
				"coal_volume": coal_v,
				"coal_tonnes": coal_t,
				"waste_volume": waste,
				"coal_percent": fraction,
				"strip_ratio": waste / coal_t if coal_t > 0 else None,
			},
		}
		totals["block_volume"] += volume
		totals["coal_volume"] += coal_v
		totals["coal_tonnes"] += coal_t
		totals["waste_volume"] += waste
		for k, x in seam_data.items():
			t = by_seam.setdefault(k, {"seam": k, "label": x["label"], "volume": 0.0, "tonnes": 0.0, "q": {}})
			t["volume"] += x["volume"]
			t["tonnes"] += x["tonnes"]
			for q, v in x["qualities"].items():
				acc = t["q"].setdefault(q, [0.0, 0.0])
				acc[0] += v * x["tonnes"]
				acc[1] += x["tonnes"]
	seam_totals = [{"seam": t["seam"], "label": t["label"], "volume": t["volume"], "tonnes": t["tonnes"], "qualities": {q: a / w for q, (a, w) in t["q"].items() if w > 0}} for t in by_seam.values()]
	totals["strip_ratio"] = totals["waste_volume"] / totals["coal_tonnes"] if totals["coal_tonnes"] > 0 else None
	result = {"geology": record.name, "updates": updates, "seams": seam_totals, "totals": totals, "rule": {"min_coal": min_coal, "material_by": naming, "coal_material": coal_name, "waste_material": waste_name, "default_density": default_density}}
	message = _("{0} blocks: {1} t of coal ({2} m³), strip ratio {3} bcm/t.").format(len(blocks), round(totals["coal_tonnes"]), round(totals["coal_volume"]), round(totals["strip_ratio"], 2) if totals["strip_ratio"] else "—")
	return result, message
