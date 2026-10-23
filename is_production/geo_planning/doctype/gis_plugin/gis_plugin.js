// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const GIS_PLUGINS = "is_production.geo_planning.services.gis_plugin_service.";

function gis_plugin_call(frm, method, args, message) {
	return frappe
		.call({ method: GIS_PLUGINS + method, args: { name: frm.doc.name, ...args }, freeze: true, freeze_message: message })
		.then(() => frm.reload_doc());
}

frappe.ui.form.on("GIS Plugin", {
	refresh(frm) {
		const s = frm.doc.status;
		const color = { Registered: "grey", Analysed: "blue", Installed: "orange", Enabled: "green", Error: "red" }[s];
		if (!frm.is_new()) frm.dashboard.set_headline_alert(`${__(s)} · ${__(frm.doc.compatibility_status || "Not Analysed")}${frm.doc.last_error ? " — " + frappe.utils.escape_html(frm.doc.last_error) : ""}`, color);
		if (frm.is_new() || !frappe.user.has_role("System Manager")) return;
		const installed = ["Installed", "Enabled", "Error"].includes(s);
		if (frm.doc.source_url && !installed) frm.add_custom_button(__("Download Package"), () => gis_plugin_call(frm, "download_package", {}, __("Downloading and analysing…")));
		if (frm.doc.package && !installed) frm.add_custom_button(__("Analyse"), () => gis_plugin_call(frm, "analyse_plugin", {}, __("Analysing the package (its code is not run)…")));
		if (s === "Analysed" && ["QGIS Processing Plugin", "QGIS Server Plugin"].includes(frm.doc.category)) {
			frm.add_custom_button(__("Install"), () => gis_plugin_call(frm, "install_plugin", {}, __("Installing…")));
		}
		if (installed) {
			if (frm.doc.enabled) frm.add_custom_button(__("Disable"), () => gis_plugin_call(frm, "set_plugin_enabled", { enabled: 0 }, __("Disabling…")));
			else frm.add_custom_button(__("Enable"), () => gis_plugin_call(frm, "set_plugin_enabled", { enabled: 1 }, __("Enabling and loading it in the processing worker…")));
			frm.add_custom_button(__("Uninstall"), () =>
				frappe.confirm(__("Uninstall {0}?", [frm.doc.title]), () => gis_plugin_call(frm, "uninstall_plugin", {}, __("Uninstalling…")))
			);
		}
	},
});
