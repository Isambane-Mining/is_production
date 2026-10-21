// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const CAD_HEALTH_COLORS = { OK: "green", Warning: "orange", Error: "red" };

frappe.ui.form.on("CAD Settings", {
	refresh(frm) {
		if (frm.doc.health_status) {
			frm.dashboard.set_headline_alert(
				`${__("Health")}: ${__(frm.doc.health_status)}`,
				CAD_HEALTH_COLORS[frm.doc.health_status]
			);
		}
		frm.add_custom_button(__("Open CAD Workstation"), () => window.open("/portal_cad", "_blank"));
		frm.add_custom_button(__("CAD Models"), () => frappe.set_route("List", "CAD Model"), __("View"));
		frm.add_custom_button(__("Processing Jobs"), () => frappe.set_route("List", "CAD Processing Job"), __("View"));
		frm.add_custom_button(__("Check Dependencies"), () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the settings first."));
				return;
			}
			frm.call({
				method: "run_health_check",
				doc: frm.doc,
				freeze: true,
				freeze_message: __("Checking the CAD worker, Open CASCADE and model storage…"),
			}).then(() => frm.reload_doc());
		});
	},
});
