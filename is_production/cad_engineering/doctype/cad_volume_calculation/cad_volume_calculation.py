# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CADVolumeCalculation(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("A volume calculation is a record of what was measured and cannot be changed. Run a new one."))
