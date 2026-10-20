// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("GIS Project", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Open in GIS Workstation"), () => window.open(`/portal_gis?project=${encodeURIComponent(frm.doc.name)}`, "_blank"));
		frm.add_custom_button(__("Export to QGIS"), () =>
			frappe
				.call({ method: "is_production.geo_planning.services.gis_io_service.start_export", args: { export_format: "QGIS Project", project: frm.doc.name } })
				.then((r) => frappe.set_route("Form", "GIS Import Export Job", r.message.name))
		);
	},
});
