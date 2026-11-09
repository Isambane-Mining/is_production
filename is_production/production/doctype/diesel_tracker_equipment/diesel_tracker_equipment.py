# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class DieselTrackerEquipment(Document):
	"""A machine of the Diesel L/BCM Tracker (/portal_diesel_tracker), on one of its sites: its own list, not Asset."""

	def validate(self):
		self.equipment_name = (self.equipment_name or "").strip()
		if not self.equipment_name:
			frappe.throw(_("Type the fleet number or name."))
		dup = frappe.db.exists(
			"Diesel Tracker Equipment",
			{"site": self.site, "equipment_name": self.equipment_name, "name": ["!=", self.name]},
		)
		if dup:
			site = frappe.db.get_value("Diesel Tracker Site", self.site, "site_name") or self.site
			frappe.throw(_("{0} already has equipment called {1}.").format(site, frappe.bold(self.equipment_name)))
