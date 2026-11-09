# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class DieselTrackerSettings(Document):
	"""The coal density (coal tons to bcm) and the gauge bands of the Diesel L/BCM Tracker."""

	def validate(self):
		if flt(self.coal_density) <= 0:
			frappe.throw(_("Coal density must be more than 0."))
		if flt(self.green_limit) < 0 or flt(self.amber_limit) <= flt(self.green_limit):
			frappe.throw(_("The amber limit must be higher than the green limit."))
		if flt(self.gauge_max) <= flt(self.amber_limit):
			frappe.throw(_("The gauge maximum must be higher than the amber limit."))
