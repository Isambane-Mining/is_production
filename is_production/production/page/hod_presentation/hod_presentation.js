frappe.pages["hod-presentation"].on_page_load = function (wrapper) {
    const page = frappe.ui.make_app_page({
        parent: wrapper,
        title: __("HOD Presentation"),
        single_column: true
    });

    function previousSunday() {
        const date = frappe.datetime.str_to_obj(
            frappe.datetime.get_today()
        );
        const daysBack = date.getDay() || 7;
        date.setDate(date.getDate() - daysBack);
        return frappe.datetime.obj_to_str(date);
    }

    const definitions = [
        {
            fieldname: "end_date",
            label: __("End Date"),
            fieldtype: "Date",
            reqd: 1,
            default: previousSunday()
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
            default: "Average Per Machine"
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
            default: "Include Swing/Spare"
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
            default: "Isambane & Excavo Assets"
        },
        {
            fieldname: "au_target_filter",
            label: __("A & U Target"),
            fieldtype: "Select",
            options: ["100% A & U", "85% A & U"],
            default: "85% A & U"
        }
    ];

    const controls = {};
    let autoLoadTimer = null;
    let filtersReady = false;

    function scheduleAutoLoad() {
        if (!filtersReady) return;

        clearTimeout(autoLoadTimer);

        autoLoadTimer = setTimeout(() => {
            const filters = getFilters();
            const sites = filters.site;

            if (
                filters.end_date &&
                sites &&
                (!Array.isArray(sites) || sites.length)
            ) {
                refresh();
            }
        }, 500);
    }

    definitions.forEach(definition => {
        controls[definition.fieldname] = page.add_field({
            ...definition,
            change: scheduleAutoLoad
        });
    });

    filtersReady = true;

    const $body = $('<div class="hod-page-content"></div>')
        .appendTo(page.main);

    function getFilters() {
        const filters = {};
        for (const [name, control] of Object.entries(controls)) {
            filters[name] = control.get_value();
        }
        return filters;
    }

    let currentAdapter = null;

    async function refresh() {
        currentAdapter = null;
        const filters = getFilters();

        if (!filters.end_date || !filters.site ||
            (Array.isArray(filters.site) && !filters.site.length)) {
            frappe.msgprint(__("Select at least one site and an End Date."));
            return;
        }

        $body.empty().append(
            $('<p class="text-muted"></p>').text(__("Loading HOD data..."))
        );

        try {
            const response = await frappe.call({
                method:
                    "is_production.production.page.hod_presentation." +
                    "hod_presentation.get_page_data",
                args: {filters: JSON.stringify(filters)},
                freeze: false
            });

            const rows = response.message?.rows || [];
            $body.empty();

            if (!rows.length) {
                $body.append(
                    $('<p class="text-muted"></p>')
                        .text(__("No HOD data available for these filters."))
                );
                return;
            }

            await frappe.require(
                "/assets/is_production/js/hod_presentation_shared.js"
            );

            const dashboard = window.HODPresentationShared;

            if (!dashboard) {
                throw new Error("HOD dashboard asset did not initialise.");
            }

            const adapter = {
                page: {main: $body},
                data: rows,
                raw_data: {result: rows},
                get_filter_value(fieldname) {
                    return controls[fieldname]?.get_value();
                }
            };

            dashboard.injectStyles();
            dashboard.bindTabs(adapter);
            dashboard.render(adapter);

            currentAdapter = adapter;

        } catch (error) {
            $body.empty();
            console.error("HOD Page data request failed", error);
            frappe.msgprint({
                title: __("HOD Presentation"),
                indicator: "red",
                message: __("Could not load HOD data. Check your filters and permissions.")
            });
        }
    }

    page.set_primary_action(__("Load Presentation"), refresh);

    page.add_inner_button(__("Download Presentation"), async () => {
        if (!currentAdapter || !window.HODPresentationShared) {
            frappe.msgprint(
                __("Load the HOD Presentation before downloading.")
            );
            return;
        }

        await window.HODPresentationShared.download(currentAdapter);
    });
};
