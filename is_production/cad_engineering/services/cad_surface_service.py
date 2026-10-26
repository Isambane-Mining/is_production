"""Surfaces (TINs) and surface analysis: the bench side of ``cad_worker/surface_worker.py``.

A surface is a CAD Entity of type **Surface** whose ``geometry_ref`` is the SHA-256 of a TIN file in model storage
(authoritative: double-precision vertices + triangles). Next to it the server writes ``<sha>.json`` (its own record
of the statistics and validation, as for solids) and a display tile pyramid under the mesh cache, so the
workstation loads only the tiles in view at a suitable level of detail. **CAD Surface** records mirror the Surface
entities of each model (name, type, statistics, validation, source) and are reconciled after every change.

Jobs
----
Surface   build (points/breaklines/boundary/holes from entities, GIS datasets or other surfaces), repair, clip,
          smooth → a new TIN; the workstation then adds or updates the Surface entity (one undoable edit).
Analysis  contours, profile, sections, volume, intersect, analyse, section_solids → results; volumes are also
          recorded as CAD Volume Calculation (method, units, inputs by checksum, checks).
"""

import json
import os
import shutil

import frappe
from frappe import _
from frappe.utils import cint, flt

from is_production.cad_engineering.services import cad_geometry, cad_job_service, cad_revision_service, cad_worker_service
from is_production.cad_engineering.services import cad_storage_service as storage

SURFACE = "CAD Surface"
ENTITY = "CAD Entity"
OPERATIONS = {"build", "repair", "clip", "smooth", "tiles"}
ANALYSES = {"contours", "profile", "sections", "volume", "intersect", "analyse", "section_solids"}
MAX_SOURCE_POINTS = 20_000_000
UNITS_CUBED = {"Metres": "m³", "Millimetres": "mm³", "Kilometres": "km³", "Feet": "ft³", "US Survey Feet": "US survey ft³"}


# ---------------------------------------------------------------------------
# storage of TINs and their display tiles
# ---------------------------------------------------------------------------


def tile_key(model, digest, name):
	storage.check_model_code(model)
	if not storage.SHA256.match(digest or "") or not (name == "index.json" or (name.startswith("t") and name.endswith(".cadmesh") and "/" not in name)):
		frappe.throw(_("Invalid surface tile {0}.").format(name))
	return f"mesh/{model}/{digest}/{name}"


def stage_tin(model, digest, workspace, name):
	source = storage.provider().local_path(storage.blob_key(model, digest, "tin"))
	if not os.path.isfile(source):
		frappe.throw(_("Surface file {0} is missing from model storage.").format(digest[:12]))
	target = os.path.join(workspace, "inputs", f"{name}.tin")
	os.makedirs(os.path.dirname(target), exist_ok=True)
	shutil.copyfile(source, target)
	return target


def summary(validation):
	problems = [
		f"{validation[k]} {label}"
		for k, label in (
			("degenerate_triangles", "degenerate triangles"),
			("flipped_triangles", "flipped triangles"),
			("duplicate_triangles", "duplicate triangles"),
			("non_manifold_edges", "non-manifold edges"),
			("duplicate_plan_points", "points on top of each other"),
		)
		if validation.get(k)
	]
	if validation.get("spike_count"):
		problems.append(f"{validation['spike_count']} possible spikes")
	return "; ".join(problems) or None


def store_tin(job, workspace, path, stats, source, entity_type="Surface", log=None):
	"""Keep a worker's TIN in model storage with the server's record of it, and build its display tiles."""
	model = job.cad_model
	digest = storage.store_blob(model, cad_job_service.checked_output(workspace, path), "tin")
	validation = stats.get("validation") or {}
	record = {
		"bbox": stats["bbox"],
		"valid": bool(validation.get("ok", True)) if entity_type == "Surface" else not validation.get("non_manifold_edges"),
		"message": summary(validation),
		"area": stats.get("surface_area"),
		"volume": stats.get("volume"),
		"stats": stats,
		"validation": validation,
		"source": source,
		"job": job.name,
	}
	storage.provider().write(storage.blob_key(model, digest, "json"), json.dumps(record).encode())
	make_tiles(job, workspace, model, digest, log or [])
	return digest


def make_tiles(job, workspace, model, digest, log):
	store = storage.provider()
	if store.exists(tile_key(model, digest, "index.json")):
		return
	tin = stage_tin(model, digest, workspace, f"tiles_{digest[:8]}")
	out_dir = os.path.join(workspace, "outputs", f"tiles_{digest[:8]}")
	result = run(job, {"command": "tiles", "workspace": workspace, "tin": tin, "out_dir": out_dir}, log)
	index_dir = os.path.dirname(cad_job_service.checked_output(workspace, result["index"]))
	for name in os.listdir(index_dir):
		if name != "index.json":
			store.import_file(tile_key(model, digest, name), os.path.join(index_dir, name))
	store.import_file(tile_key(model, digest, "index.json"), os.path.join(index_dir, "index.json"))  # last: the index means "tiles complete"


def run(job, request, log):
	return cad_job_service.run_worker(job, request, log, script=cad_worker_service.SURFACE_WORKER)


def sidecar(model, digest):
	key = storage.blob_key(model, digest, "json")
	store = storage.provider()
	return json.loads(store.read(key)) if store.exists(key) else None


# ---------------------------------------------------------------------------
# sources: entities and GIS datasets
# ---------------------------------------------------------------------------


def densify(entity_type, g):
	"""Points of a curve with its Z, for use as a breakline."""
	if entity_type == "Line":
		return [g["p0"], g["p1"]]
	if entity_type == "Polyline":
		pts = cad_geometry.polyline_points(g["points"], g.get("bulges"), g.get("closed"), 16)
		return pts + ([pts[0]] if g.get("closed") and not g.get("bulges") else [])
	if entity_type == "Circle":
		return cad_geometry.arc_points({**g, "start_angle": 0.0, "end_angle": 2 * 3.141592653589793}, 128)
	if entity_type == "Arc":
		return cad_geometry.arc_points(g, 64)
	if entity_type == "Ellipse":
		return cad_geometry.ellipse_points(g, 128)
	return None


def loop_of(entity_type, g):
	"""A closed plan loop (boundary or hole) from a closed polyline, circle or closed ellipse."""
	if entity_type == "Polyline" and g.get("closed"):
		return [p[:2] for p in cad_geometry.polyline_points(g["points"], g.get("bulges"), True, 16)]
	if entity_type in ("Circle", "Ellipse"):
		pts = densify(entity_type, g)
		return [p[:2] for p in pts[:-1]]
	frappe.throw(_("A boundary or hole must be a closed polyline, circle or ellipse."))


def entity_rows(model, ids):
	rows = frappe.get_all(ENTITY, filters={"cad_model": model, "entity_id": ["in", ids]}, fields=["entity_id", "entity_type", "geometry", "geometry_ref"])
	missing = set(ids) - {r.entity_id for r in rows}
	if missing:
		frappe.throw(_("Save the model first: {0} not found.").format(", ".join(sorted(missing)[:5])))
	for r in rows:
		r.geometry = frappe.parse_json(r.geometry)
	return rows


def gather_sources(model, params, workspace, warnings):
	"""Points, breaklines, boundary, holes and surfaces (as TIN files) for a build."""
	points, breaklines, tins = [], [], []
	for row in entity_rows(model, params.get("points") or []):
		if row.entity_type == "Point":
			points.append(row.geometry["p"])
		elif row.entity_type in ("Surface", "Mesh"):
			tins.append(row.geometry_ref)
		else:
			pts = densify(row.entity_type, row.geometry)
			if pts:
				points.extend(pts)
			else:
				warnings.append(_("{0} {1} has no points to use.").format(_(row.entity_type), row.entity_id))
	for row in entity_rows(model, params.get("breaklines") or []):
		pts = densify(row.entity_type, row.geometry)
		if pts and len(pts) >= 2:
			breaklines.append(pts)
		else:
			warnings.append(_("{0} {1} cannot be a breakline.").format(_(row.entity_type), row.entity_id))
	boundary = None
	if params.get("boundary"):
		row = entity_rows(model, [params["boundary"]])[0]
		boundary = loop_of(row.entity_type, row.geometry)
	holes = [loop_of(r.entity_type, r.geometry) for r in entity_rows(model, params.get("holes") or [])]
	gis_points, gis_lines = gis_sources(model, params.get("gis_datasets") or [], warnings)
	points.extend(gis_points)
	breaklines.extend(gis_lines)
	data = {"points": points, "breaklines": breaklines, "boundary": boundary, "holes": holes, "max_edge": flt(params.get("max_edge")) or None}
	staged = [stage_tin(model, ref, workspace, f"source_{n}") for n, ref in enumerate(tins)]
	if staged:
		data["tins"] = staged
	if len(points) + sum(len(b) for b in breaklines) > MAX_SOURCE_POINTS:
		frappe.throw(_("Too many source points."))
	return data


def gis_sources(model, datasets, warnings):
	"""Features of GIS datasets in the model's CRS: points (and line/polygon vertices with Z as breaklines)."""
	if not datasets:
		return [], []
	from pyproj import Transformer

	target = frappe.db.get_value("CAD Model", model, "crs")
	points, lines = [], []
	for name in datasets:
		dataset = frappe.get_doc("GIS Dataset", name)
		dataset.check_permission("read")
		transform = None
		if target and dataset.crs and dataset.crs != target:
			transform = Transformer.from_crs(dataset.crs, target, always_xy=True).transform
		if not dataset.has_z:
			warnings.append(_("GIS dataset {0} has no elevations; its features were skipped.").format(name))
			continue

		def conv(c):
			x, y, z = (c + [0.0])[:3]
			if transform:
				x, y = transform(x, y)
			return [x, y, z]

		for row in frappe.get_list("GIS Feature", filters={"dataset": name}, fields=["geometry"], limit_page_length=0):
			g = frappe.parse_json(row.geometry) or {}
			kind, coords = g.get("type"), g.get("coordinates")
			if kind == "Point":
				points.append(conv(coords))
			elif kind == "MultiPoint":
				points.extend(conv(c) for c in coords)
			elif kind == "LineString":
				lines.append([conv(c) for c in coords])
			elif kind == "MultiLineString":
				lines.extend([conv(c) for c in part] for part in coords)
			elif kind == "Polygon":
				lines.extend([conv(c) for c in ring] for ring in coords)
			elif kind == "MultiPolygon":
				lines.extend([conv(c) for c in ring] for poly in coords for ring in poly)
	return points, lines


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------


def start_surface(model, operation, params):
	if operation not in OPERATIONS:
		frappe.throw(_("Unknown surface operation {0}.").format(operation))
	params = frappe.parse_json(params) if isinstance(params, str) else params or {}
	return cad_job_service.start_job("Surface", model, cad_revision_service.working_revision(model), {"operation": operation, **params})


def run_surface(job, parameters, workspace, log):
	model = job.cad_model
	operation = parameters["operation"]
	warnings = []
	request = {"command": operation, "workspace": workspace, "name": "result"}
	source = {"operation": operation}
	if operation == "tiles":  # display tiles for a surface that has none yet
		ref = parameters.get("geometry_ref")
		surface_refs_any(model, [ref])
		make_tiles(job, workspace, model, ref, log)
		job.db_set("results", json.dumps({"geometry_ref": ref}))
		return _("Display tiles made.")
	if operation == "build":
		data = gather_sources(model, parameters, workspace, warnings)
		path = os.path.join(workspace, "inputs", "build.json")
		os.makedirs(os.path.dirname(path), exist_ok=True)
		with open(path, "w") as f:
			json.dump(data, f)
		request["input"] = path
		source.update({k: parameters.get(k) for k in ("points", "breaklines", "boundary", "holes", "gis_datasets", "max_edge") if parameters.get(k)})
	else:
		ref = parameters.get("geometry_ref")
		if not storage.SHA256.match(ref or ""):
			frappe.throw(_("Choose the surface to change."))
		request["tin"] = stage_tin(model, ref, workspace, "source")
		source["from"] = ref
		if operation == "clip":
			rows = entity_rows(model, [parameters["boundary"]])
			request["boundary"] = loop_of(rows[0].entity_type, rows[0].geometry)
			request["holes"] = [loop_of(r.entity_type, r.geometry) for r in entity_rows(model, parameters.get("holes") or [])]
			request["keep"] = "outside" if parameters.get("keep") == "outside" else "inside"
			source.update({"boundary": parameters["boundary"], "keep": request["keep"]})
		elif operation == "smooth":
			request["iterations"] = cint(parameters.get("iterations")) or 3
			request["weight"] = flt(parameters.get("weight")) or 0.5
			source.update({"iterations": request["iterations"], "weight": request["weight"]})
		elif operation == "repair":
			request["options"] = {"remove_spikes": bool(parameters.get("remove_spikes")), "spike_factor": flt(parameters.get("spike_factor")) or 8.0}
			source.update(request["options"])
	result = run(job, request, log)
	digest = store_tin(job, workspace, result["tin"], result["stats"], source, log=log)
	log.extend(warnings)
	out = {"geometry_ref": digest, "stats": result["stats"], "warnings": warnings, "source": source, "bbox": result["stats"]["bbox"]}
	job.db_set("results", json.dumps(out))
	return _("Surface: {0} triangles.").format(result["stats"]["triangle_count"])


def start_analysis(model, analysis, params):
	if analysis not in ANALYSES:
		frappe.throw(_("Unknown analysis {0}.").format(analysis))
	params = frappe.parse_json(params) if isinstance(params, str) else params or {}
	frappe.get_doc("CAD Model", model).check_permission("read")
	return cad_job_service.start_job("Analysis", model, cad_revision_service.working_revision(model), {"analysis": analysis, **params}, check_permission=False)


def surface_refs_any(model, refs):
	for ref in refs:
		if not storage.SHA256.match(ref or "") or not frappe.db.exists(ENTITY, {"cad_model": model, "geometry_ref": ref}):
			frappe.throw(_("Surface {0} is not part of {1}.").format((ref or "")[:12], model))


def surface_refs(model, refs):
	"""The model's surfaces by geometry_ref, with their names (only surfaces this model has)."""
	rows = {r.geometry_ref: r for r in frappe.get_all(ENTITY, filters={"cad_model": model, "entity_type": "Surface", "geometry_ref": ["in", refs]}, fields=["entity_id", "geometry", "geometry_ref"])}
	for ref in refs:
		if ref not in rows:
			frappe.throw(_("Surface {0} is not a saved surface of {1}.").format(ref[:12], model))
	return {ref: frappe.parse_json(rows[ref].geometry).get("name") for ref in refs}


def run_analysis(job, parameters, workspace, log):
	model = job.cad_model
	analysis = parameters["analysis"]
	request = {"command": analysis, "workspace": workspace}
	if analysis == "section_solids":
		refs = parameters.get("geometry_refs") or []
		items = [{"key": ref, "brep": cad_job_service.stage_blob(model, ref, workspace, f"solid_{n}")} for n, ref in enumerate(refs)]
		result = cad_job_service.run_worker(job, {"command": "section", "workspace": workspace, "origin": parameters["origin"], "normal": parameters["normal"], "items": items}, log)
		job.db_set("results", json.dumps(result))
		return _("Section through {0} solid(s).").format(len(refs))

	refs = parameters.get("geometry_refs") or []
	names = surface_refs(model, refs)
	if analysis in ("profile", "sections"):
		request["surfaces"] = [{"key": ref, "tin": stage_tin(model, ref, workspace, f"s{n}")} for n, ref in enumerate(refs)]
		request["alignment"] = alignment_of(model, parameters["alignment"])
		if analysis == "sections":
			request.update({k: flt(parameters.get(k)) for k in ("interval", "left", "right", "start")})
	elif analysis in ("contours", "analyse"):
		request["tin"] = stage_tin(model, refs[0], workspace, "s0")
		request.update({k: parameters.get(k) for k in ("interval", "major", "base", "elevation_band") if parameters.get(k) is not None})
	elif analysis in ("volume", "intersect"):
		request["base"] = stage_tin(model, refs[0], workspace, "base")
		if len(refs) > 1:
			request["comparison"] = stage_tin(model, refs[1], workspace, "comparison")
		elif analysis == "volume":
			if parameters.get("elevation") in (None, ""):
				frappe.throw(_("Choose a comparison surface or an elevation."))
			request["elevation"] = flt(parameters["elevation"])
		if parameters.get("boundary"):
			row = entity_rows(model, [parameters["boundary"]])[0]
			request["boundary"] = loop_of(row.entity_type, row.geometry)
	result = run(job, request, log)
	if analysis == "volume":
		result["record"] = record_volume(job, parameters, refs, names, request, result)
	result["names"] = names
	job.db_set("results", json.dumps(result))
	return {
		"contours": lambda: _("{0} contour lines.").format(len(result["contours"])),
		"profile": lambda: _("Profile of {0} surface(s), {1} long.").format(len(refs), round(result["length"], 3)),
		"sections": lambda: _("{0} sections.").format(len(result["sections"])),
		"volume": lambda: _("Cut {0}, fill {1}.").format(round(result["cut"], 3), round(result["fill"], 3)),
		"intersect": lambda: _("{0} intersection line(s).").format(len(result["lines"])),
		"analyse": lambda: _("Surface analysed."),
	}[analysis]()


def alignment_of(model, value):
	"""An alignment: an entity id (line/polyline) or a list of points."""
	if isinstance(value, str):
		row = entity_rows(model, [value])[0]
		pts = densify(row.entity_type, row.geometry)
		if not pts:
			frappe.throw(_("The alignment must be a line or polyline."))
		return pts
	return [[flt(c) for c in p][:3] for p in value]


def record_volume(job, parameters, refs, names, request, result):
	model = job.cad_model
	surface_of = {r.geometry_file_reference: r.name for r in frappe.get_all(SURFACE, filters={"cad_model": model, "geometry_file_reference": ["in", refs]}, fields=["name", "geometry_file_reference"])}
	units = UNITS_CUBED.get(frappe.db.get_value("CAD Model", model, "linear_units") or "Metres", "m³")
	base, comp = refs[0], refs[1] if len(refs) > 1 else None
	title = f"{names[base]} → {names[comp]}" if comp else f"{names[base]} → elevation {request.get('elevation')}"
	doc = frappe.get_doc(
		{
			"doctype": "CAD Volume Calculation",
			"title": title[:140],
			"cad_model": model,
			"calculation_type": "Surface to Surface" if comp else "Surface to Elevation",
			"revision": job.revision,
			"processing_job": job.name,
			"valid": 1 if result["valid"] else 0,
			"base_surface": surface_of.get(base),
			"base_name": names[base],
			"base_geometry_ref": base,
			"comparison_surface": surface_of.get(comp) if comp else None,
			"comparison_name": names.get(comp) if comp else None,
			"comparison_geometry_ref": comp,
			"elevation": request.get("elevation") or 0,
			"boundary_entity": parameters.get("boundary"),
			"boundary": json.dumps(request.get("boundary")) if request.get("boundary") else None,
			"cut_volume": result["cut"],
			"fill_volume": result["fill"],
			"net_volume": result["net"],
			"cut_area": result["cut_area"],
			"fill_area": result["fill_area"],
			"plan_area": result["plan_area"],
			"units": units,
			"method": result["method"],
			"validation": json.dumps(result["validation"]),
			"statistics": json.dumps({k: result[k] for k in ("composite_triangles", "base_triangles", "comparison_triangles", "max_depth", "max_height")}),
		}
	)
	doc.flags.ignore_permissions = True
	doc.insert()
	return doc.name


# ---------------------------------------------------------------------------
# CAD Surface records
# ---------------------------------------------------------------------------


def sync_surfaces(model):
	"""Make the model's CAD Surface records match its Surface entities (after any change to them)."""
	entities = frappe.get_all(ENTITY, filters={"cad_model": model, "entity_type": "Surface"}, fields=["name", "entity_id", "geometry", "geometry_ref", "revision", "validation_status"])
	existing = {r.cad_entity: r.name for r in frappe.get_all(SURFACE, filters={"cad_model": model}, fields=["name", "cad_entity"])}
	project = frappe.db.get_value("CAD Model", model, "geo_project")
	seen = set()
	for e in entities:
		g = frappe.parse_json(e.geometry) or {}
		meta = sidecar(model, e.geometry_ref) or {}
		stats = meta.get("stats") or {}
		source = meta.get("source") or {}
		operation = source.get("operation")
		values = {
			"surface_name": g.get("name"),
			"surface_type": g.get("surface_type"),
			"geo_project": project,
			"validation_status": e.validation_status,
			"current_revision": e.revision,
			"geometry_file_reference": e.geometry_ref,
			"triangulation_type": {"build": "Constrained Delaunay", "import": "Imported TIN"}.get(operation, "Derived" if operation else None),
			"source_dataset": source.get("file") or (operation and f"{operation}") or None,
			"build_parameters": json.dumps(source),
			"vertex_count": stats.get("vertex_count"),
			"triangle_count": stats.get("triangle_count"),
			"surface_area": stats.get("surface_area"),
			"plan_area": stats.get("plan_area"),
			"min_elevation": stats.get("min_elevation"),
			"max_elevation": stats.get("max_elevation"),
			"validation_report": json.dumps(meta.get("validation") or {}),
		}
		if e.name in existing:
			frappe.db.set_value(SURFACE, existing[e.name], values, update_modified=False)
		else:
			doc = frappe.get_doc({"doctype": SURFACE, "cad_model": model, "cad_entity": e.name, **values})
			doc.flags.ignore_permissions = True
			doc.insert()
		seen.add(e.name)
	for entity, name in existing.items():
		if entity not in seen:
			frappe.db.delete(SURFACE, {"name": name})
