# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CADLayer(Document):
	def validate(self):
		self.layer_name = (self.layer_name or "").strip()
		if not self.layer_name or len(self.layer_name) > 140:
			frappe.throw(_("Layer Name is required (at most 140 characters)."))
		if frappe.db.exists("CAD Layer", {"cad_model": self.cad_model, "layer_name": self.layer_name, "name": ["!=", self.name]}):
			frappe.throw(_("Layer {0} already exists in {1}.").format(self.layer_name, self.cad_model))
		self.validate_parent()
		if not (frappe.flags.cad_batch or frappe.flags.cad_model_delete or self.flags.cad_default_layer):
			from is_production.cad_engineering.services.cad_revision_service import ensure_working_revision

			ensure_working_revision(self.cad_model)

	def validate_parent(self):
		if not self.parent_layer:
			return
		seen = {self.name}
		parent = self.parent_layer
		while parent:
			if parent in seen:
				frappe.throw(_("A layer cannot be inside itself."))
			seen.add(parent)
			row = frappe.db.get_value("CAD Layer", parent, ["cad_model", "is_group", "parent_layer"], as_dict=True)
			if not row or row.cad_model != self.cad_model:
				frappe.throw(_("The parent layer must belong to the same model."))
			if parent == self.parent_layer and not row.is_group:
				frappe.throw(_("The parent layer must be a group."))
			parent = row.parent_layer

	def on_update(self):
		if not (frappe.flags.cad_batch or self.flags.cad_default_layer):
			from is_production.cad_engineering.services.cad_model_service import refresh_model

			refresh_model(self.cad_model)
