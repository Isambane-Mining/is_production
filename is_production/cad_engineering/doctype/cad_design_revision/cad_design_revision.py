# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from is_production.cad_engineering.services.cad_revision_service import FROZEN, label

# fields the review workflow may set on a frozen revision, and the transitions it may make
WORKFLOW_FIELDS = {"revision_status", "approved_by", "approved_by_employee", "approval_date", "approval_notes", "modified", "modified_by"}
TRANSITIONS = {
	"Working": {"Working", "In Review"},
	"In Review": {"In Review", "Approved", "Rejected"},
	"Approved": {"Superseded"},
	"Rejected": set(),
	"Superseded": set(),
}


class CADDesignRevision(Document):
	def autoname(self):
		self.name = f"{self.cad_model}-{label(self.revision_number)}"

	def validate(self):
		self.revision_label = label(self.revision_number)
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if not before:
			return
		old, new = before.revision_status, self.revision_status
		if new not in TRANSITIONS.get(old, set()) and new != old:
			frappe.throw(_("A revision cannot go from {0} to {1}.").format(_(old), _(new)))
		if old in FROZEN:
			if not self.flags.allow_status_change:
				frappe.throw(_("{0} is {1}: its geometry and record can no longer change.").format(self.name, _(old)), title=_("Revision Locked"))
			changed = {
				df.fieldname
				for df in self.meta.fields
				if df.fieldname not in WORKFLOW_FIELDS and self.get(df.fieldname) != before.get(df.fieldname)
			}
			if changed:
				frappe.throw(_("{0} is frozen; only its review status can change (tried: {1}).").format(self.name, ", ".join(sorted(changed))))
		elif new != old and not self.flags.allow_status_change:
			frappe.throw(_("Use Submit for Review in the CAD Workstation or the model form."))

	def on_trash(self):
		if frappe.flags.cad_model_delete:
			return
		frappe.throw(_("Revisions are kept for the audit trail and cannot be deleted."))
