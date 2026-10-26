// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("Mining Block", {
	refresh(frm) {
		// a block published from an approved CAD design: open the design at this block
		if (frm.doc.cad_model && frm.doc.cad_entity_id) {
			frm.add_custom_button(__("Open in CAD Workstation"), () => {
				window.open(`/portal_cad?model=${encodeURIComponent(frm.doc.cad_model)}&entity=${encodeURIComponent(frm.doc.cad_entity_id)}`, "_blank");
			});
			if (frm.doc.planning_status === "Review") {
				frm.set_intro(frm.doc.remarks || __("The CAD design changed this block after it was scheduled: review it."), "orange");
			}
		}
	},
});
