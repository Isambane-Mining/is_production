# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class DieselTrackerSettings(Document):
	"""The gauge bands of the Diesel L/BCM Tracker (L/bcm), and when the production records were last brought in."""

	def validate(self):
		if flt(self.green_limit) < 0 or flt(self.amber_limit) <= flt(self.green_limit):
			frappe.throw(_("The amber limit must be higher than the green limit."))
		if flt(self.gauge_max) <= flt(self.amber_limit):
			frappe.throw(_("The gauge maximum must be higher than the amber limit."))
