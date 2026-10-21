"""CAD Processing Jobs: CAD worker runs in the background (RQ ``long`` queue).

Each job gets a private workspace (``private/cad/jobs/<job>``); the inputs it needs are copied in, the
worker writes its outputs there, and the bench moves the results into model storage and the database.
A job only ever **adds** geometry files or fills in validation fields of entities whose version has not
changed since the job read them, so a failed or cancelled job cannot corrupt a model or a revision.

Types
-----
Build Geometry  make solids (or move stored ones) → B-Rep in model storage + the server's record of its
                bounding box/measures/validity (``<sha>.json``) + a cached display mesh. The workstation then
                saves a Solid entity that refers to the B-Rep.
Validate Model  OCCT checks and measures of entities (lengths, areas, volumes) written back to them.
Tessellate      display meshes for B-Rep files whose cached mesh is missing.
"""

import json
import os
import shutil
import time

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from is_production.cad_engineering.services import cad_worker_service
from is_production.cad_engineering.services import cad_storage_service as storage

JOB = "CAD Processing Job"
LOG_LIMIT = 200000
MAX_BUILD_ITEMS = 200
SLOT_WAIT_SECONDS = 600
SOLID_OPS = {"box", "cylinder", "cone", "sphere", "extrude", "transform"}
CURVE_OPERATIONS = {"offset"}


def start_job(job_type, cad_model=None, revision=None, parameters=None, check_permission=True):
	if not frappe.db.get_single_value("CAD Settings", "cad_worker_enabled"):
		frappe.throw(_("The CAD worker is switched off (CAD Settings)."))
	if cad_model and check_permission:
		frappe.get_doc("CAD Model", cad_model).check_permission("write" if job_type != "Tessellate" else "read")
	job = frappe.get_doc(
		{
			"doctype": JOB,
			"job_type": job_type,
			"cad_model": cad_model,
			"revision": revision,
			"status": "Queued",
			"status_message": _("Waiting for a worker…"),
			"parameters": json.dumps(parameters or {}),
			"max_attempts": 3,
		}
	)
	job.insert(ignore_permissions=not check_permission)
	enqueue(job)
	return job_payload(job)


def enqueue(job):
	minutes = cint(frappe.db.get_single_value("CAD Settings", "max_processing_minutes")) or 30
	frappe.enqueue(
		"is_production.cad_engineering.services.cad_job_service.execute",
		queue="long",
		timeout=minutes * 60 + SLOT_WAIT_SECONDS + 300,
		job_id=f"cad-job-{frappe.local.site}-{job.name}-{cint(job.attempts)}",
		deduplicate=True,
		enqueue_after_commit=True,
		now=bool(frappe.flags.in_test),
		processing_job=job.name,
	)


def job_payload(job):
	return {
		"name": job.name,
		"job_type": job.job_type,
		"cad_model": job.cad_model,
		"status": job.status,
		"progress": job.progress,
		"status_message": job.status_message,
		"results": frappe.parse_json(job.results) if job.results else None,
		"attempts": cint(job.attempts),
		"max_attempts": cint(job.max_attempts),
		"creation": job.creation,
		"finished_on": job.finished_on,
	}


def get_job(name):
	job = frappe.get_doc(JOB, name)
	job.check_permission("read")
	return job_payload(job)


def cancel(name):
	job = frappe.get_doc(JOB, name)
	job.check_permission("write")
	if job.status == "Queued":
		job.db_set({"status": "Cancelled", "status_message": _("Cancelled before it started."), "finished_on": now_datetime()})
	elif job.status == "Running":
		job.db_set({"status": "Cancelling", "status_message": _("Cancelling…")})
	return job_payload(job.reload() or job)


def retry(name):
	job = frappe.get_doc(JOB, name)
	job.check_permission("write")
	if job.status not in ("Failed", "Cancelled"):
		frappe.throw(_("Only failed or cancelled jobs can be retried."))
	if cint(job.attempts) >= cint(job.max_attempts or 1):
		frappe.throw(_("{0} has already been tried {1} times.").format(job.name, job.attempts))
	job.db_set({"status": "Queued", "status_message": _("Waiting for a worker (retry)…"), "progress": 0, "finished_on": None})
	enqueue(job)
	return job_payload(job)


def history(cad_model=None, limit=30):
	filters = {"cad_model": cad_model} if cad_model else {}
	return frappe.get_list(
		JOB,
		filters=filters,
		fields=["name", "job_type", "cad_model", "status", "progress", "status_message", "creation", "owner"],
		order_by="creation desc",
		limit_page_length=min(cint(limit) or 30, 200),
	)


# ---------------------------------------------------------------------------
# running (background)
# ---------------------------------------------------------------------------


def wait_for_slot(job):
	"""At most CAD Settings → Maximum Concurrent Jobs workers per site run at once."""
	limit = cint(frappe.db.get_single_value("CAD Settings", "max_concurrent_jobs")) or 2
	waited = 0
	while frappe.db.count(JOB, {"status": "Running", "name": ["!=", job.name]}) >= limit:
		if waited >= SLOT_WAIT_SECONDS or frappe.db.get_value(JOB, job.name, "status") != "Queued":
			return False
		time.sleep(3)
		waited += 3
		frappe.db.commit()  # fresh snapshot for the next count
	return True


def execute(processing_job):
	job = frappe.get_doc(JOB, processing_job)
	if job.status != "Queued":  # cancelled while waiting
		return
	if not frappe.flags.in_test and not wait_for_slot(job):
		if frappe.db.get_value(JOB, job.name, "status") == "Queued":
			job.db_set({"status": "Failed", "status_message": _("No worker slot became free."), "finished_on": now_datetime()})
		return
	job.db_set(
		{"status": "Running", "started_on": now_datetime(), "status_message": _("Running…"), "progress": 0, "attempts": cint(job.attempts) + 1}
	)
	frappe.db.commit()
	log = []
	workspace = cad_worker_service.job_workspace(job.name)
	try:
		handler = handler_for(job.job_type)
		message = handler(job, frappe.parse_json(job.parameters or "{}"), workspace, log)
		if frappe.db.get_value(JOB, job.name, "status") in ("Cancelling", "Cancelled"):
			finish(job, "Cancelled", _("Cancelled."), log)
		else:
			finish(job, "Completed", message, log)
	except Cancelled:
		frappe.db.rollback()
		finish(job, "Cancelled", _("Cancelled."), log)
	except Exception as e:
		frappe.db.rollback()
		log.append(f"Error: {frappe.utils.strip_html(str(e))}")
		frappe.log_error(title=f"CAD job {job.name}")
		finish(job, "Failed", _("Failed: {0}").format(frappe.utils.strip_html(str(e))[:120]), log)
	finally:
		shutil.rmtree(workspace, ignore_errors=True)


class Cancelled(Exception):
	pass


def finish(job, status, message, log):
	frappe.db.set_value(
		JOB,
		job.name,
		{
			"status": status,
			"status_message": message,
			"progress": 100 if status == "Completed" else frappe.db.get_value(JOB, job.name, "progress"),
			"finished_on": now_datetime(),
			"log": "\n".join(([frappe.db.get_value(JOB, job.name, "log")] if frappe.db.get_value(JOB, job.name, "log") else []) + log)[-LOG_LIMIT:],
		},
	)
	frappe.db.commit()
	if status in ("Failed", "Cancelled") and job.job_type in ("Inspect Import", "Import", "Export"):
		from is_production.cad_engineering.services.cad_exchange_service import job_failed

		job_failed(job)


def run_worker(job, request, log, script=cad_worker_service.WORKER):
	def on_progress(value):
		frappe.db.set_value(JOB, job.name, "progress", value, update_modified=False)
		frappe.db.commit()

	def should_cancel():
		return frappe.db.get_value(JOB, job.name, "status") == "Cancelling"

	result, error = cad_worker_service.run_streaming(request, on_progress, should_cancel, log, script=script)
	if error == "Cancelled.":
		raise Cancelled()
	if error:
		raise RuntimeError(error)
	return result


def stage_blob(model, digest, workspace, name):
	"""Copy a stored B-Rep into the job workspace (the worker reads nothing outside it)."""
	source = storage.provider().local_path(storage.blob_key(model, digest, "brep"))
	if not os.path.isfile(source):
		frappe.throw(_("Geometry file {0} is missing from model storage.").format(digest[:12]))
	target = os.path.join(workspace, "inputs", f"{name}.brep")
	os.makedirs(os.path.dirname(target), exist_ok=True)
	shutil.copyfile(source, target)
	return target


def run_build(job, parameters, workspace, log):
	items = parameters.get("items") or []
	if not items or len(items) > MAX_BUILD_ITEMS:
		frappe.throw(_("Build between 1 and {0} solids at a time.").format(MAX_BUILD_ITEMS))
	request_items = []
	for n, item in enumerate(items):
		if item.get("op") not in SOLID_OPS:
			frappe.throw(_("Unknown solid operation {0}.").format(item.get("op")))
		item = {**item, "key": str(item.get("key") or n)}
		if item["op"] == "transform":
			digest = item.pop("geometry_ref", None)
			if not storage.SHA256.match(digest or ""):
				frappe.throw(_("Move needs the solid's geometry reference."))
			item["brep"] = stage_blob(job.cad_model, digest, workspace, f"source_{n}")
		request_items.append(item)
	result = run_worker(job, {"command": "build", "workspace": workspace, "items": request_items}, log)
	occt = frappe.db.get_single_value("CAD Settings", "occt_version")
	out = []
	for item, built in zip(request_items, result["items"]):
		entry = {k: built.get(k) for k in ("key", "valid", "message", "bbox", "volume", "area")}
		if built.get("valid") and built.get("brep"):
			brep = checked_output(workspace, built["brep"])
			digest = storage.store_blob(job.cad_model, brep, "brep", expected=built.get("checksum"))
			source = {k: v for k, v in item.items() if k not in ("brep", "key")}
			record = {**entry, "source": source, "occt": occt, "job": job.name}
			record.pop("key", None)
			storage.provider().write(storage.blob_key(job.cad_model, digest, "json"), json.dumps(record).encode())
			if built.get("mesh"):
				storage.provider().import_file(storage.mesh_key(job.cad_model, digest), checked_output(workspace, built["mesh"]))
			entry.update({"geometry_ref": digest, "source": source, "mesh_info": built.get("mesh_info")})
		out.append(entry)
		log.append(_("{0}: {1}").format(entry["key"], _("valid") if entry.get("valid") else entry.get("message")))
	job.db_set("results", json.dumps({"items": out}))
	return _("Built {0} of {1} solid(s).").format(sum(1 for e in out if e.get("geometry_ref")), len(out))


def checked_output(workspace, path):
	real = os.path.realpath(path)
	if not real.startswith(os.path.realpath(workspace) + os.sep) or not os.path.isfile(real):
		frappe.throw(_("The CAD worker returned a file outside its workspace."))
	return real


def run_validate(job, parameters, workspace, log):
	filters = {"cad_model": job.cad_model}
	if parameters.get("entity_ids"):
		filters["entity_id"] = ["in", parameters["entity_ids"]]
	elif parameters.get("not_checked"):  # after an import: everything not checked yet
		filters["validation_status"] = "Not Checked"
	rows = frappe.get_all(
		"CAD Entity",
		filters=filters,
		fields=["name", "entity_id", "entity_type", "geometry", "geometry_ref", "entity_version"],
		limit_page_length=0,
	)
	entities = []
	for n, row in enumerate(rows):
		entity = {"entity_id": row.entity_id, "entity_type": row.entity_type, "entity_version": row.entity_version, "geometry": frappe.parse_json(row.geometry or "{}")}
		if row.geometry_ref:
			entity["brep"] = stage_blob(job.cad_model, row.geometry_ref, workspace, f"entity_{n}")
		entities.append(entity)
	path = os.path.join(workspace, "inputs", "entities.json")
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w") as f:
		json.dump(entities, f)
	result = run_worker(job, {"command": "validate", "workspace": workspace, "input": path}, log)

	by_id = {row.entity_id: row for row in rows}
	counts = {}
	skipped = 0
	for found in result["entities"]:
		row = by_id.get(found["entity_id"])
		if not row:
			continue
		if frappe.db.get_value("CAD Entity", row.name, "entity_version") != row.entity_version:
			skipped += 1  # changed while the job ran: its next save queues a fresh check
			continue
		measures = found.get("measures") or {}
		# the version condition also guards the moment between the check above and this update
		frappe.db.sql(
			"""update `tabCAD Entity` set validation_status=%s, validation_message=%s, length=%s, area=%s, volume=%s
			where name=%s and entity_version=%s""",
			(found["status"], found.get("message"), measures.get("length") or 0, measures.get("area") or 0, measures.get("volume") or 0, row.name, row.entity_version),
		)
		counts[found["status"]] = counts.get(found["status"], 0) + 1
	frappe.db.commit()
	job.db_set("results", json.dumps({"counts": counts, "skipped_changed": skipped}))
	return _("Checked {0} entities: {1}.").format(len(result["entities"]), ", ".join(f"{v} {_(k)}" for k, v in sorted(counts.items())) or "-")


def run_tessellate(job, parameters, workspace, log):
	refs = [r for r in parameters.get("geometry_refs") or [] if storage.SHA256.match(r or "")]
	items = []
	for n, digest in enumerate(refs):
		items.append({"key": digest, "brep": stage_blob(job.cad_model, digest, workspace, f"t_{n}"), "out": os.path.join(workspace, "outputs", f"t_{n}.cadmesh")})
	result = run_worker(job, {"command": "tessellate", "workspace": workspace, "items": items}, log)
	made = 0
	for entry in result["items"]:
		if entry.get("mesh"):
			storage.provider().import_file(storage.mesh_key(job.cad_model, entry["key"]), checked_output(workspace, entry["mesh"]))
			made += 1
		else:
			log.append(f"{entry.get('key', '')[:12]}: {entry.get('error')}")
	return _("Made {0} display mesh(es).").format(made)


def run_curve_operation(job, parameters, workspace, log):
	"""Planar polyline offsets with OCCT; results go back to the workstation, which adds them as an edit."""
	items = parameters.get("items") or []
	if not items or len(items) > MAX_BUILD_ITEMS:
		frappe.throw(_("Offset between 1 and {0} curves at a time.").format(MAX_BUILD_ITEMS))
	request_items = [
		{"key": str(item.get("key") or n), "points": item["points"], "bulges": item.get("bulges"), "closed": bool(item.get("closed")), "distance": float(item["distance"]), "join": item.get("join") or "intersection"}
		for n, item in enumerate(items)
	]
	result = run_worker(job, {"command": parameters.get("operation") or "offset", "workspace": workspace, "items": request_items}, log)
	job.db_set("results", json.dumps(result))
	failed = [i for i in result["items"] if i.get("error")]
	return _("Offset {0} curve(s); {1} failed.").format(len(result["items"]) - len(failed), len(failed))


HANDLERS = {"Build Geometry": run_build, "Validate Model": run_validate, "Tessellate": run_tessellate, "Curve Operation": run_curve_operation}


def handler_for(job_type):
	if job_type in ("Inspect Import", "Import", "Export"):
		from is_production.cad_engineering.services import cad_exchange_service

		return {"Inspect Import": cad_exchange_service.run_inspect, "Import": cad_exchange_service.run_import, "Export": cad_exchange_service.run_export}[job_type]
	return HANDLERS[job_type]
