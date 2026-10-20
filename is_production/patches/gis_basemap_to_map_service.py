"""GIS Settings / GIS Project basemap: Select (OpenStreetMap / None) -> Link to GIS Map Service."""

import frappe

from is_production.geo_planning.services.gis_map_service import ensure_default_map_services


def execute():
	ensure_default_map_services()  # "OpenStreetMap" must exist for the links
	frappe.db.sql("UPDATE `tabSingles` SET value = NULL WHERE doctype = 'GIS Settings' AND field = 'basemap' AND value = 'None'")
	if frappe.db.table_exists("GIS Project"):
		frappe.db.sql("UPDATE `tabGIS Project` SET basemap = NULL WHERE basemap = 'None'")
