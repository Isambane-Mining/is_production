// Import of .rollover.json project files into Mining Simulation Project (desk form and list).
(() => {
	const API = "is_production.geo_planning.services.mining_simulation_service.";

	frappe.provide("is_production.mining_simulation");

	// Upload a .rollover.json file and check what is in it.
	is_production.mining_simulation.pick_file = function (on_checked) {
		new frappe.ui.FileUploader({
			allow_multiple: false,
			make_attachments_public: false,
			restrictions: { allowed_file_types: [".json"] },
			on_success(file) {
				frappe
					.call({
						method: API + "check_import_file",
						args: { file_url: file.file_url },
						freeze: true,
						freeze_message: __("Reading the project file…"),
					})
					.then((r) => on_checked(file.file_url, r.message));
			},
		});
	};

	is_production.mining_simulation.run_import = function (file_url, mode, target) {
		return frappe
			.call({
				method: API + "import_simulation_file",
				args: { file_url, mode, target },
				freeze: true,
				freeze_message: __("Importing the project…"),
			})
			.then((r) => {
				frappe.show_alert({ message: __("Project imported"), indicator: "green" });
				return r.message;
			});
	};

	// From the list: import as a new project, or replace the project the file came from.
	// `on_done(name)` runs after the import; by default the project form opens.
	is_production.mining_simulation.import_new = function (on_done) {
		is_production.mining_simulation.pick_file((file_url, info) => {
			const open = on_done || ((name) => frappe.set_route("Form", "Mining Simulation Project", name));
			if (!info.existing) {
				is_production.mining_simulation.run_import(file_url, "new").then(open);
				return;
			}
			const d = new frappe.ui.Dialog({
				title: __("Project already imported"),
				fields: [
					{
						fieldtype: "HTML",
						options: `<p>${__("This file is project {0}, which is already here as {1}.", [
							frappe.utils.escape_html(info.project_name || ""),
							info.existing.bold(),
						])}</p>`,
					},
				],
				primary_action_label: __("Replace It"),
				primary_action() {
					d.hide();
					is_production.mining_simulation.run_import(file_url, "replace", info.existing).then(open);
				},
				secondary_action_label: __("Import as a Copy"),
				secondary_action() {
					d.hide();
					is_production.mining_simulation.run_import(file_url, "new").then(open);
				},
			});
			d.show();
		});
	};
})();
