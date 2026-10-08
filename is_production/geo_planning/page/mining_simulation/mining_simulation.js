// Mining Simulation: the planner's simulator (www/mining_simulation.html) inside the desk.
// It runs in a frame so its full-screen layout, styles and keyboard shortcuts stay its own.
frappe.pages["mining-simulation"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Mining Simulation"),
		single_column: true,
	});

	if (frappe.model.can_create("Mining Simulation Project")) {
		page.add_inner_button(__("Import Simulation File"), () =>
			frappe.require("/assets/is_production/mining_simulation/desk_import.js", () =>
				is_production.mining_simulation.import_new((name) => wrapper.open_project(name)),
			),
		);
	}
	page.add_inner_button(__("Projects"), () => frappe.set_route("List", "Mining Simulation Project"));
	page.add_inner_button(__("Open Project Record"), () => {
		// the simulator remembers the project it has open (it may have switched inside the frame)
		let open = wrapper.project;
		try {
			open = localStorage.getItem("rollover.lastProject") || open;
		} catch (e) {
			// storage blocked
		}
		if (open) frappe.set_route("Form", "Mining Simulation Project", open);
		else frappe.set_route("List", "Mining Simulation Project");
	});

	const $frame = $(
		`<iframe class="mining-simulation-frame" title="${__("Mining Simulation")}"
			style="display:block;width:100%;border:0;border-radius:var(--border-radius-md);background:var(--bg-color)"></iframe>`,
	).appendTo(page.main);
	page.main.css({ padding: 0 });

	const fit = () => {
		const top = $frame[0].getBoundingClientRect().top + window.scrollY;
		$frame.css("height", Math.max(480, window.innerHeight - top - 16) + "px");
	};
	$(window).on("resize.mining_simulation", frappe.utils.debounce(fit, 100));
	wrapper.open_project = (project) => {
		wrapper.project = project || null;
		const url = "/mining_simulation" + (project ? "?project=" + encodeURIComponent(project) : "");
		$frame.attr("src", url);
		wrapper.loaded = true;
	};
	wrapper.fit = fit;
	wrapper.$frame = $frame;
};

frappe.pages["mining-simulation"].on_page_show = function (wrapper) {
	const project = frappe.route_options && frappe.route_options.project;
	frappe.route_options = null;
	// load once, and again only when another project is asked for (keeps unsaved view state)
	if (!wrapper.loaded || (project && project !== wrapper.project)) wrapper.open_project(project);
	setTimeout(wrapper.fit, 0);
};
