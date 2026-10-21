// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const CAD_REVISION_API = "is_production.cad_engineering.services.cad_workstation_service.";

frappe.ui.form.on("CAD Design Revision", {
	refresh(frm) {
		const colors = { Working: "orange", "In Review": "blue", Approved: "green", Rejected: "red", Superseded: "gray" };
		frm.dashboard.set_headline_alert(`${__(frm.doc.revision_status)}`, colors[frm.doc.revision_status]);
		if (["In Review", "Approved", "Rejected", "Superseded"].includes(frm.doc.revision_status)) frm.disable_form();
		frm.add_custom_button(__("Open Model in CAD Workstation"), () =>
			window.open(`/portal_cad?model=${encodeURIComponent(frm.doc.cad_model)}`, "_blank")
		);
		if (frm.doc.revision_status !== "In Review" || !frappe.model.can_write("CAD Design Revision")) return;
		const decide = (method, label, required) =>
			frappe.prompt(
				{ fieldname: "notes", fieldtype: "Small Text", label: __("Notes"), reqd: required ? 1 : 0 },
				(values) =>
					frappe
						.call({ method: CAD_REVISION_API + method, args: { revision: frm.doc.name, notes: values.notes }, freeze: true })
						.then(() => frm.reload_doc()),
				label
			);
		frm.add_custom_button(__("Approve"), () => decide("approve_revision", __("Approve {0}", [frm.doc.name]), false), __("Review"));
		frm.add_custom_button(__("Reject"), () => decide("reject_revision", __("Reject {0}", [frm.doc.name]), true), __("Review"));
	},
});
