"""Exchange of surfaces, meshes, points and solids (Phase 3): LandXML, CSV/XYZ, STL, OBJ, LAS/LAZ, STEP.

Called by cad_exchange_service for every format other than DXF, with the same two-step import (inspect → report
→ commit into the Working revision) and the same export record. Parsing runs in the isolated CAD worker
(surface_worker.py for surfaces and points, geometry_worker.py / OCCT for STEP).
"""

import json
import os

import frappe
from frappe import _
from frappe.utils import cint, flt

from is_production.cad_engineering.services import cad_job_service, cad_model_service, cad_surface_service, cad_worker_service
from is_production.cad_engineering.services import cad_storage_service as storage

SURFACE_FORMATS = {"LandXML", "CSV", "XYZ", "STL", "OBJ", "LAS", "LAZ", "IFC", "E57"}
EXPORT_FORMATS = {"IFC": "ifc", "LandXML": "xml", "STL": "stl", "OBJ": "obj", "CSV": "csv", "XYZ": "xyz", "STEP": "step", "GeoJSON": "geojson", "Blocks CSV": "csv", "Drill Holes CSV": "csv"}
POINT_ENTITY_LIMIT = 200_000


def format_of(file_name):
	ext = os.path.splitext(file_name or "")[1].lower()
	return {
		".dxf": "DXF",
		".xml": "LandXML",
		".landxml": "LandXML",
		".csv": "CSV",
		".xyz": "XYZ",
		".txt": "XYZ",
		".pts": "XYZ",
		".stl": "STL",
		".obj": "OBJ",
		".las": "LAS",
		".laz": "LAZ",
		".step": "STEP",
		".stp": "STEP",
		".ifc": "IFC",
		".e57": "E57",
	}.get(ext)


def worker_options(options):
	return {
		"unit_scale": flt(options.get("unit_scale")) or 1.0,
		"axis_mapping": options.get("axis_mapping") or "As in file",
		"columns": options.get("columns") or "",
		"ground_only": bool(options.get("ground_only")),
		"thin": flt(options.get("thin")),
		"as_mesh": bool(options.get("as_mesh")),
	}


def read_file(job, io, path, workspace, log, options):
	if io.file_format == "IFC":
		request = {"command": "import", "workspace": workspace, "input": path, "options": {"group_by": options.get("ifc_group") or "element", "georeference": options.get("ifc_georeference", True), "axis_mapping": options.get("axis_mapping"), "unit_scale": flt(options.get("unit_scale")) or 1.0}}
		return cad_job_service.run_worker(job, request, log, script=cad_worker_service.IFC_WORKER)
	if io.file_format == "STEP":
		return cad_job_service.run_worker(job, {"command": "step_read", "workspace": workspace, "input": path, "unit_scale": flt(options.get("unit_scale")) or 1.0}, log)
	request = {"command": "import", "workspace": workspace, "input": path, "format": io.file_format, "file_name": os.path.basename(io.source_file), "options": worker_options(options)}
	return cad_job_service.run_worker(job, request, log, script=cad_worker_service.SURFACE_WORKER)


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


def inspect(job, io, path, workspace, log):
	"""The validation report of a non-DXF file (nothing is stored)."""
	if io.file_format == "IFC":
		r = cad_job_service.run_worker(job, {"command": "inspect", "workspace": workspace, "input": path}, log, script=cad_worker_service.IFC_WORKER)
		return {**r, "entity_count": r["elements"], "invalid_count": 0, "units_name": None}
	if io.file_format == "E57":
		r = cad_job_service.run_worker(job, {"command": "inspect_e57", "workspace": workspace, "input": path}, log, script=cad_worker_service.CLOUD_WORKER)
		return {**r, "entity_count": 1, "invalid_count": 0, "warnings": [], "units_name": None}
	result = read_file(job, io, path, workspace, log, {})
	if io.file_format == "STEP":
		items = result["items"]
		boxes = [i["bbox"] for i in items if i.get("bbox")]
		return {
			"format": "STEP",
			"solids": len(items),
			"invalid_solids": sum(1 for i in items if not i["valid"]),
			"volumes": [round(i.get("volume") or 0, 6) for i in items[:200]],
			"extents": union(boxes),
			"entity_count": len(items),
			"warnings": [],
			"invalid_count": sum(1 for i in items if not i["valid"]),
			"units_name": None,
		}
	report = {k: v for k, v in result.items() if k not in ("points_file", "point_ids")}
	for item in report.get("surfaces", []) + report.get("meshes", []):
		item.pop("tin", None)
		v = (item["stats"] or {}).get("validation") or {}
		v.pop("examples", None)
		v["spikes"] = (v.get("spikes") or [])[:20]
	boxes = [s["stats"]["bbox"] for s in report.get("surfaces", []) + report.get("meshes", [])]
	if report.get("points_bbox"):
		boxes.append(report["points_bbox"])
	report["extents"] = union(boxes)
	report["entity_count"] = len(report.get("surfaces", [])) + len(report.get("meshes", [])) + (1 if report.get("points") else 0)
	report["invalid_count"] = sum(1 for s in report.get("surfaces", []) if not s["stats"]["validation"].get("ok", True))
	report.setdefault("warnings", [])
	report["units_name"] = None  # these formats do not say; the user chooses
	return report


def union(boxes):
	boxes = [b for b in boxes if b]
	if not boxes:
		return None
	return [min(b[k] for b in boxes) for k in range(3)] + [max(b[k] for b in boxes) for k in range(3, 6)]


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------


def add_blob_entity(model, working, entity_type, geometry, digest, layer, source_id=None, metadata=None):
	doc = frappe.get_doc(
		{
			"doctype": "CAD Entity",
			"cad_model": model,
			"entity_id": f"{source_id}-{digest[:10]}".upper() if source_id else f"S-{digest[:16]}".upper(),
			"entity_type": entity_type,
			"cad_layer": layer,
			"geometry": json.dumps(geometry),
			"geometry_ref": digest,
			"revision": working,
			"entity_version": 1,
			"source_id": source_id,
			"metadata": json.dumps(metadata) if metadata else None,
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.entity_id


def import_file(job, io, options, path, workspace, log, working):
	"""Store the file's surfaces / meshes / solids / points in the model. Returns (entity ids, warnings)."""
	model = io.cad_model
	if io.file_format == "E57" or (options.get("points_as") == "cloud" and io.file_format in ("LAS", "LAZ", "XYZ", "CSV")):
		from is_production.cad_engineering.services import cad_cloud_service

		layer = cad_model_service.ensure_layers(model, [{"layer_name": options.get("layer") or io.file_format}])[options.get("layer") or io.file_format]
		entity_id, _stats = cad_cloud_service.import_cloud(job, io, options, path, workspace, log, layer, working)
		return [entity_id], []
	result = read_file(job, io, path, workspace, log, options)
	layer = cad_model_service.ensure_layers(model, [{"layer_name": options.get("layer") or io.file_format}])[options.get("layer") or io.file_format]
	prefix = "I" + io.name.split("-")[-1]
	created, warnings = [], []
	base_name = os.path.splitext(os.path.basename(io.source_file))[0]
	surface_type = options.get("surface_type") or "Survey Surface"
	source = {"operation": "import", "file": os.path.basename(io.source_file), "format": io.file_format, "import": io.name}

	if io.file_format == "STEP":
		for item in result["items"]:
			if not item["valid"]:
				warnings.append(_("Solid {0} of the STEP file is not valid and was skipped.").format(item["key"]))
				continue
			brep = cad_job_service.checked_output(workspace, item["brep"])
			digest = storage.store_blob(model, brep, "brep")
			record = {k: item.get(k) for k in ("valid", "bbox", "volume", "area")}
			record.update({"source": {"op": "step", "file": source["file"]}, "job": job.name})
			storage.provider().write(storage.blob_key(model, digest, "json"), json.dumps(record).encode())
			if item.get("mesh"):
				storage.provider().import_file(storage.mesh_key(model, digest), cad_job_service.checked_output(workspace, item["mesh"]))
			created.append(add_blob_entity(model, working, "Solid", {"source": {"op": "step", "file": source["file"]}}, digest, layer, f"{prefix}-{item['key']}"))
		return created, warnings

	for n, item in enumerate(result.get("surfaces") or []):
		digest = cad_surface_service.store_tin(job, workspace, item["tin"], item["stats"], source, log=log)
		name = item["name"] if len(result["surfaces"]) > 1 or item["name"] != "mesh" else base_name
		created.append(add_blob_entity(model, working, "Surface", {"name": unique_surface_name(model, name), "surface_type": surface_type, "source": source}, digest, layer, f"{prefix}-{n}"))
	for n, item in enumerate(result.get("meshes") or []):
		digest = cad_surface_service.store_tin(job, workspace, item["tin"], item["stats"], source, entity_type="Mesh", log=log)
		created.append(add_blob_entity(model, working, "Mesh", {"name": item["name"] or base_name, "source": source}, digest, layer, f"{prefix}-M{n}", {"ifc": item["metadata"]} if item.get("metadata") else None))
		if not item["stats"].get("closed"):
			warnings.append(_("Mesh {0} is open ({1} open edges); it has no volume.").format(item["name"], item["stats"].get("open_edges")))

	if result.get("points"):
		as_ = options.get("points_as") or ("surface" if result["points"] > 50000 or io.file_format in ("LAS", "LAZ") else "points")
		if as_ in ("points", "both"):
			if result["points"] > POINT_ENTITY_LIMIT:
				warnings.append(_("{0} points: too many to keep as separate points (limit {1}); they were used for the surface only.").format(result["points"], POINT_ENTITY_LIMIT))
				as_ = "surface"
			else:
				created.extend(import_points(model, working, workspace, result, layer, prefix))
		if as_ in ("surface", "both"):
			request = {"command": "build", "workspace": workspace, "name": "points_surface", "input": write_json(workspace, "points_build.json", {"points_file": result["points_file"], "max_edge": flt(options.get("max_edge")) or None})}
			built = cad_job_service.run_worker(job, request, log, script=cad_worker_service.SURFACE_WORKER)
			digest = cad_surface_service.store_tin(job, workspace, built["tin"], built["stats"], {**source, "max_edge": options.get("max_edge")}, log=log)
			created.append(add_blob_entity(model, working, "Surface", {"name": unique_surface_name(model, base_name), "surface_type": surface_type, "source": source}, digest, layer, f"{prefix}-P"))
	return created, warnings


def write_json(workspace, name, data):
	path = os.path.join(workspace, "inputs", name)
	os.makedirs(os.path.dirname(path), exist_ok=True)
	with open(path, "w") as f:
		json.dump(data, f)
	return path


def import_points(model, working, workspace, result, layer, prefix):
	import numpy  # bench has NumPy (a Frappe/ERPNext dependency)

	xyz = numpy.load(cad_job_service.checked_output(workspace, result["points_file"]))
	ids = result.get("point_ids") or []
	rows = [
		{
			"entity_id": f"{prefix}-PT{n}",
			"entity_type": "Point",
			"geometry": {"p": [float(v) for v in p]},
			"layer": None,
			"source_id": ids[n] if n < len(ids) and ids[n] else None,
			"metadata": {"point_id": ids[n]} if n < len(ids) and ids[n] else None,
		}
		for n, p in enumerate(xyz.tolist())
	]
	layer_name = frappe.db.get_value("CAD Layer", layer, "layer_name")
	inserted, _skipped = cad_model_service.bulk_insert_entities(model, rows, working, {"0": layer, None: layer, layer_name: layer})
	return inserted


def unique_surface_name(model, name):
	taken = {frappe.parse_json(g).get("name") for g in frappe.get_all("CAD Entity", filters={"cad_model": model, "entity_type": "Surface"}, pluck="geometry")}
	candidate, n = name, 2
	while candidate in taken:
		candidate = f"{name} ({n})"
		n += 1
	return candidate


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def export(job, io, options, content, workspace, log):
	"""LandXML / STL / OBJ / CSV / XYZ of surfaces (and meshes), STEP of solids. Returns (path, report)."""
	model = io.cad_model
	fmt = io.file_format
	chosen = set(options.get("geometry_refs") or [])
	out = os.path.join(workspace, "outputs", f"export.{EXPORT_FORMATS[fmt]}")
	if fmt in ("GeoJSON", "Blocks CSV", "Drill Holes CSV"):  # mining design data: written here, no worker needed
		from is_production.cad_engineering.services import cad_mining_service

		os.makedirs(os.path.dirname(out), exist_ok=True)
		writer = {"GeoJSON": cad_mining_service.export_geojson, "Blocks CSV": cad_mining_service.export_blocks_csv, "Drill Holes CSV": cad_mining_service.export_drill_csv}[fmt]
		return out, writer(model, content, out)
	if fmt == "IFC":
		return export_ifc(job, model, options, content, workspace, log, out, chosen)
	if fmt == "STEP":
		solids = [e for e in content["entities"] if e["entity_type"] == "Solid" and (not chosen or e["geometry_ref"] in chosen)]
		if not solids:
			frappe.throw(_("There are no solids to export."))
		breps = [cad_job_service.stage_blob(model, e["geometry_ref"], workspace, f"solid_{n}") for n, e in enumerate(solids)]
		result = cad_job_service.run_worker(job, {"command": "step_write", "workspace": workspace, "breps": breps, "out": out}, log)
		return result["out"], {"written": {"Solid": len(solids)}, "format": "STEP AP214"}
	surfaces = [e for e in content["entities"] if e["entity_type"] in ("Surface", "Mesh") and (not chosen or e["geometry_ref"] in chosen)]
	if not surfaces:
		frappe.throw(_("There are no surfaces to export."))
	items = [{"name": (e.get("geometry") or {}).get("name") or e["entity_id"], "tin": cad_surface_service.stage_tin(model, e["geometry_ref"], workspace, f"surface_{n}")} for n, e in enumerate(surfaces)]
	request = {"command": "export", "workspace": workspace, "format": fmt, "out": out, "surfaces": items, "axis_mapping": options.get("axis_mapping") or "As in file", "date": str(frappe.utils.today())}
	result = cad_job_service.run_worker(job, request, log, script=cad_worker_service.SURFACE_WORKER)
	return result["out"], {"written": {"Surface": len(items)}, "format": fmt, "bytes": result["bytes"]}


def export_ifc(job, model, options, content, workspace, log, out, chosen):
	"""An IFC4 file of the model's surfaces, meshes, solids (their display tessellation) and lines / polylines,
	georeferenced with the model's CRS and local origin."""
	doc = frappe.get_doc("CAD Model", model)
	meshes, polylines, tessellate = [], [], []
	for n, e in enumerate(content["entities"]):
		if chosen and e.get("geometry_ref") not in chosen and e["entity_type"] in ("Surface", "Mesh", "Solid"):
			continue
		name = (e.get("geometry") or {}).get("name") or e["entity_id"]
		if e["entity_type"] in ("Surface", "Mesh"):
			meshes.append({"name": name, "kind": "surface" if e["entity_type"] == "Surface" else "mesh", "tin": cad_surface_service.stage_tin(model, e["geometry_ref"], workspace, f"ifc_{n}")})
		elif e["entity_type"] == "Solid":
			if not storage.provider().exists(storage.mesh_key(model, e["geometry_ref"])):
				tessellate.append(e["geometry_ref"])
			meshes.append({"name": (e.get("metadata") or {}).get("name") or e["entity_id"], "kind": "solid", "ref": e["geometry_ref"]})
		elif e["entity_type"] in ("Line", "Polyline", "Arc", "Circle", "Spline", "Ellipse") and not chosen:
			pts = cad_surface_service.densify(e["entity_type"], e["geometry"])
			if pts and len(pts) >= 2:
				polylines.append({"name": f"{e.get('layer') or '0'} {e['entity_type']}", "points": pts, "closed": bool((e.get("geometry") or {}).get("closed"))})
	if tessellate:
		cad_job_service.run_tessellate(job, {"geometry_refs": list(set(tessellate))}, workspace, log)
	for n, m in enumerate(meshes):
		if m.get("ref"):
			key = storage.mesh_key(model, m.pop("ref"))
			if storage.provider().exists(key):
				m["mesh"] = os.path.join(workspace, "inputs", f"solid_{n}.cadmesh")
				os.makedirs(os.path.dirname(m["mesh"]), exist_ok=True)
				import shutil

				shutil.copyfile(storage.provider().local_path(key), m["mesh"])
	meshes = [m for m in meshes if m.get("tin") or m.get("mesh")]
	if not meshes and not polylines:
		frappe.throw(_("There is nothing to export to IFC (surfaces, meshes, solids or lines)."))
	origin = [flt(doc.local_origin_x), flt(doc.local_origin_y), flt(doc.local_origin_z)]
	request = {"command": "export", "workspace": workspace, "out": out, "origin": origin, "crs": doc.crs or "unknown", "project": doc.model_name, "site": doc.model_code, "meshes": meshes, "polylines": polylines}
	result = cad_job_service.run_worker(job, request, log, script=cad_worker_service.IFC_WORKER)
	return result["out"], {"written": result["written"], "format": "IFC4", "bytes": result["bytes"]}
