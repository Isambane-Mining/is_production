"""QGIS Processing from the GIS Workstation (GIS Processing Job).

Registry: ``qgis_worker/processing_worker.py describe`` lists every installed algorithm (native, GDAL,
GRASS, …) with its parameter and output definitions. The result is cached in the site's private folder
(``gis_processing/registry.json``) and rebuilt when QGIS changes or on request (GIS Settings).

Running: ``run_algorithm`` checks the parameters against the definition and the user's permissions,
creates a GIS Processing Job and runs it in the background:

1. each input dataset is written to a GeoPackage in a private workspace — only the features the user may
   read (or only the selected ones), with Z kept — and raster datasets are given by their file;
2. the worker runs the algorithm under the system Python, at lower CPU priority, with a timeout and an
   optional memory limit; progress and log lines stream back into the job; Cancel sends SIGTERM;
3. outputs are saved: vector outputs become **new** GIS Datasets (an input is never overwritten), raster
   outputs become Raster datasets with their GeoTIFF as a private file, other files are attached to the
   job, and numbers/strings are kept in the job's results.
"""

import json
import os
import queue
import re
import shutil
import signal
import subprocess
import threading
import time

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from is_production.geo_planning.services import gis_io_service as io
from is_production.geo_planning.services.gis_health_service import WORKER_DIR, run_worker
from is_production.geo_planning.services.gis_plugin_service import enabled_processing_plugins, hidden_providers
from is_production.geo_planning.services.gis_storage_service import NAME_PATTERN, NON_SPATIAL_TYPES

JOB = "GIS Processing Job"
WORKER = "processing_worker.py"
INPUT_TYPES = {"source", "vector", "layer", "maplayer"}
DESTINATION_TYPES = {"sink", "vectorDestination", "rasterDestination", "fileDestination"}
VECTOR_DESTINATIONS = {"sink", "vectorDestination"}
LOG_LIMIT = 200000
_cache = {}


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def registry_path():
	directory = frappe.get_site_path("private", "gis_processing")
	os.makedirs(directory, exist_ok=True)
	return os.path.join(directory, "registry.json")


def refresh_registry():
	settings = frappe.get_single("GIS Settings")
	from is_production.geo_planning.services.gis_plugin_service import enabled_processing_plugins, record_load_errors

	report = run_worker(settings.qgis_python, WORKER, {"command": "describe", "plugins": enabled_processing_plugins()}, timeout=300)
	if "error" in report or "algorithms" not in report:
		frappe.throw(_("Could not list the QGIS algorithms: {0}").format(report.get("error")))
	record_load_errors(report.get("plugin_errors"), report.get("plugin_providers"))
	path = registry_path()
	with open(path + ".tmp", "w") as f:
		json.dump(report, f)
	os.replace(path + ".tmp", path)
	_cache.clear()
	supported = sum(1 for a in report["algorithms"] if a["supported"] and not a["hidden"])
	frappe.db.set_single_value(
		"GIS Settings",
		"processing_algorithm_count",
		_("{0} of {1} usable here (QGIS {2})").format(supported, len(report["algorithms"]), report["qgis_version"]),
	)
	return report


def registry():
	path = registry_path()
	if not os.path.exists(path):
		refresh_registry()
	mtime = os.path.getmtime(path)
	if _cache.get("mtime") != mtime:
		with open(path) as f:
			data = json.load(f)
		_cache.update(mtime=mtime, data=data, by_id={a["id"]: a for a in data["algorithms"]})
	return _cache


def algorithm(algorithm_id):
	alg = registry()["by_id"].get(algorithm_id)
	if not alg:
		frappe.throw(_("Algorithm {0} is not installed.").format(algorithm_id))
	return alg


@frappe.whitelist()
def list_algorithms():
	frappe.has_permission(JOB, "create", throw=True)
	data = registry()["data"]
	keys = ("id", "name", "group", "provider", "provider_name", "description", "tags", "supported", "unsupported_reason", "deprecated")
	hidden = hidden_providers()  # plugins this user switched off for their own toolbox
	return {
		"qgis_version": data["qgis_version"],
		"algorithms": [{k: a[k] for k in keys} for a in data["algorithms"] if not a["hidden"] and a["provider"] not in hidden],
	}


@frappe.whitelist()
def get_algorithm(algorithm_id):
	frappe.has_permission(JOB, "create", throw=True)
	return algorithm(algorithm_id)


@frappe.whitelist(methods=["POST"])
def refresh_algorithms():
	frappe.has_permission("GIS Settings", "write", throw=True)
	report = refresh_registry()
	return len(report["algorithms"])


# ---------------------------------------------------------------------------
# starting a job
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def run_algorithm(algorithm_id, parameters):
	frappe.has_permission(JOB, "create", throw=True)
	alg = algorithm(algorithm_id)
	if alg["provider"] in hidden_providers():
		frappe.throw(_("{0} comes from a plugin you switched off (Plugins → Manage and Install Plugins).").format(alg["name"]))
	if not alg["supported"]:
		frappe.throw(_("{0} cannot run from the web toolbox: {1}.").format(alg["name"], alg["unsupported_reason"]))
	parameters = frappe.parse_json(parameters) if isinstance(parameters, str) else parameters or {}
	clean = validate_parameters(alg, parameters)
	job = frappe.get_doc(
		{
			"doctype": JOB,
			"algorithm": alg["id"],
			"algorithm_name": alg["name"],
			"provider": alg["provider_name"],
			"status": "Queued",
			"status_message": _("Waiting for a worker…"),
			"parameters": json.dumps(clean),
		}
	).insert()
	settings = frappe.get_cached_doc("GIS Settings")
	frappe.enqueue(
		"is_production.geo_planning.services.gis_processing_service.execute",
		queue="long",
		timeout=(cint(settings.processing_timeout_minutes) or 60) * 60 + 900,
		job_id=f"gis-processing-{frappe.local.site}-{job.name}",
		deduplicate=True,
		enqueue_after_commit=True,
		now=bool(frappe.flags.in_test),
		processing_job=job.name,
	)
	return job_payload(job)


def validate_parameters(alg, values):
	"""Check the toolbox's values against the algorithm definition and the user's permissions."""
	clean, outputs = {}, set()
	for p in alg["parameters"]:
		if p["hidden"]:
			continue
		name, kind = p["name"], p["type"]
		value = values.get(name)
		label = frappe.bold(p["description"] or name)
		if value in (None, "", []):
			if not p["optional"] and kind not in DESTINATION_TYPES and p.get("default") is None:
				frappe.throw(_("{0} is required.").format(label))
			if not p["optional"] and kind in DESTINATION_TYPES:
				frappe.throw(_("Name the output {0}.").format(label))
			continue

		if kind in INPUT_TYPES or kind == "raster":
			clean[name] = check_dataset_ref(value, label, raster=kind == "raster")
		elif kind == "multilayer":
			clean[name] = [check_dataset_ref(v, label) for v in (value if isinstance(value, list) else [value])]
		elif kind in VECTOR_DESTINATIONS:
			frappe.has_permission("GIS Dataset", "create", throw=True)
			dataset_name = (value.get("dataset_name") if isinstance(value, dict) else value) or ""
			if not NAME_PATTERN.match(dataset_name):
				frappe.throw(_("{0}: the dataset name must be lower case letters, digits and underscores.").format(label))
			if frappe.db.exists("GIS Dataset", dataset_name) or dataset_name in outputs:
				# processing never overwrites a dataset
				frappe.throw(_("{0}: dataset {1} already exists; choose a new name.").format(label, dataset_name))
			outputs.add(dataset_name)
			clean[name] = {"dataset_name": dataset_name, "title": (value.get("title") if isinstance(value, dict) else None) or dataset_name}
		elif kind == "rasterDestination":
			frappe.has_permission("GIS Dataset", "create", throw=True)
			raster_name = (value.get("dataset_name") if isinstance(value, dict) else value) or ""
			if not NAME_PATTERN.match(raster_name) or frappe.db.exists("GIS Dataset", raster_name) or raster_name in outputs:
				frappe.throw(_("{0}: choose a new dataset name (lower case letters, digits and underscores).").format(label))
			outputs.add(raster_name)
			clean[name] = {"dataset_name": raster_name, "title": (value.get("title") if isinstance(value, dict) else None) or raster_name}
		elif kind == "fileDestination":
			clean[name] = {"name": io.slug(value.get("name") if isinstance(value, dict) else value, "output")}
		elif kind in ("number", "distance", "scale", "duration", "area", "band"):
			number = flt(value)
			if p.get("integer") or kind == "band":
				number = cint(value)
			for bound, check in (("minimum", lambda v, b: v < b), ("maximum", lambda v, b: v > b)):
				if p.get(bound) is not None and check(number, p[bound]):
					frappe.throw(_("{0} must be {1} {2}.").format(label, _("at least") if bound == "minimum" else _("at most"), p[bound]))
			clean[name] = number
		elif kind == "enum":
			options = p.get("options") or []
			items = value if isinstance(value, list) else [value]
			if p.get("static_strings"):
				if any(v not in options for v in items):
					frappe.throw(_("{0}: choose from the list.").format(label))
			else:
				items = [cint(v) for v in items]
				if any(v < 0 or v >= len(options) for v in items):
					frappe.throw(_("{0}: choose from the list.").format(label))
			clean[name] = items if p.get("multiple") else items[0]
		elif kind == "boolean":
			clean[name] = bool(value) and value not in ("0", "false", "False")
		elif kind == "crs":
			from is_production.geo_planning.services.gis_storage_service import resolve_crs

			clean[name], _code = resolve_crs(value)
		elif kind == "field":
			clean[name] = value if isinstance(value, list) else str(value)
		elif kind == "range":
			clean[name] = ",".join(str(flt(v)) for v in (value if isinstance(value, list) else str(value).split(",")))
		else:  # string, expression, extent, point, datetime, color
			clean[name] = str(value)
	return clean


def check_dataset_ref(value, label, raster=False):
	ref = value if isinstance(value, dict) else {"dataset": value}
	dataset = ref.get("dataset")
	if not dataset or not frappe.db.exists("GIS Dataset", dataset):
		frappe.throw(_("{0}: choose a dataset.").format(label))
	frappe.has_permission("GIS Dataset", "read", dataset, throw=True)
	kind = frappe.db.get_value("GIS Dataset", dataset, ["geometry_type", "source_file"], as_dict=True)
	if raster and (kind.geometry_type != "Raster" or not kind.source_file):
		frappe.throw(_("{0}: {1} is not a raster dataset with a file.").format(label, dataset))
	if not raster and kind.geometry_type == "Raster":
		frappe.throw(_("{0}: {1} is a raster dataset; a vector dataset is needed.").format(label, dataset))
	ids = [cint(i) for i in ref.get("selected_ids") or [] if cint(i) > 0]
	return {"dataset": dataset, "selected_ids": ids} if ids else {"dataset": dataset}


# ---------------------------------------------------------------------------
# status, history, cancel
# ---------------------------------------------------------------------------


def job_payload(job):
	return {
		"name": job.name,
		"algorithm": job.algorithm,
		"algorithm_name": job.algorithm_name,
		"status": job.status,
		"progress": job.progress,
		"status_message": job.status_message,
		"started_on": job.started_on,
		"finished_on": job.finished_on,
		"log": (job.log or "")[-8000:],
		"results": frappe.parse_json(job.results) if job.results else {},
		"outputs": [{k: row.get(k) for k in ("output_name", "output_type", "dataset", "file", "value")} for row in job.outputs],
	}


@frappe.whitelist()
def get_processing_job(name):
	job = frappe.get_doc(JOB, name)
	job.check_permission("read")
	return job_payload(job)


@frappe.whitelist()
def processing_history(limit=30):
	return frappe.get_list(
		JOB,
		fields=["name", "algorithm", "algorithm_name", "status", "progress", "creation", "owner"],
		order_by="creation desc",
		limit_page_length=min(cint(limit) or 30, 200),
	)


@frappe.whitelist(methods=["POST"])
def cancel_processing(name):
	job = frappe.get_doc(JOB, name)
	job.check_permission("write")
	if job.status == "Queued":
		job.db_set({"status": "Cancelled", "status_message": _("Cancelled before it started."), "finished_on": now_datetime()})
	elif job.status == "Running":
		job.db_set({"status": "Cancelling", "status_message": _("Cancelling…")})
	return job_payload(job.reload() or job)


# ---------------------------------------------------------------------------
# running a job (background)
# ---------------------------------------------------------------------------


def workspace(job_name):
	path = frappe.get_site_path("private", "gis_processing", "jobs", job_name)
	os.makedirs(os.path.join(path, "inputs"), exist_ok=True)
	os.makedirs(os.path.join(path, "outputs"), exist_ok=True)
	return os.path.abspath(path)


def execute(processing_job):
	job = frappe.get_doc(JOB, processing_job)
	if job.status != "Queued":  # cancelled while waiting
		return
	job.db_set({"status": "Running", "started_on": now_datetime(), "status_message": _("Preparing inputs…"), "progress": 0})
	frappe.db.commit()
	log = []
	directory = workspace(job.name)
	try:
		alg = algorithm(job.algorithm)
		values = frappe.parse_json(job.parameters or "{}")
		parameters, destinations, crs = prepare_parameters(alg, values, directory, log)
		job.db_set("status_message", _("Running {0}…").format(alg["name"]))
		frappe.db.commit()
		result, error = run_worker_streaming(job, alg, parameters, crs, log)
		if job.status == "Cancelled" or error == "Cancelled.":
			finish(job, "Cancelled", _("Cancelled."), log)
			return
		if error:
			raise RuntimeError(error)
		job.db_set("status_message", _("Saving outputs…"))
		frappe.db.commit()
		save_outputs(job, alg, result, destinations, crs, log)
		finish(job, "Completed", _("Completed: {0} output(s).").format(len(job.outputs) + len(frappe.parse_json(job.results or "{}"))), log)
	except Exception as e:
		frappe.db.rollback()
		log.append(f"Error: {frappe.utils.strip_html(str(e))}")
		frappe.log_error(title=f"GIS processing {job.name}")
		finish(job, "Failed", _("Failed: {0}").format(frappe.utils.strip_html(str(e))[:120]), log)
	finally:
		shutil.rmtree(directory, ignore_errors=True)


def finish(job, status, message, log):
	job.reload()
	job.status = status
	job.status_message = message
	job.progress = 100 if status == "Completed" else job.progress
	job.finished_on = now_datetime()
	job.log = "\n".join(([job.log] if job.log else []) + log)[-LOG_LIMIT:]
	job.save(ignore_permissions=True)
	frappe.db.commit()


def prepare_parameters(alg, values, directory, log):
	"""The QGIS parameter values: input datasets exported to GeoPackages, output paths in the workspace."""
	parameters, destinations, crs = {}, {}, None
	for p in alg["parameters"]:
		name, kind = p["name"], p["type"]
		if name not in values:
			continue
		value = values[name]
		if kind in INPUT_TYPES:
			path, dataset_crs = export_input(value, directory, log)
			parameters[name] = path
			crs = crs or dataset_crs
		elif kind == "multilayer":
			paths = []
			for ref in value:
				path, dataset_crs = export_input(ref, directory, log)
				paths.append(path)
				crs = crs or dataset_crs
			parameters[name] = paths
		elif kind == "raster":
			info = frappe.db.get_value("GIS Dataset", value["dataset"], ["source_file", "crs"], as_dict=True)
			parameters[name] = frappe.get_doc("File", {"file_url": info.source_file}).get_full_path()
			crs = crs or info.crs
		elif kind in VECTOR_DESTINATIONS:
			parameters[name] = os.path.join(directory, "outputs", f"{value['dataset_name']}.gpkg")
			destinations[name] = {"kind": "vector", **value}
		elif kind == "rasterDestination":
			parameters[name] = os.path.join(directory, "outputs", f"{value['dataset_name']}.tif")
			destinations[name] = {"kind": "raster", **value}
		elif kind == "fileDestination":
			extension = p.get("extension") or "html"
			parameters[name] = os.path.join(directory, "outputs", f"{value['name']}.{extension}")
			destinations[name] = {"kind": "file", **value}
		else:
			parameters[name] = value
	return parameters, destinations, crs


def export_input(ref, directory, log):
	"""Write a dataset (only what the user may read, or the selected features) to a GeoPackage."""
	dataset = frappe.get_doc("GIS Dataset", ref["dataset"])
	suffix = "_selection" if ref.get("selected_ids") else ""
	dump = os.path.join(directory, "inputs", f"{dataset.name}{suffix}.ndjson")
	count = io.dump_features(dataset.name, dump, ref.get("selected_ids"))
	path = os.path.join(directory, "inputs", f"{dataset.name}{suffix}.gpkg")
	io.worker(
		{
			"command": "write",
			"format": "GeoPackage",
			"input": dump,
			"out": path,
			"layer": dataset.name,
			"geometry_type": dataset.geometry_type,
			"has_z": bool(dataset.has_z),
			"crs": dataset.crs,
			# the dataset's own attribute names, so field and expression parameters match them
			"fields": [{"name": a.field_name, "data_type": a.data_type} for a in dataset.attributes],
		}
	)
	log.append(_("Input {0}: {1} features{2}.").format(dataset.name, count, _(" (selected)") if suffix else ""))
	return f"{path}|layername={dataset.name}", dataset.crs


def run_worker_streaming(job, alg, parameters, crs, log):
	"""Run the algorithm in the worker; stream progress/log into the job; honour Cancel and the timeout."""
	settings = frappe.get_cached_doc("GIS Settings")
	request = {
		"command": "run",
		"algorithm": alg["id"],
		"parameters": parameters,
		"crs": crs,
		"nice": min(max(cint(settings.processing_nice), 0), 19),
		"memory_limit_mb": cint(settings.processing_memory_limit_mb),
		"plugins": enabled_processing_plugins(),
	}
	timeout = (cint(settings.processing_timeout_minutes) or 60) * 60
	process = subprocess.Popen(
		[settings.qgis_python, "-I", str(WORKER_DIR / WORKER)],
		stdin=subprocess.PIPE,
		stdout=subprocess.PIPE,
		stderr=subprocess.PIPE,
		text=True,
		env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp"), "QT_QPA_PLATFORM": "offscreen", "LANG": "C.UTF-8"},
	)
	process.stdin.write(json.dumps(request))
	process.stdin.close()

	lines = queue.Queue()
	threading.Thread(target=lambda: [lines.put(line) for line in process.stdout] + [lines.put(None)], daemon=True).start()
	stderr = []
	threading.Thread(target=lambda: [stderr.append(line) for line in process.stderr], daemon=True).start()

	started = time.monotonic()
	last_update = last_check = 0
	progress, result, error, finished = 0, None, None, False
	cancelled_at = None
	while not finished:
		try:
			line = lines.get(timeout=1)
		except queue.Empty:
			line = ""
		if line is None:
			finished = True
		elif line:
			try:
				message = json.loads(line)
			except ValueError:
				log.append(line.rstrip())
				message = {}
			if "progress" in message:
				progress = message["progress"]
			if "log" in message:
				log.append(str(message["log"]))
			if "result" in message:
				result = message["result"]
			if "error" in message:
				error = message["error"]

		now = time.monotonic()
		if now - last_update > 2:
			last_update = now
			frappe.db.set_value(JOB, job.name, {"progress": progress}, update_modified=False)
			frappe.db.commit()  # also gives the next read a fresh snapshot
		if now - last_check > 2 and cancelled_at is None:
			last_check = now
			if frappe.db.get_value(JOB, job.name, "status") == "Cancelling":
				cancelled_at = now
				process.send_signal(signal.SIGTERM)
				log.append(_("Cancel requested."))
		if cancelled_at and now - cancelled_at > 15 and process.poll() is None:
			process.kill()
		if now - started > timeout and process.poll() is None:
			process.kill()
			error = _("Stopped after {0} minutes (GIS Settings → Processing timeout).").format(timeout // 60)
	process.wait()
	tail = "".join(stderr[-40:]).strip()
	if (error or process.returncode) and tail:
		log.append(tail[-4000:])
	if cancelled_at:
		job.db_set("status", "Cancelled")
		return None, "Cancelled."
	if result is None and not error:
		error = _("The processing worker stopped unexpectedly (exit code {0}).").format(process.returncode)
	frappe.db.set_value(JOB, job.name, {"progress": 100 if result is not None else progress}, update_modified=False)
	return result, error


def save_outputs(job, alg, result, destinations, crs, log):
	"""Turn the algorithm's results into datasets, files and values on the job."""
	results = {}
	output_types = {o["name"]: o["type"] for o in alg["outputs"]}
	for name, value in (result or {}).items():
		kind = output_types.get(name, "")
		destination = destinations.get(name)
		if destination and destination["kind"] == "vector" and value:
			path = str(value).split("|")[0]
			layer = None
			match = re.search(r"\|layername=([^|]+)", str(value))
			if match:
				layer = match.group(1)
			found = io.worker({"command": "inspect", "path": path})["layers"]
			info = next((lyr for lyr in found if not layer or lyr["layer"] == layer), found[0] if found else None)
			if not info:
				log.append(_("Output {0}: the result has no layer.").format(name))
				continue
			dataset, imported, failed = io.import_new_dataset(
				path, info, destination["dataset_name"], destination["title"], crs, {"reference": job.name, "algorithm": job.algorithm, "output": name}, log
			)
			job.append("outputs", {"output_name": name, "output_type": kind, "dataset": dataset, "value": _("{0} features").format(imported) + (_(", {0} failed").format(failed) if failed else "")})
		elif destination and destination["kind"] == "raster" and value and os.path.exists(str(value)):
			info = run_worker(frappe.get_cached_doc("GIS Settings").qgis_python, WORKER, {"command": "raster_info", "path": str(value)}, timeout=120)
			frappe.has_permission("GIS Dataset", "create", throw=True)
			dataset = frappe.get_doc(
				{
					"doctype": "GIS Dataset",
					"dataset_name": destination["dataset_name"],
					"title": destination["title"],
					"geometry_type": "Raster",
					"crs": info.get("epsg") or crs or "EPSG:4326",
					"category": "Processing Output",
					"source_type": "Processing",
					"source_reference": job.name,
					"import_metadata": json.dumps({"algorithm": job.algorithm, "output": name, **{k: info.get(k) for k in ("width", "height", "bands")}}),
				}
			).insert()
			file_url = io.register_file(str(value), "GIS Dataset", dataset.name, "source_file", f"{dataset.name}-{job.name}.tif")
			dataset.db_set("source_file", file_url)
			job.append("outputs", {"output_name": name, "output_type": kind, "dataset": dataset.name, "file": file_url, "value": f"{info.get('width')} × {info.get('height')}, {info.get('bands')} band(s)"})
		elif (destination and destination["kind"] == "file") or (kind in ("outputHtml", "outputFile") and value and os.path.isfile(str(value))):
			if value and os.path.isfile(str(value)):
				base = os.path.basename(str(value))
				file_url = io.register_file(str(value), JOB, job.name, None, f"{job.name}-{base}")
				job.append("outputs", {"output_name": name, "output_type": kind, "file": file_url})
		elif kind in ("outputVector", "outputRaster", "outputLayer", "outputMultilayer", "outputFolder"):
			continue  # a layer output not chosen as a destination (an optional output left empty)
		else:
			results[name] = value
	job.results = json.dumps(results, default=str) if results else None
	job.save(ignore_permissions=True)
	frappe.db.commit()
