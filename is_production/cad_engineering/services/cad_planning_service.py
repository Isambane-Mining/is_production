"""Mine planning integration (Phase 5): approved CAD designs as traceable references in Geo Planning and scheduling.

Publish      an Approved CAD Design Revision's mining blocks (read from its frozen, checksum-verified snapshot) become
             Geo Planning **Mining Blocks** of a Geo Pit Layout that names the CAD model as its source, with a material
             stack (one item per material, with its density) and a Material Summary per block (volume, tonnes,
             mineable or waste) — so the existing block selector, schedule selections and the scheduler use them
             unchanged. A **CAD Design Publication** records the revision, snapshot checksum, design standard version
             and design validation behind it. Publishing a later revision updates blocks not yet scheduled, keeps
             scheduled / mining / complete ones and flags them (and blocks the new design dropped) for review.
Sequence     the CAD mining sequence as a **Mining Schedule Selection** (blocks in sequence order).
Reconcile    a survey surface against the published design, block by block and bench by bench (exact, CAD worker):
             planned, mined to date, remaining in the design, mined beyond the design, and what the schedule planned
             to the survey date → **CAD Survey Reconciliation**; Mining Blocks get their % mined (and status).
Compare      what changed between two revisions (or a revision and the working state).
Overlay      schedule period / block status / % mined per CAD block, for colouring the design in the workstation.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, date_diff, flt, getdate, now_datetime

from is_production.cad_engineering.services import cad_job_service, cad_revision_service, cad_surface_service, cad_worker_service

PUBLICATION = "CAD Design Publication"
RECONCILIATION = "CAD Survey Reconciliation"
STACK_NAME = "CAD Materials"
EDITABLE_STATES = ("", None, "Draft", "Available", "Planned")  # a block in these states follows the new design


# ---------------------------------------------------------------------------
# publish
# ---------------------------------------------------------------------------


def snapshot_blocks(revision):
	"""Mining blocks of a frozen revision (checksum verified): [(entity, block metadata)]."""
	content = cad_revision_service.load_snapshot(revision)["content"]
	out = []
	for e in content["entities"]:
		meta = e.get("metadata") or {}
		if isinstance(meta, str):
			meta = frappe.parse_json(meta) or {}
		b = meta.get("mining_block")
		g = e.get("geometry") or {}
		if isinstance(g, str):
			g = frappe.parse_json(g) or {}
		if b and e["entity_type"] == "Polyline" and g.get("closed"):
			out.append((e, g, b))
	return out


def design_validation_for(revision):
	results = frappe.parse_json(frappe.db.get_value("CAD Design Revision", revision, "validation_results") or "{}") or {}
	name = results.get("design_validation")
	if not name:
		row = frappe.get_all("CAD Design Validation", filters={"revision": revision}, fields=["name"], order_by="creation desc", limit=1)
		name = row[0].name if row else None
	return frappe.get_doc("CAD Design Validation", name) if name else None


def check_publisher(model):
	frappe.get_doc("CAD Model", model).check_permission("read")
	# publishing to planning is a reviewer's act, like approving (Production Manager)
	if not frappe.has_permission("CAD Design Revision", "write") or not frappe.has_permission("Mining Block", "create"):
		frappe.throw(_("Only a reviewer who may create Mining Blocks (Production Manager) can publish a design to Geo Planning."), frappe.PermissionError)


def publish_preview(model, revision):
	"""What a publication would use: the revision, its validation, the materials and their block volumes."""
	check_publisher(model)
	rev = frappe.get_doc("CAD Design Revision", revision)
	if rev.cad_model != model:
		frappe.throw(_("{0} is not a revision of {1}.").format(revision, model))
	blocks = snapshot_blocks(revision)
	materials = {}
	for _e, _g, b in blocks:
		m = (b.get("material") or "").strip() or _("Unclassified")
		row = materials.setdefault(m, {"material": m, "blocks": 0, "volume": 0.0})
		row["blocks"] += 1
		row["volume"] += flt(b.get("volume"))
	validation = design_validation_for(revision)
	layout = frappe.db.get_value("Geo Pit Layout", {"source_cad_model": model}, ["name", "geo_project"], as_dict=True)
	return {
		"revision": rev.name,
		"revision_label": rev.revision_label,
		"revision_status": rev.revision_status,
		"blocks": len(blocks),
		"materials": sorted(materials.values(), key=lambda r: -r["volume"]),
		"validation": {"name": validation.name, "status": validation.status, "standard": validation.design_standard} if validation else None,
		"geo_project": (layout and layout.geo_project) or frappe.db.get_value("CAD Model", model, "geo_project"),
		"layout": layout and layout.name,
		"projects": frappe.get_list("Geo Project", fields=["name", "project_name"], limit_page_length=200),
	}


def publish(model, revision, geo_project=None, materials=None, make_selection=True):
	check_publisher(model)
	rev = frappe.get_doc("CAD Design Revision", revision)
	if rev.cad_model != model:
		frappe.throw(_("{0} is not a revision of {1}.").format(revision, model))
	if rev.revision_status != "Approved":
		frappe.throw(_("Only an Approved revision can be published to planning ({0} is {1}).").format(revision, _(rev.revision_status)))
	validation = design_validation_for(revision)
	if not validation:
		frappe.throw(_("{0} has no design validation: validate the design before submitting it.").format(revision))
	if validation.status == "Fail":
		frappe.throw(_("The design validation {0} of {1} failed: a failed design cannot be published.").format(validation.name, revision))
	blocks = snapshot_blocks(revision)  # verifies the snapshot checksum
	if not blocks:
		frappe.throw(_("{0} has no mining blocks to publish.").format(revision))
	model_doc = frappe.get_doc("CAD Model", model)
	geo_project = geo_project or model_doc.geo_project
	if not geo_project:
		frappe.throw(_("Choose the Geo Project to publish to."))
	if not model_doc.geo_project:
		model_doc.db_set("geo_project", geo_project)
	materials = frappe.parse_json(materials) if isinstance(materials, str) else materials or {}

	frappe.db.savepoint("cad_publish")
	try:
		common = blocks[0][2]
		layout = ensure_layout(model, rev, geo_project, common, blocks)
		names = [(b.get("material") or "").strip() or _("Unclassified") for _e, _g, b in blocks]
		order = sorted(set(names), key=lambda m: -sum(flt(b.get("volume")) for (_e, _g, b), n in zip(blocks, names) if n == m))
		stack = ensure_stack(layout, geo_project, order, materials)
		result = upsert_blocks(model, rev, layout, stack, geo_project, blocks, names, materials)
		previous = frappe.get_all(PUBLICATION, filters={"cad_model": model, "status": "Published"}, pluck="name")
		std = frappe.get_doc("CAD Design Standard", validation.design_standard) if validation.design_standard else None
		pub = frappe.get_doc(
			{
				"doctype": PUBLICATION,
				"cad_model": model,
				"revision": rev.name,
				"revision_label": rev.revision_label,
				"geometry_checksum": rev.geometry_checksum,
				"status": "Published",
				"published_by": frappe.session.user,
				"published_on": now_datetime(),
				"design_standard": validation.design_standard,
				"standard_version": std.version if std else None,
				"design_validation": validation.name,
				"validation_status": validation.status,
				"geo_project": geo_project,
				"geo_pit_layout": layout.name,
				"material_stack": stack.name,
				"blocks_published": len(blocks),
				"blocks_created": result["created"],
				"blocks_updated": result["updated"],
				"blocks_unchanged": result["unchanged"],
				"blocks_flagged": len(result["flagged"]),
				"total_volume": result["volume"],
				"total_tonnes": result["tonnes"],
				"materials": json.dumps(result["materials"]),
				"report": json.dumps({"flagged": result["flagged"][:500], "upper_ref": common.get("upper_ref"), "lower_ref": common.get("lower_ref"), "upper": common.get("upper"), "lower": common.get("lower")}),
			}
		)
		pub.flags.ignore_permissions = True
		pub.insert()
		for name in previous:
			frappe.db.set_value(PUBLICATION, name, {"status": "Superseded", "superseded_by": pub.name}, update_modified=False)
		frappe.db.set_value("Mining Block", {"name": ["in", result["block_names"]]}, "cad_publication", pub.name, update_modified=False)
		layout.db_set("cad_publication", pub.name)
		if make_selection:
			selection = sequence_selection(pub, result["sequence"])
			pub.db_set("schedule_selection", selection)
	except Exception:
		frappe.db.rollback(save_point="cad_publish")
		raise
	return publication_payload(pub.name)


def ensure_layout(model, rev, geo_project, common, blocks):
	name = frappe.db.get_value("Geo Pit Layout", {"source_cad_model": model})
	values = {
		"layout_version": rev.revision_label,
		"source_cad_revision": rev.name,
		"layout_status": "Approved",
		"block_count": len(blocks),
		"total_area": sum(flt(b.get("plan_area")) for _e, _g, b in blocks),
		"effective_area": sum(flt(b.get("plan_area")) for _e, _g, b in blocks),
		"remarks": _("Published from the approved CAD design {0} revision {1}.").format(model, rev.revision_label),
	}
	if name:
		layout = frappe.get_doc("Geo Pit Layout", name)
		if layout.geo_project != geo_project:
			frappe.throw(_("{0} was published to Geo Project {1}; publish it there again.").format(model, layout.geo_project))
		layout.update(values)
	else:
		layout = frappe.get_doc(
			{
				"doctype": "Geo Pit Layout",
				"layout_name": f"CAD {model}",
				"layout_code": model,
				"geo_project": geo_project,
				"layout_type": "Final Layout",
				"numbering_style": "Custom",
				"block_size_x": flt(common.get("block_length")) or 50,
				"block_size_y": flt(common.get("block_width")) or 25,
				"block_angle_degrees": flt(common.get("direction")),
				"source_cad_model": model,
				**values,
			}
		)
	layout.flags.ignore_permissions = True
	layout.save()
	return layout


def ensure_stack(layout, geo_project, order, materials):
	name = frappe.db.get_value("Geo Pit Layout Material Stack", {"geo_pit_layout": layout.name, "stack_name": STACK_NAME})
	stack = frappe.get_doc("Geo Pit Layout Material Stack", name) if name else frappe.new_doc("Geo Pit Layout Material Stack")
	stack.update({"stack_name": STACK_NAME, "geo_project": geo_project, "geo_pit_layout": layout.name, "stack_status": "Approved", "is_default_stack": 1, "remarks": _("Materials of the CAD design (densities given when it was published).")})
	stack.set("item", [])
	for n, m in enumerate(order, start=1):
		density = flt((materials.get(m) or {}).get("density"))
		stack.append(
			"item",
			{
				"sort_order": n,
				"mining_sequence_no": n,
				"material_seam": m,
				"value_type": "Thickness",
				"use_for_volume": 1,
				"use_for_tonnes": 1 if density else 0,
				"use_for_scheduling": 1,
				"density_source": "Manual" if density else "None",
				"manual_density": density,
				"aggregation_method": "Sum",
			},
		)
	stack.flags.ignore_permissions = True
	stack.save()
	return stack


def polygon_geojson(points):
	ring = [[float(p[0]), float(p[1])] for p in points]  # full precision: planning volumes must match the design's
	return json.dumps({"type": "Polygon", "coordinates": [ring + ring[:1]]})


def upsert_blocks(model, rev, layout, stack, geo_project, blocks, names, materials):
	from shapely.geometry import Polygon

	benches = sorted({flt(b.get("bench_bottom")) for _e, _g, b in blocks}, reverse=True)  # cut 1 = the top bench
	existing = {
		r.mining_block_code: r
		for r in frappe.get_all("Mining Block", filters={"source_pit_layout": layout.name}, fields=["name", "mining_block_code", "block_status", "planning_status", "design_volume", "polygon_geojson", "cad_model"])
	}
	created = updated = unchanged = 0
	flagged, block_names, volume, tonnes = [], [], 0.0, 0.0
	summary = {}
	sequence = []
	for (e, g, b), material in zip(blocks, names):
		code = b["block_id"]
		poly = Polygon([p[:2] for p in g["points"]])
		c = poly.centroid
		density = flt((materials.get(material) or {}).get("density"))
		status = (materials.get(material) or {}).get("status") or "Mineable"
		v = flt(b.get("volume"))
		t = v * density
		values = {
			"mining_block_code": code,
			"geo_project": geo_project,
			"source_pit_layout": layout.name,
			"cut_no": benches.index(flt(b.get("bench_bottom"))) + 1,
			"block_no": cint(b.get("sequence")),
			"row_no": cint(b.get("row")),
			"column_no": cint(b.get("col")),
			"centroid_x": c.x,
			"centroid_y": c.y,
			"area": poly.area,
			"effective_area": poly.area,
			"inside_percent": 100,
			"polygon_geojson": polygon_geojson(g["points"]),
			"cad_model": model,
			"cad_revision": rev.name,
			"cad_entity_id": e["entity_id"],
			"cad_sequence": cint(b.get("sequence")),
			"material": material,
			"bench_bottom": flt(b.get("bench_bottom")),
			"bench_top": flt(b.get("bench_top")),
			"design_volume": v,
		}
		old = existing.pop(code, None)
		if old is None:
			doc = frappe.get_doc({"doctype": "Mining Block", **values, "block_status": "Available", "planning_status": "Mineable"})
			doc.flags.ignore_permissions = True
			doc.insert()
			created += 1
		else:
			doc = frappe.get_doc("Mining Block", old.name)
			changed = abs(flt(old.design_volume) - v) > 1e-6 or (old.polygon_geojson or "") != values["polygon_geojson"]
			if old.block_status in EDITABLE_STATES:
				doc.update(values)
				if doc.planning_status == "Review":
					doc.planning_status = "Mineable"
				updated += 1 if changed else 0
				unchanged += 0 if changed else 1
			else:
				# scheduled, being mined or complete: the planning record is kept; a changed design needs a decision
				doc.update({k: values[k] for k in ("cad_revision", "cad_entity_id", "cad_sequence", "material")})
				if changed:
					doc.planning_status = "Review"
					doc.remarks = _("CAD revision {0} changed this {1} block (volume {2} → {3} m³).").format(rev.revision_label, _(old.block_status), round(flt(old.design_volume), 3), round(v, 3))
					flagged.append({"block": code, "reason": doc.remarks})
				else:
					unchanged += 1
			doc.flags.ignore_permissions = True
			doc.save()
		block_names.append(doc.name)
		sequence.append((cint(b.get("sequence")), doc.name))
		volume += v
		tonnes += t
		row = summary.setdefault(material, {"density": density, "status": status, "blocks": 0, "volume": 0.0, "tonnes": 0.0})
		row["blocks"] += 1
		row["volume"] += v
		row["tonnes"] += t
		set_material_summary(doc, stack, geo_project, material, v, density, t, status)
	# blocks the new design no longer has
	for old in existing.values():
		if old.cad_model != model or old.block_status in ("Excluded", "Complete"):
			continue
		frappe.db.set_value("Mining Block", old.name, {"planning_status": "Review", "remarks": _("Not in CAD revision {0}.").format(rev.revision_label)}, update_modified=True)
		flagged.append({"block": old.mining_block_code, "reason": _("Not in CAD revision {0} (block {1}).").format(rev.revision_label, _(old.block_status or "Draft"))})
	sequence.sort()
	return {"created": created, "updated": updated, "unchanged": unchanged, "flagged": flagged, "block_names": block_names, "volume": volume, "tonnes": tonnes, "materials": summary, "sequence": [n for _s, n in sequence]}


def set_material_summary(block, stack, geo_project, material, volume, density, tonnes, status):
	name = frappe.db.get_value("Mining Block Material Summary", {"mining_block": block.name, "material_stack": stack.name})
	doc = frappe.get_doc("Mining Block Material Summary", name) if name else frappe.new_doc("Mining Block Material Summary")
	doc.update(
		{
			"mining_block": block.name,
			"geo_project": geo_project,
			"source_pit_layout": block.source_pit_layout,
			"material_stack": stack.name,
			"material_seam": material,
			"effective_area": block.effective_area,
			"volume": volume,
			"density_value": density,
			"tonnes": tonnes,
			"material_status": "Waste" if status == "Waste" else "Mineable",
			"calculation_status": "Calculated",
			"remarks": _("From CAD block {0} ({1}).").format(block.mining_block_code, block.cad_revision),
		}
	)
	doc.flags.ignore_permissions = True
	doc.save()


def sequence_selection(pub, block_names, name=None):
	"""The CAD mining sequence as a Mining Schedule Selection (blocks in sequence order)."""
	from is_production.geo_planning.services.mining_schedule_selection_service import create_selection_from_blocks

	pub = frappe.get_doc(PUBLICATION, pub) if isinstance(pub, str) else pub
	label = name or f"CAD {pub.cad_model} {pub.revision_label}"
	existing = frappe.db.get_value("Mining Schedule Selection", {"geo_project": pub.geo_project, "selection_name": label})
	if existing:
		label = f"{label} ({frappe.utils.now_datetime().strftime('%Y-%m-%d %H%M')})"
	out = create_selection_from_blocks(
		selection_name=label,
		selection_type="Custom",
		geo_project=pub.geo_project,
		geo_pit_layout=pub.geo_pit_layout,
		material_stack=pub.material_stack,
		selected_blocks=[{"name": n, "remarks": _("CAD sequence")} for n in block_names],
		remarks=_("Mining sequence of {0} revision {1} ({2}).").format(pub.cad_model, pub.revision_label, pub.name),
	)
	return out["name"]


def selection_from_sequence(model, block_entity_ids=None):
	"""A new schedule selection from the published blocks (all, or those of some CAD blocks), in CAD sequence."""
	check_publisher(model)
	pub = latest_publication(model)
	filters = {"cad_publication": pub.name}
	if block_entity_ids:
		filters["cad_entity_id"] = ["in", frappe.parse_json(block_entity_ids) if isinstance(block_entity_ids, str) else block_entity_ids]
	rows = frappe.get_all("Mining Block", filters=filters, fields=["name"], order_by="cad_sequence asc")
	if not rows:
		frappe.throw(_("None of these blocks is published."))
	name = sequence_selection(pub, [r.name for r in rows], f"CAD {model} {pub.revision_label} · {len(rows)} blocks")
	return {"selection": name, "blocks": len(rows)}


def latest_publication(model):
	name = frappe.db.get_value(PUBLICATION, {"cad_model": model, "status": "Published"})
	if not name:
		frappe.throw(_("{0} has not been published to Geo Planning yet.").format(model))
	return frappe.get_doc(PUBLICATION, name)


def publication_payload(name):
	pub = frappe.get_doc(PUBLICATION, name)
	return {**pub.as_dict(), "materials": frappe.parse_json(pub.materials), "report": frappe.parse_json(pub.report)}


# ---------------------------------------------------------------------------
# planning overlay (schedule period / status / % mined per CAD block)
# ---------------------------------------------------------------------------


def scenarios_for(model):
	layouts = frappe.get_all("Geo Pit Layout", filters={"source_cad_model": model}, pluck="name")
	if not layouts:
		return []
	return frappe.get_list("Mining Schedule Scenario", filters={"geo_pit_layout": ["in", layouts]}, fields=["name", "scenario_name", "schedule_status", "start_date", "end_date", "period_type"], order_by="modified desc", limit_page_length=100)


def overlay(model, scenario=None):
	frappe.get_doc("CAD Model", model).check_permission("read")
	blocks = frappe.get_all("Mining Block", filters={"cad_model": model}, fields=["name", "mining_block_code", "cad_entity_id", "block_status", "planning_status", "percent_mined", "cad_sequence", "design_volume", "material"])
	by_name = {b.name: b for b in blocks}
	periods = {}
	if scenario:
		doc = frappe.get_doc("Mining Schedule Scenario", scenario)
		doc.check_permission("read")
		for row in doc.scheduled_blocks or []:
			if row.mining_block in by_name:
				p = periods.setdefault(row.mining_block, {"period_no": row.period_no, "period_label": row.period_label, "start": row.planned_start_date, "end": row.planned_end_date})
				p["period_no"] = min(p["period_no"] or row.period_no, row.period_no or p["period_no"])
		for a in frappe.get_all("Mining Schedule Allocation", filters={"schedule_scenario": scenario, "mining_block": ["in", list(by_name)], "allocation_status": ["!=", "Cancelled"]}, fields=["mining_block", "allocation_date"], order_by="allocation_date asc"):
			p = periods.setdefault(a.mining_block, {"period_no": None, "period_label": None, "start": a.allocation_date, "end": a.allocation_date})
			p["start"] = min(getdate(p["start"]), getdate(a.allocation_date)) if p.get("start") else a.allocation_date
			p["end"] = max(getdate(p["end"]), getdate(a.allocation_date)) if p.get("end") else a.allocation_date
	out = {}
	for b in blocks:
		if not b.cad_entity_id:
			continue
		p = periods.get(b.name) or {}
		out[b.cad_entity_id] = {**b, "period_no": p.get("period_no"), "period_label": p.get("period_label"), "start": str(p["start"]) if p.get("start") else None, "end": str(p["end"]) if p.get("end") else None}
	return {"blocks": out, "scenarios": scenarios_for(model), "publication": frappe.db.get_value(PUBLICATION, {"cad_model": model, "status": "Published"})}


# ---------------------------------------------------------------------------
# survey reconciliation (planned versus actual)
# ---------------------------------------------------------------------------


def run_reconcile(job, p, workspace, log):
	model = job.cad_model
	pub = frappe.get_doc(PUBLICATION, p["publication"]) if p.get("publication") else latest_publication(model)
	report = frappe.parse_json(pub.report) or {}
	pre = p.get("pre_mining") or report.get("upper_ref")
	design = p.get("design") or report.get("lower_ref")
	survey = p.get("survey")
	if not (pre and design and survey):
		frappe.throw(_("Choose the survey, pre-mining and design surfaces."))
	names = {ref: cad_surface_service_name(model, ref) for ref in (pre, design, survey)}
	survey_date = getdate(p.get("survey_date") or frappe.utils.today())
	blocks = frappe.get_all(
		"Mining Block",
		filters={"cad_publication": pub.name},
		fields=["name", "mining_block_code", "cad_entity_id", "bench_bottom", "bench_top", "material", "design_volume", "polygon_geojson", "block_status"],
		order_by="cad_sequence asc",
	)
	if not blocks:
		frappe.throw(_("{0} has no published blocks.").format(pub.name))
	# one cell per distinct outline: blocks on different benches share their outline, and a cell must not be given
	# twice (each triangle belongs to one cell); the bench picks the slab
	cell_of, cells = {}, []
	for b in blocks:
		ring = json.loads(b.polygon_geojson)["coordinates"][0][:-1]
		key = json.dumps(ring)
		if key not in cell_of:
			cell_of[key] = f"C{len(cells)}"
			cells.append({"key": cell_of[key], "polygon": ring})
		b.cell = cell_of[key]
	slabs = sorted({(flt(b.bench_bottom), flt(b.bench_top)) for b in blocks})
	slab_index = {s: i for i, s in enumerate(slabs)}
	staged = {ref: cad_surface_service.stage_tin(model, ref, workspace, f"s{n}") for n, ref in enumerate({pre, design, survey})}

	def run(upper, lower, label):
		log.append(label)
		r = cad_job_service.run_worker(job, {"command": "block_volumes", "workspace": workspace, "upper": staged[upper], "lower": staged[lower], "cells": cells, "slabs": [list(s) for s in slabs]}, log, script=cad_worker_service.MINING_WORKER)
		return {b.name: r["volumes"][b.cell][slab_index[(flt(b.bench_bottom), flt(b.bench_top))]] for b in blocks}, r["method"]

	planned, method = run(pre, design, _("Planned: pre-mining surface to design"))
	mined, _m = run(pre, survey, _("Mined: pre-mining surface to the survey"))
	remaining, _m = run(survey, design, _("Remaining: the survey to the design"))
	over, _m = run(design, survey, _("Beyond the design: the design to the survey"))
	scheduled = scheduled_to_date(p.get("scenario"), blocks, survey_date)

	rows, checks = [], {"design_matches_publication": True, "balance_holds": True, "max_balance_error": 0.0, "max_design_difference": 0.0}
	totals = dict.fromkeys(("planned", "mined", "remaining", "over", "scheduled"), 0.0)
	states = {"Not Started": 0, "In Progress": 0, "Complete": 0}
	for b in blocks:
		pl, mi, re_, ov = planned[b.name], mined[b.name], remaining[b.name], over[b.name]
		tol = 1e-6 * max(1.0, pl)
		checks["max_design_difference"] = max(checks["max_design_difference"], abs(pl - flt(b.design_volume)))
		if abs(pl - flt(b.design_volume)) > max(tol, 1e-3):
			checks["design_matches_publication"] = False
		balance = abs((mi - ov + re_) - pl)  # holds wherever the survey is not above the pre-mining surface (no dumping)
		checks["max_balance_error"] = max(checks["max_balance_error"], balance)
		if balance > max(tol, 1e-3):
			checks["balance_holds"] = False
		pct = 100.0 * min(1.0, (mi - ov) / pl) if pl > 1e-9 else 0.0
		pct = max(pct, 0.0)
		state = "Complete" if pct >= 98 or (pl > 1e-9 and re_ <= 0.02 * pl) else "In Progress" if mi > 0.02 * pl else "Not Started"
		states[state] += 1
		s = scheduled.get(b.name, 0.0)
		rows.append(
			{
				"block_id": b.mining_block_code,
				"mining_block": b.name,
				"cad_entity_id": b.cad_entity_id,
				"bench_bottom": b.bench_bottom,
				"material": b.material,
				"planned_volume": pl,
				"mined_volume": mi,
				"remaining_volume": re_,
				"over_mined_volume": ov,
				"scheduled_volume": s,
				"percent_complete": pct,
				"block_state": state,
			}
		)
		for k, v in (("planned", pl), ("mined", mi), ("remaining", re_), ("over", ov), ("scheduled", s)):
			totals[k] += v
	valid = checks["design_matches_publication"]
	if not checks["balance_holds"]:
		log.append(_("Mined − beyond design + remaining ≠ planned on some blocks: the survey is above the pre-mining surface there (dumped or rehandled material)."))
	rec = frappe.get_doc(
		{
			"doctype": RECONCILIATION,
			"cad_model": model,
			"publication": pub.name,
			"survey_date": survey_date,
			"survey_name": names[survey],
			"survey_surface": frappe.db.get_value("CAD Surface", {"cad_model": model, "geometry_file_reference": survey}),
			"survey_geometry_ref": survey,
			"processing_job": job.name,
			"valid": 1 if valid else 0,
			"pre_mining_name": names[pre],
			"pre_mining_geometry_ref": pre,
			"design_name": names[design],
			"design_geometry_ref": design,
			"schedule_scenario": p.get("scenario") or None,
			"planned_volume": totals["planned"],
			"mined_volume": totals["mined"],
			"remaining_volume": totals["remaining"],
			"over_mined_volume": totals["over"],
			"scheduled_volume": totals["scheduled"],
			"variance_to_schedule": totals["mined"] - totals["scheduled"] if p.get("scenario") else 0,
			"blocks_complete": states["Complete"],
			"blocks_in_progress": states["In Progress"],
			"blocks_not_started": states["Not Started"],
			"blocks": rows,
			"method": method + " " + _("Planned: pre-mining surface to design; mined: pre-mining surface to survey; remaining: survey to design; beyond design: design to survey — each inside the block outline and its bench."),
			"checks": json.dumps(checks),
		}
	)
	rec.flags.ignore_permissions = True
	rec.insert()
	if p.get("update_blocks", True):
		for row in rows:
			values = {"percent_mined": row["percent_complete"], "last_reconciliation": rec.name}
			status = frappe.db.get_value("Mining Block", row["mining_block"], "block_status")
			if row["block_state"] == "Complete" and status in ("Available", "Planned", "Scheduled", "Mining", "", None):
				values["block_status"] = "Complete"
			elif row["block_state"] == "In Progress" and status in ("Available", "Planned", "Scheduled", "", None):
				values["block_status"] = "Mining"
			frappe.db.set_value("Mining Block", row["mining_block"], values, update_modified=True)
	result = {"name": rec.name, "totals": totals, "states": states, "checks": checks, "valid": valid, "rows": rows, "survey": names[survey], "survey_date": str(survey_date), "scenario": p.get("scenario")}
	job.db_set("results", json.dumps(result, default=str))
	return result, _("Survey {0}: {1} of {2} m³ mined ({3} complete, {4} in progress).").format(survey_date, round(totals["mined"], 1), round(totals["planned"], 1), states["Complete"], states["In Progress"])


def cad_surface_service_name(model, ref):
	row = frappe.db.get_value("CAD Entity", {"cad_model": model, "geometry_ref": ref, "entity_type": "Surface"}, "geometry")
	if not row:
		frappe.throw(_("Surface {0} is not a saved surface of {1}.").format((ref or "")[:12], model))
	return (frappe.parse_json(row) or {}).get("name") or ref[:12]


def scheduled_to_date(scenario, blocks, survey_date):
	"""BCM the schedule planned per block up to the survey date: its allocations, else its periods pro rata."""
	if not scenario:
		return {}
	names = [b.name for b in blocks]
	out = {}
	allocations = frappe.get_all(
		"Mining Schedule Allocation",
		filters={"schedule_scenario": scenario, "mining_block": ["in", names], "allocation_date": ["<=", survey_date], "allocation_status": ["!=", "Cancelled"], "unit": "BCM"},
		fields=["mining_block", "scheduled_quantity"],
	)
	for a in allocations:
		out[a.mining_block] = out.get(a.mining_block, 0.0) + flt(a.scheduled_quantity)
	if out:
		return out
	doc = frappe.get_doc("Mining Schedule Scenario", scenario)
	for row in doc.scheduled_blocks or []:
		if row.mining_block not in names or not row.planned_start_date:
			continue
		start, end = getdate(row.planned_start_date), getdate(row.planned_end_date or row.planned_start_date)
		if survey_date < start:
			continue
		days = max(date_diff(end, start) + 1, 1)
		done = min(max(date_diff(survey_date, start) + 1, 0), days)
		out[row.mining_block] = out.get(row.mining_block, 0.0) + flt(row.total_volume) * done / days
	return out


# ---------------------------------------------------------------------------
# revision comparison
# ---------------------------------------------------------------------------


def content_of(model, revision):
	if revision in (None, "", "working"):
		return cad_revision_service.current_content(model)["entities"]
	rev = frappe.get_doc("CAD Design Revision", revision)
	if rev.cad_model != model:
		frappe.throw(_("{0} is not a revision of {1}.").format(revision, model))
	return cad_revision_service.load_snapshot(revision)["content"]["entities"]


def compare(model, older, newer=None):
	"""Entities added, removed and changed from `older` to `newer` (a revision, or the working state)."""
	frappe.get_doc("CAD Model", model).check_permission("read")

	def index(entities):
		out = {}
		for e in entities:
			for key in ("geometry", "metadata"):
				if isinstance(e.get(key), str):
					e[key] = frappe.parse_json(e[key])
			out[e["entity_id"]] = e
		return out

	a, b = index(content_of(model, older)), index(content_of(model, newer))
	added = [label(b[i]) for i in sorted(set(b) - set(a))]
	removed = [label(a[i]) for i in sorted(set(a) - set(b))]
	changed = []
	for i in sorted(set(a) & set(b)):
		what = [k for k in ("geometry", "geometry_ref", "layer", "color", "line_type", "line_weight", "metadata") if json.dumps(a[i].get(k), sort_keys=True, default=str) != json.dumps(b[i].get(k), sort_keys=True, default=str)]
		if what:
			changed.append({**label(b[i]), "changed": what})
	by_type = {}
	for kind, rows in (("added", added), ("removed", removed), ("changed", changed)):
		for r in rows:
			by_type.setdefault(r["entity_type"], dict.fromkeys(("added", "removed", "changed"), 0))[kind] += 1

	def design(entities):
		blocks = [e["metadata"]["mining_block"] for e in entities.values() if (e.get("metadata") or {}).get("mining_block")]
		holes = [e for e in entities.values() if ((e.get("metadata") or {}).get("drill_hole") or {}).get("role") == "hole"]
		stages = {(e.get("metadata") or {}).get("pit_design", {}).get("stage") for e in entities.values() if (e.get("metadata") or {}).get("pit_design")}
		return {"blocks": len(blocks), "block_volume": sum(flt(x.get("volume")) for x in blocks), "drill_holes": len(holes), "pit_stages": len(stages - {None}), "surfaces": sum(1 for e in entities.values() if e["entity_type"] == "Surface"), "entities": len(entities)}

	return {"older": older, "newer": newer or "working", "added": added[:2000], "removed": removed[:2000], "changed": changed[:2000], "counts": {"added": len(added), "removed": len(removed), "changed": len(changed)}, "by_type": by_type, "design": {"older": design(a), "newer": design(b)}}


def label(e):
	g = e.get("geometry") or {}
	meta = e.get("metadata") or {}
	name = g.get("name") or (meta.get("mining_block") or {}).get("block_id") or (meta.get("drill_hole") or {}).get("hole_id")
	return {"entity_id": e["entity_id"], "entity_type": e["entity_type"], "layer": e.get("layer"), "name": name}


def revision_content(model, revision):
	"""A frozen revision's entities, layers and blocks, for drawing it faded over the model (comparison)."""
	frappe.get_doc("CAD Model", model).check_permission("read")
	rev = frappe.get_doc("CAD Design Revision", revision)
	if rev.cad_model != model:
		frappe.throw(_("{0} is not a revision of {1}.").format(revision, model))
	content = cad_revision_service.load_snapshot(revision)["content"]
	return {"entities": content["entities"], "layers": content["layers"], "blocks": content.get("blocks") or []}

