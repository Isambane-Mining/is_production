# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import os
import sys

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from is_production.cad_engineering.services.cad_storage_service import site_directory


class CADSettings(Document):
	def validate(self):
		if not os.path.isabs(self.worker_python or ""):
			frappe.throw(_("CAD Worker Python must be an absolute path."))
		if os.path.realpath(self.worker_python) == os.path.realpath(sys.executable):
			frappe.throw(_("The CAD worker must not run in the bench's own Python environment."))
		site_directory(self.model_storage_directory, "private/cad/models")
		site_directory(self.mesh_cache_directory, "private/cad/mesh_cache")
		self.max_processing_minutes = min(max(cint(self.max_processing_minutes) or 30, 1), 24 * 60)
		self.max_concurrent_jobs = min(max(cint(self.max_concurrent_jobs) or 2, 1), 32)
		self.worker_nice = min(max(cint(self.worker_nice), 0), 19)
		self.max_import_size_mb = min(max(cint(self.max_import_size_mb) or 200, 1), 4096)
		self.coordinate_precision = min(max(cint(self.coordinate_precision), 0), 9)
		self.angular_precision = min(max(cint(self.angular_precision), 0), 9)
		if cint(self.worker_memory_limit_mb) < 0:
			self.worker_memory_limit_mb = 0

	@frappe.whitelist()
	def run_health_check(self):
		frappe.has_permission("CAD Settings", "write", throw=True)
		from is_production.cad_engineering.services.cad_health_service import run_health_check

		return run_health_check()
