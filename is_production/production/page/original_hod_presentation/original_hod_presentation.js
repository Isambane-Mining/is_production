const ORIGINAL_HOD_FILTERS = [
    {
        fieldname: "end_date",
        label: __("End Date"),
        fieldtype: "Date",
        reqd: 1,
        default: frappe.datetime.get_today()
    },
    {
        fieldname: "site",
        label: __("Sites"),
        fieldtype: "MultiSelectList",
        reqd: 1,
        get_data: txt => frappe.db.get_link_options("Location", txt)
    },
    {
        fieldname: "summary_type",
        label: __("Summary Type"),
        fieldtype: "Select",
        options: [
            "Daily Summary",
            "Average Per Machine",
            "Weekly Summary",
            "Monthly Summary"
        ],
        default: "Average Per Machine",
        reqd: 1
    },
    {
        fieldname: "machine_scope",
        label: __("Machine Scope"),
        fieldtype: "Select",
        options: [
            "Production Machines",
            "Swing/Spare Machines",
            "Include Swing/Spare"
        ],
        default: "Include Swing/Spare",
        reqd: 1
    },
    {
        fieldname: "asset_ownership",
        label: __("Asset Ownership"),
        fieldtype: "Select",
        options: [
            "Isambane & Excavo Assets",
            "Suppliers Assets",
            "All Assets"
        ],
        default: "Isambane & Excavo Assets",
        reqd: 1
    },
    {
        fieldname: "au_target_filter",
        label: __("A & U Target"),
        fieldtype: "Select",
        options: ["100% A & U", "85% A & U"],
        default: "85% A & U",
        reqd: 1
    }
];

frappe.pages["original-hod-presentation"].on_page_load = function(wrapper) {
    wrapper.original_hod = new OriginalHODPage(wrapper);
};

frappe.pages["original-hod-presentation"].on_page_show = function(wrapper) {
    return wrapper.original_hod.show();
};

class OriginalHODPage {
    constructor(wrapper) {
        this.page = frappe.ui.make_app_page({
            parent: wrapper,
            title: __("Original HOD Presentation"),
            single_column: true
        });

        this.controls = {};
        this.version = 0;
        this.initialising = true;

        const filterRow = $('<div class="row"></div>')
            .appendTo(this.page.main);

        for (const definition of ORIGINAL_HOD_FILTERS) {
            const holder = $('<div class="col-sm-6 col-lg-3"></div>')
                .appendTo(filterRow);

            const control = frappe.ui.form.make_control({
                parent: holder,
                df: {
                    ...definition,
                    onchange: () => this.changed()
                },
                render_input: true
            });

            this.controls[definition.fieldname] = control;
        }

        this.status = $('<div class="text-muted"></div>')
            .appendTo(this.page.main);

        this.content = $('<div></div>')
            .appendTo(this.page.main);

        this.view = {
            page: {main: this.content},
            data: [],
            get_filter_value: name => {
                if (name === "start_date") {
                    const endDate = this.get_value("end_date");
                    return endDate
                        ? String(endDate).slice(0, 7) + "-01"
                        : null;
                }
                return this.get_value(name);
            }
        };

        this.page.set_primary_action(
            __("Refresh"),
            () => this.refresh()
        );

        this.downloadButton = this.page.add_inner_button(
            __("Download Presentation"),
            () => {
                if (!this.downloadButton.prop("disabled")) {
                    return window.OriginalHODPresentationShared.download(
                        this.view
                    );
                }
            }
        ).prop("disabled", true);


        // Handle tabs inside dynamically loaded Engineering dashboards.
        this.content.on(
            "click.originalHodTabs",
            ".daily-dashboard-tab-button",
            function(event) {
                event.preventDefault();

                const $button = $(this);
                const selectedTab = $button.attr("data-tab");

                if (!["hours", "au"].includes(selectedTab)) {
                    return;
                }

                const $dashboard = $button.closest(
                    ".hod-browser-au-dashboard-copy"
                );

                if (!$dashboard.length) {
                    return;
                }

                $dashboard.find(
                    ".daily-dashboard-tab-button"
                ).removeClass(
                    "btn-primary active"
                ).addClass("btn-default");

                $button.removeClass(
                    "btn-default"
                ).addClass("btn-primary active");

                $dashboard.find(
                    ".daily-dashboard-tab-panel"
                ).hide();

                $dashboard.find(
                    '.daily-dashboard-tab-panel[data-panel="' +
                    selectedTab +
                    '"]'
                ).show();
            }
        );

        this.ready = this.initialise();
    }

    async initialise() {
        await frappe.require(
            "/assets/is_production/js/original_hod_presentation_shared.js"
        );

        if (!window.OriginalHODPresentationShared) {
            throw new Error("Original HOD renderer failed to load.");
        }

        window.OriginalHODPresentationShared.initialise();

        for (const definition of ORIGINAL_HOD_FILTERS) {
            if (definition.default !== undefined) {
                await this.controls[definition.fieldname]
                    .set_value(definition.default);
            }
        }

        this.initialising = false;
    }

    get_value(name) {
        return this.controls[name]?.get_value();
    }

    get_values() {
        return Object.fromEntries(
            Object.keys(this.controls).map(name => [
                name,
                this.get_value(name)
            ])
        );
    }

    changed() {
        if (this.initialising) return;

        ++this.version;
        this.downloadButton.prop("disabled", true);
        this.content.empty();

        clearTimeout(this.refreshTimer);
        this.refreshTimer = setTimeout(() => this.refresh(), 300);
    }

    async show() {
        await this.ready;
        return this.refresh();
    }

    async refresh() {
        clearTimeout(this.refreshTimer);

        const version = ++this.version;
        const filters = this.get_values();
        // Historical backend requires a Start Date.
        // Keep this automatic and hidden from the Page filters.
        if (filters.end_date) {
            filters.start_date =
                String(filters.end_date).slice(0, 7) + "-01";
        }


        if (filters.end_date) {
            filters.start_date =
                String(filters.end_date).slice(0, 7) + "-01";
        }

        this.downloadButton.prop("disabled", true);
        this.content.empty();
        this.view.data = [];

        if (!filters.end_date ||
            !filters.site ||
            (Array.isArray(filters.site) && !filters.site.length)) {
            this.status.text(
                __("Select a site and the required dates.")
            );
            return;
        }

        this.status.text(__("Loading Original HOD Presentation..."));

        try {
            const response = await frappe.call({
                method:
                    "is_production.production.page." +
                    "original_hod_presentation." +
                    "original_hod_presentation.get_page_data",
                args: {filters: JSON.stringify(filters)}
            });

            if (version !== this.version) return;

            this.view.data = response.message?.rows || [];

            if (!this.view.data.length) {
                this.status.text(
                    __("No data matches the selected filters.")
                );
                return;
            }

            this.status.empty();

            await window.OriginalHODPresentationShared.render(
                this.view
            );

            if (version !== this.version) return;

            this.downloadButton.prop("disabled", false);
        } catch (error) {
            if (version !== this.version) return;
            console.error("Original HOD Presentation:", error);
            this.status.text(__("Unable to load Original HOD Presentation."));
            frappe.msgprint({
                title: __("Original HOD Presentation"),
                message: error.message || __("Unable to load report."),
                indicator: "red"
            });
        }
    }
}
