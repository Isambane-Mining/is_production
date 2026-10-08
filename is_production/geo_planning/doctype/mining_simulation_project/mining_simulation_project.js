// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const MINING_SIM_API = "is_production.geo_planning.services.mining_simulation_service.";
const MINING_SIM_DESK_JS = "/assets/is_production/mining_simulation/desk_import.js";

frappe.ui.form.on("Mining Simulation Project", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm
			.add_custom_button(__("Open in Simulator"), () => {
				frappe.route_options = { project: frm.doc.name };
				frappe.set_route("mining-simulation");
			})
			.addClass("btn-primary");

		frm.add_custom_button(
			__("Export Simulation File"),
			() => {
				window.open(
					"/api/method/" + MINING_SIM_API + "export_simulation_file?name=" + encodeURIComponent(frm.doc.name),
				);
			},
			__("Simulation File"),
		);

		if (frm.perm[0] && frm.perm[0].write) {
			frm.add_custom_button(
				__("Replace from Simulation File"),
				() =>
					frappe.require(MINING_SIM_DESK_JS, () =>
						is_production.mining_simulation.pick_file((file_url, info) => {
							frappe.confirm(
								__(
									"Replace everything in {0} with the project in this file ({1}, {2} blocks)? This cannot be undone.",
									[frm.doc.project_name.bold(), info.project_name, info.blocks],
								),
								() =>
									is_production.mining_simulation
										.run_import(file_url, "replace", frm.doc.name)
										.then(() => frm.reload_doc()),
							);
						}),
					),
				__("Simulation File"),
			);
		}
	},
});
