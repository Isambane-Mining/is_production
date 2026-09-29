"""Remove historical All grants without resetting other roles' permissions."""
import frappe


def execute():
    # Standard JSON handles fresh sites. Existing Custom DocPerm rows override
    # standard permissions and must be removed explicitly during upgrades.
    doctype = 'Define Monthly Production'
    for table in ("DocPerm", "Custom DocPerm"):
        frappe.db.delete(table, {"parent": doctype, "role": "All"})
    frappe.clear_cache(doctype=doctype)
