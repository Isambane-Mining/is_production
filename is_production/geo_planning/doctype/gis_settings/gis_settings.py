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

	def on_update(self):
		"""The QGIS projects embed the database account and live in the project directory: republish all."""
		fields = ("qgis_db_host", "qgis_db_port", "qgis_db_name", "qgis_db_user", "qgis_db_password", "qgis_project_directory")
		if any(self.has_value_changed(f) for f in fields) and self.qgis_db_user and self.qgis_project_directory:
			from is_production.geo_planning.services.gis_publish_service import publish_all

			publish_all()

	@frappe.whitelist()
	def publish_all_datasets(self):
		frappe.has_permission("GIS Settings", "write", throw=True)
		from is_production.geo_planning.services.gis_publish_service import publish_all

		return publish_all()

	@frappe.whitelist()
	def run_health_check(self):
		frappe.has_permission("GIS Settings", "write", throw=True)
		from is_production.geo_planning.services.gis_health_service import run_health_check

		return run_health_check()
