// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("GIS Processing Job", {
	refresh(frm) {
		const color = { Completed: "green", Failed: "red", Cancelled: "grey", Running: "blue", Queued: "blue", Cancelling: "orange" }[frm.doc.status];
		if (color) frm.dashboard.set_headline_alert(`${__(frm.doc.status)}${frm.doc.status_message ? " — " + frappe.utils.escape_html(frm.doc.status_message) : ""}`, color);
		if (["Queued", "Running"].includes(frm.doc.status)) {
			frm.add_custom_button(__("Cancel"), () =>
				frappe
					.call({ method: "is_production.geo_planning.services.gis_processing_service.cancel_processing", args: { name: frm.doc.name } })
					.then(() => frm.reload_doc())
			);
			setTimeout(() => ["Queued", "Running", "Cancelling"].includes(frm.doc.status) && frm.reload_doc(), 3000);
		}
		const datasets = (frm.doc.outputs || []).filter((o) => o.dataset).map((o) => o.dataset);
		if (datasets.length) {
			frm.add_custom_button(__("Open Output Dataset"), () => frappe.set_route("Form", "GIS Dataset", datasets[0]));
		}
	},
});
