from urllib.parse import quote

import frappe
from frappe.utils.jinja_globals import bundled_asset
from frappe.website.utils import abs_url

from is_production.geo_planning.services.mining_simulation_service import DOCTYPE, asset_version

no_cache = 1


def get_context(context):
	"""The Mining Simulation portal page (portal_mining_simulation.html, built from the planner's
	simulator.html): full screen, outside the desk, for users who may read Mining Simulation Projects."""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=" + quote(frappe.request.full_path)
		raise frappe.Redirect
	if not frappe.has_permission(DOCTYPE, "read"):
		raise frappe.PermissionError

	context.no_cache = 1
	context.show_sidebar = False
	context.safe_render = False  # the simulator's own code; Jinja only fills the two values below
	context.asset_version = asset_version()
	# the site's favicon (Website Settings), as on the other portal pages
	context.favicon = abs_url(frappe.get_website_settings("favicon") or "/assets/frappe/images/frappe-favicon.svg")
	context.mining_sim = {
		"csrfToken": frappe.sessions.get_csrf_token(),
		"project": frappe.form_dict.get("project") or None,
		"canWrite": bool(frappe.has_permission(DOCTYPE, "write")),
		# the user's desk theme (Light / Dark / Automatic), for when the page is opened on its own
		"theme": frappe.db.get_value("User", frappe.session.user, "desk_theme") or "Light",
		# Frappe's theme variables and fonts are read from here (frappe_theme.js)
		"deskCss": bundled_asset("desk.bundle.css"),
	}
#Test upload