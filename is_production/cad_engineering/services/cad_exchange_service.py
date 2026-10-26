"""Engineering file exchange (CAD Import Export): DXF import with a validation report, and DXF export.

Import is two steps, so nobody commits a file they have not looked at:

1. ``start_import`` — the uploaded file (kept, never changed) is read by the DXF worker; the record gets a
   validation report: DXF version, units, layers, entity counts, unsupported/invalid/duplicate entities,
   extents and elevation range (**Inspect Import** job). Nothing is written to the model.
2. ``commit_import`` — with the chosen source units, CRS and axis mapping, the worker converts the file and
   the bench adds layers, blocks and entities to the model's **Working** revision in one transaction
   (**Import** job). An approved revision is never touched; a model in review refuses imports. Entities that
   fail the model's checks are skipped and listed. The model's transformation history records the import.

Export writes the model's live state, or any frozen revision, as DXF (**Export** job); the file is attached
to the record. Solids go out as MESH approximations of their display meshes, which the report states.
"""

import json
import os
import shutil

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from is_production.cad_engineering.services import cad_exchange_formats as formats
from is_production.cad_engineering.services import cad_job_service, cad_model_service, cad_revision_service, cad_surface_service, cad_worker_service
from is_production.cad_engineering.services import cad_storage_service as storage

IO = "CAD Import Export"
MODEL = "CAD Model"
UNITS_TO_METRES = {
	"Metres": 1.0,
	"Millimetres": 0.001,
	"Centimetres": 0.01,
	"Kilometres": 1000.0,
	"Feet": 0.3048,
	"US Survey Feet": 1200.0 / 3937.0,
	"Inches": 0.0254,
	"Yards": 0.9144,
}
AXIS_MAPPINGS = ("As in file", "Negate X and Y", "Swap X and Y")
DXF_VERSIONS = ("R12", "R2000", "R2004", "R2007", "R2010", "R2013", "R2018")
REPORT_WARNING_LIMIT = 300


def payload(io):
	return {
		"name": io.name,
		"operation_type": io.operation_type,
		"file_format": io.file_format,
		"cad_model": io.cad_model,
		"processing_status": io.processing_status,
		"processing_job": io.processing_job,
		"source_file": io.source_file,
		"result_file": io.result_file,
		"format_version": io.format_version,
		"source_units": io.source_units,
		"target_units": io.target_units,
		"unit_scale": io.unit_scale,
		"source_crs": io.source_crs,
		"target_crs": io.target_crs,
		"axis_mapping": io.axis_mapping,
		"entity_count": io.entity_count,
		"warning_count": io.warning_count,
		"report": frappe.parse_json(io.validation_results) if io.validation_results else None,
		"options": frappe.parse_json(io.options) if io.options else None,
		"warnings": io.warnings,
		"revision": io.revision,
	}


def get_exchange(name):
	io = frappe.get_doc(IO, name)
	io.check_permission("read")
	return payload(io)


def check_model(model, ptype="write"):
	doc = frappe.get_doc(MODEL, model)
	doc.check_permission(ptype)
	if ptype == "write" and doc.model_status == "Archived":
		frappe.throw(_("{0} is archived.").format(model))
	return doc


def source_file(file_url):
	"""The uploaded file: readable by the user, a supported format, within the size limit."""
	from is_production.cad_engineering.services.cad_exchange_formats import format_of

	file = frappe.get_doc("File", {"file_url": file_url})
	if not frappe.has_permission("File", "read", doc=file):
		raise frappe.PermissionError
	if not format_of(file.file_name):
		frappe.throw(_("Supported files: DXF, LandXML (.xml), CSV, XYZ/TXT/PTS, STL, OBJ, LAS, LAZ, STEP (.step/.stp). DWG/DGN: save as DXF first."))
	path = file.get_full_path()
	limit = (cint(frappe.db.get_single_value("CAD Settings", "max_import_size_mb")) or 200) * 1024 * 1024
	size = os.path.getsize(path)
	if size > limit:
		frappe.throw(_("The file is {0} MB; CAD Settings allows {1} MB.").format(round(size / 1048576, 1), limit // 1048576))
	return file, path, size


def stage(path, workspace, name):
	target = os.path.join(workspace, "inputs", name)
	os.makedirs(os.path.dirname(target), exist_ok=True)
	shutil.copyfile(path, target)
	return target


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def start_import(model, file_url):
	doc = check_model(model)
	file, _path, size = source_file(file_url)
	io = frappe.get_doc(
		{
			"doctype": IO,
			"operation_type": "Import",
			"cad_model": model,
			"file_format": formats.format_of(file.file_name),
			"source_file": file.file_url,
			"file_size": size,
			"processing_status": "Inspecting",
			"target_crs": doc.crs,
			"target_units": doc.linear_units,
		}
	).insert()
	job = cad_job_service.start_job("Inspect Import", model, None, {"io": io.name})
	io.db_set("processing_job", job["name"])
	return payload(io.reload() or io)


def run_inspect(job, parameters, workspace, log):
	io = frappe.get_doc(IO, parameters["io"])
	_file, path, _size = source_file(io.source_file)
	if io.file_format != "DXF":
		report = formats.inspect(job, io, stage(path, workspace, os.path.basename(path)), workspace, log)
		io.db_set(
			{
				"processing_status": "Ready to Commit",
				"format_version": io.file_format,
				"entity_count": report["entity_count"],
				"warning_count": len(report["warnings"]) + report["invalid_count"],
				"validation_results": json.dumps(report),
			}
		)
		return _("{0} item(s) can be imported.").format(report["entity_count"])
	request = {"command": "read", "workspace": workspace, "input": stage(path, workspace, "source.dxf"), "options": {}}
	result = cad_job_service.run_worker(job, request, log, script=cad_worker_service.DXF_WORKER)
	report = result["report"]
	model_units = io.target_units or "Metres"
	scale = (report["units_to_metres"] / UNITS_TO_METRES[model_units]) if report.get("units_to_metres") and model_units in UNITS_TO_METRES else None
	io.db_set(
		{
			"processing_status": "Ready to Commit",
			"format_version": f"{report['release']} ({report['dxf_version']})",
			"source_units": report["units_name"] if report.get("units_to_metres") else None,
			"unit_scale": scale,
			"entity_count": report["entity_count"],
			"warning_count": len(report["warnings"]) + report["invalid_count"],
			"validation_results": json.dumps(report),
		}
	)
	return _("{0} entities can be imported; {1} warnings.").format(report["entity_count"], len(report["warnings"]) + report["invalid_count"])


def commit_import(name, options):
	io = frappe.get_doc(IO, name)
	check_model(io.cad_model)
	io.check_permission("write")
	if io.processing_status != "Ready to Commit":
		frappe.throw(_("{0} is {1}.").format(name, _(io.processing_status)))
	options = frappe.parse_json(options) if isinstance(options, str) else options or {}
	model = frappe.get_doc(MODEL, io.cad_model)
	units = options.get("source_units") or io.source_units
	if units not in UNITS_TO_METRES:
		frappe.throw(_("Choose the units the drawing was made in."))
	scale = UNITS_TO_METRES[units] / UNITS_TO_METRES[model.linear_units or "Metres"]
	axes = options.get("axis_mapping") or "As in file"
	if axes not in AXIS_MAPPINGS:
		frappe.throw(_("Unknown axis mapping {0}.").format(axes))
	source_crs = (options.get("source_crs") or "").strip() or model.crs
	if io.file_format != "DXF" and source_crs and model.crs and source_crs not in (model.crs,):
		from is_production.geo_planning.services.gis_storage_service import resolve_crs as _resolve

		if _resolve(source_crs)[0] != model.crs:
			frappe.throw(_("{0} files are imported in the model's CRS ({1}); reproject them first.").format(io.file_format, model.crs))
	if source_crs:
		from is_production.geo_planning.services.gis_storage_service import resolve_crs

		source_crs, _code = resolve_crs(source_crs)
	if source_crs != model.crs and not model.crs:
		frappe.throw(_("The model has no CRS to transform into."))
	cad_revision_service.ensure_working_revision(io.cad_model)  # fails now (not in the job) while a revision is in review
	clean = {
		"source_units": units,
		"unit_scale": scale,
		"axis_mapping": axes,
		"source_crs": source_crs,
		"target_crs": model.crs,
		"skip_duplicates": bool(options.get("skip_duplicates", True)),
		"block_conflict": "keep" if options.get("block_conflict") == "keep" else "rename",
		"layers": "used" if options.get("layers") == "used" else "all",
		# surfaces, points and meshes (non-DXF formats)
		"points_as": options.get("points_as") if options.get("points_as") in ("points", "surface", "both") else None,
		"surface_type": options.get("surface_type") or "Survey Surface",
		"columns": options.get("columns") or "",
		"ground_only": bool(options.get("ground_only")),
		"thin": flt(options.get("thin")),
		"max_edge": flt(options.get("max_edge")),
		"as_mesh": bool(options.get("as_mesh")),
		"layer": (options.get("layer") or "").strip()[:140] or None,
	}
	io.db_set({"processing_status": "Importing", "source_units": units, "unit_scale": scale, "axis_mapping": axes, "source_crs": source_crs, "options": json.dumps(clean)})
	job = cad_job_service.start_job("Import", io.cad_model, None, {"io": io.name})
	io.db_set("processing_job", job["name"])
	return payload(io.reload() or io)


def run_import(job, parameters, workspace, log):
	io = frappe.get_doc(IO, parameters["io"])
	options = frappe.parse_json(io.options)
	_file, path, _size = source_file(io.source_file)
	if io.file_format != "DXF":
		return run_import_other(job, io, options, path, workspace, log)
	out = os.path.join(workspace, "outputs", "entities.ndjson")
	blocks_out = os.path.join(workspace, "outputs", "blocks.json")
	worker_options = {"unit_scale": options["unit_scale"], "axis_mapping": options["axis_mapping"], "source_crs": options["source_crs"], "target_crs": options["target_crs"]}
	request = {"command": "read", "workspace": workspace, "input": stage(path, workspace, "source.dxf"), "out": out, "blocks_out": blocks_out, "options": worker_options}
	report = cad_job_service.run_worker(job, request, log, script=cad_worker_service.DXF_WORKER)["report"]
	job.db_set("status_message", _("Adding to the model…"))
	frappe.db.commit()

	model = io.cad_model
	frappe.flags.cad_batch = True
	try:
		working = cad_revision_service.ensure_working_revision(model)
		prefix = "I" + io.name.split("-")[-1]
		with open(blocks_out) as f:
			blocks = json.load(f)
		renamed = import_blocks(model, blocks, options["block_conflict"], prefix, log)
		used = {row["layer"] for row in iter_rows(out)} | {e.get("layer") or "0" for b in blocks for e in b["entities"]}
		layers = [layer for layer in report["layers"] if options["layers"] == "all" or layer["layer_name"] in used]
		layer_map = cad_model_service.ensure_layers(model, layers + [{"layer_name": name} for name in sorted(used - {la["layer_name"] for la in layers})])
		rows, skipped_duplicates = [], 0
		for n, row in enumerate(iter_rows(out)):
			if options["skip_duplicates"] and (row.get("metadata") or {}).get("duplicate"):
				skipped_duplicates += 1
				continue
			if row["entity_type"] == "Block Reference" and row["geometry"]["block"] in renamed:
				row["geometry"]["block"] = renamed[row["geometry"]["block"]]
			row["entity_id"] = f"{prefix}-{row.get('source_id') or n}"
			rows.append(row)
		inserted, skipped = cad_model_service.bulk_insert_entities(model, rows, working, layer_map)
		inserted += import_face_sets(job, io, report, workspace, working, layer_map, prefix, log)
		cad_model_service.refresh_model(model)
		cad_model_service.sync_surfaces(model)
		history = frappe.parse_json(frappe.db.get_value(MODEL, model, "transformation_history") or "[]")
		history.append(
			{
				"on": str(now_datetime()),
				"by": frappe.session.user,
				"operation": f"DXF import {io.name}",
				"source_crs": options["source_crs"],
				"target_crs": options["target_crs"],
				"source_units": options["source_units"],
				"unit_scale": options["unit_scale"],
				"axis_mapping": options["axis_mapping"],
				"entities": len(inserted),
			}
		)
		frappe.db.set_value(MODEL, model, "transformation_history", json.dumps(history), update_modified=False)
	finally:
		frappe.flags.cad_batch = False

	warnings = list(report["warnings"])
	warnings += [_("Skipped {0} ({1}): {2}").format(s["entity_id"], s.get("source_id"), s["message"]) for s in skipped[:REPORT_WARNING_LIMIT]]
	if skipped_duplicates:
		warnings.append(_("{0} duplicate entities were not imported.").format(skipped_duplicates))
	if renamed:
		warnings.append(_("Blocks renamed because the model already has them: {0}.").format(", ".join(f"{a} → {b}" for a, b in renamed.items())))
	report["imported"] = len(inserted)
	report["skipped"] = skipped[:REPORT_WARNING_LIMIT]
	io.db_set(
		{
			"processing_status": "Completed",
			"revision": working,
			"entity_count": len(inserted),
			"warning_count": len(warnings) + report["invalid_count"],
			"warnings": "\n".join(warnings),
			"validation_results": json.dumps(report),
		}
	)
	if inserted and frappe.db.get_single_value("CAD Settings", "cad_worker_enabled"):
		frappe.db.commit()
		cad_job_service.start_job("Validate Model", model, working, {"not_checked": True}, check_permission=False)
	return _("Imported {0} entities into {1} ({2} skipped).").format(len(inserted), working, len(skipped) + skipped_duplicates)


def run_import_other(job, io, options, path, workspace, log):
	model = io.cad_model
	frappe.flags.cad_batch = True
	try:
		working = cad_revision_service.ensure_working_revision(model)
		created, warnings = formats.import_file(job, io, options, stage(path, workspace, os.path.basename(path)), workspace, log, working)
		cad_model_service.refresh_model(model)
		cad_model_service.sync_surfaces(model)
		history = frappe.parse_json(frappe.db.get_value(MODEL, model, "transformation_history") or "[]")
		history.append({"on": str(now_datetime()), "by": frappe.session.user, "operation": f"{io.file_format} import {io.name}", "source_units": options["source_units"], "unit_scale": options["unit_scale"], "axis_mapping": options["axis_mapping"], "entities": len(created)})
		frappe.db.set_value(MODEL, model, "transformation_history", json.dumps(history), update_modified=False)
	finally:
		frappe.flags.cad_batch = False
	io.db_set({"processing_status": "Completed", "revision": working, "entity_count": len(created), "warning_count": len(warnings), "warnings": "\n".join(warnings)})
	if any(e.split("-")[-1].startswith("PT") for e in created[:1]) and frappe.db.get_single_value("CAD Settings", "cad_worker_enabled"):
		frappe.db.commit()
		cad_job_service.start_job("Validate Model", model, working, {"not_checked": True}, check_permission=False)
	return _("Imported {0} item(s) into {1}.").format(len(created), working)


def import_face_sets(job, io, report, workspace, working, layer_map, prefix, log):
	"""3DFACE / POLYFACE / MESH entities of a DXF, gathered per layer: surfaces (2.5D) or meshes."""
	created = []
	source = {"operation": "import", "file": os.path.basename(io.source_file), "format": "DXF", "import": io.name}
	for kind, entity_type in (("surfaces", "Surface"), ("meshes", "Mesh")):
		for n, item in enumerate(report.get(kind) or []):
			digest = cad_surface_service.store_tin(job, workspace, item.pop("tin"), item["stats"], source, entity_type=entity_type, log=log)
			geometry = {"name": formats.unique_surface_name(io.cad_model, item["name"]) if entity_type == "Surface" else item["name"], "source": source}
			if entity_type == "Surface":
				geometry["surface_type"] = "Survey Surface"
			layer = layer_map.get(item["layer"]) or layer_map["0"]
			created.append(formats.add_blob_entity(io.cad_model, working, entity_type, geometry, digest, layer, f"{prefix}-F{kind[0].upper()}{n}"))
	return created


def iter_rows(path):
	with open(path) as f:
		for line in f:
			if line.strip():
				yield json.loads(line)


def import_blocks(model, blocks, conflict, prefix, log):
	"""Create the file's blocks (children before the blocks that nest them); returns {old: new} renames."""
	existing = set(frappe.get_all("CAD Block", filters={"cad_model": model}, pluck="block_name"))
	renamed = {}
	for block in blocks:
		if block["block_name"] in existing:
			if conflict == "keep":
				log.append(_("Block {0} exists: the model's definition is kept.").format(block["block_name"]))
				continue
			renamed[block["block_name"]] = f"{block['block_name']}_{prefix}"
	todo = [b for b in blocks if not (b["block_name"] in existing and conflict == "keep")]
	for block in todo:
		block["block_name"] = renamed.get(block["block_name"], block["block_name"])
		for e in block["entities"]:
			if e["entity_type"] == "Block Reference":
				e["geometry"]["block"] = renamed.get(e["geometry"]["block"], e["geometry"]["block"])
	cad_model_service.apply_blocks_in_order(model, todo)
	return renamed


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def start_export(model, options=None):
	doc = check_model(model, "read")
	if not frappe.has_permission(MODEL, "export", doc=doc):
		frappe.throw(_("You may not export CAD models."), frappe.PermissionError)
	options = frappe.parse_json(options) if isinstance(options, str) else options or {}
	revision = options.get("revision") or None
	if revision:
		if frappe.db.get_value("CAD Design Revision", revision, "cad_model") != model:
			frappe.throw(_("{0} is not a revision of {1}.").format(revision, model))
		if not frappe.db.get_value("CAD Design Revision", revision, "geometry_snapshot"):
			frappe.throw(_("{0} has no frozen snapshot; export the working state instead.").format(revision))
	fmt = options.get("format") or "DXF"
	if fmt != "DXF" and fmt not in formats.EXPORT_FORMATS:
		frappe.throw(_("Unknown export format {0}.").format(fmt))
	version = options.get("version") or "R2018"
	if fmt == "DXF" and version not in DXF_VERSIONS:
		frappe.throw(_("Unknown DXF version {0}.").format(version))
	axes = options.get("axis_mapping") or "As in file"
	if axes not in AXIS_MAPPINGS:
		frappe.throw(_("Unknown axis mapping {0}.").format(axes))
	clean = {
		"format": fmt,
		"revision": revision,
		"version": version,
		"axis_mapping": axes,
		"visible_layers_only": bool(options.get("visible_layers_only")),
		"include_solids": bool(options.get("include_solids", True)),
		"geometry_refs": [r for r in options.get("geometry_refs") or [] if storage.SHA256.match(r or "")],
	}
	io = frappe.get_doc(
		{
			"doctype": IO,
			"operation_type": "Export",
			"cad_model": model,
			"file_format": fmt,
			"format_version": version if fmt == "DXF" else fmt,
			"processing_status": "Exporting",
			"revision": revision,
			"source_crs": doc.crs,
			"target_crs": doc.crs,
			"source_units": doc.linear_units,
			"target_units": doc.linear_units,
			"unit_scale": 1.0,
			"axis_mapping": axes,
			"options": json.dumps(clean),
		}
	).insert(ignore_permissions=True)
	job = cad_job_service.start_job("Export", model, revision, {"io": io.name}, check_permission=False)
	io.db_set("processing_job", job["name"])
	return payload(io.reload() or io)


def run_export(job, parameters, workspace, log):
	io = frappe.get_doc(IO, parameters["io"])
	options = frappe.parse_json(io.options)
	model = frappe.get_doc(MODEL, io.cad_model)
	if options.get("revision"):
		content = cad_revision_service.load_snapshot(options["revision"])["content"]
	else:
		content = cad_revision_service.current_content(model.name)
	layers = content["layers"]
	if options["visible_layers_only"]:
		visible = {layer["layer_name"] for layer in layers if layer.get("visible")}
		layers = [layer for layer in layers if layer["layer_name"] in visible]
		content["entities"] = [e for e in content["entities"] if (e.get("layer") or "0") in visible]
	if options.get("format", "DXF") != "DXF":
		out, report = formats.export(job, io, options, content, workspace, log)
		return save_export(job, io, model, options, workspace, out, report, [])
	entities = []
	missing_meshes = []
	for e in content["entities"]:
		row = {k: e.get(k) for k in ("entity_type", "geometry", "color", "line_type", "line_weight")}
		row["layer"] = e.get("layer") or "0"
		if e["entity_type"] == "Solid":
			if not options["include_solids"]:
				continue
			key = storage.mesh_key(model.name, e["geometry_ref"])
			if not storage.provider().exists(key):
				missing_meshes.append(e["geometry_ref"])
			row["geometry_ref"] = e["geometry_ref"]
		elif e["entity_type"] in ("Surface", "Mesh"):
			row["tin"] = cad_surface_service.stage_tin(model.name, e["geometry_ref"], workspace, f"surface_{len(entities)}")
		entities.append(row)
	if missing_meshes:
		cad_job_service.run_tessellate(job, {"geometry_refs": list(set(missing_meshes))}, workspace, log)
	for n, row in enumerate(entities):
		if row.get("geometry_ref"):
			key = storage.mesh_key(model.name, row.pop("geometry_ref"))
			if storage.provider().exists(key):
				row["mesh"] = stage(storage.provider().local_path(key), workspace, f"mesh_{n}.cadmesh")
	data = {"layers": layers, "blocks": content.get("blocks") or [], "entities": entities}
	path = os.path.join(workspace, "inputs", "content.json")
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w") as f:
		json.dump(data, f)
	out = os.path.join(workspace, "outputs", "export.dxf")
	request = {
		"command": "write",
		"workspace": workspace,
		"input": path,
		"out": out,
		"options": {"version": options["version"], "units": model.linear_units, "axis_mapping": options["axis_mapping"]},
	}
	result = cad_job_service.run_worker(job, request, log, script=cad_worker_service.DXF_WORKER)
	report = result["report"]
	warnings = []
	if report.get("approximated"):
		warnings.append(_("{0} solids were written as MESH approximations of their display meshes, not as exact solids.").format(report["approximated"]))
	if report.get("skipped"):
		warnings.append(_("Not written: {0}.").format(", ".join(f"{v} {k}" for k, v in report["skipped"].items())))
	return save_export(job, io, model, options, workspace, result["out"], report, warnings)


def save_export(job, io, model, options, workspace, out, report, warnings):
	with open(cad_job_service.checked_output(workspace, out), "rb") as f:
		content_bytes = f.read()
	label = options.get("revision") or f"{model.name}-working"
	extension = os.path.splitext(out)[1] or ".dxf"
	file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{label}{extension}",
			"attached_to_doctype": IO,
			"attached_to_name": io.name,
			"is_private": 1,
			"content": content_bytes,
		}
	)
	file.save(ignore_permissions=True)
	io.db_set(
		{
			"processing_status": "Completed",
			"result_file": file.file_url,
			"entity_count": sum(report["written"].values()),
			"warning_count": len(warnings),
			"warnings": "\n".join(warnings),
			"validation_results": json.dumps(report),
		}
	)
	return _("Exported {0} item(s) as {1}.").format(sum(report["written"].values()), options.get("format", "DXF"))


def job_failed(job):
	"""A failed/cancelled import or export job also closes its record."""
	io = (frappe.parse_json(job.parameters or "{}") or {}).get("io")
	if io and frappe.db.exists(IO, io):
		status = frappe.db.get_value("CAD Processing Job", job.name, "status")
		frappe.db.set_value(IO, io, "processing_status", "Cancelled" if status == "Cancelled" else "Failed")
		frappe.db.commit()


def list_exchanges(model, limit=20):
	check_model(model, "read")
	return frappe.get_list(
		IO,
		filters={"cad_model": model},
		fields=["name", "operation_type", "processing_status", "source_file", "result_file", "entity_count", "warning_count", "creation", "owner"],
		order_by="creation desc",
		limit_page_length=min(cint(limit) or 20, 100),
	)

