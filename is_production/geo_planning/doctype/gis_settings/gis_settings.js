// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const GIS_HEALTH_COLORS = { OK: "green", Warning: "orange", Error: "red" };

frappe.ui.form.on("GIS Settings", {
	refresh(frm) {
		if (frm.doc.health_status) {
			frm.dashboard.set_headline_alert(
				`${__("Health")}: ${__(frm.doc.health_status)}`,
				GIS_HEALTH_COLORS[frm.doc.health_status]
			);
		}
		frm.add_custom_button(__("Open GIS Workstation"), () => window.open("/portal_gis", "_blank"));
		frm.add_custom_button(__("Map Services"), () => frappe.set_route("List", "GIS Map Service"), __("Configure"));
		frm.add_custom_button(__("Plugins"), () => frappe.set_route("List", "GIS Plugin"), __("Configure"));
		frm.add_custom_button(__("Plugin Settings"), () => frappe.set_route("Form", "GIS Plugin Settings"), __("Configure"));
		frm.add_custom_button(__("Publish All Datasets"), () =>
			frm.call({ method: "publish_all_datasets", doc: frm.doc }).then((r) =>
				frappe.show_alert({ message: __("{0} datasets queued for publishing.", [r.message]), indicator: "blue" })
			)
		);
		frm.add_custom_button(__("Refresh Processing Algorithms"), () =>
			frappe
				.call({
					method: "is_production.geo_planning.services.gis_processing_service.refresh_algorithms",
					freeze: true,
					freeze_message: __("Listing the QGIS Processing algorithms…"),
				})
				.then((r) => {
					frappe.show_alert({ message: __("{0} algorithms listed.", [r.message]), indicator: "green" });
					frm.reload_doc();
				})
		);
		frm.add_custom_button(__("Check Dependencies"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the settings first."));
				return;
			}
			frm.call({
				method: "run_health_check",
				doc: frm.doc,
				freeze: true,
				freeze_message: __("Checking QGIS Server, PyQGIS and the database account…"),
			}).then(() => frm.reload_doc());
		});
	},
});
