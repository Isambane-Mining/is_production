# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DieselTrackerSite(Document):
	"""A site of the Diesel L/BCM Tracker (/portal_diesel_tracker): its own list, not Location."""

	def validate(self):
		self.site_name = (self.site_name or "").strip()
		if not self.site_name:
			frappe.throw(_("Type the site's name."))
		# (the database compares names without regard to case)
		if frappe.db.exists("Diesel Tracker Site", {"site_name": self.site_name, "name": ["!=", self.name]}):
			frappe.throw(_("A site called {0} already exists.").format(frappe.bold(self.site_name)))
