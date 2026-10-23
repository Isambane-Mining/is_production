# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

"""The equipment Mining Simulation Projects need, for asset deployment planning: units of each Item,
by project and (optionally) by team."""

import frappe
from frappe import _
from frappe.query_builder.functions import Sum

from is_production.geo_planning.services.mining_simulation_service import PRODUCTION_EQUIPMENT_KINDS


def execute(filters=None):
	filters = frappe._dict(filters or {})
	by_team = bool(filters.get("by_team"))

	columns = [
		{"label": _("Project"), "fieldname": "project", "fieldtype": "Link", "options": "Mining Simulation Project", "width": 170},
		{"label": _("Project Name"), "fieldname": "project_name", "fieldtype": "Data", "width": 170},
		{"label": _("Start Date"), "fieldname": "start_date", "fieldtype": "Date", "width": 100},
	]
	if by_team:
		columns.append({"label": _("Team"), "fieldname": "team", "fieldtype": "Data", "width": 110})
	columns += [
		{"label": _("Asset Category"), "fieldname": "asset_category", "fieldtype": "Link", "options": "Asset Category", "width": 120},
		{"label": _("Item"), "fieldname": "item", "fieldtype": "Link", "options": "Item", "width": 170},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 180},
		{"label": _("Units"), "fieldname": "units", "fieldtype": "Float", "precision": 0, "width": 80},
	]

	project = frappe.qb.DocType("Mining Simulation Project")
	equipment = frappe.qb.DocType("Mining Simulation Equipment")
	item = frappe.qb.DocType("Item")
	query = (
		frappe.qb.from_(equipment)
		.join(project)
		.on((project.name == equipment.parent) & (equipment.parenttype == "Mining Simulation Project"))
		.left_join(item)
		.on(item.name == equipment.item)
		.select(
			project.name.as_("project"),
			project.project_name,
			project.start_date,
			equipment.asset_category,
			equipment.item,
			item.item_name,
			Sum(equipment.qty).as_("units"),
		)
		.where(equipment.item.isnotnull() & (equipment.item != ""))
		.where(equipment.asset_category.isin(list(PRODUCTION_EQUIPMENT_KINDS)))
		.groupby(project.name, equipment.asset_category, equipment.item)
		.orderby(project.start_date)
		.orderby(project.name)
		.orderby(equipment.asset_category)
		.orderby(equipment.item)
	)
	if by_team:
		query = query.select(equipment.team).groupby(equipment.team).orderby(equipment.team)
	for field in ("project", "asset_category", "item"):
		if filters.get(field):
			query = query.where((project.name if field == "project" else equipment[field]) == filters.get(field))
	if filters.get("geo_project"):
		query = query.where(project.geo_project == filters.geo_project)

	allowed = frappe.get_list("Mining Simulation Project", pluck="name", limit_page_length=0)
	rows = [r for r in query.run(as_dict=True) if r.project in allowed]
	return columns, rows
