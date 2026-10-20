# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from is_production.geo_planning.services.gis_storage_service import resolve_crs


class GISProject(Document):
	def validate(self):
		if self.display_crs:
			self.display_crs, _code = resolve_crs(self.display_crs)
		seen = set()
		for row in self.layers:
			if bool(row.dataset) == bool(row.map_service):
				frappe.throw(_("Layer row {0}: choose either a dataset or a map service.").format(row.idx))
			key = row.dataset or f"service:{row.map_service}"
			if key in seen:
				frappe.throw(_("{0} is in the project twice.").format(row.dataset or row.map_service))
			seen.add(key)
			row.opacity = min(max(flt(row.opacity) if row.opacity is not None else 1, 0), 1)
			row.renderer = row.renderer or "qgis"
