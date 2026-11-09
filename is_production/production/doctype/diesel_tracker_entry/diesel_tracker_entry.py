# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class DieselTrackerEntry(Document):
	"""Diesel litres and the coal tons and waste bcm one machine moved over a shift, day or week
	(Diesel L/BCM Tracker, /portal_diesel_tracker)."""

	def validate(self):
		for field, label in (("diesel_litres", _("Diesel")), ("coal_tons", _("Coal")), ("waste_bcm", _("Waste"))):
			if flt(self.get(field)) < 0:
				frappe.throw(_("{0} must be zero or more.").format(label))
		site = frappe.db.get_value("Diesel Tracker Equipment", self.equipment, "site")
		if site and site != self.site:
			frappe.throw(
				_("{0} is on another site: choose a machine of {1}.").format(
					frappe.bold(frappe.db.get_value("Diesel Tracker Equipment", self.equipment, "equipment_name")),
					frappe.db.get_value("Diesel Tracker Site", self.site, "site_name") or self.site,
				)
			)
