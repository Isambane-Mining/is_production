# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from is_production.cad_engineering.services import cad_storage_service as storage


class CADModel(Document):
	def validate(self):
		self.model_code = (self.model_code or "").strip()
		storage.check_model_code(self.model_code)
		if self.crs:
			from is_production.geo_planning.services.gis_storage_service import resolve_crs

			self.crs, _code = resolve_crs(self.crs)
		if self.source_crs:
			from is_production.geo_planning.services.gis_storage_service import resolve_crs

			self.source_crs, _code = resolve_crs(self.source_crs)
		if self.is_new():
			settings = frappe.get_cached_doc("CAD Settings")
			self.crs = self.crs or settings.default_crs
			self.linear_units = self.linear_units or settings.default_linear_units or "Metres"
			self.angular_units = self.angular_units or settings.default_angular_units or "Degrees"
			self.approval_status = "Not Submitted"
			self.annotation_scale = self.annotation_scale or settings.default_annotation_scale or 1000
			self.drawing_template = self.drawing_template or settings.default_drawing_template
		elif self.has_value_changed("crs") or self.has_value_changed("linear_units"):
			if self.entity_count:
				frappe.throw(
					_("The CRS and units of a model with geometry cannot be changed here; coordinates would be reinterpreted. Use a coordinate transformation (Phase 2)."),
					title=_("Coordinates Locked"),
				)

	def after_insert(self):
		from is_production.cad_engineering.services.cad_model_service import default_layer
		from is_production.cad_engineering.services.cad_revision_service import create_revision

		default_layer(self.name)
		revision = create_revision(self.name, description=_("Initial working revision"))
		self.db_set({"working_revision": revision.name, "layer_count": 1}, update_modified=False)
		if self.drawing_template and self.drawing_template != self.name:
			from is_production.cad_engineering.services.cad_model_service import copy_template

			frappe.flags.cad_batch = True
			try:
				copy_template(self.name, self.drawing_template)
			finally:
				frappe.flags.cad_batch = False
			self.db_set("layer_count", frappe.db.count("CAD Layer", {"cad_model": self.name}), update_modified=False)

	def on_trash(self):
		if frappe.db.exists("CAD Design Revision", {"cad_model": self.name, "revision_status": ["in", ["Approved", "Superseded"]]}):
			frappe.throw(_("{0} has approved revisions and cannot be deleted. Archive it instead.").format(self.name))
		frappe.flags.cad_model_delete = True
		try:
			self.db_set({"working_revision": None, "current_revision": None}, update_modified=False)
			frappe.db.delete("CAD Entity", {"cad_model": self.name})
			frappe.db.delete("CAD Layer", {"cad_model": self.name})
			frappe.db.delete("CAD Block", {"cad_model": self.name})
			frappe.db.delete("CAD Surface", {"cad_model": self.name})
			frappe.db.delete("CAD Volume Calculation", {"cad_model": self.name})
			frappe.db.delete("CAD Import Export", {"cad_model": self.name})
			frappe.db.delete("CAD Design Revision", {"cad_model": self.name})
			frappe.db.delete("CAD Processing Job", {"cad_model": self.name})
		finally:
			frappe.flags.cad_model_delete = False

	def after_delete(self):
		store = storage.provider()
		store.delete_prefix("models", self.name)
		store.delete_prefix("mesh", self.name)
