"""Controlled revisions of CAD Models.

A model has at most one **Working** revision; its live state is the model's CAD Layer and CAD Entity
records. Submitting it for review freezes a snapshot — layers, entities with their exact geometry, the
SHA-256 of every geometry file and the coordinate/unit metadata — as a write-once gzip JSON file, with a
SHA-256 checksum over the canonical content (never over display meshes). From then on that revision
never changes: it is approved or rejected by a Production Manager, and the next edit opens a new Working
revision whose parent it is. Restoring an old revision copies its snapshot back into the Working state.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from is_production.cad_engineering.services import cad_storage_service as storage

REVISION = "CAD Design Revision"
MODEL = "CAD Model"
FROZEN = {"In Review", "Approved", "Rejected", "Superseded"}
SNAPSHOT_FORMAT = "isp-cad-snapshot"
SNAPSHOT_VERSION = 2  # 2: blocks and external references (version 1 snapshots have neither)
LAYER_FIELDS = ("layer_name", "layer_code", "is_group", "color", "line_type", "line_weight", "visible", "locked", "printable", "display_order", "description")
ENTITY_FIELDS = ("entity_id", "entity_type", "color", "line_type", "source_id", "geometry", "geometry_ref", "geometry_format", "metadata")
MODEL_FIELDS = ("model_name", "model_type", "crs", "source_crs", "vertical_datum", "linear_units", "angular_units", "local_origin_x", "local_origin_y", "local_origin_z", "plan_orientation", "annotation_scale")


def label(number):
	return f"R{cint(number):03d}"


def latest_revision(model):
	rows = frappe.get_all(REVISION, filters={"cad_model": model}, fields=["name", "revision_number", "revision_status"], order_by="revision_number desc", limit=1)
	return rows[0] if rows else None


def create_revision(model, parent=None, description=None):
	latest = latest_revision(model)
	number = (latest.revision_number if latest else 0) + 1
	revision = frappe.get_doc(
		{
			"doctype": REVISION,
			"cad_model": model,
			"revision_number": number,
			"revision_label": label(number),
			"parent_revision": parent,
			"revision_status": "Working",
			"revision_description": description,
		}
	)
	revision.flags.ignore_permissions = True
	revision.insert()
	return revision


def working_revision(model):
	"""The model's Working revision, or None."""
	name = frappe.db.get_value(MODEL, model, "working_revision")
	if name and frappe.db.get_value(REVISION, name, "revision_status") == "Working":
		return name
	return None


def ensure_working_revision(model):
	"""The Working revision to record an edit in; opens one after a review/approval, refuses during review."""
	name = working_revision(model)
	if name:
		return name
	latest = latest_revision(model)
	if latest and latest.revision_status == "In Review":
		frappe.throw(
			_("{0} is in review. A Production Manager must approve or reject it before the model can be edited.").format(latest.name),
			title=_("Model Locked"),
		)
	revision = create_revision(model, parent=latest.name if latest else None)
	frappe.db.set_value(MODEL, model, "working_revision", revision.name, update_modified=False)
	return revision.name


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------


def canonical(value):
	"""Deterministic JSON: sorted keys, no spaces, floats in Python's shortest exact repr."""
	return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def current_content(model):
	"""Layers and entities of the model's live state, in a stable order."""
	model_doc = frappe.get_doc(MODEL, model)
	layers = frappe.get_all("CAD Layer", filters={"cad_model": model}, fields=["name", "parent_layer", *LAYER_FIELDS], order_by="layer_name asc")
	names = {layer.name: layer.layer_name for layer in layers}
	content_layers = []
	for layer in layers:
		row = {f: layer.get(f) for f in LAYER_FIELDS}
		row["parent_layer"] = names.get(layer.parent_layer)
		content_layers.append(row)
	entities = []
	for row in frappe.get_all("CAD Entity", filters={"cad_model": model}, fields=["cad_layer", *ENTITY_FIELDS], order_by="entity_id asc", limit_page_length=0):
		entity = {f: row.get(f) for f in ENTITY_FIELDS}
		for key in ("geometry", "metadata"):
			entity[key] = frappe.parse_json(entity[key]) if entity[key] else None
		entity["layer"] = names.get(row.cad_layer)
		entities.append(entity)
	blocks = [
		{
			"block_name": row.block_name,
			"description": row.description,
			"base_point": frappe.parse_json(row.base_point or "[0, 0, 0]"),
			"entities": frappe.parse_json(row.entities or "[]"),
		}
		for row in frappe.get_all("CAD Block", filters={"cad_model": model}, fields=["block_name", "description", "base_point", "entities"], order_by="block_name asc")
	]
	references = [
		{f: r.get(f) for f in ("reference_model", "reference_revision", "offset_x", "offset_y", "offset_z", "visible")}
		for r in model_doc.references
	]
	return {
		"model": {f: model_doc.get(f) for f in MODEL_FIELDS},
		"layers": content_layers,
		"entities": entities,
		"blocks": blocks,
		"references": references,
	}


def checksum(content):
	return hashlib.sha256(canonical(content).encode()).hexdigest()


def freeze(revision_doc):
	"""Write the revision's write-once snapshot from the model's live state."""
	content = current_content(revision_doc.cad_model)
	digest = checksum(content)
	payload = {
		"format": SNAPSHOT_FORMAT,
		"version": SNAPSHOT_VERSION,
		"model": revision_doc.cad_model,
		"revision": revision_doc.name,
		"parent_revision": revision_doc.parent_revision,
		"checksum": digest,
		"frozen_on": str(now_datetime()),
		"frozen_by": frappe.session.user,
		"content": content,
	}
	key = storage.write_snapshot(revision_doc.cad_model, revision_doc.name, canonical(payload).encode())
	return key, digest, len(content["entities"]), len(content["layers"])


def load_snapshot(revision):
	"""A frozen revision's content, after checking its checksum."""
	doc = frappe.get_doc(REVISION, revision)
	if not doc.geometry_snapshot:
		frappe.throw(_("{0} has no frozen snapshot.").format(revision))
	payload = json.loads(storage.read_snapshot(doc.geometry_snapshot))
	if payload.get("format") != SNAPSHOT_FORMAT or payload.get("revision") != doc.name:
		frappe.throw(_("The snapshot file of {0} does not belong to it.").format(revision))
	if checksum(payload["content"]) != doc.geometry_checksum:
		frappe.throw(_("The snapshot of {0} does not match its checksum; it may have been tampered with.").format(revision))
	return payload


# ---------------------------------------------------------------------------
# review workflow
# ---------------------------------------------------------------------------


def submit_for_review(model, description=None):
	model_doc = frappe.get_doc(MODEL, model)
	model_doc.check_permission("write")
	name = working_revision(model)
	if not name:
		frappe.throw(_("There is no Working revision to submit."))
	revision = frappe.get_doc(REVISION, name)
	invalid = frappe.db.count("CAD Entity", {"cad_model": model, "validation_status": "Invalid"})
	key, digest, entities, layers = freeze(revision)
	revision.flags.allow_status_change = True
	revision.update(
		{
			"revision_status": "In Review",
			"revision_description": description or revision.revision_description,
			"geometry_snapshot": key,
			"geometry_checksum": digest,
			"snapshot_on": now_datetime(),
			"entity_count": entities,
			"layer_count": layers,
			"validation_results": json.dumps({"invalid_entities": invalid}),
			"submitted_by": frappe.session.user,
			"submitted_on": now_datetime(),
		}
	)
	revision.save(ignore_permissions=True)
	frappe.db.set_value(MODEL, model, {"working_revision": None, "approval_status": "In Review"}, update_modified=False)
	return revision


def check_approver(revision):
	"""Approval is separate from editing: it needs write access to CAD Design Revision (Production Manager)."""
	if not frappe.has_permission(REVISION, "write", doc=revision):
		frappe.throw(_("Only a reviewer (Production Manager) can approve or reject revisions."), frappe.PermissionError)
	if revision.revision_status != "In Review":
		frappe.throw(_("{0} is {1}, not In Review.").format(revision.name, _(revision.revision_status)))


def approve(revision_name, notes=None):
	revision = frappe.get_doc(REVISION, revision_name)
	check_approver(revision)
	load_snapshot(revision.name)  # integrity check before it becomes the approved design
	for previous in frappe.get_all(REVISION, filters={"cad_model": revision.cad_model, "revision_status": "Approved"}, pluck="name"):
		old = frappe.get_doc(REVISION, previous)
		old.flags.allow_status_change = True
		old.revision_status = "Superseded"
		old.save(ignore_permissions=True)
	revision.flags.allow_status_change = True
	revision.update(
		{
			"revision_status": "Approved",
			"approved_by": frappe.session.user,
			"approved_by_employee": frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name"),
			"approval_date": now_datetime(),
			"approval_notes": notes,
		}
	)
	revision.save(ignore_permissions=True)
	frappe.db.set_value(MODEL, revision.cad_model, {"current_revision": revision.name, "approval_status": "Approved"}, update_modified=False)
	return revision


def reject(revision_name, notes=None):
	revision = frappe.get_doc(REVISION, revision_name)
	check_approver(revision)
	if not (notes or "").strip():
		frappe.throw(_("Say why the revision is rejected."))
	revision.flags.allow_status_change = True
	revision.update({"revision_status": "Rejected", "approval_notes": notes, "approved_by": frappe.session.user, "approval_date": now_datetime()})
	revision.save(ignore_permissions=True)
	frappe.db.set_value(MODEL, revision.cad_model, "approval_status", "Rejected", update_modified=False)
	return revision


def restore(revision_name):
	"""Copy a frozen revision back into the model's Working state (a new Working revision if needed)."""
	from is_production.cad_engineering.services import cad_model_service

	source = frappe.get_doc(REVISION, revision_name)
	model_doc = frappe.get_doc(MODEL, source.cad_model)
	model_doc.check_permission("write")
	payload = load_snapshot(source.name)
	working = ensure_working_revision(model_doc.name)
	cad_model_service.replace_content(model_doc.name, payload["content"], working)
	frappe.db.set_value(
		REVISION,
		working,
		"revision_description",
		_("Restored from {0}").format(source.name),
		update_modified=False,
	)
	return working
