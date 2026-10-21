# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from is_production.cad_engineering.services import cad_geometry
from is_production.cad_engineering.services import cad_storage_service as storage

BBOX_FIELDS = ("min_x", "min_y", "min_z", "max_x", "max_y", "max_z")


class CADEntity(Document):
	def validate(self):
		from is_production.cad_engineering.services.cad_model_service import ENTITY_ID, default_layer

		if not ENTITY_ID.match(self.entity_id or ""):
			frappe.throw(_("Entity Identifier may use letters, digits and . _ : - only (at most 80)."))
		if frappe.db.exists("CAD Entity", {"cad_model": self.cad_model, "entity_id": self.entity_id, "name": ["!=", self.name]}):
			frappe.throw(_("Entity {0} already exists in {1}.").format(self.entity_id, self.cad_model))
		if not self.cad_layer:
			self.cad_layer = default_layer(self.cad_model)
		elif frappe.db.get_value("CAD Layer", self.cad_layer, "cad_model") != self.cad_model:
			frappe.throw(_("Layer {0} belongs to another model.").format(self.cad_layer))
		self.normalise_geometry()
		if not frappe.flags.cad_batch:
			self.record_desk_edit()

	def normalise_geometry(self):
		"""Exact geometry checked and normalised; bounding box for the view index."""
		from is_production.cad_engineering.services.cad_model_service import block_resolver

		exists, block_extent = self.flags.block_resolver or block_resolver(self.cad_model)
		geometry = cad_geometry.normalise(self.entity_type, self.geometry or {}, exists)
		self.geometry = json.dumps(geometry)
		self.block_name = geometry.get("block") if self.entity_type == "Block Reference" else None
		if self.entity_type in cad_geometry.BLOB_TYPES:
			meta = self.blob_meta()
			box = meta.get("bbox")
			self.geometry_format = "OCCT B-Rep"
			self.validation_status = "Valid" if meta.get("valid") else "Invalid"
			self.validation_message = meta.get("message")
			self.volume, self.area, self.length = meta.get("volume") or 0, meta.get("area") or 0, 0
		else:
			box = cad_geometry.extent(self.entity_type, geometry, block_extent)
			self.geometry_ref = self.geometry_format = None
			if self.is_new() or self.has_value_changed("geometry"):
				self.validation_status = "Not Checked"
				self.validation_message = None
				self.length = self.area = self.volume = 0
		for field, value in zip(BBOX_FIELDS, box or [0.0] * 6):
			self.set(field, value)

	def blob_meta(self):
		from is_production.cad_engineering.services.cad_model_service import blob_meta

		if not storage.SHA256.match(self.geometry_ref or ""):
			frappe.throw(_("A {0} needs its geometry file reference.").format(_(self.entity_type)))
		if not storage.blob_exists(self.cad_model, self.geometry_ref):
			frappe.throw(_("Geometry file {0} is not in model {1}'s storage.").format(self.geometry_ref[:12], self.cad_model))
		meta = blob_meta(self.cad_model, self.geometry_ref)
		if not meta:
			frappe.throw(_("Geometry file {0} was not made by the CAD worker.").format(self.geometry_ref[:12]))
		return meta

	def record_desk_edit(self):
		"""An edit outside the workstation: same rules (locked layers, Working revision)."""
		from is_production.cad_engineering.services.cad_revision_service import ensure_working_revision

		before = self.get_doc_before_save()
		for layer in {self.cad_layer, before.cad_layer if before else None}:
			if layer and frappe.db.get_value("CAD Layer", layer, "locked"):
				frappe.throw(_("Layer {0} is locked.").format(frappe.db.get_value("CAD Layer", layer, "layer_name")))
		self.revision = ensure_working_revision(self.cad_model)
		self.entity_version = cint(self.entity_version) + 1

	def on_update(self):
		if not frappe.flags.cad_batch:
			from is_production.cad_engineering.services.cad_model_service import refresh_model

			refresh_model(self.cad_model)

	def on_trash(self):
		if frappe.flags.cad_batch or frappe.flags.cad_model_delete:
			return
		from is_production.cad_engineering.services.cad_revision_service import ensure_working_revision

		if self.cad_layer and frappe.db.get_value("CAD Layer", self.cad_layer, "locked"):
			frappe.throw(_("The entity is on a locked layer."))
		ensure_working_revision(self.cad_model)

	def after_delete(self):
		if not (frappe.flags.cad_batch or frappe.flags.cad_model_delete):
			from is_production.cad_engineering.services.cad_model_service import refresh_model

			refresh_model(self.cad_model)
