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
