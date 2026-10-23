# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class CADDesignPublication(Document):
	def validate(self):
		if self.is_new():
			return
		before = self.get_doc_before_save()
		frozen = ("cad_model", "revision", "geometry_checksum", "design_standard", "design_validation", "geo_pit_layout", "blocks_published", "total_volume")
		if before and any(str(before.get(f) or "") != str(self.get(f) or "") for f in frozen):
			frappe.throw(_("A publication records what was published and cannot be changed. Publish again."))
