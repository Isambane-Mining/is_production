"""CAD Model content: creating models, saving workstation edits, replacing content from a snapshot.

All edits go through ``save_edits`` in one savepoint-scoped transaction: the edit version is checked
(someone else saved since you loaded → refused), the edit is recorded in the model's Working revision,
locked layers are refused, and the model's counts, bounding box and local origin are refreshed. Entities
saved here are checked by the CAD worker afterwards (Validate Model job), which fills in their measures.
"""

import json
import math
import re
import time

import frappe
from frappe import _
from frappe.utils import cint, flt

from is_production.cad_engineering.services import cad_geometry, cad_revision_service
from is_production.cad_engineering.services import cad_storage_service as storage

MODEL = "CAD Model"
ENTITY = "CAD Entity"
LAYER = "CAD Layer"
BLOCK = "CAD Block"
ENTITY_ID = re.compile(r"^[A-Za-z0-9_.:\-]{1,80}$")
DEFAULT_LAYER = "0"
MAX_CHANGES = 20000
LAYER_EDIT_FIELDS = ("layer_code", "color", "line_type", "line_weight", "visible", "locked", "printable", "display_order", "is_group", "description")
BBOX_FIELDS = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")


class EditConflict(frappe.ValidationError):
	pass


# ---------------------------------------------------------------------------
# models and layers
# ---------------------------------------------------------------------------


def default_layer(model):
	name = frappe.db.get_value(LAYER, {"cad_model": model, "layer_name": DEFAULT_LAYER})
	if name:
		return name
	layer = frappe.get_doc({"doctype": LAYER, "cad_model": model, "layer_name": DEFAULT_LAYER, "color": "#e0e0e0", "display_order": 0})
	layer.flags.ignore_permissions = True
	layer.flags.cad_default_layer = True
	layer.insert()
	return layer.name


def layer_names(model):
	"""{CAD Layer name: row} for the model."""
	return {
		row.name: row
		for row in frappe.get_all(LAYER, filters={"cad_model": model}, fields=["name", "layer_name", "locked", "parent_layer", "is_group"])
	}


def resolve_layer(model, value, layers):
	"""A layer given by its record name or its layer name."""
	if not value:
		return default_layer(model)
	if value in layers:
		return value
	for name, row in layers.items():
		if row.layer_name == value:
			return name
	frappe.throw(_("Layer {0} does not belong to model {1}.").format(value, model))


def check_unlocked(layers, *names):
	for name in names:
		if name and layers.get(name) and layers[name].locked:
			frappe.throw(_("Layer {0} is locked.").format(layers[name].layer_name), title=_("Locked Layer"))


# ---------------------------------------------------------------------------
# saving edits
# ---------------------------------------------------------------------------


def save_edits(model, edit_version, changes):
	"""Apply a batch of workstation edits atomically. Returns what the client must refresh."""
	doc = frappe.get_doc(MODEL, model)
	doc.check_permission("write")
	if doc.model_status == "Archived":
		frappe.throw(_("{0} is archived and cannot be edited.").format(model))
	changes = frappe.parse_json(changes) if isinstance(changes, str) else changes or {}
	upserts, deletes = changes.get("upserts") or [], changes.get("deletes") or []
	layer_changes, deleted_layers = changes.get("layers") or [], changes.get("deleted_layers") or []
	block_changes, deleted_blocks = changes.get("blocks") or [], changes.get("deleted_blocks") or []
	if len(upserts) + len(deletes) + len(layer_changes) + len(block_changes) > MAX_CHANGES:
		frappe.throw(_("Save at most {0} changes at a time.").format(MAX_CHANGES))
	if cint(doc.edit_version) != cint(edit_version):
		frappe.throw(
			_("{0} was saved by {1} after you opened it. Reload the model and redo your change.").format(model, doc.modified_by),
			EditConflict,
			title=_("Edit Conflict"),
		)

	for attempt in range(3):
		frappe.db.savepoint("cad_save_edits")
		frappe.flags.cad_batch = True
		try:
			working = cad_revision_service.ensure_working_revision(model)
			apply_layer_changes(model, layer_changes, working)
			layers = layer_names(model)
			apply_blocks_in_order(model, block_changes)
			changed_blocks = [b["block_name"] for b in block_changes]
			removed = delete_entities(model, deletes, layers)
			saved = [upsert_entity(model, row, layers, working) for row in upserts]
			delete_blocks(model, deleted_blocks)
			delete_layers(model, deleted_layers, layers)
			if "references" in changes:
				set_references(model, changes.get("references") or [])
			refresh_block_references(model, changed_blocks)
			stats = refresh_model(model)
			sync_surfaces(model)
			break
		except Exception as error:
			# a deadlock (e.g. with a validation job writing the same rows) ends the whole transaction, savepoint
			# included: start again from a clean transaction (this request has written nothing else)
			retry = (frappe.db.is_deadlocked(error) or frappe.db.is_timedout(error)) and attempt < 2
			try:
				frappe.db.rollback(save_point="cad_save_edits")
			except Exception:
				frappe.db.rollback()
				if not retry:
					raise error from None
			if not retry:
				raise
			frappe.db.rollback()
			time.sleep(0.2 * (attempt + 1))
		finally:
			frappe.flags.cad_batch = False

	changed = [row["entity_id"] for row in saved if row["changed"] and row["entity_type"] not in cad_geometry.BLOB_TYPES]
	job = None
	if changed and frappe.db.get_single_value("CAD Settings", "cad_worker_enabled"):
		from is_production.cad_engineering.services import cad_job_service

		job = cad_job_service.start_job("Validate Model", model, working, {"entity_ids": changed}, check_permission=False)["name"]
	return {
		"edit_version": stats["edit_version"],
		"working_revision": working,
		"entities": [{k: v for k, v in row.items() if k != "changed"} for row in saved],
		"deleted": removed,
		"layers": get_layers(model),
		"blocks": get_blocks(model) if block_changes or deleted_blocks else None,
		"model": stats,
		"validation_job": job,
	}


def apply_layer_changes(model, rows, working):
	for row in rows:
		name = row.get("name")
		layer = frappe.get_doc(LAYER, name) if name and frappe.db.exists(LAYER, {"name": name, "cad_model": model}) else None
		if layer is None:
			existing = frappe.db.get_value(LAYER, {"cad_model": model, "layer_name": row.get("layer_name")})
			layer = frappe.get_doc(LAYER, existing) if existing else frappe.new_doc(LAYER)
			layer.cad_model = model
		if row.get("layer_name"):
			layer.layer_name = row["layer_name"]
		for field in LAYER_EDIT_FIELDS:
			if field in row:
				layer.set(field, row[field])
		if "parent_layer" in row:
			parent = row["parent_layer"]
			layer.parent_layer = resolve_layer(model, parent, layer_names(model)) if parent else None
		layer.flags.ignore_permissions = True
		layer.save()


def delete_layers(model, names, layers):
	for value in names:
		name = resolve_layer(model, value, layers)
		if layers[name].layer_name == DEFAULT_LAYER:
			frappe.throw(_("Layer 0 cannot be deleted."))
		if frappe.db.exists(ENTITY, {"cad_model": model, "cad_layer": name}):
			frappe.throw(_("Layer {0} still has entities.").format(layers[name].layer_name))
		if frappe.db.exists(LAYER, {"cad_model": model, "parent_layer": name}):
			frappe.throw(_("Layer {0} still has sub-layers.").format(layers[name].layer_name))
		frappe.delete_doc(LAYER, name, ignore_permissions=True)


def delete_entities(model, entity_ids, layers):
	removed = []
	for entity_id in entity_ids:
		row = frappe.db.get_value(ENTITY, {"cad_model": model, "entity_id": entity_id}, ["name", "cad_layer"], as_dict=True)
		if not row:
			continue
		check_unlocked(layers, row.cad_layer)
		frappe.db.delete(ENTITY, {"name": row.name})
		removed.append(entity_id)
	return removed


def upsert_entity(model, row, layers, working):
	entity_id = str(row.get("entity_id") or "")
	if not ENTITY_ID.match(entity_id):
		frappe.throw(_("Invalid entity identifier {0}.").format(entity_id))
	existing = frappe.db.get_value(ENTITY, {"cad_model": model, "entity_id": entity_id})
	doc = frappe.get_doc(ENTITY, existing) if existing else frappe.new_doc(ENTITY)
	layer = resolve_layer(model, row.get("layer") or row.get("cad_layer"), layers)
	check_unlocked(layers, doc.cad_layer if existing else None, layer)

	before = signature(doc) if existing else None
	doc.update(
		{
			"cad_model": model,
			"entity_id": entity_id,
			"entity_type": row.get("entity_type") or doc.entity_type,
			"cad_layer": layer,
			"color": row.get("color") or None,
			"line_type": row.get("line_type") or None,
			"line_weight": flt(row.get("line_weight")) if row.get("line_weight") else 0,
			"source_id": row.get("source_id") or doc.source_id,
			"metadata": json.dumps(row["metadata"]) if isinstance(row.get("metadata"), dict) else row.get("metadata") or doc.metadata,
		}
	)
	if doc.entity_type in cad_geometry.BLOB_TYPES:
		doc.geometry_ref = row.get("geometry_ref") or doc.geometry_ref
	doc.flags.block_resolver = block_resolver(model)
	doc.geometry = json.dumps(row.get("geometry")) if isinstance(row.get("geometry"), dict) else row.get("geometry") or doc.geometry
	doc.flags.ignore_permissions = True
	doc.normalise_geometry()
	changed = before is None or signature(doc) != before
	if changed:
		doc.revision = working
		doc.entity_version = cint(doc.entity_version) + 1
		doc.save()
	return {
		"entity_id": entity_id,
		"entity_type": doc.entity_type,
		"name": doc.name,
		"entity_version": cint(doc.entity_version),
		"validation_status": doc.validation_status,
		"bbox": [doc.get(f) for f in BBOX_FIELDS],
		"changed": changed,
	}


def signature(doc):
	return json.dumps(
		[doc.entity_type, doc.cad_layer, doc.color or None, doc.line_type or None, flt(doc.line_weight), doc.geometry_ref or None, frappe.parse_json(doc.geometry or "null"), frappe.parse_json(doc.metadata or "null")],
		sort_keys=True,
	)


# ---------------------------------------------------------------------------
# model statistics
# ---------------------------------------------------------------------------


def refresh_model(model, bump=True):
	"""Counts, bounding box and (first time) local origin; raises the edit version."""
	box = frappe.db.sql(
		"""select count(*), min(min_x), min(min_y), min(min_z), max(max_x), max(max_y), max(max_z)
		from `tabCAD Entity` where cad_model = %s""",
		model,
	)[0]
	values = {
		"entity_count": box[0],
		"layer_count": frappe.db.count(LAYER, {"cad_model": model}),
		**{field: flt(value) for field, value in zip(BBOX_FIELDS, box[1:])},  # Float columns are NOT NULL: empty = 0
	}
	origin = frappe.db.get_value(MODEL, model, ["local_origin_x", "local_origin_y", "local_origin_z", "edit_version"], as_dict=True)
	if box[0] and not any(flt(origin.get(f)) for f in ("local_origin_x", "local_origin_y", "local_origin_z")):
		values.update(suggest_origin(box[1:]))
	if bump:
		values["edit_version"] = cint(origin.edit_version) + 1
	frappe.db.set_value(MODEL, model, values)
	return {**values, "edit_version": values.get("edit_version", cint(origin.edit_version))}


def suggest_origin(box):
	"""The bounding-box centre rounded to 100 units (10 vertically): stable, and small offsets on screen."""
	cx, cy, cz = ((box[i] + box[i + 3]) / 2 for i in range(3))
	return {
		"local_origin_x": math.floor(cx / 100 + 0.5) * 100.0,
		"local_origin_y": math.floor(cy / 100 + 0.5) * 100.0,
		"local_origin_z": math.floor(cz / 10 + 0.5) * 10.0,
	}


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------

ENTITY_FIELDS = [
	"name",
	"entity_id",
	"entity_type",
	"cad_layer",
	"color",
	"line_type",
	"line_weight",
	"geometry",
	"geometry_ref",
	"geometry_format",
	"metadata",
	"source_id",
	"validation_status",
	"validation_message",
	"entity_version",
	"revision",
	"length",
	"area",
	"volume",
	"block_name",
	*BBOX_FIELDS,
]


def get_layers(model):
	return frappe.get_all(
		LAYER,
		filters={"cad_model": model},
		fields=["name", "layer_name", "layer_code", "parent_layer", "is_group", "color", "line_type", "line_weight", "visible", "locked", "printable", "display_order", "description"],
		order_by="display_order asc, layer_name asc",
	)


def get_entities(model, bbox=None, start=0, limit=5000):
	"""Entities whose bounding box overlaps ``bbox`` ([x0, y0, x1, y1] in model coordinates), paged."""
	filters = [["cad_model", "=", model]]
	if bbox:
		x0, y0, x1, y1 = (float(v) for v in bbox)
		filters += [["max_x", ">=", x0], ["min_x", "<=", x1], ["max_y", ">=", y0], ["min_y", "<=", y1]]
	rows = frappe.get_all(ENTITY, filters=filters, fields=ENTITY_FIELDS, order_by="entity_id asc", start=cint(start), limit_page_length=cint(limit) + 1)
	more = len(rows) > cint(limit)
	rows = rows[: cint(limit)]
	for row in rows:
		row["geometry"] = frappe.parse_json(row["geometry"]) if row["geometry"] else None
		row["metadata"] = frappe.parse_json(row["metadata"]) if row["metadata"] else None
	return {"entities": rows, "more": more, "next_start": cint(start) + len(rows) if more else None}


# ---------------------------------------------------------------------------
# replacing content (restore a revision)
# ---------------------------------------------------------------------------


def replace_content(model, content, working):
	"""Make the model's live state equal to a snapshot's content (layers, entities, model metadata)."""
	for key, value in (content.get("model") or {}).items():
		if key in cad_revision_service.MODEL_FIELDS and key not in ("model_name", "model_type"):
			frappe.db.set_value(MODEL, model, key, value, update_modified=False)
	frappe.flags.cad_batch = True
	try:
		frappe.db.delete(ENTITY, {"cad_model": model})
		frappe.db.delete(LAYER, {"cad_model": model})
		names = {}
		pending = list(content.get("layers") or [])
		# parents first: keep inserting the layers whose parent already exists
		while pending:
			progress = False
			for row in list(pending):
				if row.get("parent_layer") and row["parent_layer"] not in names:
					continue
				layer = frappe.get_doc({"doctype": LAYER, "cad_model": model, **{f: row.get(f) for f in cad_revision_service.LAYER_FIELDS}})
				layer.parent_layer = names.get(row.get("parent_layer"))
				layer.flags.ignore_permissions = True
				layer.insert()
				names[row["layer_name"]] = layer.name
				pending.remove(row)
				progress = True
			if not progress:
				frappe.throw(_("The snapshot's layer tree has a cycle."))
		if DEFAULT_LAYER not in names:
			names[DEFAULT_LAYER] = default_layer(model)
		frappe.db.delete(BLOCK, {"cad_model": model})
		apply_blocks_in_order(model, content.get("blocks") or [])
		if "references" in content:
			set_references(model, content.get("references") or [])
		for row in content.get("entities") or []:
			entity = frappe.get_doc(
				{
					"doctype": ENTITY,
					"cad_model": model,
					"cad_layer": names.get(row.get("layer")) or names[DEFAULT_LAYER],
					"revision": working,
					"entity_version": 1,
					**{f: row.get(f) for f in cad_revision_service.ENTITY_FIELDS},
				}
			)
			for key in ("geometry", "metadata"):
				if isinstance(entity.get(key), (dict, list)):
					entity.set(key, json.dumps(entity.get(key)))
			entity.flags.ignore_permissions = True
			entity.flags.block_resolver = block_resolver(model)
			entity.insert()
	finally:
		frappe.flags.cad_batch = False
	stats = refresh_model(model)
	sync_surfaces(model)
	return stats


def sync_surfaces(model):
	from is_production.cad_engineering.services.cad_surface_service import sync_surfaces as sync

	sync(model)


def create_model(values):
	values = frappe.parse_json(values) if isinstance(values, str) else values or {}
	allowed = ("model_name", "model_code", "model_type", "geo_project", "company", "branch", "location", "crs", "source_crs", "vertical_datum", "linear_units", "angular_units", "description", "plan_orientation", "annotation_scale", "drawing_template", "design_standard")
	doc = frappe.get_doc({"doctype": MODEL, **{k: values.get(k) for k in allowed if values.get(k) not in (None, "")}})
	doc.insert()
	return doc


def blob_meta(model, digest):
	"""The trusted facts the server recorded when the worker built a geometry file."""
	key = storage.blob_key(model, digest, "json")
	if not storage.provider().exists(key):
		return None
	return json.loads(storage.provider().read(key))


# ---------------------------------------------------------------------------
# blocks
# ---------------------------------------------------------------------------


def block_resolver(model):
	"""(exists(name), extent(name) → (local bbox, base point)) for a model's blocks, read once."""
	rows = {
		row.block_name: row
		for row in frappe.get_all(BLOCK, filters={"cad_model": model}, fields=["block_name", "base_point", "min_x", "min_y", "min_z", "max_x", "max_y", "max_z", "entity_count"])
	}

	def exists(name):
		return name in rows

	def extent(name):
		row = rows.get(name)
		if not row or not row.entity_count:
			return None
		return [row.min_x, row.min_y, row.min_z, row.max_x, row.max_y, row.max_z], frappe.parse_json(row.base_point or "[0, 0, 0]")

	return exists, extent


def get_blocks(model):
	rows = frappe.get_all(BLOCK, filters={"cad_model": model}, fields=["name", "block_name", "description", "base_point", "entities", "entity_count", "min_x", "min_y", "min_z", "max_x", "max_y", "max_z"], order_by="block_name asc")
	for row in rows:
		row["base_point"] = frappe.parse_json(row.base_point or "[0, 0, 0]")
		row["entities"] = frappe.parse_json(row.entities or "[]")
	return rows


def apply_block_changes(model, rows):
	"""Create or redefine blocks; returns the names whose definition changed."""
	changed = []
	for row in rows:
		name = str(row.get("block_name") or "").strip()
		existing = frappe.db.get_value(BLOCK, {"cad_model": model, "block_name": name})
		block = frappe.get_doc(BLOCK, existing) if existing else frappe.new_doc(BLOCK)
		block.update(
			{
				"cad_model": model,
				"block_name": name,
				"description": row.get("description") or block.description,
				"base_point": json.dumps(row.get("base_point") or [0, 0, 0]),
				"entities": json.dumps(row.get("entities") or []),
				"source_id": row.get("source_id") or block.source_id,
			}
		)
		block.flags.ignore_permissions = True
		block.save()
		changed.append(name)
	return changed


def apply_blocks_in_order(model, blocks):
	"""Create/redefine blocks so that nested blocks exist before the blocks that use them."""
	todo = list(blocks)
	done = set(frappe.get_all(BLOCK, filters={"cad_model": model}, pluck="block_name")) - {b["block_name"] for b in todo}
	while todo:
		ready = [b for b in todo if all((e.get("geometry") or {}).get("block") in done for e in b.get("entities") or [] if e.get("entity_type") == "Block Reference")]
		if not ready:
			frappe.throw(_("These blocks use each other in a loop: {0}.").format(", ".join(b["block_name"] for b in todo)))
		apply_block_changes(model, ready)
		for b in ready:
			done.add(b["block_name"])
			todo.remove(b)


def delete_blocks(model, names):
	for name in names:
		record = frappe.db.get_value(BLOCK, {"cad_model": model, "block_name": name})
		if record:
			frappe.delete_doc(BLOCK, record, ignore_permissions=True)


def refresh_block_references(model, names):
	"""A redefined block moves the bounding boxes of every reference to it (and of blocks nesting it)."""
	if not names:
		return
	affected = set(names)
	growing = True
	while growing:  # blocks nesting an affected block get a new extent too
		growing = False
		for row in frappe.get_all(BLOCK, filters={"cad_model": model}, fields=["name", "block_name", "entities"]):
			if row.block_name in affected:
				continue
			if any(e.get("entity_type") == "Block Reference" and e["geometry"].get("block") in affected for e in frappe.parse_json(row.entities or "[]")):
				block = frappe.get_doc(BLOCK, row.name)
				block.flags.ignore_permissions = True
				block.save()
				affected.add(row.block_name)
				growing = True
	exists, extent = block_resolver(model)
	for row in frappe.get_all(ENTITY, filters={"cad_model": model, "block_name": ["in", list(affected)]}, fields=["name", "geometry"]):
		box = cad_geometry.extent("Block Reference", frappe.parse_json(row.geometry), extent)
		frappe.db.set_value(ENTITY, row.name, dict(zip(BBOX_FIELDS, box)), update_modified=False)


# ---------------------------------------------------------------------------
# external references
# ---------------------------------------------------------------------------


def set_references(model, rows):
	doc = frappe.get_doc(MODEL, model)
	clean = []
	for row in rows:
		target = row.get("reference_model")
		if not target or target == model:
			frappe.throw(_("A model cannot reference itself."))
		if not frappe.has_permission(MODEL, "read", doc=target):
			frappe.throw(_("You may not read {0}.").format(target), frappe.PermissionError)
		revision = row.get("reference_revision") or None
		if revision and frappe.db.get_value("CAD Design Revision", revision, "cad_model") != target:
			frappe.throw(_("Revision {0} does not belong to {1}.").format(revision, target))
		clean.append({"reference_model": target, "reference_revision": revision, "offset_x": flt(row.get("offset_x")), "offset_y": flt(row.get("offset_y")), "offset_z": flt(row.get("offset_z")), "visible": cint(row.get("visible", 1))})
	doc.set("references", clean)
	doc.flags.ignore_permissions = True
	doc.save()


def reference_content(model, reference_model, revision=None):
	"""Entities, layers and blocks of a referenced model (live, or a frozen revision), read-only."""
	frappe.get_doc(MODEL, model).check_permission("read")
	allowed = {r.reference_model for r in frappe.get_doc(MODEL, model).references}
	if reference_model not in allowed:
		frappe.throw(_("{0} is not referenced by {1}.").format(reference_model, model))
	frappe.get_doc(MODEL, reference_model).check_permission("read")
	if revision:
		payload = cad_revision_service.load_snapshot(revision)
		content = payload["content"]
		return {"entities": content["entities"], "layers": content["layers"], "blocks": content.get("blocks") or []}
	layers = get_layers(reference_model)
	names = {layer.name: layer.layer_name for layer in layers}
	entities = get_entities(reference_model, None, 0, 200000)["entities"]
	for e in entities:
		e["layer"] = names.get(e.cad_layer)
	return {"entities": entities, "layers": layers, "blocks": get_blocks(reference_model)}


# ---------------------------------------------------------------------------
# bulk insert (imports) and templates
# ---------------------------------------------------------------------------

BULK_FIELDS = [
	"name",
	"creation",
	"modified",
	"owner",
	"modified_by",
	"docstatus",
	"idx",
	"cad_model",
	"entity_id",
	"entity_type",
	"cad_layer",
	"validation_status",
	"revision",
	"entity_version",
	"color",
	"line_type",
	"line_weight",
	"source_id",
	"geometry",
	"metadata",
	"block_name",
	*BBOX_FIELDS,
	"length",
	"area",
	"volume",
]


def bulk_insert_entities(model, rows, working, layer_map, on_progress=None):
	"""Insert many new entities at once (imports). Each row is checked like a single save; rows that fail
	are skipped and returned with the reason. Returns (inserted entity ids, skipped)."""
	from frappe.utils import now_datetime

	exists, extent = block_resolver(model)
	taken = set(frappe.get_all(ENTITY, filters={"cad_model": model}, pluck="entity_id"))
	now, user = now_datetime(), frappe.session.user
	values, inserted, skipped = [], [], []
	for n, row in enumerate(rows):
		entity_id = row["entity_id"]
		try:
			if entity_id in taken or not ENTITY_ID.match(entity_id):
				raise frappe.ValidationError(_("Duplicate or invalid identifier {0}.").format(entity_id))
			geometry = cad_geometry.normalise(row["entity_type"], row["geometry"], exists)
			box = cad_geometry.extent(row["entity_type"], geometry, extent) or [0.0] * 6
		except Exception as e:
			skipped.append({"entity_id": entity_id, "source_id": row.get("source_id"), "message": frappe.utils.strip_html(str(e))[:200]})
			frappe.clear_messages()
			continue
		taken.add(entity_id)
		values.append(
			(
				frappe.generate_hash(length=10),
				now,
				now,
				user,
				user,
				0,
				0,
				model,
				entity_id,
				row["entity_type"],
				layer_map.get(row.get("layer")) or layer_map["0"],
				"Not Checked",
				working,
				1,
				row.get("color"),
				row.get("line_type"),
				flt(row.get("line_weight")),
				row.get("source_id"),
				json.dumps(geometry),
				json.dumps(row["metadata"]) if row.get("metadata") else None,
				geometry.get("block") if row["entity_type"] == "Block Reference" else None,
				*box,
				0,
				0,
				0,
			)
		)
		inserted.append(entity_id)
		if len(values) >= 2000:
			frappe.db.bulk_insert(ENTITY, BULK_FIELDS, values)
			values = []
			if on_progress:
				on_progress(n)
	if values:
		frappe.db.bulk_insert(ENTITY, BULK_FIELDS, values)
	return inserted, skipped


def ensure_layers(model, layers):
	"""Create the layers that do not exist yet (existing layers keep their settings); {layer name: record}."""
	existing = {row.layer_name: row.name for row in frappe.get_all(LAYER, filters={"cad_model": model}, fields=["name", "layer_name"])}
	for n, row in enumerate(layers):
		name = row["layer_name"]
		if name in existing:
			continue
		layer = frappe.get_doc(
			{
				"doctype": LAYER,
				"cad_model": model,
				"layer_name": name,
				"color": row.get("color") or "#e0e0e0",
				"line_type": row.get("line_type") or "Continuous",
				"line_weight": row.get("line_weight") or 0.25,
				"visible": cint(row.get("visible", 1)),
				"locked": cint(row.get("locked", 0)),
				"display_order": cint(row.get("display_order", n + 1)),
				"description": row.get("description"),
			}
		)
		layer.flags.ignore_permissions = True
		layer.insert()
		existing[name] = layer.name
	if DEFAULT_LAYER not in existing:
		existing[DEFAULT_LAYER] = default_layer(model)
	return existing


def copy_template(model, template):
	"""Layers and blocks of a template model, for a new model."""
	frappe.get_doc(MODEL, template).check_permission("read")
	ensure_layers(model, [{k: v for k, v in layer.items() if k != "name"} for layer in get_layers(template)])
	apply_blocks_in_order(model, [{"block_name": b.block_name, "base_point": b.base_point, "entities": b.entities, "description": b.description} for b in get_blocks(template)])


def plan_rotated(doc):
	"""True when the plan view should turn 180° (westing/southing axes, e.g. South African Lo)."""
	if doc.plan_orientation == "Rotated 180° (Westing/Southing)":
		return True
	if doc.plan_orientation == "X East, Y North" or not doc.crs:
		return False
	try:
		from pyproj import CRS

		axes = [a.direction for a in CRS.from_user_input(doc.crs).axis_info[:2]]
		return axes == ["west", "south"]
	except Exception:
		return False
