# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class GISPlugin(Document):
	def validate(self):
		before = self.get_doc_before_save()
		if before and before.package != self.package and self.status in ("Installed", "Enabled"):
			frappe.throw(_("Uninstall the plugin before replacing its package."))
		if before and before.package != self.package:
			self.status = "Registered"
			self.compatibility_status = "Not Analysed"

	def on_trash(self):
		if self.status in ("Installed", "Enabled", "Error"):
			frappe.throw(_("Uninstall the plugin before deleting it."))
