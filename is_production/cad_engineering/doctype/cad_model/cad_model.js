// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const CAD_API = "is_production.cad_engineering.services.cad_workstation_service.";

frappe.ui.form.on("CAD Model", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Open in CAD Workstation"), () =>
			window.open(`/portal_cad?model=${encodeURIComponent(frm.doc.name)}`, "_blank")
		);
		frm.add_custom_button(__("Revisions"), () => frappe.set_route("List", "CAD Design Revision", { cad_model: frm.doc.name }), __("View"));
		frm.add_custom_button(__("Entities"), () => frappe.set_route("List", "CAD Entity", { cad_model: frm.doc.name }), __("View"));
		frm.add_custom_button(__("Layers"), () => frappe.set_route("List", "CAD Layer", { cad_model: frm.doc.name }), __("View"));
		if (frm.doc.working_revision && frm.has_perm("write")) {
			frm.add_custom_button(__("Submit for Review"), () =>
				frappe.prompt(
					{ fieldname: "description", fieldtype: "Small Text", label: __("What changed and why"), reqd: 1 },
					(values) =>
						frappe
							.call({ method: CAD_API + "submit_revision", args: { model: frm.doc.name, description: values.description }, freeze: true })
							.then((r) => {
								frappe.show_alert({ message: __("{0} submitted for review.", [r.message]), indicator: "blue" });
								frm.reload_doc();
							}),
					__("Submit {0} for Review", [frm.doc.working_revision])
				)
			);
		}
		if (frm.doc.approval_status) {
			const colors = { Approved: "green", "In Review": "blue", Rejected: "red" };
			frm.dashboard.set_headline_alert(`${__("Approval")}: ${__(frm.doc.approval_status)}`, colors[frm.doc.approval_status] || "gray");
		}
	},
});
