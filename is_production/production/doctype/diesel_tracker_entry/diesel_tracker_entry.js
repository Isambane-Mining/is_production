// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

// The machines of the entry's site only
frappe.ui.form.on("Diesel Tracker Entry", {
	setup(frm) {
		frm.set_query("equipment", () => ({ filters: frm.doc.site ? { site: frm.doc.site } : {} }));
	},
	site(frm) {
		if (frm.doc.equipment) frm.set_value("equipment", null);
	},
});
