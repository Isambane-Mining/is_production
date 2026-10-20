# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from is_production.geo_planning.services.gis_storage_service import (
	NAME_PATTERN,
	NON_SPATIAL_TYPES,
	allowed_values_of,
	coerce_value,
	refresh_dataset_statistics,
	reserved_attribute_names,
	resolve_crs,
)


class GISDataset(Document):
	def validate(self):
		if not NAME_PATTERN.match(self.dataset_name or ""):
			frappe.throw(
				_(
					"Dataset Name must be lower case letters, digits and underscores, starting with a letter (max 63)."
				)
			)

		self.crs, self.srid = resolve_crs(self.crs)
		if self.geometry_type in NON_SPATIAL_TYPES:
			self.has_z = 0

		self.storage_provider = "MariaDB"
		self.qgis_provider = "ogr"
		self.qgis_layer_name = self.dataset_name

		self.validate_attribute_schema()
		self.validate_locked_changes()

	def validate_attribute_schema(self):
		reserved = reserved_attribute_names()
		seen = set()
		for row in self.attributes:
			label = _("Attribute row {0}").format(row.idx)
			if not NAME_PATTERN.match(row.field_name or ""):
				frappe.throw(
					_(
						"{0}: Field Name must be lower case letters, digits and underscores, starting with a letter."
					).format(label)
				)
			if row.field_name in reserved:
				frappe.throw(_("{0}: {1} is a reserved name.").format(label, frappe.bold(row.field_name)))
			if row.field_name in seen:
				frappe.throw(_("{0}: {1} is used twice.").format(label, frappe.bold(row.field_name)))
			seen.add(row.field_name)

			if row.data_type != "Text":
				row.max_length = 0
			for allowed in allowed_values_of(row):
				coerce_value(frappe._dict(row.as_dict(), allowed_values=None), allowed)
			if row.default_value not in (None, ""):
				coerce_value(row, row.default_value)

	def validate_locked_changes(self):
		"""Once a dataset has features, changes that would invalidate them are refused."""
		if self.is_new() or not frappe.db.exists("GIS Feature", {"dataset": self.name}):
			return
		before = self.get_doc_before_save()
		if not before:
			return

		for fieldname in ("geometry_type", "crs", "has_z"):
			if str(before.get(fieldname)) != str(self.get(fieldname)):
				frappe.throw(
					_("{0} cannot be changed while the dataset has features.").format(
						frappe.bold(self.meta.get_label(fieldname))
					)
				)

		current = {row.field_name: row.data_type for row in self.attributes}
		for row in before.attributes:
			if row.field_name not in current:
				frappe.throw(
					_("Attribute {0} cannot be removed while the dataset has features.").format(
						frappe.bold(row.field_name)
					)
				)
			if current[row.field_name] != row.data_type:
				frappe.throw(
					_("The data type of attribute {0} cannot be changed while the dataset has features.").format(
						frappe.bold(row.field_name)
					)
				)

	@frappe.whitelist()
	def refresh_statistics(self):
		frappe.has_permission("GIS Dataset", "write", self, throw=True)
		refresh_dataset_statistics(self.name)
		self.reload()
