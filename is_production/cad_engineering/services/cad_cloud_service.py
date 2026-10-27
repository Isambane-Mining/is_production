"""Point clouds (Phase 6): Point Cloud entities stored as LAZ, their display tiles, and editing jobs.

A Point Cloud entity's geometry_ref is the SHA-256 of its LAZ file in model storage (with the server's JSON record
like every geometry file); its display tiles live with the surface tiles (mesh/<model>/<sha>/index.json + .cadpts).
Every edit (crop, classes, noise, decimate, ground) is a CAD Processing Job of type *Point Cloud* that writes a new
LAZ: the workstation swaps the entity's geometry_ref as one undoable edit, so the original stays in storage and in
earlier revisions. Surfaces from a cloud and comparisons with a surface run the same way.
"""

import json
import os

import frappe
from frappe import _
from frappe.utils import flt

from is_production.cad_engineering.services import cad_job_service, cad_revision_service, cad_surface_service, cad_worker_service
from is_production.cad_engineering.services import cad_storage_service as storage

ENTITY = "CAD Entity"
OPERATIONS = {"edit", "surface", "compare", "tiles", "export"}
EDIT_OPS = {"crop", "classes", "set_class", "noise", "decimate", "ground"}
EXPORT_FORMATS = {"LAZ": "laz", "LAS": "las", "E57": "e57", "XYZ": "xyz", "CSV": "csv"}


def run_worker(job, request, log):
	return cad_job_service.run_worker(job, request, log, script=cad_worker_service.CLOUD_WORKER)


def store_cloud(job, workspace, path, stats, source, log=None):
	"""Keep a worker's LAZ in model storage with the server's record of it, and build its display tiles."""
	model = job.cad_model
	digest = storage.store_blob(model, cad_job_service.checked_output(workspace, path), "laz")
	record = {"bbox": stats["bbox"], "valid": True, "message": None, "count": stats["count"], "stats": stats, "source": source, "job": job.name}
	storage.provider().write(storage.blob_key(model, digest, "json"), json.dumps(record).encode())
	make_tiles(job, workspace, model, digest, log or [])
	return digest


def stage_cloud(model, digest, workspace, name):
	source = storage.provider().local_path(storage.blob_key(model, digest, "laz"))
	if not os.path.isfile(source):
		frappe.throw(_("Point cloud {0} is missing from model storage.").format(digest[:12]))
	target = os.path.join(workspace, "inputs", f"{name}.laz")
	os.makedirs(os.path.dirname(target), exist_ok=True)
	import shutil

	shutil.copyfile(source, target)
	return target


def make_tiles(job, workspace, model, digest, log):
	store = storage.provider()
	key = cad_surface_service.tile_key(model, digest, "index.json")
	if store.exists(key):
		return
	cloud = stage_cloud(model, digest, workspace, f"tiles_{digest[:8]}")
	out_dir = os.path.join(workspace, "outputs", f"ptiles_{digest[:8]}")
	result = run_worker(job, {"command": "tiles", "workspace": workspace, "cloud": cloud, "out_dir": out_dir}, log)
	index_dir = os.path.dirname(cad_job_service.checked_output(workspace, result["index"]))
	for name in os.listdir(index_dir):
		if name != "index.json":
			store.import_file(cad_surface_service.tile_key(model, digest, name), os.path.join(index_dir, name))
	store.import_file(key, os.path.join(index_dir, "index.json"))  # last: the index means "tiles complete"


def cloud_name(model, ref, name=None):
	"""The cloud's name, after checking the file is this model's: a saved cloud's, or one a Point Cloud job of the
	model made (an edit not saved yet — so edits can follow one another without saving in between)."""
	row = frappe.db.get_value(ENTITY, {"cad_model": model, "geometry_ref": ref, "entity_type": "Point Cloud"}, ["entity_id", "geometry"], as_dict=True)
	if row:
		return (frappe.parse_json(row.geometry) or {}).get("name") or "cloud"
	if frappe.db.exists("CAD Processing Job", {"cad_model": model, "job_type": "Point Cloud", "status": "Completed", "results": ["like", f'%"geometry_ref": "{ref}"%']}):
		return (name or "cloud")[:120]
	frappe.throw(_("Point cloud {0} is not in model {1}.").format(ref[:12], model))


def import_cloud(job, io, options, path, workspace, log, layer, working):
	"""A LAS / LAZ / XYZ / CSV file as one Point Cloud entity (every point, with its classes, intensity and colour)."""
	from is_production.cad_engineering.services.cad_exchange_formats import add_blob_entity

	request = {"command": "import", "workspace": workspace, "input": path, "name": "import", "options": {"unit_scale": options.get("unit_scale"), "axis_mapping": options.get("axis_mapping"), "columns": options.get("columns")}}
	result = run_worker(job, request, log)
	source = {"operation": "import", "file": os.path.basename(path), "format": io.file_format}
	digest = store_cloud(job, workspace, result["cloud"], result["stats"], source, log)
	name = os.path.splitext(os.path.basename(path))[0]
	return add_blob_entity(io.cad_model, working, "Point Cloud", {"name": name, "source": source}, digest, layer, "PC"), result["stats"]


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


def start(model, operation, params):
	if operation not in OPERATIONS:
		frappe.throw(_("Unknown point-cloud operation {0}.").format(operation))
	params = frappe.parse_json(params) if isinstance(params, str) else params or {}
	return cad_job_service.start_job("Point Cloud", model, cad_revision_service.working_revision(model), {"operation": operation, **params})


def run(job, p, workspace, log):
	model = job.cad_model
	operation = p["operation"]
	ref = p.get("geometry_ref")
	if not storage.SHA256.match(ref or ""):
		frappe.throw(_("Choose a point cloud."))
	name = cloud_name(model, ref, p.get("name"))
	cloud = stage_cloud(model, ref, workspace, "cloud")
	if operation == "tiles":  # display tiles for a cloud that has none yet
		make_tiles(job, workspace, model, ref, log)
		job.db_set("results", json.dumps({"geometry_ref": ref}))
		return _("Display tiles made.")
	if operation == "edit":
		ops = [o for o in p.get("operations") or [] if o.get("op") in EDIT_OPS]
		if not ops:
			frappe.throw(_("Nothing to do."))
		for o in ops:  # outlines come from saved entities
			if o.get("outline"):
				o["polygon"] = [q[:2] for q in outline_points(model, o.pop("outline"))]
		res = run_worker(job, {"command": "edit", "workspace": workspace, "cloud": cloud, "operations": ops, "name": "edited"}, log)
		source = {"operation": "edit", "from": ref, "operations": ops}
		digest = store_cloud(job, workspace, res["cloud"], res["stats"], source, log)
		result = {"geometry_ref": digest, "stats": res["stats"], "report": res["report"], "source": source}
		message = _("{0}: {1} → {2} points.").format(name, res["report"][0]["before"], res["stats"]["count"])
	elif operation == "surface":
		res = run_worker(job, {"command": "surface", "workspace": workspace, "cloud": cloud, "classes": p.get("classes") or [], "thin": flt(p.get("thin")), "max_edge": flt(p.get("max_edge")) or None, "name": "cloud_surface"}, log)
		source = {"operation": "point_cloud_surface", "from": ref, "classes": p.get("classes") or [], "thin": flt(p.get("thin"))}
		digest = cad_surface_service.store_tin(job, workspace, res["tin"], res["stats"], source, log=log)
		result = {"geometry_ref": digest, "stats": res["stats"], "points": res["points"], "source": source, "name": _("{0} surface").format(name)}
		message = _("Surface from {0}: {1} points, {2} triangles.").format(name, res["points"], res["stats"].get("triangle_count"))
	elif operation == "compare":
		surface = cad_surface_service.stage_tin(model, p["surface"], workspace, "compare_surface")
		res = run_worker(job, {"command": "compare", "workspace": workspace, "cloud": cloud, "surface": surface, "tolerance": flt(p.get("tolerance")) or 0.1, "name": "compared"}, log)
		source = {"operation": "compare", "from": ref, "surface": p["surface"]}
		digest = store_cloud(job, workspace, res["cloud"], res["stats"], source, log)
		result = {"geometry_ref": digest, "stats": res["stats"], "comparison": res["comparison"], "source": source}
		c = res["comparison"]
		message = _("{0}: {1} % within ±{2} m, mean {3} m, RMSE {4} m.").format(name, round(flt(c.get("within")), 1), c["tolerance"], round(flt(c.get("mean")), 3), round(flt(c.get("rmse")), 3))
	else:  # export
		fmt = (p.get("format") or "LAZ").upper()
		if fmt not in EXPORT_FORMATS:
			frappe.throw(_("Export point clouds as LAZ, LAS, E57, XYZ or CSV."))
		res = run_worker(job, {"command": "export", "workspace": workspace, "cloud": cloud, "format": fmt, "name": name}, log)
		path = cad_job_service.checked_output(workspace, res["file"])
		with open(path, "rb") as f:
			file = frappe.get_doc({"doctype": "File", "file_name": f"{name}.{EXPORT_FORMATS[fmt]}", "is_private": 1, "content": f.read(), "attached_to_doctype": "CAD Model", "attached_to_name": model})
			file.flags.ignore_permissions = True
			file.insert()
		result = {"file_url": file.file_url, "points": res["points"], "format": fmt}
		message = _("{0} exported as {1}: {2} points.").format(name, fmt, res["points"])
	job.db_set("results", json.dumps(result, default=float))
	return message


def outline_points(model, entity_id):
	from is_production.cad_engineering.services.cad_mining_service import polygon_of

	poly, _z = polygon_of(model, entity_id)
	return [list(c) for c in poly.exterior.coords][:-1]
