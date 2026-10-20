# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import os
from urllib.parse import urlparse

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from is_production.geo_planning.services.gis_storage_service import resolve_crs


class GISSettings(Document):
	def validate(self):
		if urlparse(self.qgis_server_url or "").scheme not in ("http", "https"):
			frappe.throw(_("QGIS Server URL must start with http:// or https://."))
		if not os.path.isfile(self.qgis_python or ""):
			frappe.throw(_("PyQGIS Python {0} does not exist on this host.").format(frappe.bold(self.qgis_python)))
		self.default_crs, _code = resolve_crs(self.default_crs)
		self.max_features_per_request = min(max(cint(self.max_features_per_request) or 5000, 1), 20000)
		self.default_zoom = min(max(cint(self.default_zoom), 1), 22)

	@frappe.whitelist()
	def run_health_check(self):
		frappe.has_permission("GIS Settings", "write", throw=True)
		from is_production.geo_planning.services.gis_health_service import run_health_check

		return run_health_check()
