"""GIS Projects for the GIS Workstation's Project menu (new, open, save, save as)."""

import frappe
from frappe import _
from frappe.utils import flt

LAYER_FIELDS = ("dataset", "map_service", "visible", "opacity", "color", "renderer")


@frappe.whitelist()
def list_projects():
	return frappe.get_list(
		"GIS Project",
		fields=["name", "title", "description", "modified", "owner"],
		order_by="modified desc",
		limit_page_length=200,
	)


@frappe.whitelist()
def get_project(name):
	project = frappe.get_doc("GIS Project", name)
	project.check_permission("read")
	layers, hidden = [], []
	for row in project.layers:
		if row.map_service:  # an overlay service: shown if still enabled
			if frappe.db.get_value("GIS Map Service", row.map_service, "enabled"):
				layers.append({k: row.get(k) for k in LAYER_FIELDS})
			continue
		# layers whose dataset this user may not read are left out of the map
		if frappe.has_permission("GIS Dataset", "read", row.dataset):
			layers.append({k: row.get(k) for k in LAYER_FIELDS})
		else:
			hidden.append(row.dataset)
	extent = [project.extent_min_x, project.extent_min_y, project.extent_max_x, project.extent_max_y]
	return {
		"name": project.name,
		"title": project.title,
		"display_crs": project.display_crs,
		"basemap": project.basemap,
		"extent": extent if project.extent_max_x or project.extent_min_x else None,
		"layers": layers,
		"hidden_layers": len(hidden),
		"can_write": bool(project.has_permission("write")),
	}


@frappe.whitelist(methods=["POST"])
def save_project(title, layers, name=None, display_crs=None, basemap=None, extent=None):
	layers = frappe.parse_json(layers) if isinstance(layers, str) else layers or []
	extent = frappe.parse_json(extent) if isinstance(extent, str) else extent
	if name:
		project = frappe.get_doc("GIS Project", name)
		project.check_permission("write")
		# keep layers of datasets this user cannot see, so saving does not drop other people's layers
		kept = [
			row.as_dict()
			for row in project.layers
			if row.dataset and not frappe.has_permission("GIS Dataset", "read", row.dataset)
		]
	else:
		frappe.has_permission("GIS Project", "create", throw=True)
		project = frappe.new_doc("GIS Project")
		kept = []
	project.title = title or _("Untitled map")
	project.display_crs = display_crs
	project.basemap = basemap or None
	if extent and len(extent) == 4:
		project.extent_min_x, project.extent_min_y, project.extent_max_x, project.extent_max_y = (flt(v) for v in extent)
	project.set("layers", [])
	for item in layers:
		if item.get("map_service"):
			if not frappe.db.exists("GIS Map Service", item["map_service"]):
				frappe.throw(_("Map service {0} does not exist.").format(item["map_service"]))
			item["dataset"] = None
		else:
			frappe.has_permission("GIS Dataset", "read", item.get("dataset"), throw=True)
		project.append("layers", {k: item.get(k) for k in LAYER_FIELDS})
	for row in kept:
		project.append("layers", {k: row.get(k) for k in LAYER_FIELDS})
	project.save()
	return {"name": project.name, "title": project.title}
