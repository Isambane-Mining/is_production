// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const GIS_IO = "is_production.geo_planning.services.gis_io_service.";

frappe.ui.form.on("GIS Import Export Job", {
	refresh(frm) {
		const color = { Completed: "green", "Completed with Errors": "orange", Failed: "red", Running: "blue", Queued: "blue" }[frm.doc.status];
		if (color) frm.dashboard.set_headline_alert(`${__(frm.doc.status)}${frm.doc.status_message ? " — " + frappe.utils.escape_html(frm.doc.status_message) : ""}`, color);
		if (frm.doc.output_file) {
			frm.add_custom_button(__("Download"), () => window.open(frm.doc.output_file, "_blank"));
		}
		if (frm.doc.job_type === "Import" && frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(__("Run Import"), () =>
				frappe.call({ method: GIS_IO + "run_import", args: { job: frm.doc.name } }).then(() => frm.reload_doc())
			);
		}
		if (frm.doc.gis_project) {
			frm.add_custom_button(__("Open Project in GIS Workstation"), () => window.open(`/portal_gis?project=${encodeURIComponent(frm.doc.gis_project)}`, "_blank"));
		}
		if (["Queued", "Running"].includes(frm.doc.status)) {
			setTimeout(() => frm.doc.status && ["Queued", "Running"].includes(frm.doc.status) && frm.reload_doc(), 3000);
		}
	},
});
