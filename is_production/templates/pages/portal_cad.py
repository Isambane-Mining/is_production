from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils.jinja_globals import bundled_asset
from frappe.website.utils import abs_url

from is_production.cad_engineering.services.cad_workstation_service import get_workstation_config

no_cache = 1


def get_context(context):
	"""The CAD Engineering Workstation: full screen, outside the desk, for users who may read CAD Models."""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=" + quote(frappe.request.full_path)
		raise frappe.Redirect
	if not frappe.has_permission("CAD Model", "read"):
		raise frappe.PermissionError
	if not frappe.db.get_single_value("CAD Settings", "enable_cad_workstation"):
		frappe.throw(_("The CAD Workstation is switched off (CAD Settings)."), frappe.PermissionError)

	context.no_cache = 1
	context.show_sidebar = False
	context.favicon = abs_url(frappe.get_website_settings("favicon") or "/assets/frappe/images/frappe-favicon.svg")
	context.cad_js = bundled_asset("cad_workstation.bundle.js")
	context.cad_css = bundled_asset("cad_workstation.bundle.css")
	context.cad = {
		"csrfToken": frappe.sessions.get_csrf_token(),
		"config": get_workstation_config(),
	}
	# read by mining_simulation/frappe_theme.js (shared with the Mining Simulation and GIS pages)
	context.page_theme = {
		"theme": frappe.db.get_value("User", frappe.session.user, "desk_theme") or "Light",
		"deskCss": bundled_asset("desk.bundle.css"),
	}
