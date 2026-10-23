# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from is_production.geo_planning.services.gis_storage_service import (
	GEOMETRY_COLUMN,
	coerce_attributes,
	normalize_geometry,
	parse_json_value,
	refresh_dataset_statistics,
	write_feature_geometry,
)


class GISFeature(Document):
	def load_from_db(self):
		super().load_from_db()
		# SELECT * also returns the native geometry column (WKB bytes); the
		# GeoJSON field is the document's geometry, so drop the binary copy.
		self.__dict__.pop(GEOMETRY_COLUMN, None)

	def validate(self):
		dataset = frappe.get_cached_doc("GIS Dataset", self.dataset)
		if dataset.status == "Archived":
			frappe.throw(_("Dataset {0} is archived; its features cannot be changed.").format(dataset.name))
		if dataset.geometry_type == "Raster":
			frappe.throw(_("Dataset {0} is a raster dataset and has no features.").format(dataset.name))

		self.crs = dataset.crs
		self.srid = dataset.srid
		self.has_z = dataset.has_z
		self._wkb_2d = None

		if dataset.geometry_type == "No Geometry":
			if parse_json_value(self.geometry, _("Geometry")):
				frappe.throw(_("Dataset {0} has no geometry; leave Geometry empty.").format(dataset.name))
			self.geometry = None
			self.geometry_type = "None"
			self.bbox_min_x = self.bbox_min_y = self.bbox_max_x = self.bbox_max_y = None
			self.min_z = self.max_z = None
			self.validation_status = "Valid"
			self.validation_message = None
		else:
			result = normalize_geometry(self.geometry, dataset.geometry_type, dataset.has_z)
			if not result["is_valid"] and dataset.reject_invalid_geometry:
				frappe.throw(_("Invalid geometry: {0}").format(result["validation_message"]))
			self.geometry = result["geojson"]
			self.geometry_type = result["geometry_type"]
			self.bbox_min_x, self.bbox_min_y, self.bbox_max_x, self.bbox_max_y = result["bbox"]
			self.min_z, self.max_z = result["z_range"]
			self.validation_status = "Valid" if result["is_valid"] else "Invalid"
			self.validation_message = result["validation_message"]
			self._wkb_2d = result["wkb_2d"]

		self.attributes = coerce_attributes(self.attributes, dataset.attributes, self.is_new())
		self.set_revision()

	def set_revision(self):
		before = None if self.is_new() else self.get_doc_before_save()
		if not before:
			self.revision = 1
			self._geometry_changed = True
			return

		self._geometry_changed = parse_json_value(before.geometry, "") != parse_json_value(self.geometry, "")
		attributes_changed = parse_json_value(before.attributes, "") != parse_json_value(self.attributes, "")
		# the label counts too: the workstation's conflict check compares revisions
		label_changed = (before.feature_label or "") != (self.feature_label or "")
		if self._geometry_changed or attributes_changed or label_changed:
			self.revision = (before.revision or 0) + 1

	def on_update(self):
		if getattr(self, "_geometry_changed", False):
			write_feature_geometry(self.name, self._wkb_2d, self.srid)
		self.refresh_dataset()

	def on_trash(self):
		if frappe.db.get_value("GIS Dataset", self.dataset, "status") == "Archived":
			frappe.throw(_("Dataset {0} is archived; its features cannot be deleted.").format(self.dataset))

	def after_delete(self):
		self.refresh_dataset()

	def refresh_dataset(self):
		if not (self.flags.skip_dataset_statistics or frappe.flags.in_gis_bulk_write):
			refresh_dataset_statistics(self.dataset)
