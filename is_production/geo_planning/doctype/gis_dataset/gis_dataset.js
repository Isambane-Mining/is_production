// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("GIS Dataset", {
	refresh(frm) {
		if (frm.is_new()) return;
		frm.add_custom_button(__("Features"), () =>
			frappe.set_route("List", "GIS Feature", { dataset: frm.doc.name })
		);
		if (frm.perm[0] && frm.perm[0].write) {
			frm.add_custom_button(__("Refresh Statistics"), () =>
				frm.call("refresh_statistics").then(() => frm.reload_doc())
			);
		}
	},
});
