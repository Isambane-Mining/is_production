// Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
// For license information, please see license.txt

frappe.pages["diesel-cap"].on_page_load = function (wrapper) {
	wrapper.diesel_cap = new frappe.DieselCapPage(wrapper);
};

frappe.DieselCapPage = class DieselCapPage {
	constructor(wrapper) {
		this.page = frappe.ui.make_app_page({parent: wrapper, title: __("Diesel Cap"), single_column: true});
		this.request_id = 0;
		this.initializing = true;
		this.$root = $(`<div class="diesel-cap-page">
			<section class="dc-hero">
				<div class="dc-hero-icon" aria-hidden="true">${frappe.utils.icon("assets", "lg")}</div>
				<div><div class="dc-brand">ISAMBANE MINING · ${__("PRODUCTION")}</div>
				<h1>${__("Diesel Cap")}</h1>
				<p>${__("Diesel consumption, production and fuel cap monitoring.")}</p></div>
			</section>
			<section class="dc-filters dc-panel" aria-label="${__("Report filters")}">
				<div data-filter="start_date"></div><div data-filter="end_date"></div><div data-filter="site"></div>
				<button type="button" class="btn btn-primary dc-refresh">${frappe.utils.icon("refresh", "sm")} ${__("Refresh / Rebuild")}</button>
			</section>
			<section class="dc-kpis" aria-label="${__("Report summary")}"></section>
			<section class="dc-panel dc-results">
				<div class="dc-table-heading"><h2>${__("Site overview")}</h2><span class="dc-row-count text-muted"></span></div>
				<div class="dc-status" role="status" aria-live="polite"></div>
				<div class="dc-table" aria-label="${__("Diesel Cap Report data")}"></div>
				<div class="dc-report-message"></div>
				<footer class="dc-footer"><span>${__("For comparison, use >5, <10 or =324. For ranges, use 5:10 (for values between 5 & 10).")}</span><span class="dc-execution"></span></footer>
			</section>
		</div>`).appendTo(this.page.main);
		this.kpis = [
			["actual_bcm", __("Total Month to Date BCM"), "BCM", "project"],
			["total_tallies_cubes", __("Total Tallies Cubes"), "m³", "stock"],
			["total_diesel_litres", __("Total Diesel Litres"), "L", "assets"],
			["diesel_cap", __("Diesel Cap"), "L/BCM", "dashboard"],
			["tallies_fuel_cap", __("Tallies Fuel Cap"), "L/m³", "dashboard"],
		];
		for (const [field, label, unit, icon] of this.kpis) {
			$(`<article class="dc-kpi"><div class="dc-kpi-icon" aria-hidden="true">${frappe.utils.icon(icon, "lg")}</div>
				<div><div class="dc-kpi-label">${label}</div><div class="dc-kpi-value"><span data-kpi="${field}">—</span> <small>${unit}</small></div></div></article>`)
				.appendTo(this.$root.find(".dc-kpis"));
		}
		this.$root.find('[data-kpi="diesel_cap"], [data-kpi="tallies_fuel_cap"]').closest(".dc-kpi")
			.attr("title", __("Total diesel divided by total volume for the selected sites. Zero when volume is zero."));
		this.controls = {};
		const fields = [
			{fieldname: "start_date", label: __("Start Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_start()},
			{fieldname: "end_date", label: __("End Date"), fieldtype: "Date", reqd: 1, default: frappe.datetime.month_end()},
			{fieldname: "site", label: __("Site"), fieldtype: "Link", options: "Location"},
		];
		for (const df of fields) {
			this.controls[df.fieldname] = frappe.ui.form.make_control({
				parent: this.$root.find(`[data-filter="${df.fieldname}"]`),
				df: {...df, onchange: () => {if (!this.initializing) this.refresh();}}, render_input: true,
			});
		}
		this.$root.find(".dc-refresh").on("click", () => this.refresh());
		this.page.add_menu_item(__("Open Diesel Cap Report"), () => {
			frappe.route_options = this.get_filters();
			frappe.set_route("query-report", "Diesel Cap Report");
		});
		this.ready = this.initialize(fields);
	}

	async initialize(fields) {
		await Promise.all(fields.filter(df => df.default).map(df => this.controls[df.fieldname].set_value(df.default)));
		this.initializing = false;
		await this.refresh();
	}

	get_filters() {
		return Object.fromEntries(Object.entries(this.controls).map(([name, control]) => [name, control.get_value()]));
	}

	async ensure_table() {
		await frappe.require(["report.bundle.js", "report.bundle.css"]);
	}

	async refresh() {
		const request = ++this.request_id;
		const filters = this.get_filters();
		this.clear_results();
		this.set_busy(false);
		if (!filters.start_date || !filters.end_date) {
			this.show_status(__("Start Date and End Date are required."));
			return;
		}
		if (filters.start_date > filters.end_date) {
			this.show_status(__("Start Date cannot be after End Date"));
			return;
		}
		this.set_busy(true);
		this.show_status(__("Loading Diesel Cap Report…"));
		try {
			const response = await frappe.call({
				method: "frappe.desk.query_report.run",
				args: {report_name: "Diesel Cap Report", filters, ignore_prepared_report: 1},
			});
			if (request !== this.request_id) return;
			if (response.exc || !response.message) throw new Error("Report unavailable");
			await this.ensure_table();
			if (request !== this.request_id) return;
			this.render(response.message);
		} catch (error) {
			if (request !== this.request_id) return;
			this.clear_results();
			this.show_status(__("Unable to load Diesel Cap Report. Check your filters and report access, then try Refresh / Rebuild."), true);
		} finally {
			if (request === this.request_id) this.set_busy(false);
		}
	}

	set_busy(busy) {
		this.$root.attr("aria-busy", String(busy));
		this.$root.find(".dc-refresh").prop("disabled", busy);
	}

	show_status(message, error = false) {
		this.$root.find(".dc-status").text(message).toggleClass("text-danger", error).toggle(Boolean(message));
	}

	clear_results() {
		if (this.datatable) this.datatable.destroy();
		this.datatable = null;
		this.$root.find(".dc-table, .dc-execution, .dc-row-count, .dc-report-message").empty();
		this.$root.find("[data-kpi]").text("—");
	}

	summarize(rows) {
		const totals = {actual_bcm: 0, total_tallies_cubes: 0, total_diesel_litres: 0};
		for (const row of rows) {
			for (const key of Object.keys(totals)) totals[key] += Number(row[key]) || 0;
		}
		// Ratios follow the report's formulas, applied to the returned totals.
		return {...totals,
			diesel_cap: totals.actual_bcm ? totals.total_diesel_litres / totals.actual_bcm : 0,
			tallies_fuel_cap: totals.total_tallies_cubes ? totals.total_diesel_litres / totals.total_tallies_cubes : 0,
		};
	}

	render(report) {
		const rows = report.result || [];
		const summary = this.summarize(rows);
		for (const [field] of this.kpis) {
			const column = report.columns.find(column => column.fieldname === field);
			this.$root.find(`[data-kpi="${field}"]`).html(frappe.format(summary[field],
				{fieldtype: "Float", precision: column?.precision ?? 2}, {always_show_decimals: true}));
		}
		this.show_status(rows.length ? "" : __("No data for the selected filters."));
		this.$root.find(".dc-row-count").text(__("{0} sites", [rows.length]));
		this.$root.find(".dc-execution").text(__("Execution Time: {0} sec", [report.execution_time || 0]));
		// Report messages are trusted server-rendered HTML, as on the standard report.
		this.$root.find(".dc-report-message").html(report.message || "");
		if (!rows.length) return;
		const columns = report.columns.map(column => ({
			...column, id: column.fieldname, name: column.label, editable: false,
			align: ["Float", "Int", "Currency"].includes(column.fieldtype) ? "right" : "left",
			compareValue: column.fieldtype === "Date" ? (cell, keyword) => {
				if (!cell.content || keyword.length !== 10) return null;
				return [+frappe.datetime.str_to_obj(cell.content), +frappe.datetime.user_to_obj(keyword)];
			} : null,
			format: value => frappe.format(value, column, {for_print: false, always_show_decimals: true}),
		}));
		this.datatable = new window.DataTable(this.$root.find(".dc-table")[0], {
			columns, data: rows.map(row => report.columns.map(column => row[column.fieldname])),
			inlineFilters: true, layout: "fixed", cellHeight: 44, serialNoColumn: true,
			checkboxColumn: false, noDataMessage: __("No matching rows"),
			language: frappe.boot.lang, translations: frappe.utils.datatable.get_translations(),
			direction: frappe.utils.is_rtl() ? "rtl" : "ltr",
		});
	}
};
