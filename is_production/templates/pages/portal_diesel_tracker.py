# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import os
from urllib.parse import quote

import frappe
from frappe.website.utils import abs_url

from is_production.production.diesel_tracker import ENTRY

no_cache = 1


def asset_version():
	"""Cache-buster for the page's stylesheet and scripts."""
	base = os.path.join(frappe.get_app_path("is_production"), "public", "diesel_tracker")
	stamp = 0
	for root, _dirs, files in os.walk(base):
		for fn in files:
			stamp = max(stamp, int(os.path.getmtime(os.path.join(root, fn))))
	return str(stamp)


def get_context(context):
	"""The Diesel L/BCM Tracker (portal_diesel_tracker.html): full screen, outside the desk, for users who may
	read Diesel Tracker Entries. Its data comes from is_production.production.diesel_tracker."""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=" + quote(frappe.request.full_path)
		raise frappe.Redirect
	if not frappe.has_permission(ENTRY, "read"):
		raise frappe.PermissionError

	desk_theme = frappe.db.get_value("User", frappe.session.user, "desk_theme") or "Light"
	context.no_cache = 1
	context.show_sidebar = False
	context.asset_version = asset_version()
	# the user's desk theme; "Automatic" follows the operating system (no data-theme)
	context.theme = {"Light": "light", "Dark": "dark"}.get(desk_theme, "")
	context.favicon = abs_url(frappe.get_website_settings("favicon") or "/assets/frappe/images/frappe-favicon.svg")
	context.diesel_tracker = {"csrfToken": frappe.sessions.get_csrf_token()}
