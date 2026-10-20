// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

frappe.query_reports["Mining Simulation Equipment Plan"] = {
	filters: [
		{
			fieldname: "project",
			label: __("Mining Simulation Project"),
			fieldtype: "Link",
			options: "Mining Simulation Project",
		},
		{ fieldname: "geo_project", label: __("Geo Project"), fieldtype: "Link", options: "Geo Project" },
		{
			fieldname: "asset_category",
			label: __("Asset Category"),
			fieldtype: "Link",
			options: "Asset Category",
			get_query: () => ({ filters: { name: ["in", ["ADT", "Rigid", "Excavator", "Dozer"]] } }),
		},
		{
			fieldname: "item",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
			get_query: () => {
				const category = frappe.query_report.get_filter_value("asset_category");
				return { filters: { is_fixed_asset: 1, ...(category ? { asset_category: category } : {}) } };
			},
		},
		{ fieldname: "by_team", label: __("By Team"), fieldtype: "Check" },
	],
};
