function hodSyncLocationUsers(frm) {
    if (frm.doc.doctype !== 'HOD Presentation Schedule') return;
    return Promise.all((frm.doc.sites || []).filter(row => row.user !== (frm.doc.run_as || ''))
        .map(row => frappe.model.set_value(row.doctype, row.name, 'user', frm.doc.run_as || '')));
}

frappe.ui.form.on('HOD Presentation Schedule', {
    refresh(frm) {
        // These properties affect only this form, not other users of the shared tables.
        frm.fields_dict.sites.grid.update_docfield_property('user', 'read_only', 1);
        frm.fields_dict.recipients.grid.update_docfield_property('enabled', 'read_only', 1);
        return hodSyncLocationUsers(frm);
    },
    run_as: hodSyncLocationUsers,
});

frappe.ui.form.on('WearCheck Users', {
    sites_add: hodSyncLocationUsers,
});
