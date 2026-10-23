# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CADDesignValidation(Document):
	def validate(self):
		if not self.is_new():
			frappe.throw(_("A design validation is a record of a check that was made and cannot be changed. Run a new one."))
