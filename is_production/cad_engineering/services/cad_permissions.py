"""Record permissions of CAD records follow their CAD Model.

Roles decide what a user may do with each DocType; these hooks add that the user must also be allowed the
model itself (User Permissions on Company, Branch, Location, Geo Project or CAD Model apply through the
model's links). Frappe hooks can only take access away, never grant it.
"""

import frappe

READ_TYPES = {"read", "select", "print", "report", "export", "email", "share"}
RESTRICTING_DOCTYPES = {"CAD Model", "Company", "Branch", "Location", "Geo Project"}


def model_allowed(model, ptype, user):
	if not model:
		return True
	return frappe.has_permission("CAD Model", "read" if ptype in READ_TYPES else "write", doc=model, user=user)


def has_model_permission(doc, ptype=None, user=None):
	return model_allowed(doc.get("cad_model"), ptype or "read", user or frappe.session.user)


def model_condition(user=None, doctype=None):
	"""List views: only rows of models the user may read, when User Permissions restrict CAD Models."""
	user = user or frappe.session.user
	if user == "Administrator":
		return ""
	restricted = set(frappe.permissions.get_user_permissions(user) or {}) & RESTRICTING_DOCTYPES
	if not restricted:
		return ""
	models = frappe.get_list("CAD Model", pluck="name", limit_page_length=0, user=user)
	if not models:
		return "1=0"
	table = f"`tab{doctype}`" if doctype else ""
	column = f"{table}.`cad_model`" if table else "`cad_model`"
	return f"({column} is null or {column} in ({', '.join(frappe.db.escape(m) for m in models)}))"

