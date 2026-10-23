// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const METHOD = "is_production.cad_engineering.doctype.cad_design_standard.cad_design_standard.";

frappe.ui.form.on("CAD Design Standard", {
	refresh(frm) {
		if (frm.is_new()) return;
		if (frm.doc.status !== "Draft") {
			frm.set_intro(__("This version is {0}: its rules are frozen because designs record which version they were validated against. Make a new version to change them.", [__(frm.doc.status)]), "blue");
			frm.disable_form();
		}
		if (frm.doc.status === "Draft" && frm.perm[0].write) {
			frm.add_custom_button(__("Activate"), () =>
				frappe.confirm(__("Activate {0}? Any Active version of {1} becomes Superseded, and the rules are frozen.", [frm.doc.name, frm.doc.standard_code]), () =>
					frappe.call(METHOD + "activate", { name: frm.doc.name }).then(() => frm.reload_doc())
				)
			);
		}
		frm.add_custom_button(__("New Version"), () =>
			frappe.call(METHOD + "new_version", { name: frm.doc.name }).then((r) => frappe.set_route("Form", "CAD Design Standard", r.message))
		);
	},
});
