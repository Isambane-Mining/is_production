// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

const GIS_PLUGIN_SVC = "is_production.geo_planning.services.gis_plugin_service.";

frappe.ui.form.on("GIS Plugin Settings", {
	refresh(frm) {
		frm.add_custom_button(__("Plugins"), () => frappe.set_route("List", "GIS Plugin"));
		frm.add_custom_button(__("Scan Plugins"), () =>
			frappe.call({ method: GIS_PLUGIN_SVC + "scan_plugins" }).then(() => frm.reload_doc())
		);
		if (!frappe.user.has_role("System Manager")) return;
		if (frm.doc.server_restart_required) {
			frm.dashboard.set_headline_alert(__("A QGIS Server plugin changed: run <code>sudo systemctl restart qgis-server</code>, then Mark QGIS Server Restarted."), "orange");
			frm.add_custom_button(__("Mark QGIS Server Restarted"), () => frm.call("mark_restarted").then(() => frm.reload_doc()));
		}
		frm.add_custom_button(__("Browse QGIS Plugin Repository"), () => browse_repository(frm));
	},
});

function browse_repository(frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("QGIS Plugin Repository"),
		size: "large",
		fields: [
			{ fieldname: "query", fieldtype: "Data", label: __("Search"), description: __("Name, description or tag; the list is for the installed QGIS version.") },
			{ fieldname: "results", fieldtype: "HTML" },
		],
		primary_action_label: __("Search"),
		primary_action(values) {
			dialog.fields_dict.results.$wrapper.html(`<p class="text-muted">${__("Searching…")}</p>`);
			frappe.call({ method: GIS_PLUGIN_SVC + "search_repository", args: { query: values.query || "" } }).then((r) => {
				const rows = (r.message || []).map(
					(p, i) => `<tr><td><b>${frappe.utils.escape_html(p.name)}</b> ${frappe.utils.escape_html(p.version || "")}
						${p.experimental ? '<span class="indicator-pill orange">experimental</span>' : ""}
						${p.server ? '<span class="indicator-pill blue">server</span>' : ""}
						<div class="text-muted small">${frappe.utils.escape_html((p.description || "").slice(0, 200))}</div></td>
						<td><button class="btn btn-xs btn-default" data-i="${i}">${__("Register")}</button></td></tr>`
				);
				const $w = dialog.fields_dict.results.$wrapper;
				$w.html(rows.length ? `<table class="table table-condensed">${rows.join("")}</table>` : `<p class="text-muted">${__("Nothing found.")}</p>`);
				$w.find("button[data-i]").on("click", (e) => {
					const p = r.message[Number(e.currentTarget.dataset.i)];
					frappe
						.call({ method: GIS_PLUGIN_SVC + "register_from_repository", args: { name: p.name, download_url: p.download_url }, freeze: true, freeze_message: __("Downloading and analysing…") })
						.then((res) => {
							dialog.hide();
							frappe.set_route("Form", "GIS Plugin", res.message);
						});
				});
			});
		},
	});
	dialog.show();
}
