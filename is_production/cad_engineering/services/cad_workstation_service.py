"""Server API of the CAD Workstation portal page (/portal_cad).

Every method checks Frappe permissions on the server; the browser is never trusted with access control.
Coordinates are model coordinates (double precision); the browser draws relative to the model's local
origin. Display meshes are served as binary files and are never used for calculations.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt
from werkzeug.wrappers import Response

from is_production.cad_engineering.services import cad_job_service, cad_model_service, cad_revision_service
from is_production.cad_engineering.services import cad_storage_service as storage

MODEL_FIELDS = [
	"name",
	"design_standard",
	"last_design_validation",
	"model_name",
	"model_code",
	"model_type",
	"geo_project",
	"company",
	"branch",
	"location",
	"model_status",
	"approval_status",
	"current_revision",
	"working_revision",
	"crs",
	"source_crs",
	"vertical_datum",
	"linear_units",
	"angular_units",
	"local_origin_x",
	"local_origin_y",
	"local_origin_z",
	"min_x",
	"min_y",
	"min_z",
	"max_x",
	"max_y",
	"max_z",
	"entity_count",
	"layer_count",
	"edit_version",
	"modified",
	"modified_by",
	"plan_orientation",
	"annotation_scale",
	"drawing_template",
]


def check_enabled():
	if not frappe.db.get_single_value("CAD Settings", "enable_cad_workstation"):
		frappe.throw(_("The CAD Workstation is switched off (CAD Settings)."))


@frappe.whitelist()
def get_workstation_config():
	frappe.has_permission("CAD Model", "read", throw=True)
	settings = frappe.get_cached_doc("CAD Settings")
	return {
		"enabled": bool(settings.enable_cad_workstation),
		"worker_enabled": bool(settings.cad_worker_enabled),
		"coordinate_precision": cint(settings.coordinate_precision),
		"angular_precision": cint(settings.angular_precision),
		"default_linear_units": settings.default_linear_units,
		"default_angular_units": settings.default_angular_units,
		"default_crs": settings.default_crs,
		"advanced_geometry": bool(settings.enable_advanced_geometry),
		"mining_tools": bool(settings.enable_mining_design_tools),
		"health": {
			"status": settings.health_status,
			"checked_on": settings.last_check_on,
			"summary": settings.health_summary,
			"occt_version": settings.occt_version,
		},
		"can_create_models": bool(frappe.has_permission("CAD Model", "create")),
		"can_approve": bool(frappe.has_permission("CAD Design Revision", "write")),
		"can_manage_settings": bool(frappe.has_permission("CAD Settings", "write")),
		"model_types": frappe.get_meta("CAD Model").get_field("model_type").options.split("\n"),
		"surface_types": frappe.get_meta("CAD Surface").get_field("surface_type").options.split("\n"),
		"linear_units": frappe.get_meta("CAD Model").get_field("linear_units").options.split("\n"),
		"user": frappe.session.user,
	}


@frappe.whitelist()
def list_projects():
	return frappe.get_list("Geo Project", fields=["name", "project_name", "project_code", "crs_name"], order_by="project_name asc", limit_page_length=0)


@frappe.whitelist()
def list_models(geo_project=None, include_archived=0):
	filters = {}
	if geo_project:
		filters["geo_project"] = geo_project
	if not cint(include_archived):
		filters["model_status"] = ["!=", "Archived"]
	return frappe.get_list(
		"CAD Model",
		filters=filters,
		fields=["name", "model_name", "model_type", "geo_project", "model_status", "approval_status", "entity_count", "modified"],
		order_by="modified desc",
		limit_page_length=500,
	)


@frappe.whitelist(methods=["POST"])
def create_model(values):
	check_enabled()
	doc = cad_model_service.create_model(values)
	return get_model(doc.name)


@frappe.whitelist()
def get_model(model):
	doc = frappe.get_doc("CAD Model", model)
	doc.check_permission("read")
	from is_production.cad_engineering.services.cad_exchange_service import UNITS_TO_METRES

	settings = frappe.get_cached_doc("CAD Settings")
	scale = cint(doc.annotation_scale) or cint(settings.default_annotation_scale) or 1000
	text_height = (flt(settings.default_text_height_mm) or 2.5) * scale / 1000 / UNITS_TO_METRES.get(doc.linear_units or "Metres", 1.0)
	return {
		"model": {f: doc.get(f) for f in MODEL_FIELDS},
		"layers": cad_model_service.get_layers(model),
		"blocks": cad_model_service.get_blocks(model),
		"references": [
			{f: r.get(f) for f in ("reference_model", "reference_revision", "offset_x", "offset_y", "offset_z", "visible")}
			for r in doc.references
			if frappe.has_permission("CAD Model", "read", doc=r.reference_model)
		],
		"plan_rotated": cad_model_service.plan_rotated(doc),
		"text_height": text_height,
		"revisions": revision_history(model),
		"can_edit": bool(doc.has_permission("write")) and doc.model_status != "Archived",
		"can_approve": bool(frappe.has_permission("CAD Design Revision", "write")),
		"in_review": next((r["name"] for r in revision_history(model) if r["revision_status"] == "In Review"), None),
	}


@frappe.whitelist()
def get_entities(model, bbox=None, start=0, limit=5000):
	frappe.get_doc("CAD Model", model).check_permission("read")
	bbox = frappe.parse_json(bbox) if isinstance(bbox, str) else bbox
	return cad_model_service.get_entities(model, bbox, cint(start), min(max(cint(limit), 1), 20000))


@frappe.whitelist(methods=["POST"])
def save_edits(model, edit_version, changes):
	check_enabled()
	return cad_model_service.save_edits(model, edit_version, changes)


@frappe.whitelist(methods=["POST"])
def build_solids(model, items):
	check_enabled()
	items = frappe.parse_json(items) if isinstance(items, str) else items
	return cad_job_service.start_job("Build Geometry", model, cad_revision_service.working_revision(model), {"items": items})


@frappe.whitelist(methods=["POST"])
def validate_model(model):
	return cad_job_service.start_job("Validate Model", model, cad_revision_service.working_revision(model), {})


@frappe.whitelist()
def get_job(name):
	return cad_job_service.get_job(name)


@frappe.whitelist(methods=["POST"])
def cancel_job(name):
	return cad_job_service.cancel(name)


@frappe.whitelist(methods=["POST"])
def retry_job(name):
	return cad_job_service.retry(name)


@frappe.whitelist()
def job_history(model=None, limit=30):
	return cad_job_service.history(model, limit)


@frappe.whitelist(methods=["GET"])
def get_mesh(model, geometry_ref):
	"""The cached display mesh of a solid (binary, see geometry_worker.tessellate). 404 → ask for Tessellate."""
	frappe.get_doc("CAD Model", model).check_permission("read")
	if not frappe.db.exists("CAD Entity", {"cad_model": model, "geometry_ref": geometry_ref}) and not frappe.db.exists(
		"CAD Processing Job", {"cad_model": model, "job_type": "Build Geometry", "results": ["like", f"%{geometry_ref}%"]}
	):
		raise frappe.PermissionError
	key = storage.mesh_key(model, geometry_ref)
	store = storage.provider()
	if not store.exists(key):
		return Response(b"", status=404)
	response = Response(store.read(key), mimetype="application/octet-stream")
	response.headers["Cache-Control"] = "private, max-age=31536000, immutable"  # content-addressed
	return response


@frappe.whitelist(methods=["POST"])
def request_meshes(model, geometry_refs):
	refs = frappe.parse_json(geometry_refs) if isinstance(geometry_refs, str) else geometry_refs
	return cad_job_service.start_job("Tessellate", model, None, {"geometry_refs": refs[:500]})


# ---------------------------------------------------------------------------
# revisions
# ---------------------------------------------------------------------------


@frappe.whitelist()
def revision_history(model):
	frappe.get_doc("CAD Model", model).check_permission("read")
	return frappe.get_all(
		"CAD Design Revision",
		filters={"cad_model": model},
		fields=[
			"name",
			"revision_number",
			"revision_label",
			"parent_revision",
			"revision_status",
			"revision_description",
			"geometry_checksum",
			"entity_count",
			"submitted_by",
			"submitted_on",
			"approved_by",
			"approval_date",
			"approval_notes",
			"creation",
			"owner",
		],
		order_by="revision_number desc",
	)


@frappe.whitelist(methods=["POST"])
def submit_revision(model, description=None):
	return cad_revision_service.submit_for_review(model, description).name


@frappe.whitelist(methods=["POST"])
def approve_revision(revision, notes=None):
	return cad_revision_service.approve(revision, notes).name


@frappe.whitelist(methods=["POST"])
def reject_revision(revision, notes=None):
	return cad_revision_service.reject(revision, notes).name


@frappe.whitelist(methods=["POST"])
def restore_revision(revision):
	check_enabled()
	return cad_revision_service.restore(revision)


# ---------------------------------------------------------------------------
# curve operations, references, exchange
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def offset_curves(model, items):
	check_enabled()
	items = frappe.parse_json(items) if isinstance(items, str) else items
	return cad_job_service.start_job("Curve Operation", model, cad_revision_service.working_revision(model), {"operation": "offset", "items": items})


@frappe.whitelist()
def get_reference_content(model, reference_model, revision=None):
	return cad_model_service.reference_content(model, reference_model, revision or None)


@frappe.whitelist()
def list_revisions_of(model):
	"""Frozen revisions of another model, for pinning an external reference."""
	frappe.get_doc("CAD Model", model).check_permission("read")
	return frappe.get_all("CAD Design Revision", filters={"cad_model": model, "geometry_snapshot": ["is", "set"]}, fields=["name", "revision_label", "revision_status"], order_by="revision_number desc")


@frappe.whitelist(methods=["POST"])
def start_import(model, file_url):
	check_enabled()
	from is_production.cad_engineering.services import cad_exchange_service

	return cad_exchange_service.start_import(model, file_url)


@frappe.whitelist(methods=["POST"])
def commit_import(name, options):
	check_enabled()
	from is_production.cad_engineering.services import cad_exchange_service

	return cad_exchange_service.commit_import(name, options)


@frappe.whitelist(methods=["POST"])
def start_export(model, options=None):
	check_enabled()
	from is_production.cad_engineering.services import cad_exchange_service

	return cad_exchange_service.start_export(model, options)


@frappe.whitelist()
def get_exchange(name):
	from is_production.cad_engineering.services import cad_exchange_service

	return cad_exchange_service.get_exchange(name)


@frappe.whitelist()
def list_exchanges(model, limit=20):
	from is_production.cad_engineering.services import cad_exchange_service

	return cad_exchange_service.list_exchanges(model, limit)


# ---------------------------------------------------------------------------
# surfaces and analyses (Phase 3)
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def surface_job(model, operation, params=None):
	check_enabled()
	from is_production.cad_engineering.services import cad_surface_service

	return cad_surface_service.start_surface(model, operation, params)


@frappe.whitelist(methods=["POST"])
def analysis_job(model, analysis, params=None):
	from is_production.cad_engineering.services import cad_surface_service

	return cad_surface_service.start_analysis(model, analysis, params)


def check_surface_ref(model, geometry_ref):
	"""Only surfaces of this model (saved, or just made by one of its jobs) are served."""
	frappe.get_doc("CAD Model", model).check_permission("read")
	if not storage.SHA256.match(geometry_ref or ""):
		raise frappe.PermissionError
	if frappe.db.exists("CAD Entity", {"cad_model": model, "geometry_ref": geometry_ref}):
		return
	if frappe.db.exists("CAD Processing Job", {"cad_model": model, "job_type": ["in", ["Surface", "Import", "Mining Design"]], "results": ["like", f"%{geometry_ref}%"]}):
		return
	raise frappe.PermissionError


@frappe.whitelist(methods=["GET"])
def get_surface_index(model, geometry_ref):
	"""The display tile index of a surface (JSON), or 404 while the tiles are not made."""
	from is_production.cad_engineering.services.cad_surface_service import sidecar, tile_key

	check_surface_ref(model, geometry_ref)
	store = storage.provider()
	key = tile_key(model, geometry_ref, "index.json")
	if not store.exists(key):
		return Response(b"", status=404)
	index = json.loads(store.read(key))
	meta = sidecar(model, geometry_ref) or {}
	index["stats"] = meta.get("stats")
	response = Response(json.dumps(index), mimetype="application/json")
	response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
	return response


@frappe.whitelist(methods=["GET"])
def get_surface_tile(model, geometry_ref, file):
	from is_production.cad_engineering.services.cad_surface_service import tile_key

	check_surface_ref(model, geometry_ref)
	store = storage.provider()
	key = tile_key(model, geometry_ref, file)
	if not store.exists(key):
		return Response(b"", status=404)
	response = Response(store.read(key), mimetype="application/octet-stream")
	response.headers["Cache-Control"] = "private, max-age=31536000, immutable"  # content-addressed
	return response


@frappe.whitelist()
def list_volume_calculations(model, limit=50):
	frappe.get_doc("CAD Model", model).check_permission("read")
	return frappe.get_all(
		"CAD Volume Calculation",
		filters={"cad_model": model},
		fields=["name", "title", "calculation_type", "cut_volume", "fill_volume", "net_volume", "plan_area", "units", "valid", "creation", "owner"],
		order_by="creation desc",
		limit_page_length=min(cint(limit) or 50, 200),
	)


@frappe.whitelist()
def get_volume_calculation(name):
	doc = frappe.get_doc("CAD Volume Calculation", name)
	doc.check_permission("read")
	return doc.as_dict()


@frappe.whitelist()
def list_gis_datasets():
	"""GIS datasets with elevations, usable as surface sources (only those the user may read)."""
	if not frappe.has_permission("GIS Dataset", "read"):
		return []
	return frappe.get_list(
		"GIS Dataset",
		filters={"has_z": 1, "status": ["!=", "Archived"]},
		fields=["name", "title", "dataset_name", "geometry_type", "feature_count", "crs"],
		order_by="modified desc",
		limit_page_length=500,
	)


# ---------------------------------------------------------------------------
# mining design (Phase 4)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_design_standards():
	from is_production.cad_engineering.services import cad_mining_service

	return cad_mining_service.list_standards()


@frappe.whitelist()
def get_design_standard(model):
	from is_production.cad_engineering.services import cad_mining_service

	frappe.get_doc("CAD Model", model).check_permission("read")
	return cad_mining_service.model_standard(model)


@frappe.whitelist(methods=["POST"])
def set_design_standard(model, standard=None):
	from is_production.cad_engineering.services import cad_mining_service

	return cad_mining_service.set_standard(model, standard)


@frappe.whitelist(methods=["POST"])
def mining_job(model, operation, params=None):
	check_enabled()
	from is_production.cad_engineering.services import cad_mining_service

	return cad_mining_service.start(model, operation, params)


@frappe.whitelist(methods=["POST"])
def validate_design(model):
	from is_production.cad_engineering.services import cad_mining_service

	return cad_mining_service.validate(model)


@frappe.whitelist()
def get_design_validation(name):
	doc = frappe.get_doc("CAD Design Validation", name)
	doc.check_permission("read")
	return {**doc.as_dict(), "results": frappe.parse_json(doc.results), "standard_snapshot": frappe.parse_json(doc.standard_snapshot)}


@frappe.whitelist()
def list_material_types():
	if not frappe.has_permission("Material Type", "read"):
		return []
	return frappe.get_list("Material Type", pluck="name", order_by="name asc", limit_page_length=500)


# ---------------------------------------------------------------------------
# mine planning integration (Phase 5)
# ---------------------------------------------------------------------------


@frappe.whitelist()
def publish_preview(model, revision):
	from is_production.cad_engineering.services import cad_planning_service

	return cad_planning_service.publish_preview(model, revision)


@frappe.whitelist(methods=["POST"])
def publish_design(model, revision, geo_project=None, materials=None, make_selection=1):
	from is_production.cad_engineering.services import cad_planning_service

	return cad_planning_service.publish(model, revision, geo_project, materials, bool(cint(make_selection)))


@frappe.whitelist()
def list_publications(model):
	frappe.get_doc("CAD Model", model).check_permission("read")
	return frappe.get_list(
		"CAD Design Publication",
		filters={"cad_model": model},
		fields=["name", "revision", "revision_label", "status", "published_on", "blocks_published", "total_volume", "total_tonnes", "geo_pit_layout", "schedule_selection", "validation_status"],
		order_by="creation desc",
		limit_page_length=50,
	)


@frappe.whitelist(methods=["POST"])
def selection_from_sequence(model, block_entity_ids=None):
	from is_production.cad_engineering.services import cad_planning_service

	return cad_planning_service.selection_from_sequence(model, block_entity_ids)


@frappe.whitelist()
def planning_overlay(model, scenario=None):
	from is_production.cad_engineering.services import cad_planning_service

	return cad_planning_service.overlay(model, scenario or None)


@frappe.whitelist()
def compare_revisions(model, older, newer=None):
	from is_production.cad_engineering.services import cad_planning_service

	return cad_planning_service.compare(model, older, newer or None)


@frappe.whitelist()
def get_revision_content(model, revision):
	from is_production.cad_engineering.services import cad_planning_service

	return cad_planning_service.revision_content(model, revision)


@frappe.whitelist()
def list_reconciliations(model):
	frappe.get_doc("CAD Model", model).check_permission("read")
	return frappe.get_list(
		"CAD Survey Reconciliation",
		filters={"cad_model": model},
		fields=["name", "survey_date", "survey_name", "planned_volume", "mined_volume", "remaining_volume", "over_mined_volume", "scheduled_volume", "blocks_complete", "blocks_in_progress", "valid"],
		order_by="survey_date desc, creation desc",
		limit_page_length=50,
	)
