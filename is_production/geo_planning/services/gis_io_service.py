"""GIS import and export (GIS Import Export Job).

Import:  upload a file (private File) -> ``start_import`` inspects it (formats, layers, geometry types
actually present, CRS, fields) and proposes a mapping -> the user adjusts it -> ``run_import`` runs it in
the background: per layer, create a dataset or append to one, read the features with GDAL (optionally
reprojecting, only when asked), and insert them as GIS Features (validated like any other save), in
chunks. Features that fail are skipped and listed in the job log.

Export:  ``start_export`` (datasets, a GIS Project, or selected features) -> background job dumps the
features the user may read and writes them with GDAL: GeoPackage, GeoJSON, Shapefile, KML, GML, CSV, or a
QGIS project package (GeoPackage + .qgz). The result is a private File on the job.

A QGIS project package (a zip with a .qgz/.qgs and its data) imports back into datasets plus a GIS
Project with the same layer order, visibility, opacity and colour.

GDAL and QGIS work runs in ``qgis_worker/gis_io.py`` under the system Python. Every job is an audit
record: who, when, which datasets, format, CRS, counts and log.
"""

import hashlib
import json
import os
import re
import shutil
import zipfile

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from is_production.geo_planning.services.gis_health_service import run_worker
from is_production.geo_planning.services.gis_storage_service import (
	NAME_PATTERN,
	NON_SPATIAL_TYPES,
	refresh_dataset_statistics,
	reserved_attribute_names,
	resolve_crs,
)

JOB = "GIS Import Export Job"
IMPORT_EXTENSIONS = (".gpkg", ".geojson", ".json", ".zip", ".kml", ".kmz", ".gml", ".gpx", ".csv", ".dxf")
EXPORT_FORMATS = {
	"GeoPackage": ".gpkg",
	"GeoJSON": ".geojson",
	"Shapefile": ".shp",
	"KML": ".kml",
	"GML": ".gml",
	"CSV": ".csv",
	"QGIS Project": ".gpkg",
}
OGR_TYPES = {"Integer": "Integer", "Integer64": "Integer", "Real": "Float", "Date": "Date", "DateTime": "Datetime"}
PACKAGE_EXTENSIONS = (".qgz", ".qgs", ".gpkg", ".shp", ".shx", ".dbf", ".prj", ".cpg", ".qix", ".geojson", ".json", ".kml", ".gml", ".csv", ".qml")
MAX_PACKAGE_BYTES = 2 * 1024**3
CHUNK = 500
LOG_LIMIT = 300
WORKER_TIMEOUT = 3600


# ---------------------------------------------------------------------------
# shared
# ---------------------------------------------------------------------------


def work_dir(job_name):
	path = frappe.get_site_path("private", "gis_io", job_name)
	os.makedirs(path, exist_ok=True)
	return os.path.abspath(path)


def worker(request, timeout=WORKER_TIMEOUT):
	python = frappe.db.get_single_value("GIS Settings", "qgis_python") or "/usr/bin/python3"
	report = run_worker(python, "gis_io.py", request, timeout=timeout)
	if "error" in report:
		raise RuntimeError(report["error"])
	if not report.get("ok"):
		raise RuntimeError(report.get("message") or _("The GIS worker failed."))
	return report


def slug(text, fallback="layer"):
	value = re.sub(r"[^a-z0-9_]+", "_", (text or "").lower()).strip("_")
	if not value or not value[0].isalpha():
		value = f"{fallback}_{value}".strip("_")
	return value[:63]


def unique_dataset_name(base, taken):
	name, n = base, 1
	while name in taken or frappe.db.exists("GIS Dataset", name):
		n += 1
		suffix = f"_{n}"
		name = base[: 63 - len(suffix)] + suffix
	taken.add(name)
	return name


def job_payload(job):
	return {
		"name": job.name,
		"job_type": job.job_type,
		"status": job.status,
		"progress": job.progress,
		"status_message": job.status_message,
		"is_project": job.is_project,
		"export_format": job.export_format,
		"output_file": job.output_file,
		"gis_project": job.gis_project,
		"features_processed": job.features_processed,
		"features_failed": job.features_failed,
		"log": (job.log or "")[-6000:],
		"layers": [
			{
				k: row.get(k)
				for k in (
					"idx", "source_layer", "action", "dataset", "layer_status", "source_geometry_type", "feature_count",
					"source_crs", "has_z", "new_dataset_name", "new_title", "new_geometry_type", "dataset_crs",
					"reproject", "csv_x", "csv_y", "csv_z", "imported", "failed", "output_name",
				)
			}
			| {"field_mapping": frappe.parse_json(row.field_mapping) if row.field_mapping else []}
			for row in job.layers
		],
	}


@frappe.whitelist()
def get_job(name):
	job = frappe.get_doc(JOB, name)
	job.check_permission("read")
	return job_payload(job)


def append_log(job, lines):
	text = "\n".join(lines)
	job.log = ((job.log or "") + ("\n" if job.log else "") + text)[-200000:]


# ---------------------------------------------------------------------------
# import: inspect
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def start_import(file_url):
	frappe.has_permission(JOB, "create", throw=True)
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	if file_doc.owner != frappe.session.user and not file_doc.has_permission("read"):
		frappe.throw(_("You cannot use this file."), frappe.PermissionError)
	extension = os.path.splitext(file_doc.file_name or "")[1].lower()
	if extension not in IMPORT_EXTENSIONS:
		frappe.throw(_("{0} files cannot be imported. Supported: {1}.").format(extension or "?", ", ".join(IMPORT_EXTENSIONS)))

	job = frappe.get_doc(
		{"doctype": JOB, "job_type": "Import", "source_file": file_url, "source_format": extension.lstrip(".").upper()}
	).insert()
	file_doc.db_set({"attached_to_doctype": JOB, "attached_to_name": job.name, "attached_to_field": "source_file"})
	try:
		inspect_file(job, file_doc.get_full_path())
		job.status_message = _("Inspected: check the mapping, then run the import.")
	except Exception as e:
		job.status = "Failed"
		job.status_message = _("The file could not be read.")
		append_log(job, [f"Inspection failed: {e}"])
	job.save()
	return job_payload(job)


def inspect_file(job, path):
	settings = frappe.get_cached_doc("GIS Settings")
	taken = set()
	package = project_file_in(path)
	if package:
		root = os.path.join(work_dir(job.name), "package")
		extract_package(path, root)
		qgz = os.path.join(root, package)
		project = worker({"command": "project_read", "qgz": qgz, "root": root}, timeout=600)
		job.is_project = 1
		job.options = json.dumps({"title": project.get("title"), "crs": project.get("crs"), "extent": project.get("extent")})
		for item in project["unsupported"]:
			append_log(job, [_("Layer {0} skipped: {1}.").format(item["name"], item["reason"])])
		for item in project["layers"]:
			found = worker({"command": "inspect", "path": os.path.join(root, item["path"])}, timeout=600)["layers"]
			info = next((lyr for lyr in found if not item.get("layer") or lyr["layer"] == item["layer"]), None)
			if not info:
				append_log(job, [_("Layer {0}: its data could not be read.").format(item["title"])])
				continue
			row = default_row(info, taken, settings, title=item["title"])
			row["project_style"] = json.dumps({k: item.get(k) for k in ("visible", "opacity", "color")})
			job.append("layers", row)
		return

	report = worker({"command": "inspect", "path": path}, timeout=600)
	append_log(job, [_("Could not read {0}").format(p) for p in report.get("problems", [])])
	if not report["layers"]:
		frappe.throw(_("No GIS layers were found in the file."))
	for info in report["layers"]:
		job.append("layers", default_row(info, taken, settings))


def default_row(info, taken, settings, title=None):
	crs = info["crs"]["epsg"]
	types = info.get("geometry_types") or {}
	summary = ", ".join(f"{k} × {v}" for k, v in sorted(types.items())) or info.get("declared_geometry") or ""
	if info.get("empty_geometries"):
		summary += f" (+{info['empty_geometries']} without geometry)"
	name = unique_dataset_name(slug(title or info["layer"]), taken)
	return {
		"source_layer": info["layer"],
		"source_path": info["source"],
		"action": "Create Dataset",
		"source_geometry_type": summary[:140],
		"feature_count": info.get("feature_count") or info.get("scanned") or 0,
		"source_crs": crs or "",
		"has_z": int(bool(info.get("has_z"))),
		"new_dataset_name": name,
		"new_title": title or (info["layer"] or name).replace("_", " ").strip().title(),
		"new_geometry_type": info.get("suggested_type") or "Geometry",
		"dataset_crs": crs or settings.default_crs or "EPSG:4326",
		"reproject": 0,
		"field_mapping": json.dumps(default_field_mapping(info.get("fields") or [])),
	}


def default_field_mapping(fields):
	reserved = reserved_attribute_names()
	used, mapping = set(), []
	for field in fields:
		if field["type"] == "Binary":
			data_type, include = "Text", False
		elif field["type"] in ("Integer", "Integer64") and field.get("subtype") == "Boolean":
			data_type, include = "Boolean", True
		else:
			data_type, include = OGR_TYPES.get(field["type"], "Text"), True
		name = slug(field["name"], "f")
		if name in reserved:
			name = f"{name}_1"
		base, n = name, 1
		while name in used:
			n += 1
			name = f"{base[:60]}_{n}"
		used.add(name)
		mapping.append({"source": field["name"], "field_name": name, "data_type": data_type, "include": include})
	return mapping


def project_file_in(path):
	"""Name of the .qgz/.qgs inside a zip, if the zip is a QGIS project package."""
	if not path.lower().endswith(".zip"):
		return None
	with zipfile.ZipFile(path) as archive:
		for name in archive.namelist():
			if name.lower().endswith((".qgz", ".qgs")) and not name.startswith("__MACOSX/"):
				return name
	return None


def extract_package(path, root):
	"""Extract a project package safely: known file types only, no paths outside root, size-capped."""
	os.makedirs(root, exist_ok=True)
	real_root = os.path.realpath(root)
	total = 0
	with zipfile.ZipFile(path) as archive:
		for member in archive.infolist():
			name = member.filename
			if member.is_dir() or name.startswith("__MACOSX/") or not name.lower().endswith(PACKAGE_EXTENSIONS):
				continue
			target = os.path.realpath(os.path.join(root, name))
			if not target.startswith(real_root + os.sep):
				frappe.throw(_("The package contains an unsafe path: {0}").format(name))
			total += member.file_size
			if total > MAX_PACKAGE_BYTES:
				frappe.throw(_("The package is too large to extract."))
			os.makedirs(os.path.dirname(target), exist_ok=True)
			with archive.open(member) as src, open(target, "wb") as dst:
				shutil.copyfileobj(src, dst)


# ---------------------------------------------------------------------------
# import: run
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def run_import(job, layers=None):
	job = frappe.get_doc(JOB, job)
	job.check_permission("write")
	if job.job_type != "Import" or job.status != "Draft":
		frappe.throw(_("Only an inspected import that has not run yet can be started."))
	if layers:
		apply_mapping(job, frappe.parse_json(layers) if isinstance(layers, str) else layers)
	validate_mapping(job)
	job.status = "Queued"
	job.status_message = _("Waiting for a worker…")
	job.save()
	frappe.enqueue(
		"is_production.geo_planning.services.gis_io_service.execute_import",
		queue="long",
		timeout=4 * 3600,
		job_id=f"gis-io-{frappe.local.site}-{job.name}",
		deduplicate=True,
		enqueue_after_commit=True,
		now=bool(frappe.flags.in_test),  # tests run the job inline
		io_job=job.name,  # not job_name: frappe.enqueue has its own parameter of that name
	)
	return job_payload(job)


EDITABLE = ("action", "dataset", "new_dataset_name", "new_title", "new_geometry_type", "dataset_crs", "source_crs", "reproject", "csv_x", "csv_y", "csv_z")


def apply_mapping(job, layers):
	by_idx = {cint(item.get("idx")): item for item in layers}
	for row in job.layers:
		item = by_idx.get(row.idx)
		if not item:
			continue
		for key in EDITABLE:
			if key in item:
				row.set(key, item[key])
		if "field_mapping" in item:
			row.field_mapping = json.dumps(item["field_mapping"])


def validate_mapping(job):
	names = set()
	for row in job.layers:
		label = _("Layer {0}").format(row.source_layer)
		if row.action == "Skip":
			continue
		if row.action == "Create Dataset":
			frappe.has_permission("GIS Dataset", "create", throw=True)
			if not NAME_PATTERN.match(row.new_dataset_name or ""):
				frappe.throw(_("{0}: the dataset name must be lower case letters, digits and underscores.").format(label))
			if frappe.db.exists("GIS Dataset", row.new_dataset_name) or row.new_dataset_name in names:
				frappe.throw(_("{0}: dataset {1} already exists.").format(label, row.new_dataset_name))
			names.add(row.new_dataset_name)
			row.dataset_crs, _code = resolve_crs(row.dataset_crs)
			fields = [m for m in frappe.parse_json(row.field_mapping or "[]") if m.get("include")]
			reserved = reserved_attribute_names()
			seen = set()
			for m in fields:
				if not NAME_PATTERN.match(m.get("field_name") or "") or m["field_name"] in reserved or m["field_name"] in seen:
					frappe.throw(_("{0}: field name {1} is not allowed or used twice.").format(label, m.get("field_name")))
				seen.add(m["field_name"])
		elif row.action == "Append":
			if not row.dataset:
				frappe.throw(_("{0}: choose the dataset to append to.").format(label))
			frappe.has_permission("GIS Dataset", "read", row.dataset, throw=True)
			frappe.has_permission("GIS Feature", "create", throw=True)
			row.dataset_crs = frappe.db.get_value("GIS Dataset", row.dataset, "crs")
		else:
			frappe.throw(_("{0}: unknown action {1}.").format(label, row.action))

		if row.source_crs:
			row.source_crs, _code = resolve_crs(row.source_crs)
		if row.source_crs and row.source_crs != row.dataset_crs and not row.reproject:
			frappe.throw(
				_("{0}: the file is in {1} but the dataset is in {2}. Tick Reproject to transform the coordinates, or choose {1} for the dataset.").format(
					label, row.source_crs, row.dataset_crs
				)
			)


def execute_import(io_job):
	job = frappe.get_doc(JOB, io_job)
	job.db_set({"status": "Running", "started_on": now_datetime(), "status_message": _("Importing…")})
	frappe.db.commit()
	rows = [row for row in job.layers if row.action != "Skip"]
	total = sum(cint(row.feature_count) for row in rows) or 1
	counters = {"done": 0, "failed": 0}
	log = []
	for row in rows:
		try:
			import_layer(job, row, total, counters, log)
		except Exception as e:
			frappe.db.rollback()
			row.layer_status = _("Failed")
			log.append(_("Layer {0} failed: {1}").format(row.source_layer, e))
			frappe.log_error(title=f"GIS import {job.name}: {row.source_layer}")

	for row in rows:
		frappe.db.set_value("GIS Import Export Layer", row.name, {
			"dataset": row.dataset, "imported": row.imported, "failed": row.failed, "layer_status": row.layer_status,
		})
	job.reload()
	append_log(job, log)
	if job.is_project:
		try:
			job.gis_project = create_project_from_import(job)
		except Exception as e:
			append_log(job, [_("The GIS Project could not be created: {0}").format(e)])
	failed_layers = sum(1 for row in job.layers if row.layer_status == _("Failed"))
	job.features_processed = counters["done"]
	job.features_failed = counters["failed"]
	job.status = "Failed" if failed_layers == len(rows) and rows else "Completed with Errors" if (counters["failed"] or failed_layers) else "Completed"
	job.progress = 100
	job.finished_on = now_datetime()
	imported = sum(cint(row.imported) for row in job.layers)
	job.status_message = _("{0} features imported, {1} failed.").format(imported, counters["failed"])
	job.save(ignore_permissions=True)
	frappe.db.commit()
	shutil.rmtree(work_dir(job.name), ignore_errors=True)


def import_layer(job, row, total, counters, log):
	from is_production.geo_planning.services.gis_publish_service import can_publish, enqueue_publish

	dataset = target_dataset(job, row)
	mapping = {m["source"]: m for m in frappe.parse_json(row.field_mapping or "[]") if m.get("include")}
	if row.action == "Append":
		existing = {a.field_name for a in dataset.attributes}
		for m in mapping.values():
			if m["field_name"] not in existing:
				log.append(_("Layer {0}: field {1} has no attribute {2} in {3}; its values are not imported.").format(row.source_layer, m["source"], m["field_name"], dataset.name))
		mapping = {k: m for k, m in mapping.items() if m["field_name"] in existing}

	source_crs = row.source_crs or dataset.crs
	if not row.source_crs:
		log.append(_("Layer {0}: the file has no CRS; its coordinates were taken to be {1}.").format(row.source_layer, dataset.crs))
	reproject = bool(row.reproject) and source_crs != dataset.crs
	out = os.path.join(work_dir(job.name), f"layer-{row.idx}.ndjson")
	request = {
		"command": "read",
		"source": row.source_path,
		"layer": row.source_layer,
		"out": out,
		"keep_z": bool(dataset.has_z),
		"fields": list(mapping),
	}
	if reproject:
		request.update({"reproject_from": source_crs, "reproject_to": dataset.crs})
	if row.csv_x or row.csv_y:
		request["csv"] = {"x": row.csv_x, "y": row.csv_y, "z": row.csv_z, "force": True}
	report = worker(request)
	for skipped in report.get("skipped", [])[:LOG_LIMIT]:
		log.append(_("Layer {0}, source feature {1}: {2}").format(row.source_layer, skipped["fid"], skipped["message"]))

	def progress(n):
		frappe.db.set_value(JOB, job.name, {
			"progress": min(99, 100 * counters["done"] / total),
			"status_message": _("{0}: {1} features…").format(row.source_layer, n),
		}, update_modified=False)

	def count(_ok):
		counters["done"] += 1

	imported, failed = insert_features(dataset, out, mapping, row.source_layer, log, on_feature=count, on_chunk=progress)

	refresh_dataset_statistics(dataset.name)
	counters["failed"] += failed + cint(report.get("skipped_count"))
	row.dataset = dataset.name
	row.imported = imported
	row.failed = failed + cint(report.get("skipped_count"))
	row.layer_status = _("Imported") if not row.failed else _("Imported with errors")
	frappe.db.commit()
	if can_publish():
		enqueue_publish(dataset.name)


def insert_features(dataset, ndjson_path, mapping, label, log, on_feature=None, on_chunk=None):
	"""Insert NDJSON features (from the worker's ``read``) as GIS Features of ``dataset``.

	``mapping``: {source field: {"field_name": ...}}. Each feature is saved through the GIS Feature
	document (validation, permissions) inside its own savepoint: one that fails is skipped and logged,
	the rest are kept. Commits every CHUNK features. Returns (imported, failed).
	"""
	imported = failed = 0
	frappe.flags.in_gis_bulk_write = True
	try:
		with open(ndjson_path, encoding="utf-8") as lines:
			for n, line in enumerate(lines, 1):
				item = json.loads(line)
				attributes = {m["field_name"]: item["properties"].get(source) for source, m in mapping.items()}
				frappe.db.savepoint("gis_import_feature")
				try:
					doc = frappe.get_doc(
						{
							"doctype": "GIS Feature",
							"dataset": dataset.name,
							"geometry": item["geometry"],
							"attributes": attributes,
							"source_fid": str(item.get("fid")),
						}
					)
					doc.flags.ignore_version = True
					doc.insert()
					imported += 1
				except Exception as e:
					frappe.db.rollback(save_point="gis_import_feature")
					failed += 1
					if failed <= LOG_LIMIT:
						log.append(_("{0}, source feature {1}: {2}").format(label, item.get("fid"), frappe.utils.strip_html(str(e))))
				frappe.local.message_log = []
				if on_feature:
					on_feature(True)
				if n % CHUNK == 0:
					if on_chunk:
						on_chunk(n)
					frappe.db.commit()
	finally:
		frappe.flags.in_gis_bulk_write = False
	refresh_dataset_statistics(dataset.name)
	return imported, failed


def import_new_dataset(path, layer_info, dataset_name, title, fallback_crs, source, log, category="Processing Output"):
	"""Create a dataset from one inspected layer of a file and import its features (processing outputs)."""
	from is_production.geo_planning.services.gis_publish_service import can_publish, enqueue_publish

	frappe.has_permission("GIS Dataset", "create", throw=True)
	crs = layer_info["crs"]["epsg"] or fallback_crs
	mapping = {m["source"]: m for m in default_field_mapping(layer_info.get("fields") or []) if m["include"]}
	geometry_type = layer_info.get("suggested_type") or "Geometry"
	dataset = frappe.get_doc(
		{
			"doctype": "GIS Dataset",
			"dataset_name": dataset_name,
			"title": title or dataset_name,
			"geometry_type": geometry_type,
			"crs": crs,
			"has_z": int(bool(layer_info.get("has_z"))) if geometry_type not in NON_SPATIAL_TYPES else 0,
			"category": category,
			"source_type": "Processing",
			"source_reference": source.get("reference"),
			"import_metadata": json.dumps(source, default=str),
			"attributes": [
				{"field_name": m["field_name"], "label": m["source"], "data_type": m["data_type"], "source_field_name": m["source"]}
				for m in mapping.values()
			],
		}
	).insert()
	frappe.db.commit()
	out = path + f".{dataset_name}.ndjson"
	report = worker({"command": "read", "source": layer_info["source"], "layer": layer_info["layer"], "out": out, "keep_z": bool(dataset.has_z), "fields": list(mapping)})
	imported, failed = insert_features(dataset, out, mapping, dataset_name, log)
	frappe.db.commit()
	if can_publish():
		enqueue_publish(dataset.name)
	return dataset.name, imported, failed + cint(report.get("skipped_count"))


def register_file(path, doctype, name, fieldname, file_name=None):
	"""Move a server-generated file into the site's private files and register it as a File of a document.

	It is registered in place (``copy_from_existing_file``): Frappe would otherwise read it back, apply the
	upload size limit meant for user uploads, and write a second copy. The content hash is set so that
	deleting the File (or its document) also deletes the file on disk.
	"""
	file_name = file_name or os.path.basename(path)
	destination = frappe.get_site_path("private", "files", file_name)
	shutil.move(path, destination)
	digest = hashlib.md5()
	with open(destination, "rb") as f:
		for block in iter(lambda: f.read(1 << 20), b""):
			digest.update(block)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"file_url": f"/private/files/{file_name}",
			"is_private": 1,
			"file_size": os.path.getsize(destination),
			"content_hash": digest.hexdigest(),
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"attached_to_field": fieldname,
		}
	)
	file_doc.flags.copy_from_existing_file = True
	file_doc.insert(ignore_permissions=True)
	return file_doc.file_url


def target_dataset(job, row):
	if row.action == "Append":
		return frappe.get_doc("GIS Dataset", row.dataset)
	mapping = [m for m in frappe.parse_json(row.field_mapping or "[]") if m.get("include")]
	dataset = frappe.get_doc(
		{
			"doctype": "GIS Dataset",
			"dataset_name": row.new_dataset_name,
			"title": row.new_title or row.new_dataset_name,
			"geometry_type": row.new_geometry_type,
			"crs": row.dataset_crs,
			"has_z": row.has_z if row.new_geometry_type not in NON_SPATIAL_TYPES else 0,
			"category": "Survey",
			"source_type": "Import",
			"source_reference": job.name,
			"source_file": job.source_file,
			"source_crs": row.source_crs if row.reproject else None,
			"import_metadata": json.dumps(
				{
					"job": job.name,
					"source_layer": row.source_layer,
					"source_geometry": row.source_geometry_type,
					"source_crs": row.source_crs or None,
					"reprojected": bool(row.reproject),
				}
			),
			"attributes": [
				{"field_name": m["field_name"], "label": m["source"], "data_type": m["data_type"], "source_field_name": m["source"]}
				for m in mapping
			],
		}
	)
	dataset.insert()
	frappe.db.commit()
	return dataset


def create_project_from_import(job):
	options = frappe.parse_json(job.options or "{}")
	layers = []
	for row in job.layers:
		if not row.dataset or row.action == "Skip":
			continue
		style = frappe.parse_json(row.project_style or "{}")
		layers.append(
			{
				"dataset": row.dataset,
				"visible": 1 if style.get("visible", True) else 0,
				"opacity": style.get("opacity", 1) if style.get("opacity") is not None else 1,
				"color": style.get("color"),
				"renderer": "qgis",
			}
		)
	extent = options.get("extent") or [None] * 4
	crs = options.get("crs")
	project = frappe.get_doc(
		{
			"doctype": "GIS Project",
			"title": options.get("title") or os.path.splitext(os.path.basename(job.source_file or "Imported project"))[0],
			"display_crs": crs if crs and crs.startswith("EPSG:") else None,
			"description": _("Imported from {0} ({1}).").format(os.path.basename(job.source_file or ""), job.name),
			"extent_min_x": extent[0],
			"extent_min_y": extent[1],
			"extent_max_x": extent[2],
			"extent_max_y": extent[3],
			"layers": layers,
		}
	).insert()
	return project.name


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def start_export(export_format, datasets=None, project=None, target_crs=None, feature_ids=None):
	frappe.has_permission(JOB, "create", throw=True)
	if export_format not in EXPORT_FORMATS:
		frappe.throw(_("Unknown export format {0}.").format(export_format))
	datasets = frappe.parse_json(datasets) if isinstance(datasets, str) else datasets or []
	feature_ids = frappe.parse_json(feature_ids) if isinstance(feature_ids, str) else feature_ids
	if project:
		project_doc = frappe.get_doc("GIS Project", project)
		project_doc.check_permission("read")
		datasets = [row.dataset for row in project_doc.layers if row.dataset]
	elif export_format == "QGIS Project":
		frappe.throw(_("Save the map as a GIS Project first, then export the project."))
	if not datasets:
		frappe.throw(_("Choose at least one dataset to export."))
	for name in datasets:
		frappe.has_permission("GIS Dataset", "read", name, throw=True)
	if feature_ids and len(datasets) != 1:
		frappe.throw(_("Selected features can only be exported from one dataset."))
	if target_crs:
		target_crs, _code = resolve_crs(target_crs)

	job = frappe.get_doc(
		{
			"doctype": JOB,
			"job_type": "Export",
			"status": "Queued",
			"status_message": _("Waiting for a worker…"),
			"export_format": export_format,
			"target_crs": target_crs,
			"selection_only": 1 if feature_ids else 0,
			"feature_ids": json.dumps([cint(i) for i in feature_ids]) if feature_ids else None,
			"gis_project": project,
			"layers": [{"action": "Export", "dataset": name, "source_layer": name} for name in dict.fromkeys(datasets)],
		}
	).insert()
	frappe.enqueue(
		"is_production.geo_planning.services.gis_io_service.execute_export",
		queue="long",
		timeout=4 * 3600,
		job_id=f"gis-io-{frappe.local.site}-{job.name}",
		deduplicate=True,
		enqueue_after_commit=True,
		now=bool(frappe.flags.in_test),  # tests run the job inline
		io_job=job.name,  # not job_name: frappe.enqueue has its own parameter of that name
	)
	return job_payload(job)


def execute_export(io_job):
	job = frappe.get_doc(JOB, io_job)
	job.db_set({"status": "Running", "started_on": now_datetime(), "status_message": _("Exporting…")})
	frappe.db.commit()
	log = []
	try:
		path = export_files(job, log)
		attach_output(job, path)
		job.status = "Completed with Errors" if job.features_failed else "Completed"
		job.status_message = _("{0} features exported.").format(job.features_processed)
	except Exception as e:
		frappe.db.rollback()
		job.reload()
		job.status = "Failed"
		job.status_message = _("The export failed.")
		log.append(str(e))
		frappe.log_error(title=f"GIS export {job.name}")
	append_log(job, log)
	job.progress = 100
	job.finished_on = now_datetime()
	job.save(ignore_permissions=True)
	frappe.db.commit()
	shutil.rmtree(work_dir(job.name), ignore_errors=True)


def export_files(job, log):
	fmt = job.export_format
	directory = work_dir(job.name)
	out_dir = os.path.join(directory, "out")
	os.makedirs(out_dir, exist_ok=True)
	ids = frappe.parse_json(job.feature_ids) if job.feature_ids else None
	project = frappe.get_doc("GIS Project", job.gis_project) if job.gis_project else None
	base = slug(project.title if project else job.layers[0].dataset, "export")
	single_file = fmt in ("GeoPackage", "QGIS Project")
	gpkg = os.path.join(out_dir, f"{base}.gpkg")
	target = job.target_crs or ("EPSG:4326" if fmt == "KML" else None)
	if fmt == "KML" and job.target_crs and job.target_crs != "EPSG:4326":
		log.append(_("KML is always written in EPSG:4326 (WGS 84)."))
		target = "EPSG:4326"
	processed = failed = 0
	written_layers = []
	for i, row in enumerate(job.layers):
		dataset = frappe.get_doc("GIS Dataset", row.dataset)
		dump = os.path.join(directory, f"{dataset.name}.ndjson")
		count = dump_features(dataset.name, dump, ids)
		output = gpkg if single_file else os.path.join(out_dir, f"{dataset.name}{EXPORT_FORMATS[fmt]}")
		report = worker(
			{
				"command": "write",
				"format": "GeoPackage" if fmt == "QGIS Project" else fmt,
				"input": dump,
				"out": output,
				"append": single_file and i > 0,
				"layer": dataset.name,
				"geometry_type": dataset.geometry_type if dataset.geometry_type != "Raster" else "No Geometry",
				"has_z": bool(dataset.has_z),
				"crs": dataset.crs,
				"target_crs": target,
				"fields": [
					{"name": a.source_field_name or a.field_name, "key": a.field_name, "data_type": a.data_type}
					for a in dataset.attributes
				],
			}
		)
		if report.get("renamed_fields"):
			log.append(_("{0}: field names shortened by the format: {1}").format(dataset.name, report["renamed_fields"]))
		for item in report.get("failed", [])[:LOG_LIMIT]:
			log.append(_("{0}, feature {1}: {2}").format(dataset.name, item["id"], item["message"]))
		processed += report["written"]
		failed += cint(report.get("failed_count"))
		frappe.db.set_value("GIS Import Export Layer", row.name, {"imported": report["written"], "failed": report.get("failed_count"), "output_name": os.path.basename(output), "layer_status": _("Exported")})
		written_layers.append((row, dataset, count))
		frappe.db.set_value(JOB, job.name, {"progress": 90 * (i + 1) / len(job.layers)}, update_modified=False)
		frappe.db.commit()

	job.features_processed = processed
	job.features_failed = failed

	if fmt == "QGIS Project":
		style = {row.dataset: row for row in project.layers if row.dataset}
		datasets_by_name = {dataset.name: dataset for _row, dataset, _count in written_layers}
		ordered = []  # project order (top first): datasets and map services
		for row in project.layers:
			if row.dataset and row.dataset in datasets_by_name:
				dataset = datasets_by_name[row.dataset]
				ordered.append({"name": dataset.name, "title": dataset.title, "geometry_type": dataset.geometry_type,
					"color": style[dataset.name].color or dataset.default_color, "visible": row.visible, "opacity": row.opacity})
			elif row.map_service:
				service = qgis_service_layer(row.map_service, log)
				if service:
					ordered.append({**service, "visible": row.visible, "opacity": row.opacity})
		if project.basemap:
			basemap = qgis_service_layer(project.basemap, log)
			if basemap:
				ordered.append({**basemap, "visible": 1, "opacity": 1})
		worker(
			{
				"command": "project_write",
				"gpkg": gpkg,
				"qgz": os.path.join(out_dir, f"{base}.qgz"),
				"title": project.title,
				"crs": project.display_crs or "EPSG:3857",
				"extent": [project.extent_min_x, project.extent_min_y, project.extent_max_x, project.extent_max_y]
				if project.extent_max_x
				else None,
				"layers": ordered,
			},
			timeout=600,
		)

	files = sorted(os.listdir(out_dir))
	if len(files) == 1:
		return os.path.join(out_dir, files[0])
	archive = os.path.join(directory, f"{base}.zip")
	with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
		for name in files:
			z.write(os.path.join(out_dir, name), name)
	return archive


def qgis_service_layer(name, log):
	"""A map service as a QGIS project layer, if it needs no credentials (none go into a package)."""
	doc = frappe.get_doc("GIS Map Service", name)
	if doc.authentication != "None" or doc.service_type == "Google Map Tiles":
		log.append(_("Map service {0} left out of the QGIS project: it needs credentials, which are not put in a package.").format(doc.title))
		return None
	layer = {"name": doc.name, "title": doc.title, "url": doc.url, "min_zoom": doc.min_zoom, "max_zoom": doc.max_zoom}
	if doc.service_type == "WMS":
		layer.update(kind="wms", layers=doc.wms_layers, styles=doc.wms_styles or "", format=doc.wms_format or "image/png")
	else:
		layer["kind"] = "xyz"
	return layer


def dump_features(dataset, path, ids=None, page=1000):
	"""Write the dataset's features the current user may read, as NDJSON (dataset CRS)."""
	filters = {"dataset": dataset}
	if ids:
		filters["name"] = ["in", ids]
	count, start = 0, 0
	with open(path, "w", encoding="utf-8") as out:
		while True:
			rows = frappe.get_list(
				"GIS Feature",
				filters=filters,
				fields=["name", "feature_label", "geometry", "attributes"],
				order_by="name asc",
				limit_start=start,
				limit_page_length=page,
			)
			if not rows:
				break
			for row in rows:
				out.write(
					json.dumps(
						{
							"id": row.name,
							"label": row.feature_label,
							"geometry": frappe.parse_json(row.geometry) if row.geometry else None,
							"attributes": frappe.parse_json(row.attributes) if row.attributes else {},
						}
					)
				)
				out.write("\n")
			count += len(rows)
			start += page
	return count


def attach_output(job, path):
	stamp = now_datetime().strftime("%Y%m%d-%H%M")
	name, extension = os.path.splitext(os.path.basename(path))
	job.output_file = register_file(path, JOB, job.name, "output_file", f"{name}-{stamp}-{job.name}{extension}")
