// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("GIS Dataset", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Features"), () =>
			frappe.set_route("List", "GIS Feature", { dataset: frm.doc.name })
		);
		const status = frm.doc.qgis_publish_status;
		if (status && status !== "Not Published") {
			frm.dashboard.set_headline_alert(
				`QGIS: ${__(status)}${frm.doc.qgis_publish_message ? " — " + frappe.utils.escape_html(frm.doc.qgis_publish_message) : ""}`,
				{ Published: "green", Queued: "blue", Error: "red" }[status]
			);
		}
		if (frm.perm[0] && frm.perm[0].write) {
			frm.add_custom_button(__("Publish to QGIS"), () =>
				frm
					.call({ method: "publish_to_qgis", doc: frm.doc, freeze: true, freeze_message: __("Writing the QGIS project…") })
					.then((r) => {
						frm.reload_doc();
						if (r.message && !r.message.ok) frappe.msgprint({ title: __("Publish failed"), message: r.message.message, indicator: "red" });
					})
			);
			frm.add_custom_button(__("Refresh Statistics"), () =>
				frm.call("refresh_statistics").then(() => frm.reload_doc())
			);
		}
	},
});
