# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class GISPluginSettings(Document):
	def validate(self):
		for line in (self.allowed_sources or "").splitlines():
			if line.strip() and not line.strip().startswith("https://"):
				frappe.throw(_("Allowed sources must be https:// URL prefixes: {0}").format(line.strip()))
		self.max_package_mb = min(max(cint(self.max_package_mb) or 50, 1), 500)

	def onload(self):
		from is_production.geo_planning.services.gis_plugin_service import processing_dir

		self.processing_plugin_directory = processing_dir()

	@frappe.whitelist()
	def mark_restarted(self):
		frappe.only_for("System Manager")
		frappe.db.set_single_value("GIS Plugin Settings", "server_restart_required", 0)
