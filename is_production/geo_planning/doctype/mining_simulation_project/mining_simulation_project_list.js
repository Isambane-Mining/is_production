// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

frappe.listview_settings["Mining Simulation Project"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Open Simulator"), () => frappe.set_route("mining-simulation"));
		if (frappe.model.can_create("Mining Simulation Project")) {
			listview.page.add_inner_button(__("Import Simulation File"), () =>
				frappe.require("/assets/is_production/mining_simulation/desk_import.js", () =>
					is_production.mining_simulation.import_new(),
				),
			);
		}
	},
};
