frappe.query_reports["Productivity"] = {
    "filters": [
        {
            "fieldname": "start_date",
            "label": __("Start Date"),
            "fieldtype": "Date",
            "reqd": 1
        },
        {
            "fieldname": "end_date",
            "label": __("End Date"),
            "fieldtype": "Date",
            "reqd": 1
        },
        {
            "fieldname": "site",
            "label": __("Site"),
            "fieldtype": "Link",
            "options": "Location",
            "reqd": 1
        },
        {
            "fieldname": "shift",
            "label": __("Shift"),
            "fieldtype": "Select",
            "options": "\nDay\nNight\nMorning\nAfternoon"
        },
        {
            "fieldname": "machine_type",
            "label": __("Machine Type"),
            "fieldtype": "Select",
            "options": "\nExcavator\nDozer\nADT"
        },
        {
            fieldname: "asset",
            label: __("Asset"),
            fieldtype: "Link",
            options: "Asset",

            get_query: function () {
                const allowedCategories = [
                    "Excavator",
                    "Dozer",
                    "ADT"
                ];

                const machineType =
                    frappe.query_report
                        .get_filter_value(
                            "machine_type"
                        );

                const site =
                    frappe.query_report
                        .get_filter_value(
                            "site"
                        );

                const filters = {};

                if (
                    machineType &&
                    allowedCategories.includes(
                        machineType
                    )
                ) {
                    filters.asset_category =
                        machineType;
                } else {
                    filters.asset_category = [
                        "in",
                        allowedCategories
                    ];
                }

                if (site) {
                    filters.location = site;
                }

                return {
                    filters: filters
                };
            }
        },
        {
            fieldname: "bcm_basis",
            label: __("BCM Basis"),
            fieldtype: "Select",
            options: "Tallies BCMs\nActual BCMs",
            default: "Tallies BCMs"
        },
        {
            fieldname: "summary_view",
            label: __("Summary"),
            fieldtype: "Select",
            options: "Summary Per Machine\nHours and Material",
            default: "Summary Per Machine"
        }
    ],

    // 🔴 Highlight invalid rows (0 hours or output)
    formatter: function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {
        // ====================================================
        // PRODUCTIVITY FLAT MACHINE + MATERIAL FORMATTER
        //
        // Keep formatter deliberately simple.
        // The previous report was tree-based through "indent".
        // The new report is a normal flat table.
        // ====================================================

        let formatted_value;

        try {
            formatted_value = default_formatter(
                value,
                row,
                column,
                data
            );
        } catch (error) {
            console.error(
                "Productivity formatter error:",
                error,
                {
                    value: value,
                    row: row,
                    column: column,
                    data: data
                }
            );

            formatted_value =
                value === null ||
                value === undefined
                    ? ""
                    : String(value);
        }

        // ====================================================
        // KOSI_PRODUCTIVITY_ZERO_DECIMAL_DISPLAY_V1
        //
        // Display report numeric values with 0 decimals.
        // This changes DISPLAY only.
        // Underlying calculations keep their precision.
        // ====================================================

        const zeroDecimalFields = [
            "working_hours",
            "tallies_bcm",
            "output",
            "productivity",
            "hauling_distance_m"
        ];

        if (
            column &&
            zeroDecimalFields.includes(
                column.fieldname
            )
        ) {
            const rawNumber = Number(
                String(
                    value === null ||
                    value === undefined ||
                    value === ""
                        ? 0
                        : value
                ).replace(/,/g, "")
            );

            if (
                Number.isFinite(
                    rawNumber
                )
            ) {
                formatted_value =
                    Math.round(
                        rawNumber
                    ).toLocaleString(
                        "en-US",
                        {
                            minimumFractionDigits: 0,
                            maximumFractionDigits: 0
                        }
                    );
            }
        }

        // Category total rows:
        // Excavator / ADT / Dozer
        if (
            data &&
            (
                // KOSI_PRODUCTIVITY_CATEGORY_BOLD_ONLY_V1
                // KOSI_PRODUCTIVITY_TOTAL_FLEET_BOLD_V1
                (
                    Number(
                        data.is_category_total || 0
                    ) === 1
                    ||
                    Number(
                        data.is_total_fleet || 0
                    ) === 1
                )
            )
        ) {
            return (
                '<span style="' +
                'font-weight:700;' +
                'display:block;' +
                '">' +
                formatted_value +
                '</span>'
            );
        }

        return formatted_value;
    },

    get_datatable_options: function (options) {
        options = options || {};

        // ====================================================
        // FORCE NORMAL FLAT DATATABLE
        // ====================================================

        options.treeView = false;
        options.inlineFilters = false;

        return options;
    }
};


// ============================================================
// KOSI_PRODUCTIVITY_PRINT_SCREENSHOT_V1
//
// Adds a clean Print / Screenshot preview to Productivity.
//
// - Uses CURRENT report filters
// - Uses CURRENT visible report columns
// - Uses CURRENT report data
// - Keeps category headings bold
// - Keeps Total Fleet bold
// - Numbers display at 0 decimals
// - Works with Tallies BCMs
// - Works with Actual BCMs
// - Works with Summary Per Machine
// - Works with Hours and Material
//
// No backend calculation is changed.
// ============================================================


function productivity_print_escape(value) {

    if (
        value === null
        || value === undefined
    ) {
        return "";
    }

    return String(value)
        .replace(
            /&/g,
            "&amp;"
        )
        .replace(
            /</g,
            "&lt;"
        )
        .replace(
            />/g,
            "&gt;"
        )
        .replace(
            /"/g,
            "&quot;"
        )
        .replace(
            /'/g,
            "&#039;"
        );
}


function productivity_print_number(value) {

    if (
        value === null
        || value === undefined
        || value === ""
    ) {
        return "";
    }

    const number = Number(
        String(value).replace(
            /,/g,
            ""
        )
    );

    if (
        !Number.isFinite(
            number
        )
    ) {
        return productivity_print_escape(
            value
        );
    }

    return Math.round(
        number
    ).toLocaleString(
        "en-US",
        {
            minimumFractionDigits: 0,
            maximumFractionDigits: 0
        }
    );
}


function productivity_print_format_cell(
    fieldname,
    value
) {

    const numeric_fields = [
        "working_hours",
        "output",
        "tallies_bcm",
        "adjusted_bcm",
        "productivity",
        "hauling_distance_m"
    ];

    if (
        numeric_fields.includes(
            fieldname
        )
    ) {
        return productivity_print_number(
            value
        );
    }

    return productivity_print_escape(
        value
    );
}


function productivity_print_columns(
    report,
    filters
) {

    let columns = (
        report.columns
        || []
    )
        .filter(
            function (column) {

                if (!column) {
                    return false;
                }

                const fieldname = (
                    column.fieldname
                    || column.id
                    || ""
                );

                if (!fieldname) {
                    return false;
                }

                // Internal reconciliation column must never
                // appear in Actual BCM view.
                if (
                    fieldname
                    === "tallies_bcm"
                ) {
                    return false;
                }

                return !column.hidden;
            }
        )
        .map(
            function (column) {

                return {
                    fieldname:
                        column.fieldname
                        || column.id,

                    label:
                        column.label
                        || column.name
                        || column.fieldname
                        || column.id
                };
            }
        );


    // --------------------------------------------------------
    // FALLBACK
    //
    // Used only if DataTable columns are unavailable.
    // --------------------------------------------------------

    if (!columns.length) {

        const bcm_label = (
            filters.bcm_basis
            === "Actual BCMs"
        )
            ? "Adjusted BCM"
            : "Tallies BCMs";

        columns = [
            {
                fieldname: "label",
                label: "Label"
            },
            {
                fieldname: "working_hours",
                label: "Working Hours"
            },
            {
                fieldname: "output",
                label: bcm_label
            },
            {
                fieldname: "productivity",
                label: "Productivity (BCM/hr)"
            },
            {
                fieldname: "material",
                label: "Material"
            },
            {
                fieldname: "hauling_distance_m",
                label: "Hauling Distance (M)"
            }
        ];
    }

    return columns;
}


function productivity_print_filter_html(
    filters
) {

    const labels = {
        start_date:
            "Start Date",

        end_date:
            "End Date",

        site:
            "Site",

        shift:
            "Shift",

        machine_type:
            "Machine Type",

        asset:
            "Asset",

        bcm_basis:
            "BCM Basis",

        summary_view:
            "Summary"
    };


    const items = [];


    Object.keys(
        labels
    ).forEach(
        function (fieldname) {

            const value = (
                filters[fieldname]
                || ""
            );

            if (!value) {
                return;
            }

            items.push(
                `
                <div class="prod-print-filter">
                    <div class="prod-print-filter-label">
                        ${productivity_print_escape(
                            labels[fieldname]
                        )}
                    </div>

                    <div class="prod-print-filter-value">
                        ${productivity_print_escape(
                            value
                        )}
                    </div>
                </div>
                `
            );
        }
    );


    return items.join(
        ""
    );
}


function productivity_print_summary_html(
    report
) {

    const summary = (
        report.report_summary
        || report.summary
        || []
    );


    if (
        !Array.isArray(summary)
        || !summary.length
    ) {
        return "";
    }


    const cards = summary.map(
        function (item) {

            const label = (
                item.label
                || ""
            );

            const value = (
                item.value
                ?? ""
            );


            return `
                <div class="prod-print-kpi">
                    <div class="prod-print-kpi-label">
                        ${productivity_print_escape(
                            label
                        )}
                    </div>

                    <div class="prod-print-kpi-value">
                        ${productivity_print_escape(
                            value
                        )}
                    </div>
                </div>
            `;
        }
    );


    return `
        <div class="prod-print-kpis">
            ${cards.join("")}
        </div>
    `;
}


function productivity_print_table_html(
    report,
    columns
) {

    const rows = (
        report.data
        || []
    );


    if (!rows.length) {

        return `
            <div class="prod-print-empty">
                No report data to print.
            </div>
        `;
    }


    const head = columns.map(
        function (column) {

            return `
                <th>
                    ${productivity_print_escape(
                        column.label
                    )}
                </th>
            `;
        }
    ).join(
        ""
    );


    const body = rows.map(
        function (row) {

            const is_bold = (
                Number(
                    row.is_category_total
                    || 0
                ) === 1
                ||
                Number(
                    row.is_total_fleet
                    || 0
                ) === 1
            );


            const row_class = (
                is_bold
                ? "prod-print-bold-row"
                : ""
            );


            const cells = columns.map(
                function (column) {

                    const value = (
                        row[
                            column.fieldname
                        ]
                    );

                    const numeric = [
                        "working_hours",
                        "output",
                        "productivity",
                        "hauling_distance_m"
                    ].includes(
                        column.fieldname
                    );


                    return `
                        <td class="${
                            numeric
                                ? "prod-print-number"
                                : ""
                        }">
                            ${
                                productivity_print_format_cell(
                                    column.fieldname,
                                    value
                                )
                            }
                        </td>
                    `;
                }
            ).join(
                ""
            );


            return `
                <tr class="${row_class}">
                    ${cells}
                </tr>
            `;
        }
    ).join(
        ""
    );


    return `
        <table class="prod-print-table">
            <thead>
                <tr>
                    ${head}
                </tr>
            </thead>

            <tbody>
                ${body}
            </tbody>
        </table>
    `;
}


function open_productivity_print_preview(
    report
) {

    const filters = (
        report.get_filter_values
            ? report.get_filter_values()
            : (
                frappe.query_report
                    ? frappe.query_report.get_filter_values()
                    : {}
            )
    ) || {};


    const columns = (
        productivity_print_columns(
            report,
            filters
        )
    );


    const filter_html = (
        productivity_print_filter_html(
            filters
        )
    );


    const summary_html = (
        productivity_print_summary_html(
            report
        )
    );


    const table_html = (
        productivity_print_table_html(
            report,
            columns
        )
    );


    const generated_at = (
        new Date()
            .toLocaleString()
    );


    const print_window = window.open(
        "",
        "_blank",
        "width=1500,height=900,scrollbars=yes,resizable=yes"
    );


    if (!print_window) {

        frappe.msgprint(
            __(
                "Please allow pop-ups to open the print preview."
            )
        );

        return;
    }


    print_window.document.open();


    print_window.document.write(
        `
<!DOCTYPE html>

<html>

<head>

    <meta charset="utf-8">

    <title>
        Productivity Report
    </title>

    <style>

        * {
            box-sizing: border-box;
        }


        body {
            margin: 0;
            padding: 20px;
            font-family:
                Arial,
                Helvetica,
                sans-serif;
            color: #1f2937;
            background: #ffffff;
            font-size: 12px;
        }


        .prod-print-actions {
            position: sticky;
            top: 0;
            z-index: 100;
            display: flex;
            gap: 8px;
            align-items: center;
            padding: 10px;
            margin: -20px -20px 18px -20px;
            background: #f8f9fa;
            border-bottom: 1px solid #d1d5db;
        }


        .prod-print-actions button {
            border: 1px solid #b9bec5;
            border-radius: 6px;
            background: #ffffff;
            padding: 7px 12px;
            font-size: 13px;
            cursor: pointer;
        }


        .prod-print-actions button:hover {
            background: #eef2f7;
        }


        .prod-print-help {
            margin-left: 8px;
            color: #6b7280;
            font-size: 11px;
        }


        .prod-print-title {
            font-size: 24px;
            font-weight: 700;
            margin-bottom: 4px;
        }


        .prod-print-subtitle {
            color: #6b7280;
            margin-bottom: 18px;
        }


        .prod-print-filters {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 18px;
        }


        .prod-print-filter {
            border: 1px solid #d7dce2;
            border-radius: 6px;
            padding: 7px 10px;
            min-width: 130px;
            background: #fafafa;
        }


        .prod-print-filter-label {
            color: #6b7280;
            font-size: 10px;
            text-transform: uppercase;
            margin-bottom: 2px;
        }


        .prod-print-filter-value {
            font-size: 12px;
            font-weight: 600;
        }


        .prod-print-kpis {
            display: flex;
            gap: 15px;
            margin-bottom: 18px;
            flex-wrap: wrap;
        }


        .prod-print-kpi {
            min-width: 230px;
            border: 1px solid #d7dce2;
            border-radius: 8px;
            padding: 12px 14px;
        }


        .prod-print-kpi-label {
            color: #6b7280;
            font-size: 11px;
            margin-bottom: 5px;
        }


        .prod-print-kpi-value {
            font-size: 22px;
            font-weight: 700;
        }


        .prod-print-table {
            width: 100%;
            border-collapse: collapse;
            border-spacing: 0;
        }


        .prod-print-table thead {
            display: table-header-group;
        }


        .prod-print-table th {
            background: #f1f3f5;
            border: 1px solid #cfd4da;
            padding: 7px 8px;
            text-align: left;
            font-weight: 700;
            white-space: nowrap;
        }


        .prod-print-table td {
            border: 1px solid #d9dde2;
            padding: 6px 8px;
            vertical-align: middle;
        }


        .prod-print-number {
            text-align: right;
        }


        .prod-print-bold-row td {
            font-weight: 700;
            background: #f8f9fa;
        }


        .prod-print-empty {
            padding: 30px;
            text-align: center;
            color: #6b7280;
            border: 1px solid #d7dce2;
        }


        .prod-print-footer {
            margin-top: 12px;
            color: #6b7280;
            font-size: 10px;
        }


        @page {
            size: landscape;
            margin: 10mm;
        }


        @media print {

            body {
                padding: 0;
                font-size: 9px;
            }


            .prod-print-actions {
                display: none !important;
            }


            .prod-print-title {
                font-size: 18px;
            }


            .prod-print-filter {
                padding: 4px 6px;
            }


            .prod-print-kpi {
                padding: 7px 9px;
            }


            .prod-print-kpi-value {
                font-size: 16px;
            }


            .prod-print-table th,
            .prod-print-table td {
                padding: 4px 5px;
            }


            tr {
                page-break-inside: avoid;
            }
        }

    </style>

</head>


<body>

    <div class="prod-print-actions">

        <button
            type="button"
            onclick="window.print()"
        >
            🖨 Print / Save as PDF
        </button>

        <button
            type="button"
            onclick="window.close()"
        >
            Close
        </button>

        <span class="prod-print-help">
            You can also take a screenshot from this clean preview.
        </span>

    </div>


    <div class="prod-print-title">
        Productivity
    </div>


    <div class="prod-print-subtitle">
        Generated ${productivity_print_escape(
            generated_at
        )}
    </div>


    <div class="prod-print-filters">
        ${filter_html}
    </div>


    ${summary_html}


    ${table_html}


    <div class="prod-print-footer">
        Isambane Mining - Productivity Report
    </div>

</body>

</html>
        `
    );


    print_window.document.close();


    print_window.focus();
}


function add_productivity_print_button(
    report
) {

    if (
        !report
        || !report.page
    ) {
        return;
    }


    if (
        report.__productivity_print_button_added
    ) {
        return;
    }


    report.__productivity_print_button_added = true;


    const button = (
        report.page.add_inner_button(
            __(
                "🖨 Print / Screenshot"
            ),
            function () {

                open_productivity_print_preview(
                    report
                );
            }
        )
    );


    if (button) {

        button.attr(
            "title",
            __(
                "Print or open a clean screenshot preview"
            )
        );

        button.addClass(
            "productivity-print-preview-btn"
        );
    }
}


// ============================================================
// PRESERVE ANY EXISTING PRODUCTIVITY ONLOAD
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_onload = (
        config.onload
    );


    config.onload = function (
        report
    ) {

        if (
            typeof previous_onload
            === "function"
        ) {

            previous_onload(
                report
            );
        }


        add_productivity_print_button(
            report
        );
    };

})();



// ============================================================
// KOSI_PRODUCTIVITY_MULTI_MONTHLY_PLANS_V1
//
// MULTIPLE MONTHLY PRODUCTION PLANNING FILTER
//
// - User can select one or more Monthly Production Planning docs
// - Options are restricted to selected Site
// - Start / End Date update automatically
// - Backend renders each selected production month separately
// ============================================================


function productivity_monthly_plan_values() {

    if (
        !frappe.query_report
    ) {
        return [];
    }

    let value = (
        frappe.query_report.get_filter_value(
            "monthly_production_plans"
        )
    );


    if (!value) {
        return [];
    }


    if (
        Array.isArray(
            value
        )
    ) {

        return value
            .map(
                function (item) {

                    if (
                        item
                        && typeof item
                        === "object"
                    ) {

                        return (
                            item.value
                            || item.name
                            || ""
                        );
                    }

                    return String(
                        item || ""
                    );
                }
            )
            .filter(
                Boolean
            );
    }


    if (
        typeof value
        === "string"
    ) {

        return value
            .split(
                /[\n,]+/
            )
            .map(
                function (item) {
                    return item.trim();
                }
            )
            .filter(
                Boolean
            );
    }


    return [];
}


async function productivity_sync_monthly_plan_dates() {

    if (
        !frappe.query_report
    ) {
        return;
    }


    const names = (
        productivity_monthly_plan_values()
    );


    if (!names.length) {
        return;
    }


    let rows = [];


    try {

        rows = await frappe.db.get_list(
            "Monthly Production Planning",
            {
                fields: [
                    "name",
                    "location",
                    "prod_month_start_date",
                    "prod_month_end_date"
                ],

                filters: {
                    name: [
                        "in",
                        names
                    ]
                },

                order_by:
                    "prod_month_start_date asc",

                limit:
                    Math.max(
                        names.length,
                        20
                    )
            }
        );

    } catch (error) {

        console.error(
            "Monthly Production Planning lookup failed:",
            error
        );

        frappe.msgprint(
            __(
                "Unable to load the selected Monthly Production plans."
            )
        );

        return;
    }


    if (!rows.length) {
        return;
    }


    // --------------------------------------------------------
    // ALL SELECTED PLANS MUST BELONG TO ONE SITE
    // --------------------------------------------------------

    const sites = [
        ...new Set(
            rows
                .map(
                    function (row) {
                        return String(
                            row.location
                            || ""
                        ).trim();
                    }
                )
                .filter(
                    Boolean
                )
        )
    ];


    if (
        sites.length > 1
    ) {

        frappe.msgprint({
            title:
                __(
                    "Monthly Production"
                ),

            indicator:
                "orange",

            message:
                __(
                    "Please select Monthly Production plans from one Site only."
                )
        });

        return;
    }


    const selected_site = (
        frappe.query_report.get_filter_value(
            "site"
        )
        || ""
    );


    if (
        sites.length === 1
        && selected_site
        && sites[0] !== selected_site
    ) {

        frappe.msgprint({
            title:
                __(
                    "Monthly Production"
                ),

            indicator:
                "orange",

            message:
                __(
                    "The selected Monthly Production plan does not belong to the selected Site."
                )
        });

        return;
    }


    // If Site is blank, populate it from the selected plan.
    if (
        !selected_site
        && sites.length === 1
    ) {

        await frappe.query_report.set_filter_value(
            "site",
            sites[0]
        );
    }


    const starts = rows
        .map(
            function (row) {
                return row.prod_month_start_date;
            }
        )
        .filter(
            Boolean
        )
        .sort();


    const ends = rows
        .map(
            function (row) {
                return row.prod_month_end_date;
            }
        )
        .filter(
            Boolean
        )
        .sort();


    if (
        starts.length
    ) {

        await frappe.query_report.set_filter_value(
            "start_date",
            starts[0]
        );
    }


    if (
        ends.length
    ) {

        await frappe.query_report.set_filter_value(
            "end_date",
            ends[
                ends.length - 1
            ]
        );
    }


    frappe.query_report.refresh();
}


// ============================================================
// ADD FILTER
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (
        !config
        || !Array.isArray(
            config.filters
        )
    ) {
        return;
    }


    const already_exists = (
        config.filters.some(
            function (filter) {

                return (
                    filter.fieldname
                    ===
                    "monthly_production_plans"
                );
            }
        )
    );


    if (!already_exists) {

        const monthly_filter = {

            fieldname:
                "monthly_production_plans",

            label:
                __(
                    "Monthly Production"
                ),

            fieldtype:
                "MultiSelectList",

            get_data: function (
                txt
            ) {

                const site = (
                    frappe.query_report
                        ? frappe.query_report.get_filter_value(
                            "site"
                        )
                        : ""
                );


                const filters = {};

                if (site) {
                    filters.location = site;
                }


                return frappe.db.get_link_options(
                    "Monthly Production Planning",
                    txt,
                    filters
                );
            },


            on_change: function () {

                productivity_sync_monthly_plan_dates();
            }
        };


        let insert_at = (
            config.filters.length
        );


        const asset_index = (
            config.filters.findIndex(
                function (filter) {

                    return (
                        filter.fieldname
                        === "asset"
                    );
                }
            )
        );


        if (
            asset_index >= 0
        ) {

            insert_at = (
                asset_index + 1
            );
        }


        config.filters.splice(
            insert_at,
            0,
            monthly_filter
        );
    }


    // --------------------------------------------------------
    // MAKE MONTHLY PLAN HEADER ROW BOLD
    // --------------------------------------------------------

    const previous_formatter = (
        config.formatter
    );


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        let formatted_value;


        if (
            typeof previous_formatter
            === "function"
        ) {

            formatted_value = (
                previous_formatter(
                    value,
                    row,
                    column,
                    data,
                    default_formatter
                )
            );

        } else {

            formatted_value = (
                default_formatter(
                    value,
                    row,
                    column,
                    data
                )
            );
        }


        // KOSI_PRODUCTIVITY_MONTHLY_HEADER_HIDE_ZERO_V1
        if (
            data
            &&
            Number(
                data.is_monthly_plan_header
                || 0
            ) === 1
        ) {

            const fieldname = String(
                (column && column.fieldname) || ""
            ).trim();

            if (
                [
                    "working_hours",
                    "output",
                    "tallies_bcm",
                    "adjusted_bcm",
                    "productivity",
                    "hauling_distance_m",
                ].includes(fieldname)
            ) {
                return "";
            }

            if (fieldname === "label") {
                return (
                    '<span style="' +
                    'font-weight:700;' +
                    'font-size:14px;' +
                    'display:block;' +
                    'padding:3px 0;' +
                    '">' +
                    formatted_value +
                    '</span>'
                );
            }

            if (fieldname === "material") {
                return (
                    '<span style="' +
                    'font-weight:600;' +
                    '">' +
                    formatted_value +
                    '</span>'
                );
            }

            return formatted_value || "";
        }


        return formatted_value;
    };

})();



// ============================================================
// KOSI_PRODUCTIVITY_STICKY_HEADER_V1
//
// Keep Productivity column headings visible while scrolling.
//
// Label
// Working Hours
// Adjusted BCM / Tallies BCMs
// Productivity (BCM/Hr)
// Material
// Hauling Distance (M)
//
// Does NOT change report calculations.
// ============================================================

function apply_productivity_sticky_header(report) {

    report = (
        report
        || frappe.query_report
    );

    if (
        !report
        || report.report_name !== "Productivity"
        || !report.page
        || !report.page.wrapper
    ) {
        return;
    }

    const wrapper = $(
        report.page.wrapper
    );

    wrapper.addClass(
        "productivity-sticky-header-report"
    );


    const style_id = (
        "productivity-sticky-header-style"
    );

    let style = document.getElementById(
        style_id
    );


    if (!style) {

        style = document.createElement(
            "style"
        );

        style.id = style_id;

        style.innerHTML = `

            /*
             * PRODUCTIVITY TABLE HEADER
             *
             * Keep the heading row fixed while scrolling.
             */

            .productivity-sticky-header-report
            .datatable
            .dt-header {
                position: sticky !important;
                top: 0 !important;
                z-index: 200 !important;
                background: #f8f9fa !important;
                box-shadow:
                    0 2px 3px rgba(0, 0, 0, 0.10) !important;
            }


            .productivity-sticky-header-report
            .datatable
            .dt-row-header {
                position: sticky !important;
                top: 0 !important;
                z-index: 201 !important;
                background: #f8f9fa !important;
            }


            .productivity-sticky-header-report
            .datatable
            .dt-cell--header {
                background: #f8f9fa !important;
                z-index: 202 !important;
            }


            .productivity-sticky-header-report
            .datatable
            .dt-cell--header
            .dt-cell__content {
                background: #f8f9fa !important;
                font-weight: 500 !important;
                white-space: nowrap !important;
            }


            /*
             * Prevent the header cells from changing width
             * independently from the report body.
             */
            .productivity-sticky-header-report
            .datatable
            .dt-header
            .dt-cell,
            .productivity-sticky-header-report
            .datatable
            .dt-row-header
            .dt-cell {
                flex-shrink: 0 !important;
            }

        `;

        document.head.appendChild(
            style
        );
    }


    /*
     * Reapply directly to the generated DataTable because
     * Frappe can rebuild the table after filter changes.
     */

    wrapper
        .find(
            ".datatable .dt-header"
        )
        .css({
            position:
                "sticky",

            top:
                "0px",

            "z-index":
                "200",

            background:
                "#f8f9fa"
        });


    wrapper
        .find(
            ".datatable .dt-row-header"
        )
        .css({
            position:
                "sticky",

            top:
                "0px",

            "z-index":
                "201",

            background:
                "#f8f9fa"
        });
}


// ============================================================
// PRESERVE EXISTING PRODUCTIVITY HOOKS
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );

    if (!config) {
        return;
    }


    const previous_onload = (
        config.onload
    );


    config.onload = function (
        report
    ) {

        if (
            typeof previous_onload
            === "function"
        ) {

            previous_onload.apply(
                this,
                arguments
            );
        }


        setTimeout(
            function () {
                apply_productivity_sticky_header(
                    report
                );
            },
            100
        );


        setTimeout(
            function () {
                apply_productivity_sticky_header(
                    report
                );
            },
            600
        );
    };


    const previous_after_render = (
        config.after_datatable_render
    );


    config.after_datatable_render = function () {

        if (
            typeof previous_after_render
            === "function"
        ) {

            previous_after_render.apply(
                this,
                arguments
            );
        }


        apply_productivity_sticky_header(
            frappe.query_report
        );


        setTimeout(
            function () {
                apply_productivity_sticky_header(
                    frappe.query_report
                );
            },
            100
        );
    };

})();


// Reapply when report page/filter controls rebuild the DataTable.

$(document).on(
    "page-change.productivityStickyHeader",
    function () {

        setTimeout(
            function () {

                if (
                    frappe.query_report
                    && frappe.query_report.report_name
                        === "Productivity"
                ) {

                    apply_productivity_sticky_header(
                        frappe.query_report
                    );
                }

            },
            400
        );
    }
);



// ============================================================
// KOSI_PRODUCTIVITY_FIXED_COLUMN_WIDTHS_V1
//
// Lock Productivity table widths permanently.
//
// DataTable columns:
//
// col 0 = Row number
// col 1 = Label
// col 2 = Working Hours
// col 3 = Tallies BCMs / Adjusted BCM
// col 4 = Productivity (BCM/Hr)
// col 5 = Material
// col 6 = Hauling Distance (M)
//
// Prevent Frappe from compressing the columns after:
//
// - Refresh
// - Rebuild
// - Monthly Production selection
// - Tallies / Actual BCM selection
// - Summary mode changes
// - Browser redraw
//
// ============================================================


function apply_productivity_fixed_column_widths(
    report
) {

    report = (
        report
        || frappe.query_report
    );


    if (
        !report
        || report.report_name !== "Productivity"
        || !report.page
        || !report.page.wrapper
    ) {
        return;
    }


    const wrapper = $(
        report.page.wrapper
    );


    wrapper.addClass(
        "productivity-fixed-columns-report"
    );


    const STYLE_ID = (
        "productivity-fixed-column-widths-style"
    );


    if (
        !document.getElementById(
            STYLE_ID
        )
    ) {

        const style = document.createElement(
            "style"
        );

        style.id = STYLE_ID;


        style.innerHTML = `

            /*
             * =================================================
             * PRODUCTIVITY FIXED TABLE WIDTH
             * =================================================
             */

            .productivity-fixed-columns-report
            .datatable {
                min-width: 1188px !important;
            }


            .productivity-fixed-columns-report
            .dt-scrollable {
                overflow-x: auto !important;
            }


            .productivity-fixed-columns-report
            .dt-row {
                min-width: 1188px !important;
                width: 1188px !important;
            }


            /*
             * ROW NUMBER
             */
            .productivity-fixed-columns-report
            .dt-cell--col-0 {
                width: 38px !important;
                min-width: 38px !important;
                max-width: 38px !important;
                flex: 0 0 38px !important;
                flex-basis: 38px !important;
            }


            /*
             * LABEL
             */
            .productivity-fixed-columns-report
            .dt-cell--col-1 {
                width: 360px !important;
                min-width: 360px !important;
                max-width: 360px !important;
                flex: 0 0 360px !important;
                flex-basis: 360px !important;
            }


            /*
             * WORKING HOURS
             */
            .productivity-fixed-columns-report
            .dt-cell--col-2 {
                width: 150px !important;
                min-width: 150px !important;
                max-width: 150px !important;
                flex: 0 0 150px !important;
                flex-basis: 150px !important;
            }


            /*
             * TALLIES BCMs / ADJUSTED BCM
             */
            .productivity-fixed-columns-report
            .dt-cell--col-3 {
                width: 150px !important;
                min-width: 150px !important;
                max-width: 150px !important;
                flex: 0 0 150px !important;
                flex-basis: 150px !important;
            }


            /*
             * PRODUCTIVITY
             */
            .productivity-fixed-columns-report
            .dt-cell--col-4 {
                width: 170px !important;
                min-width: 170px !important;
                max-width: 170px !important;
                flex: 0 0 170px !important;
                flex-basis: 170px !important;
            }


            /*
             * MATERIAL
             */
            .productivity-fixed-columns-report
            .dt-cell--col-5 {
                width: 150px !important;
                min-width: 150px !important;
                max-width: 150px !important;
                flex: 0 0 150px !important;
                flex-basis: 150px !important;
            }


            /*
             * HAULING DISTANCE
             */
            .productivity-fixed-columns-report
            .dt-cell--col-6 {
                width: 170px !important;
                min-width: 170px !important;
                max-width: 170px !important;
                flex: 0 0 170px !important;
                flex-basis: 170px !important;
            }


            /*
             * Do not allow header text to wrap.
             */
            .productivity-fixed-columns-report
            .dt-cell--header
            .dt-cell__content {
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: clip !important;
            }


            /*
             * Label needs enough space for:
             *
             * MONTHLY PRODUCTION: 18-05-2026 to 21-06-2026
             */
            .productivity-fixed-columns-report
            .dt-cell--col-1
            .dt-cell__content {
                white-space: nowrap !important;
                overflow: hidden !important;
                text-overflow: clip !important;
            }

        `;


        document.head.appendChild(
            style
        );
    }


    /*
     * Also apply the widths directly because DataTable can
     * regenerate inline widths when the report is refreshed.
     */

    const widths = {
        0: 38,
        1: 360,
        2: 150,
        3: 150,
        4: 170,
        5: 150,
        6: 170
    };


    Object.keys(
        widths
    ).forEach(
        function (
            column_number
        ) {

            const width = (
                widths[
                    column_number
                ]
            );


            wrapper
                .find(
                    ".dt-cell--col-"
                    + column_number
                )
                .css({
                    width:
                        width + "px",

                    "min-width":
                        width + "px",

                    "max-width":
                        width + "px",

                    flex:
                        "0 0 "
                        + width
                        + "px",

                    "flex-basis":
                        width + "px"
                });
        }
    );


    wrapper
        .find(
            ".datatable .dt-row"
        )
        .css({
            width:
                "1188px",

            "min-width":
                "1188px"
        });
}


// ============================================================
// KEEP WIDTHS AFTER EVERY DATATABLE REDRAW
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_after_render = (
        config.after_datatable_render
    );


    config.after_datatable_render = function () {

        if (
            typeof previous_after_render
            === "function"
        ) {

            previous_after_render.apply(
                this,
                arguments
            );
        }


        apply_productivity_fixed_column_widths(
            frappe.query_report
        );


        setTimeout(
            function () {

                apply_productivity_fixed_column_widths(
                    frappe.query_report
                );

            },
            100
        );


        setTimeout(
            function () {

                apply_productivity_fixed_column_widths(
                    frappe.query_report
                );

            },
            500
        );
    };


    const previous_onload = (
        config.onload
    );


    config.onload = function (
        report
    ) {

        if (
            typeof previous_onload
            === "function"
        ) {

            previous_onload.apply(
                this,
                arguments
            );
        }


        setTimeout(
            function () {

                apply_productivity_fixed_column_widths(
                    report
                );

            },
            250
        );


        setTimeout(
            function () {

                apply_productivity_fixed_column_widths(
                    report
                );

            },
            800
        );
    };

})();


// ============================================================
// EXTRA PROTECTION FOR REBUILD / FILTER REFRESH
// ============================================================

$(document).on(
    "page-change.productivityFixedColumns",
    function () {

        setTimeout(
            function () {

                if (
                    frappe.query_report
                    && frappe.query_report.report_name
                        === "Productivity"
                ) {

                    apply_productivity_fixed_column_widths(
                        frappe.query_report
                    );
                }

            },
            400
        );
    }
);


$(document).on(
    "click.productivityFixedColumns",
    ".query-report button, .query-report .btn",
    function () {

        setTimeout(
            function () {

                if (
                    frappe.query_report
                    && frappe.query_report.report_name
                        === "Productivity"
                ) {

                    apply_productivity_fixed_column_widths(
                        frappe.query_report
                    );
                }

            },
            600
        );
    }
);



// ============================================================
// KOSI_PRODUCTIVITY_FINAL_FIXED_COLUMNS_V1
//
// Backend now controls the real Frappe column widths.
//
// Disable the older CSS width function because its numeric
// .dt-cell--col-X mapping can differ depending on Frappe's
// row-number column.
//
// Sticky header remains active.
// ============================================================


if (
    typeof apply_productivity_fixed_column_widths
    === "function"
) {

    apply_productivity_fixed_column_widths = function (
        report
    ) {

        // Intentionally blank.
        //
        // Widths now come from Python column definitions.
        // Do not alter DataTable cell widths with CSS.
        return;
    };
}


// ============================================================
// FORCE FIXED DATATABLE LAYOUT
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_options = (
        config.get_datatable_options
    );


    config.get_datatable_options = function (
        options
    ) {

        let result = (
            options
            || {}
        );


        if (
            typeof previous_options
            === "function"
        ) {

            result = (
                previous_options.call(
                    this,
                    result
                )
                || result
            );
        }


        result.layout = "fixed";

        result.treeView = false;

        return result;
    };


    // --------------------------------------------------------
    // Only visual rules.
    // Do NOT define column widths here.
    // --------------------------------------------------------

    const style_id = (
        "productivity-final-fixed-columns-style"
    );


    if (
        !document.getElementById(
            style_id
        )
    ) {

        const style = document.createElement(
            "style"
        );


        style.id = style_id;


        style.innerHTML = `

            .productivity-sticky-header-report
            .datatable
            .dt-cell__content {
                white-space: nowrap !important;
            }


            /*
             * Label must display the complete monthly period:
             *
             * MONTHLY PRODUCTION:
             * 18-05-2026 to 21-06-2026
             */

            .productivity-sticky-header-report
            .datatable
            .dt-cell--col-1
            .dt-cell__content {
                white-space: nowrap !important;
                text-overflow: clip !important;
            }


            .productivity-sticky-header-report
            .datatable {
                overflow-x: auto !important;
            }

        `;


        document.head.appendChild(
            style
        );
    }

})();



// ============================================================
// KOSI_PRODUCTIVITY_INLINE_EDIT_AREA_V1
//
// Editable cells:
//
//     From Area
//     To Area
//     Hauling Distance (M)
//
// Edit -> click outside / Enter -> SAVE TO SERVER
// ============================================================


window.productivity_editable_rows = (
    window.productivity_editable_rows
    || {}
);


function productivity_inline_escape(
    value
) {

    return String(
        value === null
        || value === undefined
            ? ""
            : value
    )
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}


(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_formatter = (
        config.formatter
    );


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        const editable_fields = [
            "from_area",
            "to_area",
            "hauling_distance_m"
        ];


        if (
            data
            && Number(
                data.productivity_editable
                || 0
            ) === 1
            && editable_fields.includes(
                fieldname
            )
        ) {

            const row_key = String(
                data.productivity_edit_key
                || ""
            );


            if (row_key) {

                window.productivity_editable_rows[
                    row_key
                ] = data;
            }


            const raw_value = (
                data[fieldname]
                ?? ""
            );


            const input_type = (
                fieldname
                === "hauling_distance_m"
                    ? "number"
                    : "text"
            );


            const step = (
                fieldname
                === "hauling_distance_m"
                    ? ' step="1" min="0" '
                    : ""
            );


            return (
                '<input ' +
                'type="' + input_type + '" ' +
                step +
                'class="form-control productivity-inline-edit" ' +
                'data-row-key="' +
                    productivity_inline_escape(
                        row_key
                    ) +
                '" ' +
                'data-field="' +
                    productivity_inline_escape(
                        fieldname
                    ) +
                '" ' +
                'value="' +
                    productivity_inline_escape(
                        raw_value
                    ) +
                '" ' +
                'title="Edit and click outside to save" ' +
                'style="' +
                    'width:100%;' +
                    'height:28px;' +
                    'padding:3px 6px;' +
                    'font-size:12px;' +
                '">' 
            );
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };

})();


// ============================================================
// INLINE EDIT EVENTS
// ============================================================

$(document)
    .off(
        "mousedown.productivityInlineEdit " +
        "click.productivityInlineEdit",
        ".productivity-inline-edit"
    )
    .on(
        "mousedown.productivityInlineEdit " +
        "click.productivityInlineEdit",
        ".productivity-inline-edit",
        function (event) {

            event.stopPropagation();
        }
    );


$(document)
    .off(
        "keydown.productivityInlineEdit",
        ".productivity-inline-edit"
    )
    .on(
        "keydown.productivityInlineEdit",
        ".productivity-inline-edit",
        function (event) {

            if (event.key === "Enter") {

                event.preventDefault();

                this.blur();
            }
        }
    );


$(document)
    .off(
        "change.productivityInlineEdit",
        ".productivity-inline-edit"
    )
    .on(
        "change.productivityInlineEdit",
        ".productivity-inline-edit",
        function () {

            const input = $(
                this
            );


            const row_key = String(
                input.data(
                    "row-key"
                )
                || ""
            );


            const fieldname = String(
                input.data(
                    "field"
                )
                || ""
            );


            if (
                !row_key
                || !fieldname
            ) {
                return;
            }


            const row_data = (
                window.productivity_editable_rows[
                    row_key
                ]
            );


            if (!row_data) {

                frappe.msgprint(
                    __(
                        "Unable to identify this Productivity row. Please refresh the report."
                    )
                );

                return;
            }


            let new_value = (
                input.val()
            );


            if (
                fieldname
                === "hauling_distance_m"
            ) {

                new_value = Number(
                    new_value
                    || 0
                );


                if (
                    !Number.isFinite(
                        new_value
                    )
                    || new_value < 0
                ) {

                    new_value = 0;
                }


                input.val(
                    new_value
                );
            }


            // Update current report data immediately.
            row_data[
                fieldname
            ] = new_value;


            if (
                frappe.query_report
                && Array.isArray(
                    frappe.query_report.data
                )
            ) {

                frappe.query_report.data.forEach(
                    function (
                        report_row
                    ) {

                        if (
                            report_row
                            && report_row.productivity_edit_key
                                === row_key
                        ) {

                            report_row[
                                fieldname
                            ] = new_value;
                        }
                    }
                );
            }


            input
                .prop(
                    "disabled",
                    true
                )
                .addClass(
                    "productivity-saving"
                );


            frappe.call({

                method:
                    "is_production.production.report.productivity.productivity.save_productivity_area_override",

                args: {

                    row_key:
                        row_key,

                    site:
                        row_data.productivity_edit_site
                        || "",

                    start_date:
                        row_data.productivity_edit_start_date
                        || "",

                    end_date:
                        row_data.productivity_edit_end_date
                        || "",

                    shift:
                        row_data.productivity_edit_shift
                        || "",

                    monthly_production_plan:
                        row_data.productivity_edit_monthly_plan
                        || "",

                    category:
                        row_data.productivity_edit_category
                        || "",

                    machine:
                        row_data.productivity_edit_machine
                        || "",

                    material:
                        row_data.productivity_edit_material
                        || "",

                    from_area:
                        row_data.from_area
                        || "",

                    to_area:
                        row_data.to_area
                        || "",

                    hauling_distance_m:
                        String(
                            row_data.hauling_distance_m
                            || ""
                        ).trim()
                },


                callback: function (
                    response
                ) {

                    input
                        .prop(
                            "disabled",
                            false
                        )
                        .removeClass(
                            "productivity-saving"
                        )
                        .addClass(
                            "productivity-saved"
                        );


                    setTimeout(
                        function () {

                            input.removeClass(
                                "productivity-saved"
                            );

                        },
                        1200
                    );


                    frappe.show_alert({
                        message:
                            __(
                                "Productivity row saved"
                            ),

                        indicator:
                            "green"
                    });


                    // KOSI_PRODUCTIVITY_DISTANCE_TOTAL_REFRESH_V1
                    //
                    // When a material distance changes, refresh
                    // the report after the server save so the
                    // bold category total updates immediately.
                    if (
                        fieldname
                        === "hauling_distance_m"
                        && frappe.query_report
                    ) {

                        setTimeout(
                            function () {

                                frappe.query_report.refresh();

                            },
                            300
                        );
                    }
                },


                error: function () {

                    input
                        .prop(
                            "disabled",
                            false
                        )
                        .removeClass(
                            "productivity-saving"
                        );
                }
            });
        }
    );


// ============================================================
// EDITABLE FIELD STYLE
// ============================================================

(function () {

    const style_id = (
        "productivity-inline-edit-style"
    );


    if (
        document.getElementById(
            style_id
        )
    ) {
        return;
    }


    const style = document.createElement(
        "style"
    );


    style.id = style_id;


    style.innerHTML = `

        .productivity-inline-edit {
            border: 1px solid transparent !important;
            background: transparent !important;
            box-shadow: none !important;
        }


        .productivity-inline-edit:hover {
            border-color: #c7ccd1 !important;
            background: #ffffff !important;
        }


        .productivity-inline-edit:focus {
            border-color: #8b949e !important;
            background: #ffffff !important;
            outline: none !important;
        }


        .productivity-inline-edit.productivity-saving {
            opacity: 0.55;
        }


        .productivity-inline-edit.productivity-saved {
            border-color: #22a06b !important;
        }

    `;


    document.head.appendChild(
        style
    );

})();



// ============================================================
// KOSI_PRODUCTIVITY_SAVE_OVERRIDE_BUTTON_V1
//
// Explicit override-saving workflow.
//
// User may edit:
//
//     From Area
//     To Area
//     Hauling Distance (M)
//
// Changes remain unsaved until:
//
//     SAVE OVERRIDE
//
// is clicked.
//
// After successful save:
//     - overrides remain after refresh
//     - report refreshes
//     - machine/category distance totals recalculate
// ============================================================


window.productivity_dirty_overrides = (
    window.productivity_dirty_overrides
    || {}
);


function productivity_override_button() {

    if (
        !frappe.query_report
        || !frappe.query_report.page
        || !frappe.query_report.page.wrapper
    ) {
        return $();
    }


    return $(
        frappe.query_report.page.wrapper
    ).find(
        ".productivity-save-override-btn"
    );
}


function productivity_update_override_button() {

    const button = (
        productivity_override_button()
    );


    if (!button.length) {
        return;
    }


    const count = Object.keys(
        window.productivity_dirty_overrides
        || {}
    ).length;


    if (count > 0) {

        button
            .addClass(
                "productivity-has-unsaved-overrides"
            )
            .attr(
                "title",
                count
                + (
                    count === 1
                        ? " unsaved override"
                        : " unsaved overrides"
                )
            );

    } else {

        button
            .removeClass(
                "productivity-has-unsaved-overrides"
            )
            .attr(
                "title",
                "Save edited Productivity overrides"
            );
    }
}


function productivity_normalize_override_value(
    fieldname,
    value
) {

    if (
        fieldname
        === "hauling_distance_m"
    ) {

        let number = Number(
            value
            || 0
        );


        if (
            !Number.isFinite(
                number
            )
            || number < 0
        ) {

            number = 0;
        }


        return number;
    }


    return String(
        value
        ?? ""
    );
}


function productivity_mark_override_dirty(
    element
) {

    const input = $(
        element
    );


    const row_key = String(
        input.data(
            "row-key"
        )
        || ""
    );


    const fieldname = String(
        input.data(
            "field"
        )
        || ""
    );


    if (
        !row_key
        || !fieldname
    ) {
        return;
    }


    const row_data = (
        window.productivity_editable_rows[
            row_key
        ]
    );


    if (!row_data) {
        return;
    }


    const value = (
        productivity_normalize_override_value(
            fieldname,
            input.val()
        )
    );


    row_data[
        fieldname
    ] = value;


    // Keep the current Query Report data synchronized.
    if (
        frappe.query_report
        && Array.isArray(
            frappe.query_report.data
        )
    ) {

        frappe.query_report.data.forEach(
            function (
                report_row
            ) {

                if (
                    report_row
                    && report_row.productivity_edit_key
                        === row_key
                ) {

                    report_row[
                        fieldname
                    ] = value;
                }
            }
        );
    }


    window.productivity_dirty_overrides[
        row_key
    ] = true;


    input.addClass(
        "productivity-override-dirty"
    );


    productivity_update_override_button();
}


function productivity_save_one_override(
    row_key
) {

    return new Promise(
        function (
            resolve,
            reject
        ) {

            const row_data = (
                window.productivity_editable_rows[
                    row_key
                ]
            );


            if (!row_data) {

                reject(
                    new Error(
                        "Productivity row data not found."
                    )
                );

                return;
            }


            frappe.call({

                method:
                    "is_production.production.report.productivity.productivity.save_productivity_area_override",

                args: {

                    row_key:
                        row_key,

                    site:
                        row_data.productivity_edit_site
                        || "",

                    start_date:
                        row_data.productivity_edit_start_date
                        || "",

                    end_date:
                        row_data.productivity_edit_end_date
                        || "",

                    shift:
                        row_data.productivity_edit_shift
                        || "",

                    monthly_production_plan:
                        row_data.productivity_edit_monthly_plan
                        || "",

                    category:
                        row_data.productivity_edit_category
                        || "",

                    machine:
                        row_data.productivity_edit_machine
                        || "",

                    material:
                        row_data.productivity_edit_material
                        || "",

                    from_area:
                        row_data.from_area
                        || "",

                    to_area:
                        row_data.to_area
                        || "",

                    hauling_distance_m:
                        String(
                            row_data.hauling_distance_m
                            || ""
                        ).trim()
                },


                callback: function (
                    response
                ) {

                    if (
                        response
                        && response.exc
                    ) {

                        reject(
                            new Error(
                                response.exc
                            )
                        );

                        return;
                    }


                    resolve(
                        response
                    );
                },


                error: function (
                    error
                ) {

                    reject(
                        error
                        || new Error(
                            "Save failed."
                        )
                    );
                }
            });
        }
    );
}


async function productivity_save_all_overrides() {

    // Ensure currently focused input has pushed its
    // latest typed value into row_data.
    const active = $(
        document.activeElement
    );


    if (
        active.hasClass(
            "productivity-inline-edit"
        )
    ) {

        productivity_mark_override_dirty(
            active
        );
    }


    const dirty_keys = Object.keys(
        window.productivity_dirty_overrides
        || {}
    );


    if (!dirty_keys.length) {

        frappe.show_alert({
            message:
                __(
                    "No override changes to save"
                ),

            indicator:
                "blue"
        });

        return;
    }


    const button = (
        productivity_override_button()
    );


    button
        .prop(
            "disabled",
            true
        )
        .text(
            __(
                "Saving..."
            )
        );


    let saved_count = 0;


    try {

        // Save one row at a time.
        //
        // This avoids multiple simultaneous writes to the
        // same override table and makes errors predictable.
        for (
            const row_key
            of dirty_keys
        ) {

            await productivity_save_one_override(
                row_key
            );


            delete (
                window.productivity_dirty_overrides[
                    row_key
                ]
            );


            $(
                '.productivity-inline-edit[data-row-key="'
                + row_key
                + '"]'
            )
                .removeClass(
                    "productivity-override-dirty"
                )
                .addClass(
                    "productivity-override-saved"
                );


            saved_count += 1;
        }


        frappe.show_alert({
            message:
                saved_count
                + (
                    saved_count === 1
                        ? " override saved"
                        : " overrides saved"
                ),

            indicator:
                "green"
        });


        // Refresh so:
        //
        // - saved values reload from server
        // - machine distance total recalculates
        // - category distance total recalculates
        // - Total Fleet distance recalculates
        setTimeout(
            function () {

                if (
                    frappe.query_report
                ) {

                    frappe.query_report.refresh();
                }

            },
            350
        );

    } catch (
        error
    ) {

        console.error(
            "Productivity override save failed:",
            error
        );


        frappe.msgprint({
            title:
                __(
                    "Save Override"
                ),

            indicator:
                "red",

            message:
                __(
                    "One or more overrides could not be saved. Unsaved changes remain highlighted."
                )
        });

    } finally {

        button
            .prop(
                "disabled",
                false
            )
            .text(
                __(
                    "Save Override"
                )
            );


        productivity_update_override_button();
    }
}


// ============================================================
// REPLACE AUTOMATIC SAVE WITH DIRTY TRACKING
//
// Previous inline-edit code saved on every change.
// We remove ONLY that change handler.
//
// User now explicitly saves with Save Override.
// ============================================================

$(document).off(
    "change.productivityInlineEdit",
    ".productivity-inline-edit"
);


$(document)
    .off(
        "input.productivitySaveOverride " +
        "change.productivitySaveOverride",
        ".productivity-inline-edit"
    )
    .on(
        "input.productivitySaveOverride " +
        "change.productivitySaveOverride",
        ".productivity-inline-edit",
        function (
            event
        ) {

            event.stopPropagation();


            productivity_mark_override_dirty(
                this
            );
        }
    );


// ============================================================
// ADD BUTTON NEXT TO REPORT ACTIONS
// ============================================================

function add_productivity_save_override_button(
    report
) {

    if (
        !report
        || !report.page
    ) {
        return;
    }


    if (
        report.__productivity_save_override_button_added
    ) {

        productivity_update_override_button();

        return;
    }


    report.__productivity_save_override_button_added = true;


    const button = (
        report.page.add_inner_button(
            __(
                "Save Override"
            ),
            function () {

                productivity_save_all_overrides();
            }
        )
    );


    if (button) {

        button
            .addClass(
                "productivity-save-override-btn"
            )
            .attr(
                "title",
                "Save edited Productivity overrides"
            );
    }


    productivity_update_override_button();
}


// ============================================================
// PRESERVE CURRENT PRODUCTIVITY ONLOAD
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_onload = (
        config.onload
    );


    config.onload = function (
        report
    ) {

        if (
            typeof previous_onload
            === "function"
        ) {

            previous_onload.apply(
                this,
                arguments
            );
        }


        add_productivity_save_override_button(
            report
        );
    };


    const previous_after_render = (
        config.after_datatable_render
    );


    config.after_datatable_render = function () {

        if (
            typeof previous_after_render
            === "function"
        ) {

            previous_after_render.apply(
                this,
                arguments
            );
        }


        setTimeout(
            function () {

                add_productivity_save_override_button(
                    frappe.query_report
                );

            },
            100
        );
    };

})();


// ============================================================
// BUTTON / DIRTY CELL STYLE
// ============================================================

(function () {

    const style_id = (
        "productivity-save-override-button-style"
    );


    if (
        document.getElementById(
            style_id
        )
    ) {
        return;
    }


    const style = document.createElement(
        "style"
    );


    style.id = style_id;


    style.innerHTML = `

        .productivity-save-override-btn {
            font-weight: 600 !important;
        }


        .productivity-save-override-btn
        .icon {
            margin-right: 4px;
        }


        .productivity-save-override-btn.productivity-has-unsaved-overrides {
            border-width: 2px !important;
            font-weight: 700 !important;
        }


        .productivity-inline-edit.productivity-override-dirty {
            border: 1px solid #d99a00 !important;
            background: #fff8dd !important;
        }


        .productivity-inline-edit.productivity-override-saved {
            border: 1px solid #22a06b !important;
        }

    `;


    document.head.appendChild(
        style
    );

})();



// ============================================================
// KOSI_PRODUCTIVITY_SAVE_OVERRIDE_SNAPSHOT_V2
//
// SAVE OVERRIDE now performs TWO permanent operations:
//
// 1. Save any edited individual override rows.
// 2. Save the complete current Productivity report as a
//    permanent Productivity Override Snapshot + PDF.
//
// A snapshot is created even when there are no new dirty cells.
// ============================================================


function productivity_create_permanent_snapshot() {

    return new Promise(
        function (
            resolve,
            reject
        ) {

            if (
                !frappe.query_report
            ) {

                reject(
                    new Error(
                        "Productivity report is not available."
                    )
                );

                return;
            }


            const filters = (
                frappe.query_report.get_filter_values
                    ? frappe.query_report.get_filter_values()
                    : {}
            ) || {};


            frappe.call({

                method:
                    "is_production.production.report.productivity.productivity.save_productivity_override_snapshot",

                args: {

                    filters_json:
                        JSON.stringify(
                            filters
                        )
                },


                callback: function (
                    response
                ) {

                    if (
                        response
                        && response.exc
                    ) {

                        reject(
                            new Error(
                                response.exc
                            )
                        );

                        return;
                    }


                    if (
                        !response
                        || !response.message
                        || !response.message.saved
                    ) {

                        reject(
                            new Error(
                                "Productivity snapshot was not created."
                            )
                        );

                        return;
                    }


                    resolve(
                        response.message
                    );
                },


                error: function (
                    error
                ) {

                    reject(
                        error
                        || new Error(
                            "Snapshot save failed."
                        )
                    );
                }
            });
        }
    );
}


// Replace V1 save behaviour.
//
// The existing Save Override button callback calls this
// function by name, so redefining it here upgrades the button
// without adding a second button.

productivity_save_all_overrides = async function () {

    const active = $(
        document.activeElement
    );


    if (
        active.hasClass(
            "productivity-inline-edit"
        )
    ) {

        productivity_mark_override_dirty(
            active
        );
    }


    const dirty_keys = Object.keys(
        window.productivity_dirty_overrides
        || {}
    );


    const button = (
        productivity_override_button()
    );


    button
        .prop(
            "disabled",
            true
        )
        .text(
            __(
                "Saving..."
            )
        );


    let saved_rows = 0;


    try {

        // ----------------------------------------------------
        // 1. SAVE ROW OVERRIDES FIRST
        // ----------------------------------------------------

        for (
            const row_key
            of dirty_keys
        ) {

            await productivity_save_one_override(
                row_key
            );


            delete (
                window.productivity_dirty_overrides[
                    row_key
                ]
            );


            $(
                '.productivity-inline-edit[data-row-key="'
                + row_key
                + '"]'
            )
                .removeClass(
                    "productivity-override-dirty"
                )
                .addClass(
                    "productivity-override-saved"
                );


            saved_rows += 1;
        }


        // ----------------------------------------------------
        // 2. SAVE COMPLETE REPORT SNAPSHOT
        //
        // Backend re-runs the report AFTER the row overrides
        // above, therefore all totals in the PDF are final.
        // ----------------------------------------------------

        button.text(
            __(
                "Saving Snapshot..."
            )
        );


        const snapshot = (
            await productivity_create_permanent_snapshot()
        );


        productivity_update_override_button();


        const pdf_url = (
            snapshot.file_url
            || ""
        );


        let message = (
            "<div style='line-height:1.7;'>"
            +
            "<b>Productivity Override saved permanently.</b>"
            +
            "<br>"
            +
            "Reference: <b>"
            +
            frappe.utils.escape_html(
                snapshot.snapshot_reference
                || snapshot.name
                || ""
            )
            +
            "</b>"
            +
            "<br>"
            +
            "Rows saved in snapshot: "
            +
            String(
                snapshot.row_count
                || 0
            )
        );


        if (pdf_url) {

            message += (
                "<br>"
                +
                "<a href='"
                +
                frappe.utils.escape_html(
                    pdf_url
                )
                +
                "' target='_blank'>"
                +
                "<b>Open Saved Productivity PDF</b>"
                +
                "</a>"
            );
        }


        message += (
            "</div>"
        );


        frappe.msgprint({

            title:
                __(
                    "Save Override"
                ),

            indicator:
                "green",

            message:
                message
        });


        // Reload report to show final persisted values
        // and all recalculated totals.
        setTimeout(
            function () {

                if (
                    frappe.query_report
                ) {

                    frappe.query_report.refresh();
                }

            },
            400
        );

    } catch (
        error
    ) {

        console.error(
            "Productivity permanent snapshot save failed:",
            error
        );


        frappe.msgprint({

            title:
                __(
                    "Save Override"
                ),

            indicator:
                "red",

            message:
                __(
                    "The Productivity override snapshot could not be saved. Please check the error and try again."
                )
        });

    } finally {

        button
            .prop(
                "disabled",
                false
            )
            .text(
                __(
                    "Save Override"
                )
            );


        productivity_update_override_button();
    }
};


// END KOSI_PRODUCTIVITY_SAVE_OVERRIDE_SNAPSHOT_V2


// ============================================================
// KOSI_PRODUCTIVITY_SAVED_OVERRIDES_BUTTON_V1
//
// Direct access to all permanently saved Productivity
// Override Snapshot records and their PDF attachments.
// ============================================================


function add_productivity_saved_overrides_button(
    report
) {

    if (
        !report
        || !report.page
    ) {
        return;
    }


    if (
        report.__productivity_saved_overrides_button_added
    ) {
        return;
    }


    report.__productivity_saved_overrides_button_added = true;


    const button = report.page.add_inner_button(
        __("Saved Overrides"),
        function () {

            frappe.set_route(
                "List",
                "Productivity Override Snapshot"
            );
        }
    );


    if (button) {

        button
            .addClass(
                "productivity-saved-overrides-btn"
            )
            .attr(
                "title",
                __(
                    "Open saved Productivity reports and PDFs"
                )
            );
    }
}


// ============================================================
// PRESERVE CURRENT PRODUCTIVITY ONLOAD
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_onload = (
        config.onload
    );


    config.onload = function (
        report
    ) {

        if (
            typeof previous_onload
            === "function"
        ) {

            previous_onload.apply(
                this,
                arguments
            );
        }


        add_productivity_saved_overrides_button(
            report
        );
    };


    const previous_after_render = (
        config.after_datatable_render
    );


    config.after_datatable_render = function () {

        if (
            typeof previous_after_render
            === "function"
        ) {

            previous_after_render.apply(
                this,
                arguments
            );
        }


        setTimeout(
            function () {

                add_productivity_saved_overrides_button(
                    frappe.query_report
                );

            },
            100
        );
    };

})();



// ============================================================
// KOSI_PRODUCTIVITY_EXCAVATOR_DISTANCE_BLANK_V1
//
// Excavator Hauling Distance must remain blank.
//
// From Area / To Area remain available.
//
// ADT and Dozer Hauling Distance remain editable.
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_formatter = (
        config.formatter
    );


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        if (
            fieldname
            === "hauling_distance_m"
            && data
        ) {

            const category = String(
                data.productivity_edit_category
                || data.category
                || (
                    Number(
                        data.is_category_total
                        || 0
                    ) === 1
                        ? data.label
                        : ""
                )
                || ""
            ).trim();


            if (
                category
                === "Excavator"
            ) {

                return "";
            }
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };

})();


// END KOSI_PRODUCTIVITY_EXCAVATOR_DISTANCE_BLANK_V1


// ============================================================
// KOSI_PRODUCTIVITY_EXCAVATOR_NO_AREA_V1
//
// Defence-in-depth:
// Never render From Area / To Area / Hauling Distance
// inputs or values for Excavator rows.
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_formatter = (
        config.formatter
    );


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        if (
            data
            && [
                "from_area",
                "to_area",
                "hauling_distance_m"
            ].includes(
                fieldname
            )
        ) {

            const category = String(
                data.productivity_edit_category
                || data.category
                || (
                    Number(
                        data.is_category_total
                        || 0
                    ) === 1
                        ? data.label
                        : ""
                )
                || ""
            ).trim();


            if (
                category
                === "Excavator"
            ) {

                return "";
            }
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };

})();


// END KOSI_PRODUCTIVITY_EXCAVATOR_NO_AREA_V1



// ============================================================
// KOSI_PRODUCTIVITY_HAULING_DISTANCE_TEXT_V1
//
// Hauling Distance accepts text/range values:
//
//     100-500
//     500-1000
//     1000-1500
//
// Do NOT convert it to Number.
// ============================================================


// ------------------------------------------------------------
// Override dirty-value normalization.
// ------------------------------------------------------------

if (
    typeof productivity_normalize_override_value
    === "function"
) {

    const previous_productivity_normalize_override_value =
        productivity_normalize_override_value;


    productivity_normalize_override_value = function (
        fieldname,
        value
    ) {

        if (
            fieldname
            === "hauling_distance_m"
        ) {

            return String(
                value
                ?? ""
            ).trim();
        }


        return previous_productivity_normalize_override_value(
            fieldname,
            value
        );
    };
}


// ------------------------------------------------------------
// Ensure Hauling Distance renders as TEXT input rather than
// number input.
// ------------------------------------------------------------

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_formatter = (
        config.formatter
    );


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        let formatted;


        if (
            typeof previous_formatter
            === "function"
        ) {

            formatted = previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );

        } else {

            formatted = default_formatter(
                value,
                row,
                column,
                data
            );
        }


        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        if (
            fieldname
            === "hauling_distance_m"
            && typeof formatted
                === "string"
        ) {

            formatted = formatted
                .replace(
                    'type="number"',
                    'type="text"'
                )
                .replace(
                    ' step="1" min="0" ',
                    " "
                );
        }


        return formatted;
    };

})();


// END KOSI_PRODUCTIVITY_HAULING_DISTANCE_TEXT_V1


// ============================================================
// KOSI_PRODUCTIVITY_BCM_HD_FORMAT_V1
//
// BCM/HD:
//     blank stays blank
//     never display 0.000
//
// Hauling Distance totals:
//     blank stays blank
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_formatter = (
        config.formatter
    );


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        if (
            fieldname
            === "productivity_bcm_hd"
        ) {

            const raw = (
                data
                ? data.productivity_bcm_hd
                : value
            );


            if (
                raw === null
                || raw === undefined
                || String(
                    raw
                ).trim() === ""
                || String(
                    raw
                ).trim() === "0"
                || String(
                    raw
                ).trim() === "0.000"
            ) {

                return "";
            }


            return String(
                raw
            );
        }


        if (
            fieldname
            === "hauling_distance_m"
        ) {

            const raw = (
                data
                ? data.hauling_distance_m
                : value
            );


            if (
                raw === null
                || raw === undefined
                || String(
                    raw
                ).trim() === ""
                || String(
                    raw
                ).trim() === "0"
                || String(
                    raw
                ).trim() === "0.0"
                || String(
                    raw
                ).trim() === "0.000"
            ) {

                return "";
            }
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };

})();


// END KOSI_PRODUCTIVITY_BCM_HD_FORMAT_V1


// ============================================================
// KOSI_PRODUCTIVITY_FORCE_MACHINE_HD_INPUT_V3
//
// FINAL formatter layer.
//
// In Summary Per Machine:
//
//     ADT + Material
//     Dozer + Material
//
// MUST ALWAYS show an editable Hauling Distance input.
//
// This runs AFTER all previous Productivity formatters so
// another formatter cannot turn the input back into a blank
// normal DataTable cell.
// ============================================================


(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_formatter = (
        config.formatter
    );


    function hd_escape(
        value
    ) {

        return String(
            value === null
            || value === undefined
                ? ""
                : value
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        // ----------------------------------------------------
        // ONLY TARGET HAULING DISTANCE
        // ----------------------------------------------------

        if (
            fieldname
            === "hauling_distance_m"
            && data
        ) {

            const category = String(
                data.productivity_edit_category
                || data.category
                || ""
            ).trim();


            const material = String(
                data.material
                || ""
            ).trim();


            const row_key = String(
                data.productivity_edit_key
                || ""
            ).trim();


            const machine = String(
                data.productivity_edit_machine
                || data.machine
                || data.label
                || ""
            ).trim();


            const is_machine_detail = (
                (
                    category === "ADT"
                    ||
                    category === "Dozer"
                )
                &&
                material
                &&
                row_key
            );


            if (
                is_machine_detail
            ) {

                // Make sure Save Override can find the row.
                window.productivity_editable_rows = (
                    window.productivity_editable_rows
                    || {}
                );


                window.productivity_editable_rows[
                    row_key
                ] = data;


                const raw_value = String(
                    data.hauling_distance_m
                    || ""
                ).trim();


                return (
                    '<input'
                    + ' type="text"'
                    + ' class="form-control productivity-inline-edit productivity-machine-hd-input"'
                    + ' data-row-key="'
                    + hd_escape(
                        row_key
                    )
                    + '"'
                    + ' data-field="hauling_distance_m"'
                    + ' data-machine="'
                    + hd_escape(
                        machine
                    )
                    + '"'
                    + ' data-material="'
                    + hd_escape(
                        material
                    )
                    + '"'
                    + ' value="'
                    + hd_escape(
                        raw_value
                    )
                    + '"'
                    + ' placeholder=""'
                    + ' autocomplete="off"'
                    + ' title="Enter hauling distance for '
                    + hd_escape(
                        machine
                    )
                    + ' / '
                    + hd_escape(
                        material
                    )
                    + '"'
                    + ' />'
                );
            }


            // Category totals, machine totals, Total Fleet,
            // Excavator, etc. remain blank.
            if (
                category === "Excavator"
                || !material
            ) {

                return "";
            }
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    // --------------------------------------------------------
    // Prevent DataTable from swallowing clicks/typing.
    // --------------------------------------------------------

    $(document)
        .off(
            "mousedown.productivityMachineHDV3 "
            + "click.productivityMachineHDV3",
            ".productivity-machine-hd-input"
        )
        .on(
            "mousedown.productivityMachineHDV3 "
            + "click.productivityMachineHDV3",
            ".productivity-machine-hd-input",
            function (
                event
            ) {

                event.stopPropagation();
            }
        );


    $(document)
        .off(
            "keydown.productivityMachineHDV3",
            ".productivity-machine-hd-input"
        )
        .on(
            "keydown.productivityMachineHDV3",
            ".productivity-machine-hd-input",
            function (
                event
            ) {

                event.stopPropagation();


                if (
                    event.key === "Enter"
                ) {

                    event.preventDefault();

                    $(this).blur();
                }
            }
        );


    // --------------------------------------------------------
    // Make absolutely sure typed values are registered as
    // dirty for the existing Save Override button.
    // --------------------------------------------------------

    $(document)
        .off(
            "input.productivityMachineHDV3 "
            + "change.productivityMachineHDV3",
            ".productivity-machine-hd-input"
        )
        .on(
            "input.productivityMachineHDV3 "
            + "change.productivityMachineHDV3",
            ".productivity-machine-hd-input",
            function (
                event
            ) {

                event.stopPropagation();


                if (
                    typeof productivity_mark_override_dirty
                    === "function"
                ) {

                    productivity_mark_override_dirty(
                        this
                    );
                }
            }
        );


    // --------------------------------------------------------
    // STYLE
    // Same obvious capture box for ADT and Dozer.
    // --------------------------------------------------------

    const style_id = (
        "productivity-machine-hd-input-v3-style"
    );


    if (
        !document.getElementById(
            style_id
        )
    ) {

        const style = document.createElement(
            "style"
        );


        style.id = style_id;


        style.innerHTML = `

            .productivity-machine-hd-input {
                width: 100% !important;
                height: 26px !important;

                padding:
                    2px 6px !important;

                border:
                    1px solid #d99a00 !important;

                background:
                    #fff8dd !important;

                box-shadow:
                    none !important;

                font-size:
                    12px !important;

                line-height:
                    20px !important;

                cursor:
                    text !important;

                pointer-events:
                    auto !important;

                position:
                    relative !important;

                z-index:
                    5 !important;
            }


            .productivity-machine-hd-input:hover {
                background:
                    #fff3bf !important;
            }


            .productivity-machine-hd-input:focus {
                background:
                    #ffffff !important;

                border:
                    2px solid #d99a00 !important;

                outline:
                    none !important;
            }


            .productivity-machine-hd-input.productivity-override-dirty {
                background:
                    #fff1b8 !important;

                border:
                    2px solid #d99a00 !important;
            }

        `;


        document.head.appendChild(
            style
        );
    }

})();


// END KOSI_PRODUCTIVITY_FORCE_MACHINE_HD_INPUT_V3


// ============================================================
// KOSI_PRODUCTIVITY_PENDING_MACHINE_HD_V4
//
// FIX:
//
// When user types an ADT / Dozer hauling distance, keep the
// value locally until SAVE OVERRIDE succeeds.
//
// This prevents Frappe DataTable redraws from replacing the
// typed value with a blank backend value before it has been
// saved.
//
// Applies to:
//
//     ADT + Material
//     Dozer + Material
//
// ============================================================


window.productivity_pending_machine_hd = (
    window.productivity_pending_machine_hd
    || {}
);


// ============================================================
// CAPTURE TYPED VALUE IMMEDIATELY
// ============================================================

function productivity_capture_pending_machine_hd(
    element
) {

    const input = $(
        element
    );


    const row_key = String(
        input.attr(
            "data-row-key"
        )
        || ""
    ).trim();


    if (!row_key) {
        return;
    }


    const value = String(
        input.val()
        ?? ""
    ).trim();


    // --------------------------------------------------------
    // 1. KEEP PENDING VALUE
    // --------------------------------------------------------

    window.productivity_pending_machine_hd[
        row_key
    ] = value;


    // --------------------------------------------------------
    // 2. MARK ROW DIRTY
    // --------------------------------------------------------

    window.productivity_dirty_overrides = (
        window.productivity_dirty_overrides
        || {}
    );


    window.productivity_dirty_overrides[
        row_key
    ] = true;


    // --------------------------------------------------------
    // 3. UPDATE EDITABLE ROW OBJECT
    // --------------------------------------------------------

    window.productivity_editable_rows = (
        window.productivity_editable_rows
        || {}
    );


    const row_data = (
        window.productivity_editable_rows[
            row_key
        ]
    );


    if (row_data) {

        row_data[
            "hauling_distance_m"
        ] = value;
    }


    // --------------------------------------------------------
    // 4. UPDATE QUERY REPORT DATA
    // --------------------------------------------------------

    if (
        frappe.query_report
        && Array.isArray(
            frappe.query_report.data
        )
    ) {

        frappe.query_report.data.forEach(
            function (
                report_row
            ) {

                if (
                    report_row
                    && String(
                        report_row.productivity_edit_key
                        || ""
                    ) === row_key
                ) {

                    report_row[
                        "hauling_distance_m"
                    ] = value;
                }
            }
        );
    }


    input.addClass(
        "productivity-override-dirty"
    );


    if (
        typeof productivity_update_override_button
        === "function"
    ) {

        productivity_update_override_button();
    }
}


// ============================================================
// INPUT EVENTS
//
// Do NOT save here.
// Save only when user clicks SAVE OVERRIDE.
// ============================================================

$(document)
    .off(
        "input.productivityPendingHDV4 "
        + "change.productivityPendingHDV4 "
        + "blur.productivityPendingHDV4",
        ".productivity-machine-hd-input"
    )
    .on(
        "input.productivityPendingHDV4",
        ".productivity-machine-hd-input",
        function (
            event
        ) {

            event.stopPropagation();


            productivity_capture_pending_machine_hd(
                this
            );
        }
    )
    .on(
        "change.productivityPendingHDV4 "
        + "blur.productivityPendingHDV4",
        ".productivity-machine-hd-input",
        function (
            event
        ) {

            event.stopPropagation();


            productivity_capture_pending_machine_hd(
                this
            );
        }
    );


// ============================================================
// REMOVE OLD AUTOMATIC CHANGE SAVE AGAIN
//
// We want:
//     TYPE
//     KEEP VALUE
//     CLICK SAVE OVERRIDE
//
// not automatic server save on blur.
// ============================================================

$(document).off(
    "change.productivityInlineEdit",
    ".productivity-inline-edit"
);


// ============================================================
// ENSURE SAVE OVERRIDE USES THE PENDING VALUE
// ============================================================

if (
    typeof productivity_save_one_override
    === "function"
) {

    const productivity_save_one_override_before_pending_hd =
        productivity_save_one_override;


    productivity_save_one_override = function (
        row_key
    ) {

        row_key = String(
            row_key
            || ""
        ).trim();


        const pending = (
            window.productivity_pending_machine_hd
            || {}
        );


        const has_pending = (
            Object.prototype.hasOwnProperty.call(
                pending,
                row_key
            )
        );


        if (has_pending) {

            const value = String(
                pending[
                    row_key
                ]
                ?? ""
            ).trim();


            const row_data = (
                window.productivity_editable_rows
                && window.productivity_editable_rows[
                    row_key
                ]
            );


            if (row_data) {

                row_data[
                    "hauling_distance_m"
                ] = value;
            }
        }


        return (
            productivity_save_one_override_before_pending_hd(
                row_key
            )
        ).then(
            function (
                result
            ) {

                // Server save succeeded.
                //
                // Safe to remove the pending browser value.
                delete (
                    window.productivity_pending_machine_hd[
                        row_key
                    ]
                );


                return result;
            }
        );
    };
}


// ============================================================
// FINAL FORMATTER
//
// If DataTable redraws BEFORE Save Override, use pending value
// rather than the blank backend value.
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_formatter = (
        config.formatter
    );


    function pending_hd_escape(
        value
    ) {

        return String(
            value === null
            || value === undefined
                ? ""
                : value
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        if (
            fieldname
            === "hauling_distance_m"
            && data
        ) {

            const category = String(
                data.productivity_edit_category
                || data.category
                || ""
            ).trim();


            const material = String(
                data.material
                || ""
            ).trim();


            const row_key = String(
                data.productivity_edit_key
                || ""
            ).trim();


            if (
                (
                    category === "ADT"
                    ||
                    category === "Dozer"
                )
                &&
                material
                &&
                row_key
            ) {

                window.productivity_editable_rows = (
                    window.productivity_editable_rows
                    || {}
                );


                window.productivity_editable_rows[
                    row_key
                ] = data;


                const pending = (
                    window.productivity_pending_machine_hd
                    || {}
                );


                const has_pending = (
                    Object.prototype.hasOwnProperty.call(
                        pending,
                        row_key
                    )
                );


                const display_value = (
                    has_pending
                        ? pending[
                            row_key
                        ]
                        : (
                            data.hauling_distance_m
                            || ""
                        )
                );


                return (
                    '<input'
                    + ' type="text"'
                    + ' class="form-control productivity-inline-edit productivity-machine-hd-input"'
                    + ' data-row-key="'
                    + pending_hd_escape(
                        row_key
                    )
                    + '"'
                    + ' data-field="hauling_distance_m"'
                    + ' value="'
                    + pending_hd_escape(
                        display_value
                    )
                    + '"'
                    + ' placeholder=""'
                    + ' autocomplete="off"'
                    + ' />'
                );
            }
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };

})();


// END KOSI_PRODUCTIVITY_PENDING_MACHINE_HD_V4


// ============================================================
// KOSI_PRODUCTIVITY_NO_ROW_VIRTUALIZATION_V5
//
// IMPORTANT FOR EDITABLE HAULING DISTANCE:
//
// Frappe DataTable normally virtualizes/recycles rows while
// scrolling.
//
// That is good for very large read-only reports, but not for
// our editable ADT/Dozer Hauling Distance inputs because an
// unsaved input can be destroyed when the row scrolls out of
// view.
//
// Productivity only:
//     clusterize = false
//
// This keeps all report rows rendered until Save Override.
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_get_datatable_options = (
        config.get_datatable_options
    );


    config.get_datatable_options = function (
        options
    ) {

        let result = (
            options
            || {}
        );


        if (
            typeof previous_get_datatable_options
            === "function"
        ) {

            const previous_result = (
                previous_get_datatable_options.apply(
                    this,
                    arguments
                )
            );


            if (
                previous_result
                && typeof previous_result
                    === "object"
            ) {

                result = previous_result;
            }
        }


        // ----------------------------------------------------
        // KEEP ALL ROWS IN THE DOM.
        //
        // This prevents ADT / Dozer input values disappearing
        // when the user scrolls down to another machine.
        // ----------------------------------------------------

        result.clusterize = false;


        return result;
    };

})();


// END KOSI_PRODUCTIVITY_NO_ROW_VIRTUALIZATION_V5


// ============================================================
// KOSI_PRODUCTIVITY_HD_SESSION_DRAFT_V6
//
// FINAL FIX FOR SCROLLING.
//
// Every unsaved ADT / Dozer Hauling Distance is stored in
// browser sessionStorage immediately as the user types.
//
// If Frappe DataTable destroys/recreates a row while scrolling,
// the formatter restores the unsaved value from sessionStorage.
//
// Draft is removed ONLY after Save Override succeeds.
// ============================================================


(function () {

    const STORAGE_PREFIX = (
        "productivity_hd_draft::"
    );


    function storage_key(
        row_key
    ) {

        return (
            STORAGE_PREFIX
            + String(
                row_key
                || ""
            )
        );
    }


    function has_draft(
        row_key
    ) {

        if (!row_key) {
            return false;
        }


        try {

            return (
                window.sessionStorage.getItem(
                    storage_key(
                        row_key
                    )
                )
                !== null
            );

        } catch (
            error
        ) {

            return (
                window.productivity_hd_session_fallback
                &&
                Object.prototype.hasOwnProperty.call(
                    window.productivity_hd_session_fallback,
                    row_key
                )
            );
        }
    }


    function get_draft(
        row_key
    ) {

        if (!row_key) {
            return "";
        }


        try {

            const value = (
                window.sessionStorage.getItem(
                    storage_key(
                        row_key
                    )
                )
            );


            return (
                value === null
                    ? ""
                    : value
            );

        } catch (
            error
        ) {

            return (
                (
                    window.productivity_hd_session_fallback
                    || {}
                )[
                    row_key
                ]
                || ""
            );
        }
    }


    function set_draft(
        row_key,
        value
    ) {

        if (!row_key) {
            return;
        }


        value = String(
            value
            ?? ""
        );


        try {

            window.sessionStorage.setItem(
                storage_key(
                    row_key
                ),
                value
            );

        } catch (
            error
        ) {

            window.productivity_hd_session_fallback = (
                window.productivity_hd_session_fallback
                || {}
            );


            window.productivity_hd_session_fallback[
                row_key
            ] = value;
        }
    }


    function remove_draft(
        row_key
    ) {

        if (!row_key) {
            return;
        }


        try {

            window.sessionStorage.removeItem(
                storage_key(
                    row_key
                )
            );

        } catch (
            error
        ) {

            if (
                window.productivity_hd_session_fallback
            ) {

                delete (
                    window.productivity_hd_session_fallback[
                        row_key
                    ]
                );
            }
        }
    }


    function all_draft_row_keys() {

        const result = [];


        try {

            for (
                let index = 0;
                index < window.sessionStorage.length;
                index += 1
            ) {

                const key = (
                    window.sessionStorage.key(
                        index
                    )
                );


                if (
                    key
                    && key.startsWith(
                        STORAGE_PREFIX
                    )
                ) {

                    result.push(
                        key.substring(
                            STORAGE_PREFIX.length
                        )
                    );
                }
            }

        } catch (
            error
        ) {

            Object.keys(
                window.productivity_hd_session_fallback
                || {}
            ).forEach(
                function (
                    row_key
                ) {

                    result.push(
                        row_key
                    );
                }
            );
        }


        return result;
    }


    window.productivity_hd_has_draft = (
        has_draft
    );

    window.productivity_hd_get_draft = (
        get_draft
    );

    window.productivity_hd_set_draft = (
        set_draft
    );

    window.productivity_hd_remove_draft = (
        remove_draft
    );


    // ========================================================
    // CAPTURE INPUT AT NATIVE CAPTURE PHASE
    //
    // This runs BEFORE DataTable / jQuery can stop the event.
    // ========================================================

    function capture_hd_value(
        event
    ) {

        const target = (
            event
            && event.target
        );


        if (
            !target
            || !target.matches
            || !target.matches(
                ".productivity-machine-hd-input"
            )
        ) {

            return;
        }


        const row_key = String(
            target.getAttribute(
                "data-row-key"
            )
            || ""
        ).trim();


        if (!row_key) {
            return;
        }


        const value = String(
            target.value
            ?? ""
        );


        // ----------------------------------------------------
        // SAVE DRAFT IMMEDIATELY
        // ----------------------------------------------------

        set_draft(
            row_key,
            value
        );


        // ----------------------------------------------------
        // KEEP NORMAL IN-MEMORY DRAFT TOO
        // ----------------------------------------------------

        window.productivity_pending_machine_hd = (
            window.productivity_pending_machine_hd
            || {}
        );


        window.productivity_pending_machine_hd[
            row_key
        ] = value;


        // ----------------------------------------------------
        // MARK DIRTY FOR SAVE OVERRIDE
        // ----------------------------------------------------

        window.productivity_dirty_overrides = (
            window.productivity_dirty_overrides
            || {}
        );


        window.productivity_dirty_overrides[
            row_key
        ] = true;


        // ----------------------------------------------------
        // UPDATE EDITABLE ROW OBJECT
        // ----------------------------------------------------

        window.productivity_editable_rows = (
            window.productivity_editable_rows
            || {}
        );


        const row_data = (
            window.productivity_editable_rows[
                row_key
            ]
        );


        if (row_data) {

            row_data[
                "hauling_distance_m"
            ] = value;
        }


        // ----------------------------------------------------
        // UPDATE CURRENT REPORT DATA TOO
        // ----------------------------------------------------

        if (
            frappe.query_report
            && Array.isArray(
                frappe.query_report.data
            )
        ) {

            frappe.query_report.data.forEach(
                function (
                    report_row
                ) {

                    if (
                        report_row
                        && String(
                            report_row.productivity_edit_key
                            || ""
                        ) === row_key
                    ) {

                        report_row[
                            "hauling_distance_m"
                        ] = value;
                    }
                }
            );
        }


        if (
            typeof productivity_update_override_button
            === "function"
        ) {

            productivity_update_override_button();
        }
    }


    if (
        !window.__productivity_hd_capture_v6
    ) {

        window.__productivity_hd_capture_v6 = true;


        document.addEventListener(
            "input",
            capture_hd_value,
            true
        );


        document.addEventListener(
            "change",
            capture_hd_value,
            true
        );


        document.addEventListener(
            "keyup",
            capture_hd_value,
            true
        );


        document.addEventListener(
            "blur",
            capture_hd_value,
            true
        );
    }


    // ========================================================
    // SAVE ONE OVERRIDE
    //
    // Make sure server save uses session draft.
    // Clear draft ONLY after successful save.
    // ========================================================

    if (
        typeof productivity_save_one_override
        === "function"
        && !window.__productivity_save_one_v6
    ) {

        window.__productivity_save_one_v6 = true;


        const previous_save_one = (
            productivity_save_one_override
        );


        productivity_save_one_override = function (
            row_key
        ) {

            row_key = String(
                row_key
                || ""
            ).trim();


            if (
                row_key
                && has_draft(
                    row_key
                )
            ) {

                const draft_value = (
                    get_draft(
                        row_key
                    )
                );


                const row_data = (
                    window.productivity_editable_rows
                    && window.productivity_editable_rows[
                        row_key
                    ]
                );


                if (row_data) {

                    row_data[
                        "hauling_distance_m"
                    ] = draft_value;
                }
            }


            const result = (
                previous_save_one(
                    row_key
                )
            );


            return Promise.resolve(
                result
            ).then(
                function (
                    response
                ) {

                    remove_draft(
                        row_key
                    );


                    if (
                        window.productivity_pending_machine_hd
                    ) {

                        delete (
                            window.productivity_pending_machine_hd[
                                row_key
                            ]
                        );
                    }


                    return response;
                }
            );
        };
    }


    // ========================================================
    // SAVE ALL OVERRIDES
    //
    // Even if DataTable redraw somehow cleared the dirty map,
    // rebuild it from sessionStorage before saving.
    // ========================================================

    if (
        typeof productivity_save_all_overrides
        === "function"
        && !window.__productivity_save_all_v6
    ) {

        window.__productivity_save_all_v6 = true;


        const previous_save_all = (
            productivity_save_all_overrides
        );


        productivity_save_all_overrides = async function () {

            window.productivity_dirty_overrides = (
                window.productivity_dirty_overrides
                || {}
            );


            all_draft_row_keys().forEach(
                function (
                    row_key
                ) {

                    window.productivity_dirty_overrides[
                        row_key
                    ] = true;


                    const row_data = (
                        window.productivity_editable_rows
                        && window.productivity_editable_rows[
                            row_key
                        ]
                    );


                    if (row_data) {

                        row_data[
                            "hauling_distance_m"
                        ] = get_draft(
                            row_key
                        );
                    }
                }
            );


            return previous_save_all.apply(
                this,
                arguments
            );
        };
    }


    // ========================================================
    // FINAL FORMATTER
    //
    // ALWAYS render stored draft before backend value.
    // ========================================================

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {
        return;
    }


    const previous_formatter = (
        config.formatter
    );


    function escape_html(
        value
    ) {

        return String(
            value === null
            || value === undefined
                ? ""
                : value
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        if (
            fieldname
            === "hauling_distance_m"
            && data
        ) {

            const category = String(
                data.productivity_edit_category
                || data.category
                || ""
            ).trim();


            const material = String(
                data.material
                || ""
            ).trim();


            const row_key = String(
                data.productivity_edit_key
                || ""
            ).trim();


            if (
                (
                    category === "ADT"
                    ||
                    category === "Dozer"
                )
                &&
                material
                &&
                row_key
            ) {

                window.productivity_editable_rows = (
                    window.productivity_editable_rows
                    || {}
                );


                // ------------------------------------------------
                // IMPORTANT:
                //
                // Do not replace draft row object with stale
                // backend value before copying draft into it.
                // ------------------------------------------------

                if (
                    has_draft(
                        row_key
                    )
                ) {

                    data[
                        "hauling_distance_m"
                    ] = get_draft(
                        row_key
                    );
                }


                window.productivity_editable_rows[
                    row_key
                ] = data;


                const display_value = (
                    has_draft(
                        row_key
                    )
                        ? get_draft(
                            row_key
                        )
                        : String(
                            data.hauling_distance_m
                            || ""
                        )
                );


                return (
                    '<input'
                    + ' type="text"'
                    + ' class="form-control productivity-inline-edit productivity-machine-hd-input"'
                    + ' data-row-key="'
                    + escape_html(
                        row_key
                    )
                    + '"'
                    + ' data-field="hauling_distance_m"'
                    + ' value="'
                    + escape_html(
                        display_value
                    )
                    + '"'
                    + ' placeholder=""'
                    + ' autocomplete="off"'
                    + ' spellcheck="false"'
                    + ' />'
                );
            }


            if (
                category === "Excavator"
                || !material
            ) {

                return "";
            }
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


})();


// END KOSI_PRODUCTIVITY_HD_SESSION_DRAFT_V6


// ============================================================
// KOSI_PRODUCTIVITY_CLEAN_MACHINE_DRAFT_V7
//
// Old test values must NOT refill ADT / Dozer cells.
//
// Start a new V7 draft namespace.
//
// Only values typed by the user in THIS current report session
// are kept while scrolling.
// ============================================================

(function () {

    const PREFIX_V6 = (
        "productivity_hd_draft::"
    );


    const PREFIX_V7 = (
        "productivity_hd_draft_v7::"
    );


    // ========================================================
    // ONE-TIME REMOVE OLD V6 TEST DRAFTS
    // ========================================================

    if (
        !window.__productivity_clear_old_hd_drafts_v7
    ) {

        window.__productivity_clear_old_hd_drafts_v7 = true;


        try {

            const remove_keys = [];


            for (
                let i = 0;
                i < window.sessionStorage.length;
                i += 1
            ) {

                const key = (
                    window.sessionStorage.key(
                        i
                    )
                );


                if (
                    key
                    && key.startsWith(
                        PREFIX_V6
                    )
                ) {

                    remove_keys.push(
                        key
                    );
                }
            }


            remove_keys.forEach(
                function (
                    key
                ) {

                    window.sessionStorage.removeItem(
                        key
                    );
                }
            );

        } catch (
            error
        ) {

            console.warn(
                "Could not clear old Productivity HD drafts",
                error
            );
        }


        window.productivity_pending_machine_hd = {};
        window.productivity_dirty_overrides = {};
    }


    function draft_key(
        row_key
    ) {

        return (
            PREFIX_V7
            + row_key
        );
    }


    function has_draft(
        row_key
    ) {

        try {

            return (
                window.sessionStorage.getItem(
                    draft_key(
                        row_key
                    )
                )
                !== null
            );

        } catch (
            error
        ) {

            return false;
        }
    }


    function get_draft(
        row_key
    ) {

        try {

            return (
                window.sessionStorage.getItem(
                    draft_key(
                        row_key
                    )
                )
                ?? ""
            );

        } catch (
            error
        ) {

            return "";
        }
    }


    function set_draft(
        row_key,
        value
    ) {

        try {

            window.sessionStorage.setItem(
                draft_key(
                    row_key
                ),
                String(
                    value
                    ?? ""
                )
            );

        } catch (
            error
        ) {

            console.warn(
                "Could not save Productivity HD draft",
                error
            );
        }
    }


    function remove_draft(
        row_key
    ) {

        try {

            window.sessionStorage.removeItem(
                draft_key(
                    row_key
                )
            );

        } catch (
            error
        ) {
        }
    }


    window.productivity_hd_v7_has = has_draft;
    window.productivity_hd_v7_get = get_draft;
    window.productivity_hd_v7_set = set_draft;
    window.productivity_hd_v7_remove = remove_draft;


    // ========================================================
    // CAPTURE USER INPUT BEFORE DATATABLE CAN DESTROY ROW.
    // ========================================================

    function capture(
        event
    ) {

        const target = (
            event.target
        );


        if (
            !target
            || !target.matches
            || !target.matches(
                ".productivity-machine-hd-input"
            )
        ) {

            return;
        }


        const row_key = String(
            target.getAttribute(
                "data-row-key"
            )
            || ""
        ).trim();


        if (!row_key) {

            return;
        }


        const value = String(
            target.value
            ?? ""
        );


        set_draft(
            row_key,
            value
        );


        window.productivity_dirty_overrides = (
            window.productivity_dirty_overrides
            || {}
        );


        window.productivity_dirty_overrides[
            row_key
        ] = true;


        window.productivity_editable_rows = (
            window.productivity_editable_rows
            || {}
        );


        const data = (
            window.productivity_editable_rows[
                row_key
            ]
        );


        if (data) {

            data[
                "hauling_distance_m"
            ] = value;
        }


        if (
            typeof productivity_update_override_button
            === "function"
        ) {

            productivity_update_override_button();
        }
    }


    document.addEventListener(
        "input",
        capture,
        true
    );


    document.addEventListener(
        "keyup",
        capture,
        true
    );


    document.addEventListener(
        "change",
        capture,
        true
    );


    document.addEventListener(
        "blur",
        capture,
        true
    );


    // ========================================================
    // FINAL FORMATTER
    // ========================================================

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {

        return;
    }


    const previous_formatter = (
        config.formatter
    );


    function esc(
        value
    ) {

        return String(
            value
            ?? ""
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        if (
            fieldname
            === "hauling_distance_m"
            && data
        ) {

            const category = String(
                data.productivity_edit_category
                || ""
            ).trim();


            const material = String(
                data.material
                || ""
            ).trim();


            const row_key = String(
                data.productivity_edit_key
                || ""
            ).trim();


            if (
                (
                    category === "ADT"
                    ||
                    category === "Dozer"
                )
                &&
                material
                &&
                row_key
            ) {

                window.productivity_editable_rows = (
                    window.productivity_editable_rows
                    || {}
                );


                if (
                    has_draft(
                        row_key
                    )
                ) {

                    data[
                        "hauling_distance_m"
                    ] = get_draft(
                        row_key
                    );
                }


                window.productivity_editable_rows[
                    row_key
                ] = data;


                const display_value = (
                    has_draft(
                        row_key
                    )
                        ? get_draft(
                            row_key
                        )
                        : (
                            data.hauling_distance_m
                            || ""
                        )
                );


                return (
                    '<input'
                    + ' type="text"'
                    + ' class="form-control productivity-inline-edit productivity-machine-hd-input"'
                    + ' data-row-key="'
                    + esc(
                        row_key
                    )
                    + '"'
                    + ' data-field="hauling_distance_m"'
                    + ' value="'
                    + esc(
                        display_value
                    )
                    + '"'
                    + ' placeholder=""'
                    + ' autocomplete="off"'
                    + ' />'
                );
            }
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    // ========================================================
    // SAVE OVERRIDE
    //
    // Make V7 draft become the value that is sent to server.
    // ========================================================

    if (
        typeof productivity_save_one_override
        === "function"
    ) {

        const previous_save_one = (
            productivity_save_one_override
        );


        productivity_save_one_override = function (
            row_key
        ) {

            if (
                has_draft(
                    row_key
                )
            ) {

                const row_data = (
                    window.productivity_editable_rows
                    && window.productivity_editable_rows[
                        row_key
                    ]
                );


                if (row_data) {

                    row_data[
                        "hauling_distance_m"
                    ] = get_draft(
                        row_key
                    );
                }
            }


            return Promise.resolve(
                previous_save_one(
                    row_key
                )
            ).then(
                function (
                    result
                ) {

                    remove_draft(
                        row_key
                    );


                    return result;
                }
            );
        };
    }


})();


// END KOSI_PRODUCTIVITY_CLEAN_MACHINE_DRAFT_V7


// ============================================================
// KOSI_PRODUCTIVITY_VIEW_SCOPED_BROWSER_V8
//
// Hours and Material and Summary Per Machine are separate.
//
// Clear old unscoped draft values once.
//
// New row keys already contain:
//
// VIEW::Hours and Material::
//
// or
//
// VIEW::Summary Per Machine::
//
// so browser drafts also stay completely separate.
// ============================================================

(function () {

    if (
        window.__productivity_view_scoped_browser_v8
    ) {

        return;
    }


    window.__productivity_view_scoped_browser_v8 = true;


    try {

        const remove_keys = [];


        for (
            let i = 0;
            i < window.sessionStorage.length;
            i += 1
        ) {

            const key = (
                window.sessionStorage.key(
                    i
                )
            );


            if (
                key
                && (
                    key.startsWith(
                        "productivity_hd_draft::"
                    )
                    ||
                    key.startsWith(
                        "productivity_hd_draft_v7::"
                    )
                )
                &&
                !key.includes(
                    "VIEW::"
                )
            ) {

                remove_keys.push(
                    key
                );
            }
        }


        remove_keys.forEach(
            function (
                key
            ) {

                window.sessionStorage.removeItem(
                    key
                );
            }
        );


        console.log(
            "Productivity: removed old unscoped HD drafts:",
            remove_keys.length
        );

    } catch (
        error
    ) {

        console.warn(
            "Productivity view-scoped draft cleanup:",
            error
        );
    }


    window.productivity_pending_machine_hd = {};
    window.productivity_dirty_overrides = {};
    window.productivity_editable_rows = {};

})();


// END KOSI_PRODUCTIVITY_VIEW_SCOPED_BROWSER_V8


// ============================================================
// KOSI_PRODUCTIVITY_CLEAR_SUMMARY_MACHINE_DRAFTS_V9
//
// Remove OLD/test browser drafts belonging specifically to:
//
//     Summary Per Machine
//
// Do NOT remove Hours and Material drafts.
//
// This runs once whenever Productivity JS is loaded.
//
// After this cleanup, new user-entered Summary Per Machine
// values can again stay in session while scrolling until
// Save Override is clicked.
// ============================================================

(function () {

    if (
        window.__productivity_clear_summary_machine_drafts_v9
    ) {

        return;
    }


    window.__productivity_clear_summary_machine_drafts_v9 = true;


    try {

        const remove_keys = [];


        for (
            let index = 0;
            index < window.sessionStorage.length;
            index += 1
        ) {

            const key = (
                window.sessionStorage.key(
                    index
                )
            );


            if (
                key
                &&
                key.includes(
                    "VIEW::Summary Per Machine::"
                )
                &&
                (
                    key.startsWith(
                        "productivity_hd_draft::"
                    )
                    ||
                    key.startsWith(
                        "productivity_hd_draft_v7::"
                    )
                )
            ) {

                remove_keys.push(
                    key
                );
            }
        }


        remove_keys.forEach(
            function (
                key
            ) {

                window.sessionStorage.removeItem(
                    key
                );
            }
        );


        console.log(
            "Productivity Summary Per Machine old drafts cleared:",
            remove_keys.length
        );

    } catch (
        error
    ) {

        console.warn(
            "Could not clear old Summary Per Machine drafts:",
            error
        );
    }


    // --------------------------------------------------------
    // Remove stale in-memory Summary Per Machine values too.
    // --------------------------------------------------------

    [
        "productivity_pending_machine_hd",
        "productivity_dirty_overrides",
        "productivity_editable_rows"
    ].forEach(
        function (
            map_name
        ) {

            const map = (
                window[
                    map_name
                ]
            );


            if (
                !map
                || typeof map !== "object"
            ) {

                return;
            }


            Object.keys(
                map
            ).forEach(
                function (
                    row_key
                ) {

                    if (
                        String(
                            row_key
                        ).includes(
                            "VIEW::Summary Per Machine::"
                        )
                    ) {

                        delete map[
                            row_key
                        ];
                    }
                }
            );
        }
    );

})();


// END KOSI_PRODUCTIVITY_CLEAR_SUMMARY_MACHINE_DRAFTS_V9


// ============================================================
// KOSI_PRODUCTIVITY_SUMMARY_SCROLL_DRAFT_V10
//
// SUMMARY PER MACHINE ONLY.
//
// Problem:
// Frappe DataTable destroys/recycles rows while scrolling.
//
// Required behaviour:
//
// 1. User types ADT01 Coal = 500-1000
// 2. User scrolls to ADT20 / Dozers
// 3. User captures more values
// 4. User scrolls back
// 5. ADT01 Coal MUST STILL show 500-1000
// 6. User clicks Save Override ONCE
//
// Hours and Material is NOT affected.
// ============================================================

(function () {

    if (
        window.__productivity_summary_scroll_draft_v10
    ) {
        return;
    }


    window.__productivity_summary_scroll_draft_v10 = true;


    const PREFIX = (
        "productivity_summary_machine_v10::"
    );


    function is_summary_key(
        row_key
    ) {

        return String(
            row_key
            || ""
        ).includes(
            "VIEW::Summary Per Machine::"
        );
    }


    function storage_key(
        row_key
    ) {

        return (
            PREFIX
            + String(
                row_key
                || ""
            )
        );
    }


    function has_draft(
        row_key
    ) {

        if (
            !row_key
            || !is_summary_key(
                row_key
            )
        ) {
            return false;
        }


        try {

            return (
                sessionStorage.getItem(
                    storage_key(
                        row_key
                    )
                )
                !== null
            );

        } catch (
            error
        ) {

            return (
                window.productivity_summary_v10_memory
                &&
                Object.prototype.hasOwnProperty.call(
                    window.productivity_summary_v10_memory,
                    row_key
                )
            );
        }
    }


    function get_draft(
        row_key
    ) {

        try {

            const value = (
                sessionStorage.getItem(
                    storage_key(
                        row_key
                    )
                )
            );


            return (
                value === null
                    ? ""
                    : value
            );

        } catch (
            error
        ) {

            return String(
                (
                    window.productivity_summary_v10_memory
                    || {}
                )[
                    row_key
                ]
                || ""
            );
        }
    }


    function set_draft(
        row_key,
        value
    ) {

        if (
            !row_key
            || !is_summary_key(
                row_key
            )
        ) {
            return;
        }


        value = String(
            value
            ?? ""
        );


        try {

            sessionStorage.setItem(
                storage_key(
                    row_key
                ),
                value
            );

        } catch (
            error
        ) {

            window.productivity_summary_v10_memory = (
                window.productivity_summary_v10_memory
                || {}
            );


            window.productivity_summary_v10_memory[
                row_key
            ] = value;
        }


        // Existing Save Override structures.
        window.productivity_pending_machine_hd = (
            window.productivity_pending_machine_hd
            || {}
        );


        window.productivity_pending_machine_hd[
            row_key
        ] = value;


        window.productivity_dirty_overrides = (
            window.productivity_dirty_overrides
            || {}
        );


        window.productivity_dirty_overrides[
            row_key
        ] = true;


        update_row_objects(
            row_key,
            value
        );
    }


    function update_row_objects(
        row_key,
        value
    ) {

        // ----------------------------------------------------
        // Editable row map used by Save Override.
        // ----------------------------------------------------

        window.productivity_editable_rows = (
            window.productivity_editable_rows
            || {}
        );


        const editable_row = (
            window.productivity_editable_rows[
                row_key
            ]
        );


        if (editable_row) {

            editable_row[
                "hauling_distance_m"
            ] = value;
        }


        // ----------------------------------------------------
        // Query Report data.
        // ----------------------------------------------------

        if (
            frappe.query_report
            && Array.isArray(
                frappe.query_report.data
            )
        ) {

            frappe.query_report.data.forEach(
                function (
                    report_row
                ) {

                    if (
                        report_row
                        &&
                        String(
                            report_row.productivity_edit_key
                            || ""
                        ) === row_key
                    ) {

                        report_row[
                            "hauling_distance_m"
                        ] = value;
                    }
                }
            );
        }


        // ----------------------------------------------------
        // DataTable internal data where available.
        // ----------------------------------------------------

        try {

            const data = (
                frappe.query_report
                && frappe.query_report.datatable
                && frappe.query_report.datatable.datamanager
                && frappe.query_report.datatable.datamanager.data
            );


            if (
                Array.isArray(
                    data
                )
            ) {

                data.forEach(
                    function (
                        item
                    ) {

                        const row_data = (
                            item
                            && (
                                item.__data
                                || item
                            )
                        );


                        if (
                            row_data
                            &&
                            String(
                                row_data.productivity_edit_key
                                || ""
                            ) === row_key
                        ) {

                            row_data[
                                "hauling_distance_m"
                            ] = value;
                        }
                    }
                );
            }

        } catch (
            error
        ) {
            // DataTable internals differ by Frappe version.
        }
    }


    // ========================================================
    // CAPTURE EVERY KEYSTROKE BEFORE DATATABLE CAN REMOVE ROW
    // ========================================================

    function capture_input(
        event
    ) {

        const target = (
            event
            && event.target
        );


        if (
            !target
            || !target.matches
            || !target.matches(
                ".productivity-machine-hd-input"
            )
        ) {
            return;
        }


        const row_key = String(
            target.getAttribute(
                "data-row-key"
            )
            || ""
        ).trim();


        if (
            !is_summary_key(
                row_key
            )
        ) {
            return;
        }


        const value = String(
            target.value
            ?? ""
        );


        set_draft(
            row_key,
            value
        );


        target.classList.add(
            "productivity-override-dirty"
        );


        if (
            typeof productivity_update_override_button
            === "function"
        ) {

            productivity_update_override_button();
        }
    }


    [
        "input",
        "keyup",
        "change",
        "blur"
    ].forEach(
        function (
            event_name
        ) {

            document.addEventListener(
                event_name,
                capture_input,
                true
            );
        }
    );


    // ========================================================
    // RESTORE VISIBLE INPUTS
    //
    // Frappe can recycle the same DOM cell during scrolling.
    // Therefore we restore AFTER every scroll frame.
    // ========================================================

    let sync_scheduled = false;


    function sync_visible_inputs() {

        sync_scheduled = false;


        document
            .querySelectorAll(
                ".productivity-machine-hd-input"
            )
            .forEach(
                function (
                    input
                ) {

                    const row_key = String(
                        input.getAttribute(
                            "data-row-key"
                        )
                        || ""
                    ).trim();


                    if (
                        !is_summary_key(
                            row_key
                        )
                    ) {
                        return;
                    }


                    if (
                        !has_draft(
                            row_key
                        )
                    ) {
                        return;
                    }


                    const draft = (
                        get_draft(
                            row_key
                        )
                    );


                    if (
                        input.value
                        !== draft
                    ) {

                        input.value = draft;
                    }


                    input.classList.add(
                        "productivity-override-dirty"
                    );


                    update_row_objects(
                        row_key,
                        draft
                    );
                }
            );
    }


    function schedule_sync() {

        if (
            sync_scheduled
        ) {
            return;
        }


        sync_scheduled = true;


        window.requestAnimationFrame(
            function () {

                sync_visible_inputs();


                // DataTable sometimes redraws one frame later.
                setTimeout(
                    sync_visible_inputs,
                    25
                );


                setTimeout(
                    sync_visible_inputs,
                    100
                );
            }
        );
    }


    document.addEventListener(
        "scroll",
        schedule_sync,
        true
    );


    document.addEventListener(
        "wheel",
        schedule_sync,
        true
    );


    document.addEventListener(
        "touchmove",
        schedule_sync,
        true
    );


    // ========================================================
    // MUTATION OBSERVER
    //
    // Restore draft when DataTable creates/recycles rows.
    // ========================================================

    const observer = new MutationObserver(
        function () {

            schedule_sync();
        }
    );


    observer.observe(
        document.body,
        {
            childList:
                true,

            subtree:
                true,

            attributes:
                true,

            attributeFilter: [
                "data-row-key",
                "value"
            ]
        }
    );


    // ========================================================
    // BEFORE SAVE OVERRIDE:
    //
    // Rehydrate ALL Summary Per Machine drafts, including rows
    // currently far outside the visible scroll area.
    // ========================================================

    function all_summary_drafts() {

        const result = {};


        try {

            for (
                let i = 0;
                i < sessionStorage.length;
                i += 1
            ) {

                const key = (
                    sessionStorage.key(
                        i
                    )
                );


                if (
                    !key
                    || !key.startsWith(
                        PREFIX
                    )
                ) {
                    continue;
                }


                const row_key = (
                    key.substring(
                        PREFIX.length
                    )
                );


                if (
                    !is_summary_key(
                        row_key
                    )
                ) {
                    continue;
                }


                result[
                    row_key
                ] = (
                    sessionStorage.getItem(
                        key
                    )
                    ?? ""
                );
            }

        } catch (
            error
        ) {
        }


        return result;
    }


    function prepare_all_drafts_for_save() {

        const drafts = (
            all_summary_drafts()
        );


        window.productivity_dirty_overrides = (
            window.productivity_dirty_overrides
            || {}
        );


        Object.entries(
            drafts
        ).forEach(
            function (
                entry
            ) {

                const row_key = (
                    entry[0]
                );


                const value = (
                    entry[1]
                );


                window.productivity_dirty_overrides[
                    row_key
                ] = true;


                update_row_objects(
                    row_key,
                    value
                );
            }
        );
    }


    // Wrap Save All where available.
    if (
        typeof productivity_save_all_overrides
        === "function"
    ) {

        const previous_save_all_v10 = (
            productivity_save_all_overrides
        );


        productivity_save_all_overrides = function () {

            prepare_all_drafts_for_save();


            return previous_save_all_v10.apply(
                this,
                arguments
            );
        };
    }


    // Wrap individual save where available.
    if (
        typeof productivity_save_one_override
        === "function"
    ) {

        const previous_save_one_v10 = (
            productivity_save_one_override
        );


        productivity_save_one_override = function (
            row_key
        ) {

            row_key = String(
                row_key
                || ""
            ).trim();


            if (
                has_draft(
                    row_key
                )
            ) {

                update_row_objects(
                    row_key,
                    get_draft(
                        row_key
                    )
                );
            }


            return previous_save_one_v10.apply(
                this,
                arguments
            );
        };
    }


    // Initial sync.
    setTimeout(
        sync_visible_inputs,
        100
    );


    setTimeout(
        sync_visible_inputs,
        500
    );

})();


// END KOSI_PRODUCTIVITY_SUMMARY_SCROLL_DRAFT_V10


// ============================================================
// KOSI_PRODUCTIVITY_SUMMARY_TO_AREA_SCROLL_V11
//
// SUMMARY PER MACHINE ONLY.
//
// Makes To Area behave exactly like Hauling Distance:
//
//     User types To Area
//     Scrolls away
//     Row is recycled by DataTable
//     Scrolls back
//     Value is still there
//
// Then Save Override permanently saves:
//
//     To Area
//     Hauling Distance
//
// Hours and Material is NOT affected.
// From Area remains automatic.
// ============================================================

(function () {

    if (
        window.__productivity_summary_to_area_scroll_v11
    ) {

        return;
    }


    window.__productivity_summary_to_area_scroll_v11 = true;


    const PREFIX = (
        "productivity_summary_to_area_v11::"
    );


    // ========================================================
    // HELPERS
    // ========================================================

    function is_summary_key(
        row_key
    ) {

        return String(
            row_key
            || ""
        ).includes(
            "VIEW::Summary Per Machine::"
        );
    }


    function storage_key(
        row_key
    ) {

        return (
            PREFIX
            + String(
                row_key
                || ""
            )
        );
    }


    function has_draft(
        row_key
    ) {

        if (
            !row_key
            || !is_summary_key(
                row_key
            )
        ) {

            return false;
        }


        try {

            return (
                sessionStorage.getItem(
                    storage_key(
                        row_key
                    )
                )
                !== null
            );

        } catch (
            error
        ) {

            return (
                window.productivity_summary_to_area_memory_v11
                &&
                Object.prototype.hasOwnProperty.call(
                    window.productivity_summary_to_area_memory_v11,
                    row_key
                )
            );
        }
    }


    function get_draft(
        row_key
    ) {

        try {

            const value = (
                sessionStorage.getItem(
                    storage_key(
                        row_key
                    )
                )
            );


            return (
                value === null
                    ? ""
                    : value
            );

        } catch (
            error
        ) {

            return String(
                (
                    window.productivity_summary_to_area_memory_v11
                    || {}
                )[
                    row_key
                ]
                || ""
            );
        }
    }


    function set_draft(
        row_key,
        value
    ) {

        if (
            !row_key
            || !is_summary_key(
                row_key
            )
        ) {

            return;
        }


        value = String(
            value
            ?? ""
        );


        try {

            sessionStorage.setItem(
                storage_key(
                    row_key
                ),
                value
            );

        } catch (
            error
        ) {

            window.productivity_summary_to_area_memory_v11 = (
                window.productivity_summary_to_area_memory_v11
                || {}
            );


            window.productivity_summary_to_area_memory_v11[
                row_key
            ] = value;
        }


        // Save Override dirty map.
        window.productivity_dirty_overrides = (
            window.productivity_dirty_overrides
            || {}
        );


        window.productivity_dirty_overrides[
            row_key
        ] = true;


        update_row_objects(
            row_key,
            value
        );
    }


    function remove_draft(
        row_key
    ) {

        try {

            sessionStorage.removeItem(
                storage_key(
                    row_key
                )
            );

        } catch (
            error
        ) {

            if (
                window.productivity_summary_to_area_memory_v11
            ) {

                delete (
                    window.productivity_summary_to_area_memory_v11[
                        row_key
                    ]
                );
            }
        }
    }


    // ========================================================
    // UPDATE ALL ROW OBJECTS USED BY SAVE OVERRIDE
    // ========================================================

    function update_row_objects(
        row_key,
        value
    ) {

        window.productivity_editable_rows = (
            window.productivity_editable_rows
            || {}
        );


        const editable_row = (
            window.productivity_editable_rows[
                row_key
            ]
        );


        if (editable_row) {

            editable_row[
                "to_area"
            ] = value;
        }


        // Query Report data.
        if (
            frappe.query_report
            && Array.isArray(
                frappe.query_report.data
            )
        ) {

            frappe.query_report.data.forEach(
                function (
                    report_row
                ) {

                    if (
                        report_row
                        &&
                        String(
                            report_row.productivity_edit_key
                            || ""
                        ) === row_key
                    ) {

                        report_row[
                            "to_area"
                        ] = value;
                    }
                }
            );
        }


        // DataTable internal data where available.
        try {

            const table_data = (
                frappe.query_report
                && frappe.query_report.datatable
                && frappe.query_report.datatable.datamanager
                && frappe.query_report.datatable.datamanager.data
            );


            if (
                Array.isArray(
                    table_data
                )
            ) {

                table_data.forEach(
                    function (
                        item
                    ) {

                        const row_data = (
                            item
                            && (
                                item.__data
                                || item
                            )
                        );


                        if (
                            row_data
                            &&
                            String(
                                row_data.productivity_edit_key
                                || ""
                            ) === row_key
                        ) {

                            row_data[
                                "to_area"
                            ] = value;
                        }
                    }
                );
            }

        } catch (
            error
        ) {
        }
    }


    // ========================================================
    // CAPTURE USER TYPING IMMEDIATELY
    // ========================================================

    function capture_to_area(
        event
    ) {

        const target = (
            event
            && event.target
        );


        if (
            !target
            || !target.matches
            || !target.matches(
                ".productivity-machine-to-area-input"
            )
        ) {

            return;
        }


        const row_key = String(
            target.getAttribute(
                "data-row-key"
            )
            || ""
        ).trim();


        if (
            !is_summary_key(
                row_key
            )
        ) {

            return;
        }


        const value = String(
            target.value
            ?? ""
        );


        set_draft(
            row_key,
            value
        );


        target.classList.add(
            "productivity-override-dirty"
        );


        if (
            typeof productivity_update_override_button
            === "function"
        ) {

            productivity_update_override_button();
        }
    }


    [
        "input",
        "keyup",
        "change",
        "blur"
    ].forEach(
        function (
            event_name
        ) {

            document.addEventListener(
                event_name,
                capture_to_area,
                true
            );
        }
    );


    // ========================================================
    // FINAL FORMATTER
    //
    // Force To Area to be an editable input for every:
    //
    // ADT + Material
    // Dozer + Material
    //
    // in Summary Per Machine.
    // ========================================================

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {

        return;
    }


    const previous_formatter = (
        config.formatter
    );


    function escape_html(
        value
    ) {

        return String(
            value
            ?? ""
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            (
                column
                && column.fieldname
            )
            || ""
        ).trim();


        if (
            fieldname === "to_area"
            && data
        ) {

            const category = String(
                data.productivity_edit_category
                || data.category
                || ""
            ).trim();


            const material = String(
                data.material
                || ""
            ).trim();


            const row_key = String(
                data.productivity_edit_key
                || ""
            ).trim();


            const is_machine_row = (
                (
                    category === "ADT"
                    ||
                    category === "Dozer"
                )
                &&
                material
                &&
                row_key
                &&
                is_summary_key(
                    row_key
                )
            );


            if (
                is_machine_row
            ) {

                window.productivity_editable_rows = (
                    window.productivity_editable_rows
                    || {}
                );


                if (
                    has_draft(
                        row_key
                    )
                ) {

                    data[
                        "to_area"
                    ] = get_draft(
                        row_key
                    );
                }


                window.productivity_editable_rows[
                    row_key
                ] = data;


                const display_value = (
                    has_draft(
                        row_key
                    )
                        ? get_draft(
                            row_key
                        )
                        : String(
                            data.to_area
                            || ""
                        )
                );


                return (
                    '<input'
                    + ' type="text"'
                    + ' class="form-control productivity-inline-edit productivity-machine-to-area-input"'
                    + ' data-row-key="'
                    + escape_html(
                        row_key
                    )
                    + '"'
                    + ' data-field="to_area"'
                    + ' value="'
                    + escape_html(
                        display_value
                    )
                    + '"'
                    + ' placeholder=""'
                    + ' autocomplete="off"'
                    + ' spellcheck="false"'
                    + ' />'
                );
            }
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    // ========================================================
    // RESTORE INPUT AFTER DATATABLE SCROLL / REDRAW
    // ========================================================

    let sync_scheduled = false;


    function sync_visible_to_area() {

        sync_scheduled = false;


        document
            .querySelectorAll(
                ".productivity-machine-to-area-input"
            )
            .forEach(
                function (
                    input
                ) {

                    const row_key = String(
                        input.getAttribute(
                            "data-row-key"
                        )
                        || ""
                    ).trim();


                    if (
                        !is_summary_key(
                            row_key
                        )
                        || !has_draft(
                            row_key
                        )
                    ) {

                        return;
                    }


                    const draft = (
                        get_draft(
                            row_key
                        )
                    );


                    if (
                        input.value !== draft
                    ) {

                        input.value = draft;
                    }


                    input.classList.add(
                        "productivity-override-dirty"
                    );


                    update_row_objects(
                        row_key,
                        draft
                    );
                }
            );
    }


    function schedule_sync() {

        if (
            sync_scheduled
        ) {

            return;
        }


        sync_scheduled = true;


        requestAnimationFrame(
            function () {

                sync_visible_to_area();


                setTimeout(
                    sync_visible_to_area,
                    25
                );


                setTimeout(
                    sync_visible_to_area,
                    100
                );
            }
        );
    }


    document.addEventListener(
        "scroll",
        schedule_sync,
        true
    );


    document.addEventListener(
        "wheel",
        schedule_sync,
        true
    );


    document.addEventListener(
        "touchmove",
        schedule_sync,
        true
    );


    const observer = new MutationObserver(
        function () {

            schedule_sync();
        }
    );


    observer.observe(
        document.body,
        {
            childList:
                true,

            subtree:
                true,

            attributes:
                true,

            attributeFilter: [
                "data-row-key",
                "value"
            ]
        }
    );


    // ========================================================
    // ALL TO AREA DRAFTS
    // ========================================================

    function all_drafts() {

        const result = {};


        try {

            for (
                let index = 0;
                index < sessionStorage.length;
                index += 1
            ) {

                const key = (
                    sessionStorage.key(
                        index
                    )
                );


                if (
                    !key
                    || !key.startsWith(
                        PREFIX
                    )
                ) {

                    continue;
                }


                const row_key = (
                    key.substring(
                        PREFIX.length
                    )
                );


                if (
                    !is_summary_key(
                        row_key
                    )
                ) {

                    continue;
                }


                result[
                    row_key
                ] = (
                    sessionStorage.getItem(
                        key
                    )
                    ?? ""
                );
            }

        } catch (
            error
        ) {
        }


        return result;
    }


    // ========================================================
    // BEFORE SAVE OVERRIDE
    //
    // Rehydrate all To Area values, including rows outside
    // the current scroll window.
    // ========================================================

    function prepare_to_area_for_save() {

        const drafts = (
            all_drafts()
        );


        window.productivity_dirty_overrides = (
            window.productivity_dirty_overrides
            || {}
        );


        Object.entries(
            drafts
        ).forEach(
            function (
                entry
            ) {

                const row_key = (
                    entry[0]
                );


                const to_area = (
                    entry[1]
                );


                window.productivity_dirty_overrides[
                    row_key
                ] = true;


                update_row_objects(
                    row_key,
                    to_area
                );
            }
        );
    }


    if (
        typeof productivity_save_all_overrides
        === "function"
    ) {

        const previous_save_all_v11 = (
            productivity_save_all_overrides
        );


        productivity_save_all_overrides = function () {

            prepare_to_area_for_save();


            return previous_save_all_v11.apply(
                this,
                arguments
            );
        };
    }


    if (
        typeof productivity_save_one_override
        === "function"
    ) {

        const previous_save_one_v11 = (
            productivity_save_one_override
        );


        productivity_save_one_override = function (
            row_key
        ) {

            row_key = String(
                row_key
                || ""
            ).trim();


            if (
                has_draft(
                    row_key
                )
            ) {

                update_row_objects(
                    row_key,
                    get_draft(
                        row_key
                    )
                );
            }


            const result = (
                previous_save_one_v11.apply(
                    this,
                    arguments
                )
            );


            return Promise.resolve(
                result
            ).then(
                function (
                    response
                ) {

                    // Successful permanent save.
                    remove_draft(
                        row_key
                    );


                    return response;
                }
            );
        };
    }


    // ========================================================
    // STYLE
    //
    // Same capture style as Hauling Distance.
    // ========================================================

    if (
        !document.getElementById(
            "productivity-machine-to-area-v11-style"
        )
    ) {

        const style = document.createElement(
            "style"
        );


        style.id = (
            "productivity-machine-to-area-v11-style"
        );


        style.innerHTML = `

            .productivity-machine-to-area-input {
                width: 100% !important;
                height: 26px !important;

                padding:
                    2px 6px !important;

                border:
                    1px solid #d99a00 !important;

                background:
                    #fff8dd !important;

                box-shadow:
                    none !important;

                font-size:
                    12px !important;

                cursor:
                    text !important;

                pointer-events:
                    auto !important;

                position:
                    relative !important;

                z-index:
                    5 !important;
            }


            .productivity-machine-to-area-input:focus {
                background:
                    #ffffff !important;

                border:
                    2px solid #d99a00 !important;

                outline:
                    none !important;
            }


            .productivity-machine-to-area-input.productivity-override-dirty {
                background:
                    #fff1b8 !important;

                border:
                    2px solid #d99a00 !important;
            }

        `;


        document.head.appendChild(
            style
        );
    }


    // Initial restore.
    setTimeout(
        sync_visible_to_area,
        100
    );


    setTimeout(
        sync_visible_to_area,
        500
    );

})();


// END KOSI_PRODUCTIVITY_SUMMARY_TO_AREA_SCROLL_V11


// ============================================================
// KOSI_PRODUCTIVITY_CLEAN_SAVE_PIPELINE_V13
//
// FINAL Save Override implementation.
//
// IMPORTANT:
//
// Do NOT pass through the many previous save wrappers.
//
// Save directly:
//
//     Browser drafts
//         |
//         v
//     save_productivity_area_override()
//         |
//         v
//     save_productivity_override_snapshot()
//         |
//         v
//     Permanent PDF
//
// Supports:
//
//     To Area
//     Hauling Distance
//
// including rows that are currently outside the visible
// DataTable scroll window.
//
// Hours and Material and Summary Per Machine remain separated
// by their VIEW::...:: row keys.
// ============================================================


(function () {

    if (
        window.__productivity_clean_save_pipeline_v13
    ) {

        return;
    }


    window.__productivity_clean_save_pipeline_v13 = true;


    // ========================================================
    // FRAPPE CALL -> REAL PROMISE
    // ========================================================

    function productivity_v13_call(
        method,
        args
    ) {

        return new Promise(
            function (
                resolve,
                reject
            ) {

                frappe.call({

                    method:
                        method,

                    args:
                        args || {},


                    callback: function (
                        response
                    ) {

                        if (
                            response
                            && response.exc
                        ) {

                            reject(
                                new Error(
                                    String(
                                        response.exc
                                    )
                                )
                            );

                            return;
                        }


                        resolve(
                            response
                                ? response.message
                                : null
                        );
                    },


                    error: function (
                        error
                    ) {

                        reject(
                            error
                            || new Error(
                                "Server request failed."
                            )
                        );
                    }

                });
            }
        );
    }


    // ========================================================
    // ERROR TEXT
    // ========================================================

    function productivity_v13_error_text(
        error
    ) {

        if (!error) {

            return (
                "Unknown error."
            );
        }


        if (
            error.message
        ) {

            return String(
                error.message
            );
        }


        if (
            error.responseJSON
            && error.responseJSON
                ._server_messages
        ) {

            return String(
                error.responseJSON
                    ._server_messages
            );
        }


        if (
            error.responseText
        ) {

            return String(
                error.responseText
            );
        }


        try {

            return JSON.stringify(
                error
            );

        } catch (
            ignored
        ) {

            return String(
                error
            );
        }
    }


    // ========================================================
    // GET CURRENT REPORT VIEW
    // ========================================================

    function productivity_v13_current_view() {

        try {

            const filters = (
                frappe.query_report
                    .get_filter_values()
                || {}
            );


            return String(
                filters.summary_view
                || ""
            ).trim();

        } catch (
            error
        ) {

            return "";
        }
    }


    // ========================================================
    // FIND REPORT ROW BY SCOPED KEY
    // ========================================================

    function productivity_v13_find_row(
        row_key
    ) {

        row_key = String(
            row_key
            || ""
        ).trim();


        if (!row_key) {

            return null;
        }


        // ----------------------------------------------------
        // Editable-row cache.
        // ----------------------------------------------------

        if (
            window.productivity_editable_rows
            && window.productivity_editable_rows[
                row_key
            ]
        ) {

            return (
                window.productivity_editable_rows[
                    row_key
                ]
            );
        }


        // ----------------------------------------------------
        // Full Query Report dataset.
        // ----------------------------------------------------

        if (
            frappe.query_report
            && Array.isArray(
                frappe.query_report.data
            )
        ) {

            const match = (
                frappe.query_report.data.find(
                    function (
                        row
                    ) {

                        return (
                            row
                            &&
                            String(
                                row.productivity_edit_key
                                || ""
                            ).trim()
                            === row_key
                        );
                    }
                )
            );


            if (match) {

                return match;
            }
        }


        return null;
    }


    // ========================================================
    // COLLECT CURRENT VISIBLE INPUTS FIRST
    //
    // This catches the field that currently has keyboard focus.
    // ========================================================

    function productivity_v13_visible_values() {

        const values = {};


        document
            .querySelectorAll(
                ".productivity-inline-edit[data-row-key]"
            )
            .forEach(
                function (
                    input
                ) {

                    const row_key = String(
                        input.getAttribute(
                            "data-row-key"
                        )
                        || ""
                    ).trim();


                    const field = String(
                        input.getAttribute(
                            "data-field"
                        )
                        || ""
                    ).trim();


                    if (
                        !row_key
                        || !field
                    ) {

                        return;
                    }


                    values[
                        row_key
                    ] = (
                        values[
                            row_key
                        ]
                        || {}
                    );


                    values[
                        row_key
                    ][
                        field
                    ] = String(
                        input.value
                        ?? ""
                    );


                    window.productivity_dirty_overrides = (
                        window.productivity_dirty_overrides
                        || {}
                    );


                    window.productivity_dirty_overrides[
                        row_key
                    ] = true;
                }
            );


        return values;
    }


    // ========================================================
    // READ UNSAVED VALUES STORED WHILE SCROLLING
    // ========================================================

    function productivity_v13_session_values() {

        const values = {};


        const hd_prefixes = [

            "productivity_summary_machine_v10::",

            "productivity_hd_draft_v7::",

            "productivity_hd_draft::"
        ];


        const to_area_prefixes = [

            "productivity_summary_to_area_v11::"
        ];


        try {

            for (
                let index = 0;
                index < sessionStorage.length;
                index += 1
            ) {

                const storage_name = (
                    sessionStorage.key(
                        index
                    )
                );


                if (!storage_name) {

                    continue;
                }


                for (
                    const prefix
                    of hd_prefixes
                ) {

                    if (
                        storage_name.startsWith(
                            prefix
                        )
                    ) {

                        const row_key = (
                            storage_name.substring(
                                prefix.length
                            )
                        );


                        values[
                            row_key
                        ] = (
                            values[
                                row_key
                            ]
                            || {}
                        );


                        values[
                            row_key
                        ].hauling_distance_m = (
                            sessionStorage.getItem(
                                storage_name
                            )
                            ?? ""
                        );


                        break;
                    }
                }


                for (
                    const prefix
                    of to_area_prefixes
                ) {

                    if (
                        storage_name.startsWith(
                            prefix
                        )
                    ) {

                        const row_key = (
                            storage_name.substring(
                                prefix.length
                            )
                        );


                        values[
                            row_key
                        ] = (
                            values[
                                row_key
                            ]
                            || {}
                        );


                        values[
                            row_key
                        ].to_area = (
                            sessionStorage.getItem(
                                storage_name
                            )
                            ?? ""
                        );


                        break;
                    }
                }
            }

        } catch (
            error
        ) {

            console.warn(
                "Productivity V13 session draft scan failed:",
                error
            );
        }


        return values;
    }


    // ========================================================
    // MERGE VALUES
    // ========================================================

    function productivity_v13_collect_rows() {

        const visible = (
            productivity_v13_visible_values()
        );


        const stored = (
            productivity_v13_session_values()
        );


        const dirty = (
            window.productivity_dirty_overrides
            || {}
        );


        const keys = new Set();


        Object.keys(
            dirty
        ).forEach(
            function (
                key
            ) {

                keys.add(
                    key
                );
            }
        );


        Object.keys(
            visible
        ).forEach(
            function (
                key
            ) {

                keys.add(
                    key
                );
            }
        );


        Object.keys(
            stored
        ).forEach(
            function (
                key
            ) {

                keys.add(
                    key
                );
            }
        );


        const current_view = (
            productivity_v13_current_view()
        );


        const expected_prefix = (
            current_view
                ? (
                    "VIEW::"
                    + current_view
                    + "::"
                )
                : ""
        );


        const result = [];


        keys.forEach(
            function (
                row_key
            ) {

                row_key = String(
                    row_key
                    || ""
                ).trim();


                if (!row_key) {

                    return;
                }


                // ------------------------------------------------
                // Never save another report view's draft.
                // ------------------------------------------------

                if (
                    row_key.startsWith(
                        "VIEW::"
                    )
                    &&
                    expected_prefix
                    &&
                    !row_key.startsWith(
                        expected_prefix
                    )
                ) {

                    return;
                }


                const row = (
                    productivity_v13_find_row(
                        row_key
                    )
                );


                if (!row) {

                    console.warn(
                        "Productivity V13 could not find row:",
                        row_key
                    );

                    return;
                }


                const draft = {};


                // Existing backend values.
                draft.to_area = String(
                    row.to_area
                    || ""
                );


                draft.hauling_distance_m = String(
                    row.hauling_distance_m
                    || ""
                );


                // Session values.
                if (
                    stored[
                        row_key
                    ]
                ) {

                    Object.assign(
                        draft,
                        stored[
                            row_key
                        ]
                    );
                }


                // Visible value wins because it is newest.
                if (
                    visible[
                        row_key
                    ]
                ) {

                    Object.assign(
                        draft,
                        visible[
                            row_key
                        ]
                    );
                }


                result.push({

                    row_key:
                        row_key,

                    site:
                        row.productivity_edit_site
                        || "",

                    start_date:
                        row.productivity_edit_start_date
                        || "",

                    end_date:
                        row.productivity_edit_end_date
                        || "",

                    shift:
                        row.productivity_edit_shift
                        || "",

                    monthly_production_plan:
                        row.productivity_edit_monthly_plan
                        || "",

                    category:
                        row.productivity_edit_category
                        || row.category
                        || "",

                    machine:
                        row.productivity_edit_machine
                        || row.machine
                        || row.label
                        || "",

                    material:
                        row.productivity_edit_material
                        || row.material
                        || "",

                    from_area:
                        row.from_area
                        || "",

                    to_area:
                        draft.to_area
                        || "",

                    hauling_distance_m:
                        draft.hauling_distance_m
                        || ""
                });
            }
        );


        return result;
    }


    // ========================================================
    // CLEAR ONE SAVED ROW'S TEMPORARY DRAFTS
    // ========================================================

    function productivity_v13_clear_drafts(
        row_key
    ) {

        const prefixes = [

            "productivity_summary_machine_v10::",

            "productivity_summary_to_area_v11::",

            "productivity_hd_draft_v7::",

            "productivity_hd_draft::"
        ];


        try {

            prefixes.forEach(
                function (
                    prefix
                ) {

                    sessionStorage.removeItem(
                        prefix
                        + row_key
                    );
                }
            );

        } catch (
            error
        ) {
        }


        if (
            window.productivity_dirty_overrides
        ) {

            delete (
                window.productivity_dirty_overrides[
                    row_key
                ]
            );
        }


        if (
            window.productivity_pending_machine_hd
        ) {

            delete (
                window.productivity_pending_machine_hd[
                    row_key
                ]
            );
        }
    }


    // ========================================================
    // DIRECT ROW SAVE
    // ========================================================

    async function productivity_v13_save_row(
        row
    ) {

        const result = (
            await productivity_v13_call(

                "is_production.production.report.productivity.productivity.save_productivity_area_override",

                row
            )
        );


        if (
            !result
            || !result.saved
        ) {

            throw new Error(
                "Productivity row override was not saved: "
                + (
                    row.machine
                    || row.material
                    || row.row_key
                )
            );
        }


        return result;
    }


    // ========================================================
    // DIRECT SNAPSHOT SAVE
    // ========================================================

    async function productivity_v13_save_snapshot() {

        if (
            !frappe.query_report
        ) {

            throw new Error(
                "Productivity report is not available."
            );
        }


        const filters = (
            frappe.query_report
                .get_filter_values
                ? frappe.query_report
                    .get_filter_values()
                : {}
        ) || {};


        const result = (
            await productivity_v13_call(

                "is_production.production.report.productivity.productivity.save_productivity_override_snapshot",

                {
                    filters_json:
                        JSON.stringify(
                            filters
                        )
                }
            )
        );


        if (
            !result
            || !result.saved
        ) {

            throw new Error(
                "Productivity snapshot was not created."
            );
        }


        return result;
    }


    // ========================================================
    // FINAL SAVE OVERRIDE FUNCTION
    // ========================================================

    productivity_save_all_overrides = async function () {

        const button = (
            typeof productivity_override_button
            === "function"
                ? productivity_override_button()
                : null
        );


        if (
            button
            && button.length
        ) {

            button
                .prop(
                    "disabled",
                    true
                )
                .text(
                    __(
                        "Saving..."
                    )
                );
        }


        try {

            const rows = (
                productivity_v13_collect_rows()
            );


            console.log(
                "Productivity V13 rows to save:",
                rows
            );


            let saved_rows = 0;


            // ------------------------------------------------
            // SAVE USER CAPTURED ROWS DIRECTLY
            // ------------------------------------------------

            for (
                const row
                of rows
            ) {

                await (
                    productivity_v13_save_row(
                        row
                    )
                );


                saved_rows += 1;
            }


            // ------------------------------------------------
            // CREATE SNAPSHOT AFTER ALL ROWS ARE ON SERVER
            // ------------------------------------------------

            if (
                button
                && button.length
            ) {

                button.text(
                    __(
                        "Saving Snapshot..."
                    )
                );
            }


            const snapshot = (
                await productivity_v13_save_snapshot()
            );


            // ------------------------------------------------
            // ONLY NOW CLEAR BROWSER DRAFTS
            // ------------------------------------------------

            rows.forEach(
                function (
                    row
                ) {

                    productivity_v13_clear_drafts(
                        row.row_key
                    );
                }
            );


            if (
                typeof productivity_update_override_button
                === "function"
            ) {

                productivity_update_override_button();
            }


            const pdf_url = String(
                snapshot.file_url
                || ""
            );


            let message = (

                "<div style='line-height:1.7;'>"

                + "<b>Productivity Override saved permanently.</b>"

                + "<br>Rows saved: <b>"

                + String(
                    saved_rows
                )

                + "</b>"

                + "<br>Reference: <b>"

                + frappe.utils.escape_html(
                    snapshot.snapshot_reference
                    || snapshot.name
                    || ""
                )

                + "</b>"
            );


            if (
                pdf_url
            ) {

                message += (

                    "<br>"

                    + "<a href='"

                    + frappe.utils.escape_html(
                        pdf_url
                    )

                    + "' target='_blank'>"

                    + "<b>Open Saved Productivity PDF</b>"

                    + "</a>"
                );
            }


            message += (
                "</div>"
            );


            frappe.msgprint({

                title:
                    __(
                        "Save Override"
                    ),

                indicator:
                    "green",

                message:
                    message
            });


            // ------------------------------------------------
            // REFRESH AFTER SUCCESS
            // ------------------------------------------------

            setTimeout(
                function () {

                    if (
                        frappe.query_report
                    ) {

                        frappe.query_report.refresh();
                    }
                },
                500
            );


        } catch (
            error
        ) {

            console.error(
                "Productivity V13 Save Override failed:",
                error
            );


            const error_text = (
                productivity_v13_error_text(
                    error
                )
            );


            // ------------------------------------------------
            // IMPORTANT:
            //
            // Show the ACTUAL error now instead of hiding it.
            // ------------------------------------------------

            frappe.msgprint({

                title:
                    __(
                        "Save Override"
                    ),

                indicator:
                    "red",

                message:
                    (
                        "<div style='line-height:1.6;'>"
                        + "<b>Save Override failed.</b>"
                        + "<br><br>"
                        + frappe.utils.escape_html(
                            error_text
                        )
                        + "</div>"
                    )
            });


        } finally {

            if (
                button
                && button.length
            ) {

                button
                    .prop(
                        "disabled",
                        false
                    )
                    .text(
                        __(
                            "Save Override"
                        )
                    );
            }


            if (
                typeof productivity_update_override_button
                === "function"
            ) {

                productivity_update_override_button();
            }
        }
    };


})();


// END KOSI_PRODUCTIVITY_CLEAN_SAVE_PIPELINE_V13


// ============================================================
// KOSI_PRODUCTIVITY_ALL_TOTALS_BOLD_V14
//
// FINAL formatting layer.
//
// Bold:
//
//     Monthly Production heading
//     Excavator / ADT / Dozer
//     Every machine TOTAL
//     Total Fleet
//
// Material rows remain normal.
// ============================================================

(function () {

    const config = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!config) {

        return;
    }


    const previous_formatter = (
        config.formatter
    );


    config.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        let formatted;


        if (
            typeof previous_formatter
            === "function"
        ) {

            formatted = previous_formatter(
                value,
                row,
                column,
                data,
                default_formatter
            );

        } else {

            formatted = default_formatter(
                value,
                row,
                column,
                data
            );
        }


        if (!data) {

            return formatted;
        }


        const label = String(
            data.label
            || ""
        ).trim();


        const is_month_heading = (
            Number(
                data.is_monthly_plan_header
                || 0
            ) === 1
            ||
            label.startsWith(
                "MONTHLY PRODUCTION:"
            )
        );


        const is_category_total = (
            Number(
                data.is_category_total
                || 0
            ) === 1
        );


        const is_machine_total = (
            Number(
                data.productivity_is_machine_total
                || 0
            ) === 1
        );


        const is_total_fleet = (
            Number(
                data.is_total_fleet
                || 0
            ) === 1
            ||
            label === "Total Fleet"
        );


        const should_be_bold = (
            is_month_heading
            ||
            is_category_total
            ||
            is_machine_total
            ||
            is_total_fleet
        );


        if (!should_be_bold) {

            return formatted;
        }


        // Do not disturb an input if a future total row ever
        // becomes editable.
        if (
            String(
                formatted
                || ""
            ).includes(
                "<input"
            )
        ) {

            return formatted;
        }


        return (
            '<span style="'
            + 'font-weight:700 !important;'
            + 'display:block;'
            + '">'
            + (
                formatted
                ?? ""
            )
            + '</span>'
        );
    };

})();


// END KOSI_PRODUCTIVITY_ALL_TOTALS_BOLD_V14


// ============================================================
// KOSI_PRODUCTIVITY_SURVEY_CHILD_EDIT_V23
//
// Exact V22 Survey rows:
//   From Area
//   To Area
//   Hauling Distance
//
// become editable.
//
// Parent material total rows stay locked.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );

    if (!report) {
        return;
    }


    function esc(
        value
    ) {

        return String(
            value ?? ""
        )
            .replace(
                /&/g,
                "&amp;"
            )
            .replace(
                /</g,
                "&lt;"
            )
            .replace(
                />/g,
                "&gt;"
            )
            .replace(
                /"/g,
                "&quot;"
            )
            .replace(
                /'/g,
                "&#039;"
            );
    }


    function report_rows() {

        const result = [];

        try {

            if (
                frappe.query_report
                && Array.isArray(
                    frappe.query_report.data
                )
            ) {

                frappe.query_report.data.forEach(
                    function (
                        row
                    ) {

                        if (
                            row
                            && !result.includes(
                                row
                            )
                        ) {

                            result.push(
                                row
                            );
                        }
                    }
                );
            }

        } catch (
            error
        ) {
        }


        try {

            const data = (
                frappe.query_report
                && frappe.query_report.datatable
                && frappe.query_report.datatable.datamanager
                && frappe.query_report.datatable.datamanager.data
            );

            if (
                Array.isArray(
                    data
                )
            ) {

                data.forEach(
                    function (
                        item
                    ) {

                        const row = (
                            item
                            && (
                                item.__data
                                || item
                            )
                        );

                        if (
                            row
                            && !result.includes(
                                row
                            )
                        ) {

                            result.push(
                                row
                            );
                        }
                    }
                );
            }

        } catch (
            error
        ) {
        }


        return result;
    }


    function find_row(
        row_key
    ) {

        row_key = String(
            row_key
            || ""
        ).trim();


        return (
            report_rows().find(
                function (
                    row
                ) {

                    return (
                        String(
                            row.productivity_edit_key
                            || ""
                        ).trim()
                        === row_key
                    );
                }
            )
            || null
        );
    }


    function update_row(
        row_key,
        fieldname,
        value
    ) {

        report_rows().forEach(
            function (
                row
            ) {

                if (
                    String(
                        row.productivity_edit_key
                        || ""
                    ).trim()
                    !== row_key
                ) {
                    return;
                }

                row[
                    fieldname
                ] = value;
            }
        );
    }


    const previous_formatter = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        const area_fields = [
            "from_area",
            "to_area",
            "hauling_distance_m"
        ];


        // Parent material totals are display totals only.
        if (
            data
            && data.productivity_survey_parent_locked_v23
            && area_fields.includes(
                fieldname
            )
        ) {

            return "";
        }


        // Exact Survey child row editor.
        if (
            data
            && data.productivity_survey_editable_v23
            && area_fields.includes(
                fieldname
            )
        ) {

            const row_key = String(
                data.productivity_edit_key
                || ""
            ).trim();


            if (!row_key) {
                return "";
            }


            const current_value = String(
                data[
                    fieldname
                ]
                ?? ""
            );


            return (
                '<input'
                + ' type="text"'
                + ' class="productivity-inline-edit productivity-survey-v23-input"'
                + ' data-row-key="'
                + esc(
                    row_key
                )
                + '"'
                + ' data-field="'
                + esc(
                    fieldname
                )
                + '"'
                + ' value="'
                + esc(
                    current_value
                )
                + '"'
                + ' style="'
                + 'width:100%;'
                + 'height:26px;'
                + 'padding:2px 6px;'
                + 'border:1px solid #d99a00;'
                + 'background:#fff8dd;'
                + 'box-sizing:border-box;'
                + '"'
                + ' />'
            );
        }


        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter.apply(
                this,
                arguments
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    // ========================================================
    // INPUT CAPTURE
    // ========================================================

    $(document)
        .off(
            ".productivitySurveyV23"
        )
        .on(
            "input.productivitySurveyV23 change.productivitySurveyV23",
            ".productivity-survey-v23-input",
            function () {

                const input = $(
                    this
                );

                const row_key = String(
                    input.attr(
                        "data-row-key"
                    )
                    || ""
                ).trim();

                const fieldname = String(
                    input.attr(
                        "data-field"
                    )
                    || ""
                ).trim();

                const value = String(
                    input.val()
                    ?? ""
                );


                if (
                    !row_key
                    || !fieldname
                ) {
                    return;
                }


                update_row(
                    row_key,
                    fieldname,
                    value
                );


                window.productivity_dirty_overrides = (
                    window.productivity_dirty_overrides
                    || {}
                );


                window.productivity_dirty_overrides[
                    row_key
                ] = true;


                input.addClass(
                    "productivity-override-dirty"
                );
            }
        );


    // ========================================================
    // SAVE
    //
    // Reuse the existing Save Override button.
    // Standard rows continue through the old save method.
    // Survey child rows use the V23 endpoint.
    // ========================================================

    if (
        typeof productivity_save_one_override
        === "function"
    ) {

        const previous_save_one_v23 = (
            productivity_save_one_override
        );


        productivity_save_one_override = function (
            row_key
        ) {

            row_key = String(
                row_key
                || ""
            ).trim();


            if (
                !row_key.startsWith(
                    "SURVEYV23::"
                )
            ) {

                return previous_save_one_v23.apply(
                    this,
                    arguments
                );
            }


            const data = find_row(
                row_key
            );


            if (!data) {

                return Promise.reject(
                    new Error(
                        "Survey Productivity row was not found."
                    )
                );
            }


            const filters = (
                frappe.query_report
                && frappe.query_report.get_filter_values
                ? frappe.query_report.get_filter_values()
                : {}
            ) || {};


            return new Promise(
                function (
                    resolve,
                    reject
                ) {

                    frappe.call({

                        method:
                            "is_production.production.report.productivity.productivity.save_productivity_survey_override_v23",

                        args: {

                            row_key:
                                row_key,

                            site:
                                filters.site
                                || filters.location
                                || "",

                            start_date:
                                filters.start_date
                                || filters.from_date
                                || "",

                            end_date:
                                filters.end_date
                                || filters.to_date
                                || "",

                            shift:
                                filters.shift
                                || "",

                            monthly_production_plan:
                                data.productivity_survey_plan_v23
                                || "",

                            category:
                                data.productivity_survey_category_v23
                                || "",

                            survey_name:
                                data.productivity_survey_name_v23
                                || "",

                            survey_idx:
                                data.productivity_survey_row_idx_v21
                                || "",

                            material:
                                data.label
                                || data.material
                                || "",

                            from_area:
                                data.from_area
                                || "",

                            to_area:
                                data.to_area
                                || "",

                            hauling_distance_m:
                                data.hauling_distance_m
                                || ""
                        },


                        callback: function (
                            response
                        ) {

                            if (
                                response
                                && response.exc
                            ) {

                                reject(
                                    new Error(
                                        response.exc
                                    )
                                );

                                return;
                            }


                            if (
                                !response
                                || !response.message
                                || !response.message.saved
                            ) {

                                reject(
                                    new Error(
                                        "Survey Productivity override was not saved."
                                    )
                                );

                                return;
                            }


                            resolve(
                                response.message
                            );
                        },


                        error: function (
                            error
                        ) {

                            reject(
                                error
                                || new Error(
                                    "Survey Productivity override save failed."
                                )
                            );
                        }
                    });
                }
            );
        };
    }


    // Small visual indication.
    if (
        !document.getElementById(
            "productivity-survey-v23-style"
        )
    ) {

        const style = document.createElement(
            "style"
        );

        style.id = (
            "productivity-survey-v23-style"
        );

        style.innerHTML = `

            .productivity-survey-v23-input:focus {
                background: #ffffff !important;
                border: 2px solid #d99a00 !important;
                outline: none !important;
            }

            .productivity-survey-v23-input.productivity-override-dirty {
                background: #fff1b8 !important;
                border: 2px solid #d99a00 !important;
            }

        `;

        document.head.appendChild(
            style
        );
    }

})();


// END KOSI_PRODUCTIVITY_SURVEY_CHILD_EDIT_V23


// ============================================================
// KOSI_PRODUCTIVITY_CHILD_PRODUCTIVITY_V24
//
// - Child productivity displays 2 decimals.
// - Material totals are bold.
// - Parent Material column stays blank.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );

    if (!report) {
        return;
    }


    const previous_formatter_v24 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        // ----------------------------------------------------
        // CHILD PRODUCTIVITY
        //
        // Show the independent calculated value with decimals.
        // ----------------------------------------------------

        if (
            data
            && data.productivity_child_recalculated_v24
            && fieldname === "productivity"
        ) {

            const number = Number(
                data.productivity
                || 0
            );

            if (
                !Number.isFinite(
                    number
                )
            ) {

                return "";
            }


            return number.toLocaleString(
                undefined,
                {
                    minimumFractionDigits:
                        0,

                    maximumFractionDigits:
                        0
                }
            );
        }


        let formatted;


        if (
            typeof previous_formatter_v24
            === "function"
        ) {

            formatted = (
                previous_formatter_v24.apply(
                    this,
                    arguments
                )
            );

        } else {

            formatted = (
                default_formatter(
                    value,
                    row,
                    column,
                    data
                )
            );
        }


        // ----------------------------------------------------
        // BOLD MATERIAL TOTAL ROWS
        // ----------------------------------------------------

        if (
            data
            && data.productivity_material_total_v24
        ) {

            return (
                "<strong>"
                + (
                    formatted
                    ?? ""
                )
                + "</strong>"
            );
        }


        return formatted;
    };

})();


// END KOSI_PRODUCTIVITY_CHILD_PRODUCTIVITY_V24


// ============================================================
// KOSI_PRODUCTIVITY_SAFE_SAVE_PIPELINE_V26
//
// Fix:
//     "Productivity row key does not match the selected
//      report row."
//
// Rules:
//
// 1. Ignore stale dirty keys that no longer exist in the
//    current report.
//
// 2. SURVEYV23 rows ALWAYS use:
//      save_productivity_survey_override_v23()
//
// 3. Existing normal Productivity rows continue through
//    the previous save pipeline.
//
// This keeps the existing Save Override + Snapshot behaviour.
// ============================================================

(function () {

    if (
        typeof productivity_save_one_override
        !== "function"
    ) {
        return;
    }


    function current_rows_v26() {

        const result = [];


        try {

            const rows = (
                frappe.query_report
                && frappe.query_report.data
            );

            if (
                Array.isArray(
                    rows
                )
            ) {

                rows.forEach(
                    function (
                        row
                    ) {

                        if (
                            row
                            && !result.includes(
                                row
                            )
                        ) {

                            result.push(
                                row
                            );
                        }
                    }
                );
            }

        } catch (
            error
        ) {
        }


        try {

            const rows = (
                frappe.query_report
                && frappe.query_report.datatable
                && frappe.query_report.datatable.datamanager
                && frappe.query_report.datatable.datamanager.data
            );

            if (
                Array.isArray(
                    rows
                )
            ) {

                rows.forEach(
                    function (
                        item
                    ) {

                        const row = (
                            item
                            && (
                                item.__data
                                || item
                            )
                        );

                        if (
                            row
                            && !result.includes(
                                row
                            )
                        ) {

                            result.push(
                                row
                            );
                        }
                    }
                );
            }

        } catch (
            error
        ) {
        }


        return result;
    }


    function find_row_v26(
        row_key
    ) {

        row_key = String(
            row_key
            || ""
        ).trim();


        return (
            current_rows_v26().find(
                function (
                    row
                ) {

                    return (
                        String(
                            row.productivity_edit_key
                            || ""
                        ).trim()
                        === row_key
                    );
                }
            )
            || null
        );
    }


    function filters_v26() {

        try {

            if (
                frappe.query_report
                && frappe.query_report.get_filter_values
            ) {

                return (
                    frappe.query_report.get_filter_values()
                    || {}
                );
            }

        } catch (
            error
        ) {
        }


        return {};
    }


    function save_survey_row_v26(
        row_key,
        row
    ) {

        const filters = (
            filters_v26()
        );


        return new Promise(
            function (
                resolve,
                reject
            ) {

                frappe.call({

                    method:
                        "is_production.production.report.productivity.productivity.save_productivity_survey_override_v23",

                    args: {

                        row_key:
                            row_key,

                        site:
                            filters.site
                            || filters.location
                            || "",

                        start_date:
                            filters.start_date
                            || filters.from_date
                            || "",

                        end_date:
                            filters.end_date
                            || filters.to_date
                            || "",

                        shift:
                            filters.shift
                            || "",

                        monthly_production_plan:
                            row.productivity_survey_plan_v23
                            || "",

                        category:
                            row.productivity_survey_category_v23
                            || "",

                        survey_name:
                            row.productivity_survey_name_v23
                            || "",

                        survey_idx:
                            row.productivity_survey_row_idx_v21
                            || "",

                        material:
                            row.label
                            || row.material
                            || "",

                        from_area:
                            row.from_area
                            || "",

                        to_area:
                            row.to_area
                            || "",

                        hauling_distance_m:
                            row.hauling_distance_m
                            || ""
                    },


                    callback: function (
                        response
                    ) {

                        if (
                            response
                            && response.exc
                        ) {

                            reject(
                                new Error(
                                    response.exc
                                )
                            );

                            return;
                        }


                        const message = (
                            response
                            && response.message
                        );


                        if (
                            !message
                            || !message.saved
                        ) {

                            reject(
                                new Error(
                                    "Survey Productivity override was not saved."
                                )
                            );

                            return;
                        }


                        // Keep current row data synchronized.
                        row.from_area = (
                            message.from_area
                            || ""
                        );

                        row.to_area = (
                            message.to_area
                            || ""
                        );

                        row.hauling_distance_m = (
                            message.hauling_distance_m
                            || ""
                        );


                        resolve(
                            message
                        );
                    },


                    error: function (
                        error
                    ) {

                        reject(
                            error
                            || new Error(
                                "Survey Productivity save failed."
                            )
                        );
                    }
                });
            }
        );
    }


    const previous_save_one_v26 = (
        productivity_save_one_override
    );


    productivity_save_one_override = function (
        row_key
    ) {

        row_key = String(
            row_key
            || ""
        ).trim();


        if (!row_key) {

            return Promise.resolve({
                saved:
                    false,

                skipped:
                    true,

                reason:
                    "blank-row-key"
            });
        }


        const row = find_row_v26(
            row_key
        );


        // ----------------------------------------------------
        // STALE KEY
        //
        // Old V10/V11/V13 drafts can remain after the report
        // row structure changes.
        //
        // Never send a stale key to the backend validator.
        // ----------------------------------------------------

        if (!row) {

            console.warn(
                "Productivity V26: skipped stale override key:",
                row_key
            );


            if (
                window.productivity_dirty_overrides
            ) {

                delete (
                    window.productivity_dirty_overrides[
                        row_key
                    ]
                );
            }


            return Promise.resolve({
                saved:
                    false,

                skipped:
                    true,

                reason:
                    "stale-row-key",

                row_key:
                    row_key
            });
        }


        // ----------------------------------------------------
        // EXACT SURVEY CHILD ROW
        // ----------------------------------------------------

        if (
            row_key.startsWith(
                "SURVEYV23::"
            )
        ) {

            return save_survey_row_v26(
                row_key,
                row
            );
        }


        // ----------------------------------------------------
        // NORMAL EXISTING PRODUCTIVITY OVERRIDE
        // ----------------------------------------------------

        return previous_save_one_v26.apply(
            this,
            arguments
        );
    };


    // ========================================================
    // REMOVE CURRENTLY DIRTY STALE KEYS
    // ========================================================

    function cleanup_dirty_keys_v26() {

        const dirty = (
            window.productivity_dirty_overrides
            || {}
        );


        Object.keys(
            dirty
        ).forEach(
            function (
                row_key
            ) {

                if (
                    !find_row_v26(
                        row_key
                    )
                ) {

                    delete dirty[
                        row_key
                    ];
                }
            }
        );


        window.productivity_dirty_overrides = (
            dirty
        );
    }


    setTimeout(
        cleanup_dirty_keys_v26,
        500
    );

    setTimeout(
        cleanup_dirty_keys_v26,
        1500
    );


    console.log(
        "KOSI_PRODUCTIVITY_SAFE_SAVE_PIPELINE_V26 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_SAFE_SAVE_PIPELINE_V26


// ============================================================
// KOSI_PRODUCTIVITY_SAVE_BUTTON_ROUTER_V27
//
// FINAL Save Override button router.
//
// Why:
// The original Save Override button already had an older
// callback bound to it. Replacing productivity_save_one_override
// later did not guarantee that old callback was bypassed.
//
// V27 takes ownership of the BUTTON CLICK itself.
//
// SURVEYV23 rows:
//     save_productivity_survey_override_v23()
//
// Normal existing rows:
//     existing productivity_save_one_override()
//
// Stale row keys:
//     skipped and removed
//
// After row saves:
//     permanent Productivity snapshot is created normally.
// ============================================================

(function () {

    const V27_METHOD_SURVEY = (
        "is_production.production.report.productivity."
        + "productivity.save_productivity_survey_override_v23"
    );

    const V27_METHOD_SNAPSHOT = (
        "is_production.production.report.productivity."
        + "productivity.save_productivity_override_snapshot"
    );


    function v27_filters() {

        try {

            if (
                frappe.query_report
                && frappe.query_report.get_filter_values
            ) {

                return (
                    frappe.query_report.get_filter_values()
                    || {}
                );
            }

        } catch (
            error
        ) {
        }

        return {};
    }


    function v27_rows() {

        const result = [];


        try {

            const rows = (
                frappe.query_report
                && frappe.query_report.data
            );

            if (
                Array.isArray(
                    rows
                )
            ) {

                rows.forEach(
                    function (
                        row
                    ) {

                        if (
                            row
                            && !result.includes(
                                row
                            )
                        ) {

                            result.push(
                                row
                            );
                        }
                    }
                );
            }

        } catch (
            error
        ) {
        }


        try {

            const rows = (
                frappe.query_report
                && frappe.query_report.datatable
                && frappe.query_report.datatable.datamanager
                && frappe.query_report.datatable.datamanager.data
            );

            if (
                Array.isArray(
                    rows
                )
            ) {

                rows.forEach(
                    function (
                        item
                    ) {

                        const row = (
                            item
                            && (
                                item.__data
                                || item
                            )
                        );

                        if (
                            row
                            && !result.includes(
                                row
                            )
                        ) {

                            result.push(
                                row
                            );
                        }
                    }
                );
            }

        } catch (
            error
        ) {
        }


        return result;
    }


    function v27_find_row(
        row_key
    ) {

        row_key = String(
            row_key
            || ""
        ).trim();


        return (
            v27_rows().find(
                function (
                    row
                ) {

                    return (
                        String(
                            row.productivity_edit_key
                            || ""
                        ).trim()
                        === row_key
                    );
                }
            )
            || null
        );
    }


    function v27_call(
        options
    ) {

        return new Promise(
            function (
                resolve,
                reject
            ) {

                frappe.call({

                    method:
                        options.method,

                    args:
                        options.args
                        || {},


                    callback: function (
                        response
                    ) {

                        if (
                            response
                            && response.exc
                        ) {

                            reject(
                                new Error(
                                    response.exc
                                )
                            );

                            return;
                        }


                        resolve(
                            response
                            && response.message
                            ? response.message
                            : {}
                        );
                    },


                    error: function (
                        error
                    ) {

                        reject(
                            error
                            || new Error(
                                "Productivity save failed."
                            )
                        );
                    }
                });
            }
        );
    }


    async function v27_save_survey_row(
        row_key,
        row
    ) {

        const filters = (
            v27_filters()
        );


        console.log(
            "V27 Survey save:",
            row_key,
            row.label
        );


        const message = await v27_call({

            method:
                V27_METHOD_SURVEY,

            args: {

                row_key:
                    row_key,

                site:
                    filters.site
                    || filters.location
                    || "",

                start_date:
                    filters.start_date
                    || filters.from_date
                    || "",

                end_date:
                    filters.end_date
                    || filters.to_date
                    || "",

                shift:
                    filters.shift
                    || "",

                monthly_production_plan:
                    row.productivity_survey_plan_v23
                    || "",

                category:
                    row.productivity_survey_category_v23
                    || "",

                survey_name:
                    row.productivity_survey_name_v23
                    || "",

                survey_idx:
                    row.productivity_survey_row_idx_v21
                    || "",

                material:
                    row.label
                    || row.material
                    || "",

                from_area:
                    row.from_area
                    || "",

                to_area:
                    row.to_area
                    || "",

                hauling_distance_m:
                    row.hauling_distance_m
                    || ""
            }
        });


        if (
            !message
            || !message.saved
        ) {

            throw new Error(
                "Survey Productivity override was not saved."
            );
        }


        row.from_area = (
            message.from_area
            || ""
        );

        row.to_area = (
            message.to_area
            || ""
        );

        row.hauling_distance_m = (
            message.hauling_distance_m
            || ""
        );


        return message;
    }


    async function v27_save_normal_row(
        row_key
    ) {

        if (
            typeof productivity_save_one_override
            !== "function"
        ) {

            throw new Error(
                "Standard Productivity save function is unavailable."
            );
        }


        return await Promise.resolve(
            productivity_save_one_override(
                row_key
            )
        );
    }


    async function v27_create_snapshot() {

        const filters = (
            v27_filters()
        );


        const message = await v27_call({

            method:
                V27_METHOD_SNAPSHOT,

            args: {

                filters_json:
                    JSON.stringify(
                        filters
                    )
            }
        });


        if (
            !message
            || !message.saved
        ) {

            throw new Error(
                "Productivity snapshot was not created."
            );
        }


        return message;
    }


    function v27_button() {

        try {

            if (
                typeof productivity_override_button
                === "function"
            ) {

                const button = (
                    productivity_override_button()
                );

                if (
                    button
                    && button.length
                ) {

                    return button;
                }
            }

        } catch (
            error
        ) {
        }


        const candidates = $(
            "button, .btn"
        ).filter(
            function () {

                return (
                    String(
                        $(this).text()
                        || ""
                    ).trim()
                    === "Save Override"
                );
            }
        );


        return candidates.first();
    }


    function v27_error_text(
        error
    ) {

        try {

            if (
                error
                && error.message
            ) {

                return String(
                    error.message
                );
            }


            if (
                error
                && error.responseJSON
                && error.responseJSON.exception
            ) {

                return String(
                    error.responseJSON.exception
                );
            }

        } catch (
            ignored
        ) {
        }


        return String(
            error
            || "Unknown error"
        );
    }


    async function v27_save_all() {

        const button = (
            v27_button()
        );


        button
            .prop(
                "disabled",
                true
            )
            .text(
                __(
                    "Saving..."
                )
            );


        // Make sure the active input has pushed its latest
        // value into the row object before saving.
        try {

            const active = $(
                document.activeElement
            );

            if (
                active.hasClass(
                    "productivity-survey-v23-input"
                )
            ) {

                active.trigger(
                    "change"
                );
            }

        } catch (
            error
        ) {
        }


        window.productivity_dirty_overrides = (
            window.productivity_dirty_overrides
            || {}
        );


        const dirty_keys = Object.keys(
            window.productivity_dirty_overrides
            || {}
        );


        console.log(
            "V27 dirty keys:",
            dirty_keys
        );


        let saved_rows = 0;
        let stale_rows = 0;


        try {

            // =================================================
            // SAVE ROW OVERRIDES
            // =================================================

            for (
                const row_key
                of dirty_keys
            ) {

                const key = String(
                    row_key
                    || ""
                ).trim();


                if (!key) {

                    delete (
                        window.productivity_dirty_overrides[
                            row_key
                        ]
                    );

                    continue;
                }


                const row = (
                    v27_find_row(
                        key
                    )
                );


                // ---------------------------------------------
                // STALE KEY
                // ---------------------------------------------

                if (!row) {

                    console.warn(
                        "V27 skipped stale row key:",
                        key
                    );


                    delete (
                        window.productivity_dirty_overrides[
                            row_key
                        ]
                    );


                    stale_rows += 1;

                    continue;
                }


                // ---------------------------------------------
                // EXACT SURVEY CHILD
                // ---------------------------------------------

                if (
                    key.startsWith(
                        "SURVEYV23::"
                    )
                ) {

                    await v27_save_survey_row(
                        key,
                        row
                    );

                } else {

                    // -----------------------------------------
                    // NORMAL EXISTING PRODUCTIVITY ROW
                    // -----------------------------------------

                    await v27_save_normal_row(
                        key
                    );
                }


                delete (
                    window.productivity_dirty_overrides[
                        row_key
                    ]
                );


                $(
                    '.productivity-inline-edit[data-row-key="'
                    + key
                    + '"]'
                )
                    .removeClass(
                        "productivity-override-dirty"
                    )
                    .addClass(
                        "productivity-override-saved"
                    );


                saved_rows += 1;
            }


            // =================================================
            // SNAPSHOT
            // =================================================

            button.text(
                __(
                    "Saving Snapshot..."
                )
            );


            const snapshot = (
                await v27_create_snapshot()
            );


            const reference = String(
                snapshot.snapshot_reference
                || snapshot.name
                || ""
            );


            const pdf_url = String(
                snapshot.file_url
                || ""
            );


            let message = (
                "<div style='line-height:1.7;'>"
                + "<b>Productivity Override saved permanently.</b>"
                + "<br>Overrides saved: <b>"
                + String(
                    saved_rows
                )
                + "</b>"
            );


            if (
                stale_rows > 0
            ) {

                message += (
                    "<br>Old stale rows ignored: <b>"
                    + String(
                        stale_rows
                    )
                    + "</b>"
                );
            }


            if (reference) {

                message += (
                    "<br>Reference: <b>"
                    + frappe.utils.escape_html(
                        reference
                    )
                    + "</b>"
                );
            }


            if (pdf_url) {

                message += (
                    "<br>"
                    + "<a href='"
                    + frappe.utils.escape_html(
                        pdf_url
                    )
                    + "' target='_blank'>"
                    + "<b>Open Saved Productivity PDF</b>"
                    + "</a>"
                );
            }


            message += (
                "</div>"
            );


            frappe.msgprint({

                title:
                    __(
                        "Save Override"
                    ),

                indicator:
                    "green",

                message:
                    message
            });


            setTimeout(
                function () {

                    if (
                        frappe.query_report
                    ) {

                        frappe.query_report.refresh();
                    }
                },
                500
            );


        } catch (
            error
        ) {

            console.error(
                "Productivity V27 save failed:",
                error
            );


            frappe.msgprint({

                title:
                    __(
                        "Save Override"
                    ),

                indicator:
                    "red",

                message:
                    (
                        "<b>Save Override failed.</b>"
                        + "<br><br>"
                        + frappe.utils.escape_html(
                            v27_error_text(
                                error
                            )
                        )
                    )
            });


        } finally {

            button
                .prop(
                    "disabled",
                    false
                )
                .text(
                    __(
                        "Save Override"
                    )
                );
        }
    }


    // ========================================================
    // TAKE OWNERSHIP OF ACTUAL BUTTON CLICK
    // ========================================================

    function v27_bind_button() {

        const button = (
            v27_button()
        );


        if (
            !button
            || !button.length
        ) {

            return;
        }


        const element = (
            button.get(
                0
            )
        );


        if (!element) {
            return;
        }


        if (
            element.dataset
            && element.dataset.productivityV27Bound
            === "1"
        ) {

            return;
        }


        if (
            element.dataset
        ) {

            element.dataset.productivityV27Bound = (
                "1"
            );
        }


        // Capture-phase listener runs BEFORE the old
        // Save Override click callback.
        element.addEventListener(
            "click",

            function (
                event
            ) {

                event.preventDefault();
                event.stopPropagation();
                event.stopImmediatePropagation();


                v27_save_all();
            },

            true
        );


        console.log(
            "KOSI_PRODUCTIVITY_SAVE_BUTTON_ROUTER_V27 bound"
        );
    }


    // Initial bind.
    setTimeout(
        v27_bind_button,
        300
    );

    setTimeout(
        v27_bind_button,
        1000
    );

    setTimeout(
        v27_bind_button,
        2500
    );


    // Report can rebuild the toolbar/button after refresh.
    window.__productivity_v27_bind_interval = (
        window.__productivity_v27_bind_interval
        || setInterval(
            v27_bind_button,
            1500
        )
    );


    console.log(
        "KOSI_PRODUCTIVITY_SAVE_BUTTON_ROUTER_V27 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_SAVE_BUTTON_ROUTER_V27


// ============================================================
// KOSI_PRODUCTIVITY_BCM_HD_TWO_DECIMAL_V30
//
// Final display:
//     Productivity (BCM/HR) = 0 decimals
//     Productivity (BCM/HD) = 2 decimals
//
// Examples:
//     136.427 -> 136.43
//     72.14   -> 72.14
//     12.734  -> 12.73
//     0.376   -> 0.38
//
// Calculation itself is unchanged.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );

    if (!report) {
        return;
    }


    const previous_formatter_v30 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        if (
            fieldname
            === "productivity_bcm_hd"
        ) {

            const raw = (
                data
                && data.productivity_bcm_hd
                !== undefined
                ? data.productivity_bcm_hd
                : value
            );


            if (
                raw === ""
                || raw === null
                || raw === undefined
            ) {

                return "";
            }


            const number = Number(
                String(
                    raw
                ).replace(
                    /,/g,
                    ""
                )
            );


            if (
                !Number.isFinite(
                    number
                )
            ) {

                return "";
            }


            return number.toLocaleString(
                undefined,
                {
                    minimumFractionDigits:
                        2,

                    maximumFractionDigits:
                        2
                }
            );
        }


        if (
            typeof previous_formatter_v30
            === "function"
        ) {

            return (
                previous_formatter_v30.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    console.log(
        "KOSI_PRODUCTIVITY_BCM_HD_TWO_DECIMAL_V30 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_BCM_HD_TWO_DECIMAL_V30


// ============================================================
// KOSI_PRODUCTIVITY_SUMMARY_CAPTURED_DETAIL_V36
//
// Summary Per Machine hierarchy:
//
// ADT01
//     Coal
//         Ramp 2 - 3 2#Coal
//         Ramp 1 - 2 2#Coal
//
// Overrides older V35 display formatting.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );

    if (!report) {
        return;
    }


    function v36_escape(
        value
    ) {

        return String(
            value ?? ""
        )
            .replace(
                /&/g,
                "&amp;"
            )
            .replace(
                /</g,
                "&lt;"
            )
            .replace(
                />/g,
                "&gt;"
            )
            .replace(
                /"/g,
                "&quot;"
            )
            .replace(
                /'/g,
                "&#039;"
            );
    }


    function v36_is_summary() {

        try {

            const filters = (
                frappe.query_report
                    .get_filter_values()
                || {}
            );

            return (
                String(
                    filters.summary_view
                    || ""
                ).trim()
                === "Summary Per Machine"
            );

        } catch (
            error
        ) {

            return false;
        }
    }


    const previous_formatter_v36 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        if (
            v36_is_summary()
            && data
        ) {

            // Exact captured Geo / Material child.
            if (
                data.productivity_summary_captured_detail_v36
            ) {

                if (
                    fieldname
                    === "label"
                ) {

                    return (
                        '<span style="'
                        + 'display:inline-block;'
                        + 'padding-left:42px;'
                        + 'font-weight:400;'
                        + '">'
                        + v36_escape(
                            data.label
                        )
                        + '</span>'
                    );
                }


                if (
                    fieldname
                    === "material"
                ) {

                    return (
                        v36_escape(
                            data.material
                        )
                    );
                }
            }


            // Broad material parent:
            // Coal / Hards / Softs.
            if (
                data.material
                && !data.productivity_summary_captured_detail_v36
            ) {

                if (
                    fieldname
                    === "label"
                ) {

                    return (
                        '<span style="'
                        + 'display:inline-block;'
                        + 'padding-left:18px;'
                        + 'font-weight:700;'
                        + '">'
                        + v36_escape(
                            data.material
                        )
                        + '</span>'
                    );
                }


                if (
                    fieldname
                    === "material"
                ) {

                    return (
                        '<span style="font-weight:700;">'
                        + v36_escape(
                            data.material
                        )
                        + '</span>'
                    );
                }
            }
        }


        if (
            typeof previous_formatter_v36
            === "function"
        ) {

            return (
                previous_formatter_v36.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    console.log(
        "KOSI_PRODUCTIVITY_SUMMARY_CAPTURED_DETAIL_V36 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_SUMMARY_CAPTURED_DETAIL_V36


// ============================================================
// KOSI_PRODUCTIVITY_SUMMARY_TREE_V38
//
// Summary Per Machine visual tree:
//
// ADT
//     ADT01
//         Coal
//             4 - 2Seam Coal
//         Hards
//             1 - Overburden
//         Softs
//             3 - Softs
//
// Uses backend indent levels:
//
// Category       = 0
// Machine        = 1
// Material       = 2
// Material Detail= 3
//
// Does not alter report values.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    // --------------------------------------------------------
    // ENABLE NATIVE TREE BEHAVIOUR
    // --------------------------------------------------------

    report.tree = true;

    report.treeView = true;

    report.initial_depth = 3;


    function v38_is_summary() {

        try {

            const filters = (
                frappe.query_report
                && frappe.query_report.get_filter_values
                ? (
                    frappe.query_report
                        .get_filter_values()
                    || {}
                )
                : {}
            );


            return (
                String(
                    filters.summary_view
                    || ""
                ).trim()
                === "Summary Per Machine"
            );

        } catch (
            error
        ) {

            return false;
        }
    }


    const previous_formatter_v38 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        let formatted;


        if (
            typeof previous_formatter_v38
            === "function"
        ) {

            formatted = (
                previous_formatter_v38.apply(
                    this,
                    arguments
                )
            );

        } else {

            formatted = (
                default_formatter(
                    value,
                    row,
                    column,
                    data
                )
            );
        }


        if (
            !v38_is_summary()
            || !data
        ) {

            return formatted;
        }


        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        const level = String(
            data.productivity_summary_tree_level_v38
            || ""
        ).trim();


        // ----------------------------------------------------
        // CATEGORY / MACHINE / MATERIAL TOTALS BOLD
        // ----------------------------------------------------

        if (
            (
                level === "category"
                || level === "machine"
                || level === "material"
                || level === "fleet_total"
            )
            && (
                fieldname === "label"
                || fieldname === "working_hours"
                || fieldname === "output"
                || fieldname === "adjusted_bcm"
                || fieldname === "productivity"
                || fieldname === "productivity_bcm_hd"
            )
        ) {

            return (
                '<span style="font-weight:700;">'
                + formatted
                + '</span>'
            );
        }


        return formatted;
    };


    console.log(
        "KOSI_PRODUCTIVITY_SUMMARY_TREE_V38 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_SUMMARY_TREE_V38


// ============================================================
// KOSI_PRODUCTIVITY_MACHINE_MATERIAL_COLUMN_V44
//
// Summary Per Machine:
//
// ADT01  ...  Coal / Hards / Softs
//
// Keep the machine name in Label even though Material is
// populated on the same machine row.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    function esc_v44(value) {

        return String(
            value
            ?? ""
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    const previous_formatter_v44 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        if (
            data
            && data.productivity_summary_machine_material_list_v44
        ) {

            // Keep ADT01 / ADT02 / etc in Label.
            if (
                fieldname === "label"
            ) {

                return (
                    '<span style="font-weight:700;">'
                    + esc_v44(
                        data.label
                    )
                    + '</span>'
                );
            }


            // Show combined material on the right.
            if (
                fieldname === "material"
            ) {

                return (
                    esc_v44(
                        data.material
                    )
                );
            }
        }


        if (
            typeof previous_formatter_v44
            === "function"
        ) {

            return (
                previous_formatter_v44.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    console.log(
        "KOSI_PRODUCTIVITY_MACHINE_MATERIAL_COLUMN_V44 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_MACHINE_MATERIAL_COLUMN_V44


// ============================================================
// KOSI_PRODUCTIVITY_ADT_MATERIAL_ROWS_V45
//
// ADT material display:
//
// ADT01    ...    Coal
// ADT01    ...    Hards
// ADT01    ...    Softs
//
// Do NOT render Coal/Hards/Softs inside Label.
// Material must appear in Material column.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    function v45_escape(
        value
    ) {

        return String(
            value
            ?? ""
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    const previous_formatter_v45 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        // ====================================================
        // ADT MATERIAL ROW
        //
        // Force:
        //
        // Label    = ADT01
        // Material = Coal
        //
        // This overrides old V35/V36 display formatters.
        // ====================================================

        if (
            data
            && data.productivity_summary_adt_material_row_v45
        ) {

            if (
                fieldname
                === "label"
            ) {

                return (
                    '<span style="font-weight:400;">'
                    + v45_escape(
                        data.label
                    )
                    + '</span>'
                );
            }


            if (
                fieldname
                === "material"
            ) {

                return (
                    '<span style="font-weight:400;">'
                    + v45_escape(
                        data.material
                    )
                    + '</span>'
                );
            }
        }


        if (
            typeof previous_formatter_v45
            === "function"
        ) {

            return (
                previous_formatter_v45.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    console.log(
        "KOSI_PRODUCTIVITY_ADT_MATERIAL_ROWS_V45 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_ADT_MATERIAL_ROWS_V45

// KOSI_PRODUCTIVITY_V45_BLANK_MATERIAL_LABEL_V46
(function () {
    const report = frappe.query_reports["Productivity"];
    if (!report) return;

    const previous_formatter_v46 = report.formatter;

    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {
        const fieldname = String(
            column && column.fieldname || ""
        ).trim();

        if (
            data &&
            data.productivity_summary_adt_material_row_v45
        ) {
            // Material child row:
            // left Label must be blank.
            if (fieldname === "label") {
                return "";
            }

            // Show Coal / Hards / Softs only here.
            if (fieldname === "material") {
                return String(
                    data.material || ""
                );
            }
        }

        if (typeof previous_formatter_v46 === "function") {
            return previous_formatter_v46.apply(
                this,
                arguments
            );
        }

        return default_formatter(
            value,
            row,
            column,
            data
        );
    };
})();
// END KOSI_PRODUCTIVITY_V45_BLANK_MATERIAL_LABEL_V46


// ============================================================
// KOSI_PRODUCTIVITY_ADT_DOZER_MATERIAL_ROWS_V47
//
// ADT + DOZER material rows:
//
// Label column    = blank
// Material column = Coal / Hards / Softs / Midburden / etc.
//
// Machine total remains on its own row.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    function v47_escape(
        value
    ) {

        return String(
            value
            ?? ""
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    const previous_formatter_v47 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        if (
            data
            && data.productivity_summary_equipment_material_v47
        ) {

            // Material row:
            // do not repeat the machine/material wording
            // in the left Label column.
            if (
                fieldname === "label"
            ) {

                return "";
            }


            // Material appears only in Material column.
            if (
                fieldname === "material"
            ) {

                return (
                    '<span style="font-weight:400;">'
                    + v47_escape(
                        data.material
                    )
                    + '</span>'
                );
            }
        }


        if (
            typeof previous_formatter_v47
            === "function"
        ) {

            return (
                previous_formatter_v47.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    console.log(
        "KOSI_PRODUCTIVITY_ADT_DOZER_MATERIAL_ROWS_V47 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_ADT_DOZER_MATERIAL_ROWS_V47


// ============================================================
// KOSI_PRODUCTIVITY_AREA_ON_BREAKDOWN_V51
//
// SUMMARY PER MACHINE:
//
// Coal / Hards / Softs:
//     BCM/HD visible
//     From / To / Distance blank
//
// Material breakdown:
//     BCM/HD blank
//     From / To / Distance visible
//
// This final formatter overrides old yellow parent inputs.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    function v51_escape(
        value
    ) {

        return String(
            value
            ?? ""
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    const previous_formatter_v51 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        // ====================================================
        // BROAD MATERIAL PARENT
        //
        // Coal/Hards/Softs:
        // do not show area controls here.
        // ====================================================

        if (
            data
            && data.productivity_summary_area_parent_v51
        ) {

            if (
                fieldname === "from_area"
                || fieldname === "to_area"
                || fieldname === "hauling_distance_m"
            ) {

                return "";
            }
        }


        // ====================================================
        // MATERIAL BREAKDOWN
        //
        // Show plain area values here.
        // BCM/HD stays blank.
        // ====================================================

        if (
            data
            && data.productivity_summary_area_detail_v51
        ) {

            if (
                fieldname
                === "productivity_bcm_hd"
            ) {

                return "";
            }


            if (
                fieldname === "from_area"
                || fieldname === "to_area"
                || fieldname === "hauling_distance_m"
            ) {

                return (
                    v51_escape(
                        data[
                            fieldname
                        ]
                    )
                );
            }
        }


        if (
            typeof previous_formatter_v51
            === "function"
        ) {

            return (
                previous_formatter_v51.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    console.log(
        "KOSI_PRODUCTIVITY_AREA_ON_BREAKDOWN_V51 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_AREA_ON_BREAKDOWN_V51


// ============================================================
// KOSI_PRODUCTIVITY_BREAKDOWN_AREA_EDIT_V52
//
// Editable ONLY on material breakdown rows:
//
//     From Area
//     To Area
//     Hauling Distance (M)
//
// Changes are saved immediately using the existing permanent
// Productivity Area Override backend.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    window.__productivity_v52_rows = (
        window.__productivity_v52_rows
        || {}
    );


    function v52_escape(
        value
    ) {

        return String(
            value
            ?? ""
        )
            .replace(
                /&/g,
                "&amp;"
            )
            .replace(
                /</g,
                "&lt;"
            )
            .replace(
                />/g,
                "&gt;"
            )
            .replace(
                /"/g,
                "&quot;"
            )
            .replace(
                /'/g,
                "&#039;"
            );
    }


    const previous_formatter_v52 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        if (
            data
            && data.productivity_summary_area_edit_v52
            && (
                fieldname === "from_area"
                || fieldname === "to_area"
                || fieldname === "hauling_distance_m"
            )
        ) {

            const row_key = String(
                data.row_key
                || ""
            ).trim();


            if (!row_key) {

                return "";
            }


            window.__productivity_v52_rows[
                row_key
            ] = data;


            const field_value = String(
                data[
                    fieldname
                ]
                || ""
            );


            return (
                '<input'
                + ' type="text"'
                + ' class="productivity-v52-area-input"'
                + ' data-row-key="'
                + v52_escape(
                    row_key
                )
                + '"'
                + ' data-field="'
                + v52_escape(
                    fieldname
                )
                + '"'
                + ' value="'
                + v52_escape(
                    field_value
                )
                + '"'
                + ' autocomplete="off"'
                + ' style="'
                + 'width:100%;'
                + 'box-sizing:border-box;'
                + 'padding:4px 6px;'
                + 'border:1px solid #d8b04c;'
                + 'border-radius:3px;'
                + 'background:#fff8d8;'
                + '"'
                + '>'
            );
        }


        if (
            typeof previous_formatter_v52
            === "function"
        ) {

            return (
                previous_formatter_v52.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    if (
        !window.__productivity_v52_input_bound
    ) {

        window.__productivity_v52_input_bound = true;


        document.addEventListener(
            "input",
            function (
                event
            ) {

                const input = (
                    event.target
                    && event.target.closest
                    ? event.target.closest(
                        ".productivity-v52-area-input"
                    )
                    : null
                );


                if (!input) {

                    return;
                }


                const row_key = String(
                    input.dataset.rowKey
                    || ""
                ).trim();


                const fieldname = String(
                    input.dataset.field
                    || ""
                ).trim();


                const data = (
                    window.__productivity_v52_rows[
                        row_key
                    ]
                );


                if (
                    !data
                    || !fieldname
                ) {

                    return;
                }


                data[
                    fieldname
                ] = input.value;
            },
            true
        );


        document.addEventListener(
            "change",
            function (
                event
            ) {

                const input = (
                    event.target
                    && event.target.closest
                    ? event.target.closest(
                        ".productivity-v52-area-input"
                    )
                    : null
                );


                if (!input) {

                    return;
                }


                const row_key = String(
                    input.dataset.rowKey
                    || ""
                ).trim();


                const fieldname = String(
                    input.dataset.field
                    || ""
                ).trim();


                const data = (
                    window.__productivity_v52_rows[
                        row_key
                    ]
                );


                if (
                    !row_key
                    || !data
                    || !fieldname
                ) {

                    return;
                }


                data[
                    fieldname
                ] = String(
                    input.value
                    || ""
                ).trim();


                input.disabled = true;


                frappe.call({
                    method:
                        "is_production.production.report.productivity.productivity.save_productivity_area_override",

                    args: {
                        row_key:
                            row_key,

                        site:
                            data.productivity_v52_site
                            || "",

                        start_date:
                            data.productivity_v52_start_date
                            || "",

                        end_date:
                            data.productivity_v52_end_date
                            || "",

                        shift:
                            data.productivity_v52_shift
                            || "",

                        monthly_production_plan:
                            data.productivity_v52_plan
                            || "",

                        category:
                            data.productivity_v52_category
                            || "",

                        machine:
                            data.productivity_v52_machine
                            || "",

                        material:
                            data.productivity_v52_material
                            || "",

                        from_area:
                            data.from_area
                            || "",

                        to_area:
                            data.to_area
                            || "",

                        hauling_distance_m:
                            data.hauling_distance_m
                            || "",
                    },


                    callback: function (
                        response
                    ) {

                        const saved = (
                            response.message
                            || {}
                        );


                        if (
                            Object.prototype.hasOwnProperty.call(
                                saved,
                                "from_area"
                            )
                        ) {

                            data.from_area = String(
                                saved.from_area
                                || ""
                            );
                        }


                        if (
                            Object.prototype.hasOwnProperty.call(
                                saved,
                                "to_area"
                            )
                        ) {

                            data.to_area = String(
                                saved.to_area
                                || ""
                            );
                        }


                        if (
                            Object.prototype.hasOwnProperty.call(
                                saved,
                                "hauling_distance_m"
                            )
                        ) {

                            data.hauling_distance_m = String(
                                saved.hauling_distance_m
                                || ""
                            );
                        }


                        input.disabled = false;


                        frappe.show_alert({
                            message:
                                "Productivity area saved",

                            indicator:
                                "green",
                        });
                    },


                    error: function () {

                        input.disabled = false;


                        frappe.show_alert({
                            message:
                                "Productivity area was not saved",

                            indicator:
                                "red",
                        });
                    },
                });
            },
            true
        );
    }


    console.log(
        "KOSI_PRODUCTIVITY_BREAKDOWN_AREA_EDIT_V52 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_BREAKDOWN_AREA_EDIT_V52


// ============================================================
// KOSI_PRODUCTIVITY_MATERIAL_HEADING_V53
//
// Coal / Hards / Softs become bold material headings.
// BCM/HD is blank on these rows.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    function v53_escape(
        value
    ) {

        return String(
            value
            ?? ""
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    const previous_formatter_v53 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        if (
            data
            && data.productivity_material_heading_v53
        ) {

            // Always blank BCM/HD on Coal/Hards/Softs parent.
            if (
                fieldname
                === "productivity_bcm_hd"
            ) {

                return "";
            }


            // Make the Material heading bold.
            if (
                fieldname
                === "material"
            ) {

                return (
                    '<span style="font-weight:700;">'
                    + v53_escape(
                        data.material
                    )
                    + '</span>'
                );
            }
        }


        if (
            typeof previous_formatter_v53
            === "function"
        ) {

            return (
                previous_formatter_v53.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    console.log(
        "KOSI_PRODUCTIVITY_MATERIAL_HEADING_V53 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_MATERIAL_HEADING_V53


// ============================================================
// KOSI_PRODUCTIVITY_DOZER_BREAKDOWN_V55
//
// DOZER:
// parent material = bold heading
// breakdown row = editable From / To / Hauling Distance
//
// To Area and Hauling Distance are NOT auto populated.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    window.__productivity_v55_dozer_rows = (
        window.__productivity_v55_dozer_rows
        || {}
    );


    function v55_escape(
        value
    ) {

        return String(
            value
            ?? ""
        )
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    const previous_formatter_v55 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        // ====================================================
        // DOZER PARENT HEADING
        // ====================================================

        if (
            data
            && data.productivity_dozer_heading_v55
        ) {

            if (
                fieldname
                === "material"
            ) {

                return (
                    '<span style="font-weight:700;">'
                    + v55_escape(
                        data.material
                    )
                    + '</span>'
                );
            }


            if (
                fieldname === "productivity_bcm_hd"
                || fieldname === "from_area"
                || fieldname === "to_area"
                || fieldname === "hauling_distance_m"
            ) {

                return "";
            }
        }


        // ====================================================
        // DOZER DETAIL
        // ====================================================

        if (
            data
            && data.productivity_dozer_detail_v55
        ) {

            if (
                fieldname === "label"
            ) {

                return "";
            }


            if (
                fieldname
                === "productivity_bcm_hd"
            ) {

                return "";
            }


            if (
                fieldname === "from_area"
                || fieldname === "to_area"
                || fieldname === "hauling_distance_m"
            ) {

                const row_key = String(
                    data.row_key
                    || ""
                ).trim();


                if (!row_key) {

                    return "";
                }


                window.__productivity_v55_dozer_rows[
                    row_key
                ] = data;


                const field_value = String(
                    data[
                        fieldname
                    ]
                    || ""
                );


                return (
                    '<input'
                    + ' type="text"'
                    + ' class="productivity-v55-dozer-input"'
                    + ' data-row-key="'
                    + v55_escape(
                        row_key
                    )
                    + '"'
                    + ' data-field="'
                    + v55_escape(
                        fieldname
                    )
                    + '"'
                    + ' value="'
                    + v55_escape(
                        field_value
                    )
                    + '"'
                    + ' autocomplete="off"'
                    + ' style="'
                    + 'width:100%;'
                    + 'box-sizing:border-box;'
                    + 'padding:4px 6px;'
                    + 'border:1px solid #d8a12e;'
                    + 'border-radius:3px;'
                    + 'background:#fff8d8;'
                    + '"'
                    + '>'
                );
            }
        }


        if (
            typeof previous_formatter_v55
            === "function"
        ) {

            return (
                previous_formatter_v55.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    if (
        !window.__productivity_v55_dozer_bound
    ) {

        window.__productivity_v55_dozer_bound = true;


        document.addEventListener(
            "input",
            function (
                event
            ) {

                const input = (
                    event.target
                    && event.target.closest
                    ? event.target.closest(
                        ".productivity-v55-dozer-input"
                    )
                    : null
                );


                if (!input) {

                    return;
                }


                const row_key = String(
                    input.dataset.rowKey
                    || ""
                ).trim();


                const fieldname = String(
                    input.dataset.field
                    || ""
                ).trim();


                const data = (
                    window.__productivity_v55_dozer_rows[
                        row_key
                    ]
                );


                if (
                    !data
                    || !fieldname
                ) {

                    return;
                }


                data[
                    fieldname
                ] = input.value;
            },
            true
        );


        document.addEventListener(
            "change",
            function (
                event
            ) {

                const input = (
                    event.target
                    && event.target.closest
                    ? event.target.closest(
                        ".productivity-v55-dozer-input"
                    )
                    : null
                );


                if (!input) {

                    return;
                }


                const row_key = String(
                    input.dataset.rowKey
                    || ""
                ).trim();


                const fieldname = String(
                    input.dataset.field
                    || ""
                ).trim();


                const data = (
                    window.__productivity_v55_dozer_rows[
                        row_key
                    ]
                );


                if (
                    !row_key
                    || !data
                    || !fieldname
                ) {

                    return;
                }


                data[
                    fieldname
                ] = String(
                    input.value
                    || ""
                ).trim();


                input.disabled = true;


                frappe.call({

                    method:
                        "is_production.production.report.productivity.productivity.save_productivity_area_override",

                    args: {

                        row_key:
                            row_key,

                        site:
                            data.productivity_v55_site
                            || "",

                        start_date:
                            data.productivity_v55_start_date
                            || "",

                        end_date:
                            data.productivity_v55_end_date
                            || "",

                        shift:
                            data.productivity_v55_shift
                            || "",

                        monthly_production_plan:
                            data.productivity_v55_plan
                            || "",

                        category:
                            "Dozer",

                        machine:
                            data.productivity_v55_machine
                            || "",

                        material:
                            data.productivity_v55_save_material
                            || "",

                        from_area:
                            data.from_area
                            || "",

                        to_area:
                            data.to_area
                            || "",

                        hauling_distance_m:
                            data.hauling_distance_m
                            || "",
                    },


                    callback: function (
                        response
                    ) {

                        const saved = (
                            response.message
                            || {}
                        );


                        data.from_area = String(
                            saved.from_area
                            ?? data.from_area
                            ?? ""
                        );


                        data.to_area = String(
                            saved.to_area
                            ?? data.to_area
                            ?? ""
                        );


                        data.hauling_distance_m = String(
                            saved.hauling_distance_m
                            ?? data.hauling_distance_m
                            ?? ""
                        );


                        input.disabled = false;


                        frappe.show_alert({
                            message:
                                "Dozer area saved",

                            indicator:
                                "green",
                        });
                    },


                    error: function () {

                        input.disabled = false;


                        frappe.show_alert({
                            message:
                                "Dozer area was not saved",

                            indicator:
                                "red",
                        });
                    },
                });
            },
            true
        );
    }


    console.log(
        "KOSI_PRODUCTIVITY_DOZER_BREAKDOWN_V55 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_DOZER_BREAKDOWN_V55


// ============================================================
// KOSI_PRODUCTIVITY_BREAKDOWN_BCM_HD_V57
//
// Show BCM/HD on ADT + Dozer breakdown rows.
//
// Formula is calculated server-side:
//
// Output / average hauling distance.
//
// Broad material headings remain blank.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    const previous_formatter_v57 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        if (
            data
            && fieldname
            === "productivity_bcm_hd"
        ) {

            // -----------------------------------------------
            // Parent material headings stay blank.
            // -----------------------------------------------

            if (
                data.productivity_material_heading_v53
                || data.productivity_dozer_heading_v55
            ) {

                return "";
            }


            // -----------------------------------------------
            // ADT / DOZER breakdown calculation.
            // -----------------------------------------------

            if (
                data.productivity_breakdown_bcm_hd_v57
            ) {

                const number = Number(
                    data.productivity_bcm_hd
                );


                if (
                    !Number.isFinite(
                        number
                    )
                ) {

                    return "";
                }


                return number.toFixed(
                    2
                );
            }
        }


        if (
            typeof previous_formatter_v57
            === "function"
        ) {

            return (
                previous_formatter_v57.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    // --------------------------------------------------------
    // When user changes Hauling Distance:
    //
    // existing V52/V55 saves it.
    //
    // Refresh shortly afterwards so the newly calculated
    // BCM/HD value appears.
    // --------------------------------------------------------

    if (
        !window.__productivity_v57_bound
    ) {

        window.__productivity_v57_bound = true;


        document.addEventListener(
            "change",
            function (
                event
            ) {

                const input = (
                    event.target
                    && event.target.closest
                    ? event.target.closest(
                        '.productivity-v52-area-input[data-field="hauling_distance_m"], '
                        + '.productivity-v55-dozer-input[data-field="hauling_distance_m"]'
                    )
                    : null
                );


                if (!input) {

                    return;
                }


                clearTimeout(
                    window.__productivity_v57_refresh_timer
                );


                window.__productivity_v57_refresh_timer = (
                    setTimeout(
                        function () {

                            if (
                                frappe.query_report
                                && frappe.query_report.refresh
                            ) {

                                frappe.query_report.refresh();
                            }

                        },
                        1500
                    )
                );
            },
            true
        );
    }


    console.log(
        "KOSI_PRODUCTIVITY_BREAKDOWN_BCM_HD_V57 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_BREAKDOWN_BCM_HD_V57


// ============================================================
// KOSI_PRODUCTIVITY_HOURS_HIDE_COAL_HARDS_LABELS_V58
//
// Hours and Material:
//
// Coal / Hards remain visible headings.
//
// Their detailed child-row wording is hidden ONLY from the
// Label column.
//
// No row or calculation is removed.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    const previous_formatter_v58 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        if (
            data
            && data.productivity_hours_hide_label_v58
            && fieldname === "label"
        ) {

            return "";
        }


        if (
            typeof previous_formatter_v58
            === "function"
        ) {

            return (
                previous_formatter_v58.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    console.log(
        "KOSI_PRODUCTIVITY_HOURS_HIDE_COAL_HARDS_LABELS_V58 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_HOURS_HIDE_COAL_HARDS_LABELS_V58


// ============================================================
// KOSI_PRODUCTIVITY_HOURS_HIDE_SOFTS_DOZER_LABELS_V59
//
// Hours and Material only.
//
// Hide Label wording for:
//
// Softs child:
//     Topsoil Dump
//
// Dozer child:
//     Midburden Dozing
//
// The row itself remains.
// ============================================================

(function () {

    const report = (
        frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    const previous_formatter_v59 = (
        report.formatter
    );


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = String(
            column
            && column.fieldname
            || ""
        ).trim();


        if (
            data
            && data.productivity_hours_hide_label_v59
            && fieldname === "label"
        ) {

            return "";
        }


        if (
            typeof previous_formatter_v59
            === "function"
        ) {

            return (
                previous_formatter_v59.apply(
                    this,
                    arguments
                )
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };


    console.log(
        "KOSI_PRODUCTIVITY_HOURS_HIDE_SOFTS_DOZER_LABELS_V59 loaded"
    );

})();


// END KOSI_PRODUCTIVITY_HOURS_HIDE_SOFTS_DOZER_LABELS_V59


// ============================================================
// KOSI_PRODUCTIVITY_EXCAVATOR_DISPLAY_FORMATTER_V66
//
// Excavator material child rows in Summary Per Machine:
//
// Label             = BLANK
// Working Hours     = BLANK
// Productivity      = BLANK
// Productivity/HD   = BLANK
// From Area         = BLANK
// To Area           = BLANK
// Hauling Distance  = BLANK
//
// Output BCM remains visible.
//
// Coal / Hards / Softs appear ONLY in Material column.
//
// Targets rows created by V64/V65 only.
// ============================================================

(function () {

    const report =
        frappe.query_reports["Productivity"];

    if (!report) {
        return;
    }


    const previous_formatter_v66 =
        report.formatter;


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        let formatted_value;


        // ----------------------------------------------------
        // KEEP ALL EXISTING FORMATTER BEHAVIOUR
        // ----------------------------------------------------

        if (
            typeof previous_formatter_v66
            === "function"
        ) {

            formatted_value =
                previous_formatter_v66(
                    value,
                    row,
                    column,
                    data,
                    default_formatter
                );

        } else {

            formatted_value =
                default_formatter(
                    value,
                    row,
                    column,
                    data
                );
        }


        // ----------------------------------------------------
        // EXCAVATOR MATERIAL CHILD ROW
        // ----------------------------------------------------

        const is_excavator_material_row =
            Boolean(
                data
                && (
                    data.productivity_excavator_material_v64
                    || data.productivity_excavator_material_display_v65
                )
            );


        if (!is_excavator_material_row) {
            return formatted_value;
        }


        const fieldname =
            String(
                column.fieldname
                || ""
            );


        // ----------------------------------------------------
        // THESE MUST DISPLAY BLANK
        // ----------------------------------------------------

        const blank_fields = [
            "label",
            "working_hours",
            "productivity",
            "productivity_bcm_hd",
            "from_area",
            "to_area",
            "hauling_distance_m"
        ];


        if (
            blank_fields.includes(
                fieldname
            )
        ) {

            return "";
        }


        // ----------------------------------------------------
        // MATERIAL MUST REMAIN VISIBLE
        // ----------------------------------------------------

        if (
            fieldname === "material"
        ) {

            return default_formatter(
                data.material || "",
                row,
                column,
                data
            );
        }


        // ----------------------------------------------------
        // OUTPUT BCM MUST REMAIN VISIBLE
        // ----------------------------------------------------

        return formatted_value;
    };


})();


// END KOSI_PRODUCTIVITY_EXCAVATOR_DISPLAY_FORMATTER_V66


// ============================================================
// KOSI_PRODUCTIVITY_MACHINE_TYPE_MULTISELECT_V72
//
// Machine Type:
//   blank             = all
//   Excavator         = Excavator only
//   ADT               = ADT only
//   Dozer             = Dozer only
//   Excavator + ADT   = both
//
// Backend V72 safely handles the selected array.
// ============================================================

(function () {
    const report =
        frappe.query_reports
        && frappe.query_reports["Productivity"];

    if (
        !report
        || !Array.isArray(report.filters)
    ) {
        return;
    }

    const filter = report.filters.find(
        item =>
            item
            && item.fieldname === "machine_type"
    );

    if (!filter) {
        return;
    }

    const machine_types = [
        "Excavator",
        "ADT",
        "Dozer"
    ];

    filter.fieldtype = "MultiSelectList";

    // Remove old Select/Link options.
    delete filter.options;

    filter.get_data = function (txt) {
        const search = String(
            txt || ""
        ).trim().toLowerCase();

        return machine_types
            .filter(value =>
                !search
                || value.toLowerCase().includes(search)
            )
            .map(value => ({
                value: value,
                description: ""
            }));
    };

    filter.placeholder = __(
        "Select Machine Types"
    );
})();

// END KOSI_PRODUCTIVITY_MACHINE_TYPE_MULTISELECT_V72


// ============================================================
// KOSI_PRODUCTIVITY_EXCAVATOR_AREA_INPUT_V73
//
// FINAL FRONT-END DISPLAY RULE.
//
// Backend V72 already provides:
//
//     from_area
//     to_area
//     hauling_distance_m
//     productivity_editable = 1
//     productivity_edit_key
//
// V66 previously hid Excavator area fields.
//
// V73 runs AFTER V66/V72 and restores editable yellow inputs
// ONLY on V71 Excavator detail rows:
//
//     Machine
//         Coal
//             Ramp detail       <-- editable here
//         Hards
//             Overburden detail <-- editable here
//         Softs
//             Topsoil detail    <-- editable here
//
// Machine totals and Coal/Hards/Softs subtotal rows remain
// clean and non-editable.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );

    if (!report) {
        return;
    }


    // ========================================================
    // MEMORY
    // ========================================================

    window.__productivity_excavator_area_v73 =
        window.__productivity_excavator_area_v73
        || {};


    function clean(value) {

        if (
            value === null
            || value === undefined
        ) {
            return "";
        }

        return String(value);
    }


    function escape_html(value) {

        return clean(value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#039;");
    }


    function get_key(data) {

        return clean(
            data.productivity_edit_key
            || data.row_key
            || ""
        ).trim();
    }


    function remember_row(data) {

        const row_key = get_key(data);

        if (!row_key) {
            return null;
        }

        const memory =
            window.__productivity_excavator_area_v73;

        if (!memory[row_key]) {

            memory[row_key] = {

                row_key:
                    row_key,

                site:
                    clean(
                        data.productivity_edit_site
                    ),

                start_date:
                    clean(
                        data.productivity_edit_start_date
                    ),

                end_date:
                    clean(
                        data.productivity_edit_end_date
                    ),

                shift:
                    clean(
                        data.productivity_edit_shift
                    ),

                monthly_production_plan:
                    clean(
                        data.productivity_edit_monthly_plan
                    ),

                category:
                    clean(
                        data.productivity_edit_category
                        || "Excavator"
                    ),

                machine:
                    clean(
                        data.productivity_edit_machine
                        || data.productivity_excavator_parent_machine_v71
                    ),

                material:
                    clean(
                        data.productivity_edit_material
                        || data.material
                    ),

                from_area:
                    clean(
                        data.from_area
                    ),

                to_area:
                    clean(
                        data.to_area
                    ),

                hauling_distance_m:
                    clean(
                        data.hauling_distance_m
                    )
            };

        } else {

            // Keep report values synchronized unless user is
            // currently typing an unsaved value.
            const state = memory[row_key];

            if (!state.dirty_from_area) {
                state.from_area =
                    clean(data.from_area);
            }

            if (!state.dirty_to_area) {
                state.to_area =
                    clean(data.to_area);
            }

            if (!state.dirty_hauling_distance_m) {
                state.hauling_distance_m =
                    clean(
                        data.hauling_distance_m
                    );
            }
        }

        return memory[row_key];
    }


    // ========================================================
    // INPUT EVENT
    // ========================================================

    window.productivityExcavatorAreaV73Input =
        function (input) {

            if (!input) {
                return;
            }

            const row_key =
                clean(
                    input.dataset.rowKey
                ).trim();

            const field =
                clean(
                    input.dataset.field
                ).trim();

            if (
                !row_key
                || !field
            ) {
                return;
            }

            const memory =
                window.__productivity_excavator_area_v73;

            const state =
                memory[row_key];

            if (!state) {
                return;
            }

            state[field] =
                clean(
                    input.value
                );

            state[
                "dirty_" + field
            ] = true;

            input.classList.add(
                "productivity-excavator-area-v73-dirty"
            );
        };


    // ========================================================
    // SAVE
    // ========================================================

    window.productivityExcavatorAreaV73Save =
        function (input) {

            if (!input) {
                return;
            }

            const row_key =
                clean(
                    input.dataset.rowKey
                ).trim();

            const field =
                clean(
                    input.dataset.field
                ).trim();

            if (
                !row_key
                || !field
            ) {
                return;
            }

            const memory =
                window.__productivity_excavator_area_v73;

            const state =
                memory[row_key];

            if (!state) {
                return;
            }

            state[field] =
                clean(
                    input.value
                );

            input.disabled = true;

            frappe.call({

                method:
                    "is_production.production.report.productivity.productivity.save_productivity_area_override",

                args: {

                    row_key:
                        state.row_key,

                    site:
                        state.site,

                    start_date:
                        state.start_date,

                    end_date:
                        state.end_date,

                    shift:
                        state.shift,

                    monthly_production_plan:
                        state.monthly_production_plan,

                    category:
                        state.category,

                    machine:
                        state.machine,

                    material:
                        state.material,

                    from_area:
                        state.from_area,

                    to_area:
                        state.to_area,

                    hauling_distance_m:
                        state.hauling_distance_m
                },

                freeze:
                    false,

                callback:
                    function (response) {

                        input.disabled = false;

                        if (
                            response
                            && response.message
                            && response.message.saved
                        ) {

                            state.dirty_from_area =
                                false;

                            state.dirty_to_area =
                                false;

                            state.dirty_hauling_distance_m =
                                false;

                            input.classList.remove(
                                "productivity-excavator-area-v73-dirty"
                            );

                            input.classList.add(
                                "productivity-excavator-area-v73-saved"
                            );

                            setTimeout(
                                function () {

                                    input.classList.remove(
                                        "productivity-excavator-area-v73-saved"
                                    );

                                },
                                1200
                            );

                            frappe.show_alert(
                                {
                                    message:
                                        __(
                                            "Productivity area saved"
                                        ),

                                    indicator:
                                        "green"
                                },
                                2
                            );

                        } else {

                            frappe.msgprint(
                                __(
                                    "Productivity area could not be saved."
                                )
                            );
                        }
                    },

                error:
                    function () {

                        input.disabled = false;

                        frappe.msgprint(
                            __(
                                "Productivity area could not be saved."
                            )
                        );
                    }
            });
        };


    // ========================================================
    // FINAL FORMATTER WRAPPER
    // ========================================================

    const previous_formatter =
        report.formatter;


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {

            let formatted_value;

            if (
                typeof previous_formatter
                === "function"
            ) {

                formatted_value =
                    previous_formatter.call(
                        this,
                        value,
                        row,
                        column,
                        data,
                        default_formatter
                    );

            } else {

                formatted_value =
                    default_formatter(
                        value,
                        row,
                        column,
                        data
                    );
            }


            if (
                !data
                || !column
            ) {
                return formatted_value;
            }


            // =================================================
            // ONLY V71 EXCAVATOR DETAIL ROWS
            // =================================================

            const is_excavator_detail =
                Number(
                    data.productivity_excavator_detail_v71
                    || 0
                ) === 1;


            const is_editable =
                Number(
                    data.productivity_editable
                    || 0
                ) === 1;


            if (
                !is_excavator_detail
                || !is_editable
            ) {
                return formatted_value;
            }


            const fieldname =
                clean(
                    column.fieldname
                );


            if (
                ![
                    "from_area",
                    "to_area",
                    "hauling_distance_m"
                ].includes(
                    fieldname
                )
            ) {
                return formatted_value;
            }


            const state =
                remember_row(
                    data
                );


            if (!state) {
                return formatted_value;
            }


            const field_value =
                clean(
                    state[
                        fieldname
                    ]
                );


            return (
                '<input'
                + ' type="text"'
                + ' class="productivity-excavator-area-v73-input"'
                + ' data-row-key="'
                + escape_html(
                    state.row_key
                )
                + '"'
                + ' data-field="'
                + escape_html(
                    fieldname
                )
                + '"'
                + ' value="'
                + escape_html(
                    field_value
                )
                + '"'
                + ' oninput="productivityExcavatorAreaV73Input(this)"'
                + ' onchange="productivityExcavatorAreaV73Save(this)"'
                + ' onblur="productivityExcavatorAreaV73Save(this)"'
                + ' />'
            );
        };


    // ========================================================
    // STYLE - SAME IDEA AS ADT YELLOW CAPTURE CELLS
    // ========================================================

    if (
        !document.getElementById(
            "productivity-excavator-area-v73-style"
        )
    ) {

        const style =
            document.createElement(
                "style"
            );

        style.id =
            "productivity-excavator-area-v73-style";

        style.innerHTML = `

            .productivity-excavator-area-v73-input {

                width:
                    100% !important;

                min-width:
                    95px !important;

                height:
                    26px !important;

                padding:
                    2px 6px !important;

                border:
                    1px solid #d99a00 !important;

                background:
                    #fff8dd !important;

                box-shadow:
                    none !important;

                font-size:
                    12px !important;

                color:
                    #222 !important;

                cursor:
                    text !important;

                pointer-events:
                    auto !important;

                position:
                    relative !important;

                z-index:
                    10 !important;
            }


            .productivity-excavator-area-v73-input:focus {

                background:
                    #ffffff !important;

                border:
                    2px solid #d99a00 !important;

                outline:
                    none !important;
            }


            .productivity-excavator-area-v73-input.productivity-excavator-area-v73-dirty {

                background:
                    #fff1b8 !important;

                border:
                    2px solid #d99a00 !important;
            }


            .productivity-excavator-area-v73-input.productivity-excavator-area-v73-saved {

                background:
                    #e7f7e7 !important;

                border:
                    1px solid #5a9f5a !important;
            }

        `;

        document.head.appendChild(
            style
        );
    }

})();

// END KOSI_PRODUCTIVITY_EXCAVATOR_AREA_INPUT_V73


// ============================================================
// KOSI_PRODUCTIVITY_EXCAVATOR_DISPLAY_V74
//
// V74 final display rules:
//
// Excavator V71 detail rows:
//
//     Label:
//         keep existing V66/V71 detail name.
//
//     Material:
//         BLANK - do not duplicate detail name.
//
//     Productivity (BCM/Hr):
//         show backend calculated productivity.
//
// Area fields:
//         leave V73 yellow editable inputs untouched.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );

    if (!report) {
        return;
    }


    const previous_formatter =
        report.formatter;


    function number_value(
        value
    ) {

        if (
            value === null
            || value === undefined
            || value === ""
        ) {

            return 0;
        }

        const number = Number(
            String(value)
                .replace(/,/g, "")
                .trim()
        );

        return Number.isFinite(
            number
        )
            ? number
            : 0;
    }


    function format_productivity(
        value
    ) {

        const number =
            number_value(
                value
            );

        if (!number) {
            return "0";
        }

        // Same clean style as the existing report.
        if (
            Math.abs(
                number
                - Math.round(
                    number
                )
            ) < 0.005
        ) {

            return String(
                Math.round(
                    number
                )
            );
        }

        return number
            .toFixed(2)
            .replace(
                /\.00$/,
                ""
            )
            .replace(
                /(\.\d)0$/,
                "$1"
            );
    }


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {

            let formatted_value;

            if (
                typeof previous_formatter
                === "function"
            ) {

                formatted_value =
                    previous_formatter.call(
                        this,
                        value,
                        row,
                        column,
                        data,
                        default_formatter
                    );

            } else {

                formatted_value =
                    default_formatter(
                        value,
                        row,
                        column,
                        data
                    );
            }


            if (
                !data
                || !column
            ) {

                return formatted_value;
            }


            const is_excavator_detail =
                Number(
                    data.productivity_excavator_detail_v71
                    || 0
                ) === 1;


            if (!is_excavator_detail) {

                return formatted_value;
            }


            const fieldname =
                String(
                    column.fieldname
                    || ""
                ).trim();


            // =================================================
            // MATERIAL MUST NOT DISPLAY UNDER EXCAVATOR
            // =================================================

            if (
                fieldname
                === "material"
            ) {

                return "";
            }


            // =================================================
            // PRODUCTIVITY BCM/HR
            // =================================================

            if (
                fieldname
                === "productivity"
            ) {

                let productivity =
                    number_value(
                        data.productivity
                    );


                // Fallback only if backend value is absent.
                if (
                    productivity <= 0
                ) {

                    const hours =
                        number_value(
                            data.working_hours
                        );

                    const output =
                        number_value(
                            (
                                data.adjusted_bcm
                                !== undefined
                                && data.adjusted_bcm
                                !== null
                                && data.adjusted_bcm
                                !== ""
                            )
                                ? data.adjusted_bcm
                                : data.output
                        );


                    if (
                        hours > 0
                        && output > 0
                    ) {

                        productivity =
                            output / hours;
                    }
                }


                return (
                    '<div style="'
                    + 'text-align:right;'
                    + 'padding-right:4px;'
                    + '">'
                    + format_productivity(
                        productivity
                    )
                    + '</div>'
                );
            }


            return formatted_value;
        };

})();

// END KOSI_PRODUCTIVITY_EXCAVATOR_DISPLAY_V74


// ============================================================
// KOSI_PRODUCTIVITY_KEEP_MATERIAL_TOTALS_DISPLAY_V75
//
// KEEP:
//
//     Coal / Hards / Softs subtotal rows + totals.
//
// DETAIL ROW:
//
//     Label:
//         Ramp 2 - 3 2#Coal
//
//     Material:
//         BLANK, because detail is already displayed in Label.
//
//     Productivity BCM/Hr:
//         display.
//
//     Productivity BCM/HD:
//         display when hauling distance exists.
//
// V73 yellow area inputs remain untouched.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );

    if (!report) {
        return;
    }


    const previous_formatter =
        report.formatter;


    function number_value(
        value
    ) {

        if (
            value === null
            || value === undefined
            || value === ""
        ) {

            return 0;
        }

        const number = Number(
            String(value)
                .replace(/,/g, "")
                .trim()
        );

        return Number.isFinite(
            number
        )
            ? number
            : 0;
    }


    function display_number(
        value,
        decimals = 2
    ) {

        const number =
            number_value(
                value
            );

        if (!number) {
            return "";
        }

        if (
            Math.abs(
                number
                - Math.round(
                    number
                )
            ) < 0.005
        ) {

            return String(
                Math.round(
                    number
                )
            );
        }

        return number
            .toFixed(
                decimals
            )
            .replace(
                /\.00$/,
                ""
            )
            .replace(
                /(\.\d)0$/,
                "$1"
            );
    }


    function right_value(
        value
    ) {

        return (
            '<div style="'
            + 'text-align:right;'
            + 'padding-right:4px;'
            + '">'
            + value
            + '</div>'
        );
    }


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {

            let formatted_value;

            if (
                typeof previous_formatter
                === "function"
            ) {

                formatted_value =
                    previous_formatter.call(
                        this,
                        value,
                        row,
                        column,
                        data,
                        default_formatter
                    );

            } else {

                formatted_value =
                    default_formatter(
                        value,
                        row,
                        column,
                        data
                    );
            }


            if (
                !data
                || !column
            ) {

                return formatted_value;
            }


            const fieldname =
                String(
                    column.fieldname
                    || ""
                ).trim();


            const is_detail =
                Number(
                    data.productivity_excavator_detail_v71
                    || 0
                ) === 1;


            // =================================================
            // MATERIAL SUBTOTAL ROWS:
            //
            // DO NOTHING.
            //
            // This preserves:
            //     Coal
            //     Hards
            //     Softs
            //
            // with all their subtotal values.
            // =================================================

            if (!is_detail) {

                return formatted_value;
            }


            // =================================================
            // DETAIL MATERIAL NAME ALREADY EXISTS IN LABEL
            // =================================================

            if (
                fieldname === "material"
            ) {

                return "";
            }


            // =================================================
            // PRODUCTIVITY BCM/HR
            // =================================================

            if (
                fieldname === "productivity"
            ) {

                let productivity =
                    number_value(
                        data.productivity
                    );

                if (
                    productivity <= 0
                ) {

                    const hours =
                        number_value(
                            data.working_hours
                        );

                    const bcm =
                        number_value(
                            (
                                data.adjusted_bcm
                                !== undefined
                                && data.adjusted_bcm
                                !== null
                                && data.adjusted_bcm !== ""
                            )
                                ? data.adjusted_bcm
                                : data.output
                        );

                    if (
                        hours > 0
                        && bcm > 0
                    ) {

                        productivity =
                            bcm / hours;
                    }
                }

                return right_value(
                    display_number(
                        productivity,
                        2
                    )
                );
            }


            // =================================================
            // PRODUCTIVITY BCM/HD
            // =================================================

            if (
                fieldname
                === "productivity_bcm_hd"
            ) {

                const bcm_hd =
                    number_value(
                        data.productivity_bcm_hd
                    );

                if (!bcm_hd) {
                    return "";
                }

                return right_value(
                    display_number(
                        bcm_hd,
                        2
                    )
                );
            }


            return formatted_value;
        };

})();

// END KOSI_PRODUCTIVITY_KEEP_MATERIAL_TOTALS_DISPLAY_V75


// ============================================================
// KOSI_PRODUCTIVITY_EXCAVATOR_SAME_AS_ADT_DISPLAY_V76
//
// Final Excavator display:
//
//   Label column:
//     machine total only (e.g. IS0604)
//
//   Material column:
//     Coal / Hards / Softs subtotal rows remain
//     detail rows show:
//         Ramp 2 - 3 2#Coal
//         Ramp 1 Overburden
//         Topsoil Dump
//
//   Detail row label is blank.
//   Detail row productivity BCM/Hr is shown.
//   Detail row productivity BCM/HD is shown when distance exists.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );

    if (!report) {
        return;
    }

    const previous_formatter = report.formatter;

    function number_value(value) {
        if (
            value === null
            || value === undefined
            || value === ""
        ) {
            return 0;
        }

        const number = Number(
            String(value)
                .replace(/,/g, "")
                .trim()
        );

        return Number.isFinite(number) ? number : 0;
    }

    function display_number(value, decimals = 2) {
        const number = number_value(value);

        if (!number) {
            return "";
        }

        if (
            Math.abs(number - Math.round(number)) < 0.005
        ) {
            return String(Math.round(number));
        }

        return number
            .toFixed(decimals)
            .replace(/\.00$/, "")
            .replace(/(\.\d)0$/, "$1");
    }

    function right_value(value) {
        return (
            '<div style="text-align:right;padding-right:4px;">'
            + value
            + '</div>'
        );
    }

    function escape_html(text) {
        return String(text || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {
        let formatted_value;

        if (typeof previous_formatter === "function") {
            formatted_value = previous_formatter.call(
                this,
                value,
                row,
                column,
                data,
                default_formatter
            );
        } else {
            formatted_value = default_formatter(
                value,
                row,
                column,
                data
            );
        }

        if (!data || !column) {
            return formatted_value;
        }

        const fieldname = String(
            column.fieldname || ""
        ).trim();

        const is_excavator_detail =
            Number(data.productivity_excavator_detail_v71 || 0) === 1;

        // Leave all non-detail rows untouched.
        // This keeps the main subtotal headings:
        // Coal / Hards / Softs
        if (!is_excavator_detail) {
            return formatted_value;
        }

        // Detail rows:
        // move visible name out of Label and into Material column
        if (fieldname === "label") {
            return "";
        }

        if (fieldname === "material") {
            const material_text = String(
                data.material || data.label || ""
            ).trim();

            if (!material_text) {
                return "";
            }

            return (
                '<div style="padding-left:0px;">'
                + escape_html(material_text)
                + '</div>'
            );
        }

        if (fieldname === "productivity") {
            let productivity = number_value(data.productivity);

            if (productivity <= 0) {
                const hours = number_value(data.working_hours);
                const bcm = number_value(
                    (
                        data.adjusted_bcm !== undefined
                        && data.adjusted_bcm !== null
                        && data.adjusted_bcm !== ""
                    )
                        ? data.adjusted_bcm
                        : data.output
                );

                if (hours > 0 && bcm > 0) {
                    productivity = bcm / hours;
                }
            }

            return right_value(
                display_number(productivity, 2)
            );
        }

        if (fieldname === "productivity_bcm_hd") {
            const bcm_hd = number_value(
                data.productivity_bcm_hd
            );

            if (!bcm_hd) {
                return "";
            }

            return right_value(
                display_number(bcm_hd, 2)
            );
        }

        return formatted_value;
    };

})();

// END KOSI_PRODUCTIVITY_EXCAVATOR_SAME_AS_ADT_DISPLAY_V76


// ============================================================
// KOSI_PRODUCTIVITY_EXCAVATOR_MATERIAL_TOTAL_DISPLAY_V78
//
// Excavator material subtotal rows:
//
//     Coal     3 hrs    122 BCM    40.67 BCM/Hr
//     Hards   10 hrs    420 BCM    42.00 BCM/Hr
//     Softs   18 hrs    750 BCM    41.67 BCM/Hr
//
// Keep detail rows unchanged.
//
// Backend V77 already calculates:
//     working_hours
//     output / adjusted_bcm
//     productivity
//
// V78 only ensures those values are VISIBLE and BOLD.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );

    if (!report) {
        return;
    }

    const previous_formatter =
        report.formatter;


    function number_value(value) {

        if (
            value === null
            || value === undefined
            || value === ""
        ) {
            return 0;
        }

        const number = Number(
            String(value)
                .replace(/,/g, "")
                .trim()
        );

        return Number.isFinite(number)
            ? number
            : 0;
    }


    function display_number(
        value,
        decimals = 2
    ) {

        const number =
            number_value(value);

        if (
            value === ""
            || value === null
            || value === undefined
        ) {
            return "";
        }

        if (
            Math.abs(
                number - Math.round(number)
            ) < 0.005
        ) {
            return String(
                Math.round(number)
            );
        }

        return number
            .toFixed(decimals)
            .replace(/\.00$/, "")
            .replace(/(\.\d)0$/, "$1");
    }


    function display_hours(value) {

        const number =
            number_value(value);

        if (!number) {
            return "";
        }

        if (
            Math.abs(
                number - Math.round(number)
            ) < 0.0005
        ) {
            return String(
                Math.round(number)
            );
        }

        return number
            .toFixed(3)
            .replace(/0+$/, "")
            .replace(/\.$/, "");
    }


    function display_bcm(value) {

        const number =
            number_value(value);

        if (!number) {
            return "";
        }

        if (
            Math.abs(
                number - Math.round(number)
            ) < 0.0005
        ) {

            return Math.round(number)
                .toLocaleString();
        }

        return number
            .toLocaleString(
                undefined,
                {
                    maximumFractionDigits: 3
                }
            );
    }


    function bold_right(value) {

        return (
            '<div style="'
            + 'font-weight:700;'
            + 'text-align:right;'
            + 'padding-right:4px;'
            + '">'
            + value
            + '</div>'
        );
    }


    function bold_text(value) {

        return (
            '<div style="'
            + 'font-weight:700;'
            + '">'
            + String(value || "")
            + '</div>'
        );
    }


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {

            let formatted_value;

            if (
                typeof previous_formatter
                === "function"
            ) {

                formatted_value =
                    previous_formatter.call(
                        this,
                        value,
                        row,
                        column,
                        data,
                        default_formatter
                    );

            } else {

                formatted_value =
                    default_formatter(
                        value,
                        row,
                        column,
                        data
                    );
            }


            if (
                !data
                || !column
            ) {
                return formatted_value;
            }


            const is_material_total =
                Number(
                    data.productivity_excavator_material_total_v77
                    || 0
                ) === 1;


            if (!is_material_total) {
                return formatted_value;
            }


            const fieldname =
                String(
                    column.fieldname
                    || ""
                ).trim();


            // =================================================
            // MATERIAL HEADING
            //
            // Coal / Hards / Softs
            // =================================================

            if (
                fieldname === "material"
            ) {

                return bold_text(
                    data.material
                    || ""
                );
            }


            // =================================================
            // WORKING HOURS
            // =================================================

            if (
                fieldname === "working_hours"
            ) {

                return bold_right(
                    display_hours(
                        data.working_hours
                    )
                );
            }


            // =================================================
            // BCM / OUTPUT
            // =================================================

            if (
                fieldname === "output"
                || fieldname === "adjusted_bcm"
            ) {

                const bcm =
                    (
                        data.adjusted_bcm !== undefined
                        && data.adjusted_bcm !== null
                        && data.adjusted_bcm !== ""
                    )
                        ? data.adjusted_bcm
                        : data.output;

                return bold_right(
                    display_bcm(
                        bcm
                    )
                );
            }


            // =================================================
            // PRODUCTIVITY BCM/Hr
            // =================================================

            if (
                fieldname === "productivity"
            ) {

                let productivity =
                    number_value(
                        data.productivity
                    );

                if (
                    productivity <= 0
                ) {

                    const hours =
                        number_value(
                            data.working_hours
                        );

                    const bcm =
                        number_value(
                            (
                                data.adjusted_bcm !== undefined
                                && data.adjusted_bcm !== null
                                && data.adjusted_bcm !== ""
                            )
                                ? data.adjusted_bcm
                                : data.output
                        );

                    if (
                        hours > 0
                        && bcm > 0
                    ) {

                        productivity =
                            bcm / hours;
                    }
                }

                return bold_right(
                    display_number(
                        productivity,
                        2
                    )
                );
            }


            return formatted_value;
        };

})();

// END KOSI_PRODUCTIVITY_EXCAVATOR_MATERIAL_TOTAL_DISPLAY_V78


// ============================================================
// KOSI_PRODUCTIVITY_MANUAL_SAVE_ONLY_V80
//
// IMPORTANT:
//
// NO AUTO SAVE.
//
// User may edit:
//
//     From Area
//     To Area
//     Hauling Distance
//
// Values stay as draft/dirty values in the current report.
//
// Nothing is written to the database on:
//
//     input
//     change
//     blur
//
// User clicks:
//
//     SAVE OVERRIDE
//
// when completely finished.
//
// Existing Save Override workflow then saves dirty overrides
// one-by-one and creates the final report snapshot.
// ============================================================

(function () {

    // ========================================================
    // CANCEL ANY V79 AUTO-SAVE TIMERS
    // ========================================================

    try {

        const locks =
            window.__productivity_area_save_v79
            || {};

        Object.keys(
            locks
        ).forEach(
            function (row_key) {

                const lock =
                    locks[row_key];

                if (
                    lock
                    && lock.timer
                ) {

                    clearTimeout(
                        lock.timer
                    );

                    lock.timer = null;
                }

                if (lock) {

                    lock.pending =
                        false;

                    lock.saving =
                        false;
                }
            }
        );

    } catch (error) {

        console.warn(
            "Productivity V80 timer cleanup:",
            error
        );
    }


    function clean(
        value
    ) {

        if (
            value === null
            || value === undefined
        ) {

            return "";
        }

        return String(
            value
        );
    }


    function get_row_key(
        input
    ) {

        return clean(
            input
            && input.dataset
                ? input.dataset.rowKey
                : ""
        ).trim();
    }


    function get_field(
        input
    ) {

        return clean(
            input
            && input.dataset
                ? input.dataset.field
                : ""
        ).trim();
    }


    // ========================================================
    // FIND CURRENT REPORT ROW
    // ========================================================

    function find_report_row(
        row_key
    ) {

        if (
            !frappe.query_report
            || !Array.isArray(
                frappe.query_report.data
            )
        ) {

            return null;
        }

        for (
            const item
            of frappe.query_report.data
        ) {

            const row = (
                item
                && (
                    item.__data
                    || item
                )
            );

            if (!row) {
                continue;
            }

            const key = clean(
                row.productivity_edit_key
                || row.row_key
                || ""
            ).trim();

            if (
                key
                === row_key
            ) {

                return row;
            }
        }

        return null;
    }


    // ========================================================
    // SYNC DRAFT INTO SAVE OVERRIDE DATA
    // ========================================================

    function sync_manual_draft(
        input
    ) {

        if (!input) {
            return;
        }


        const row_key =
            get_row_key(
                input
            );


        const field =
            get_field(
                input
            );


        if (
            !row_key
            || !field
        ) {

            return;
        }


        if (
            ![
                "from_area",
                "to_area",
                "hauling_distance_m"
            ].includes(
                field
            )
        ) {

            return;
        }


        const value =
            clean(
                input.value
            );


        // ----------------------------------------------------
        // Keep V73 memory synchronized.
        // ----------------------------------------------------

        window.__productivity_excavator_area_v73 = (
            window.__productivity_excavator_area_v73
            || {}
        );


        let state = (
            window.__productivity_excavator_area_v73[
                row_key
            ]
        );


        if (!state) {

            state = {
                row_key:
                    row_key
            };

            window.__productivity_excavator_area_v73[
                row_key
            ] = state;
        }


        state[
            field
        ] = value;


        state[
            "dirty_" + field
        ] = true;


        // ----------------------------------------------------
        // Find actual report row.
        // ----------------------------------------------------

        const report_row =
            find_report_row(
                row_key
            );


        if (report_row) {

            report_row[
                field
            ] = value;
        }


        // ----------------------------------------------------
        // Existing Save Override uses this map.
        // ----------------------------------------------------

        window.productivity_editable_rows = (
            window.productivity_editable_rows
            || {}
        );


        let editable_row = (
            window.productivity_editable_rows[
                row_key
            ]
        );


        if (!editable_row) {

            editable_row = (
                report_row
                || {}
            );


            window.productivity_editable_rows[
                row_key
            ] = editable_row;
        }


        editable_row[
            field
        ] = value;


        editable_row.productivity_edit_key =
            row_key;


        editable_row.row_key =
            row_key;


        // ----------------------------------------------------
        // If this is an Excavator V73 row, ensure all metadata
        // required by save_productivity_area_override exists.
        // ----------------------------------------------------

        if (state) {

            editable_row.productivity_edit_site = (
                editable_row.productivity_edit_site
                || state.site
                || ""
            );


            editable_row.productivity_edit_start_date = (
                editable_row.productivity_edit_start_date
                || state.start_date
                || ""
            );


            editable_row.productivity_edit_end_date = (
                editable_row.productivity_edit_end_date
                || state.end_date
                || ""
            );


            editable_row.productivity_edit_shift = (
                editable_row.productivity_edit_shift
                || state.shift
                || ""
            );


            editable_row.productivity_edit_monthly_plan = (
                editable_row.productivity_edit_monthly_plan
                || state.monthly_production_plan
                || ""
            );


            editable_row.productivity_edit_category = (
                editable_row.productivity_edit_category
                || state.category
                || "Excavator"
            );


            editable_row.productivity_edit_machine = (
                editable_row.productivity_edit_machine
                || state.machine
                || ""
            );


            editable_row.productivity_edit_material = (
                editable_row.productivity_edit_material
                || state.material
                || ""
            );


            editable_row.from_area =
                clean(
                    state.from_area
                );


            editable_row.to_area =
                clean(
                    state.to_area
                );


            editable_row.hauling_distance_m =
                clean(
                    state.hauling_distance_m
                );
        }


        // ----------------------------------------------------
        // MARK DIRTY.
        //
        // This tells the existing SAVE OVERRIDE button:
        // "save this row when user clicks the button".
        // ----------------------------------------------------

        window.productivity_dirty_overrides = (
            window.productivity_dirty_overrides
            || {}
        );


        window.productivity_dirty_overrides[
            row_key
        ] = true;


        // Add the generic class used by the existing override
        // workflow as well as our own visual dirty class.
        input.classList.add(
            "productivity-inline-edit"
        );


        input.classList.add(
            "productivity-override-dirty"
        );


        input.classList.add(
            "productivity-excavator-area-v73-dirty"
        );


        input.classList.remove(
            "productivity-excavator-area-v73-saved"
        );


        // Refresh Save Override button state/count.
        if (
            typeof productivity_update_override_button
            === "function"
        ) {

            productivity_update_override_button();
        }
    }


    // ========================================================
    // INPUT EVENT
    //
    // Capture draft ONLY.
    // NO frappe.call.
    // NO database write.
    // ========================================================

    window.productivityExcavatorAreaV73Input =
        function (
            input
        ) {

            sync_manual_draft(
                input
            );
        };


    // ========================================================
    // CHANGE / BLUR EVENT
    //
    // Keep latest value synchronized, BUT DO NOT SAVE.
    //
    // The existing HTML still calls this function from
    // onchange/onblur. We deliberately make it draft-only.
    // ========================================================

    window.productivityExcavatorAreaV73Save =
        function (
            input
        ) {

            sync_manual_draft(
                input
            );

            // IMPORTANT:
            // NO frappe.call HERE.
        };


    // ========================================================
    // STYLE UNSAVED INPUTS
    // ========================================================

    if (
        !document.getElementById(
            "productivity-manual-save-v80-style"
        )
    ) {

        const style =
            document.createElement(
                "style"
            );


        style.id =
            "productivity-manual-save-v80-style";


        style.innerHTML = `

            .productivity-excavator-area-v73-input.productivity-override-dirty {

                background:
                    #fff1b8 !important;

                border:
                    2px solid #d99a00 !important;
            }

        `;


        document.head.appendChild(
            style
        );
    }


    console.log(
        "Productivity V80: manual Save Override mode enabled."
    );

})();

// END KOSI_PRODUCTIVITY_MANUAL_SAVE_ONLY_V80


// ============================================================
// KOSI_PRODUCTIVITY_ZERO_DECIMALS_DISPLAY_V83
//
// FINAL DISPLAY RULE:
//
// Working Hours       = 0 decimals
// Output BCM          = 0 decimals
// Productivity BCM/Hr = 0 decimals
//
// BCM/HD stays unchanged.
//
// Totals remain bold:
//   Category total
//   Machine total
//   Coal/Hards/Softs subtotal
//   Total Fleet
//
// Backend calculations remain exact.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );

    if (!report) {
        return;
    }


    const previous_formatter =
        report.formatter;


    function number_value(
        value
    ) {

        if (
            value === null
            || value === undefined
            || value === ""
        ) {

            return null;
        }


        const number = Number(
            String(
                value
            )
                .replace(
                    /,/g,
                    ""
                )
                .trim()
        );


        return Number.isFinite(
            number
        )
            ? number
            : null;
    }


    function zero_decimal(
        value
    ) {

        const number =
            number_value(
                value
            );


        if (number === null) {
            return "";
        }


        return Math.round(
            number
        ).toLocaleString(
            "en-US",
            {
                maximumFractionDigits: 0,
                minimumFractionDigits: 0
            }
        );
    }


    function right_value(
        value,
        bold
    ) {

        return (
            '<div style="'
            + 'text-align:right;'
            + 'padding-right:4px;'
            + (
                bold
                    ? 'font-weight:700;'
                    : ''
            )
            + '">'
            + value
            + '</div>'
        );
    }


    function is_total_row(
        data
    ) {

        if (!data) {
            return false;
        }


        return Boolean(

            Number(
                data.is_category_total
                || 0
            )

            ||

            Number(
                data.is_machine_total
                || 0
            )

            ||

            Number(
                data.productivity_is_machine_total
                || 0
            )

            ||

            Number(
                data.productivity_excavator_material_total_v77
                || 0
            )

            ||

            Number(
                data.is_total_fleet
                || 0
            )

            ||

            Number(
                data.productivity_summary_total_fleet_v37
                || 0
            )
        );
    }


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {

            let formatted_value;


            if (
                typeof previous_formatter
                === "function"
            ) {

                formatted_value =
                    previous_formatter.call(
                        this,
                        value,
                        row,
                        column,
                        data,
                        default_formatter
                    );

            } else {

                formatted_value =
                    default_formatter(
                        value,
                        row,
                        column,
                        data
                    );
            }


            if (
                !data
                || !column
            ) {

                return formatted_value;
            }


            const fieldname =
                String(
                    column.fieldname
                    || ""
                ).trim();


            // -----------------------------------------------
            // DO NOT CHANGE BCM/HD.
            // -----------------------------------------------

            if (
                fieldname
                === "productivity_bcm_hd"
            ) {

                return formatted_value;
            }


            if (
                ![
                    "working_hours",
                    "output",
                    "adjusted_bcm",
                    "productivity"
                ].includes(
                    fieldname
                )
            ) {

                return formatted_value;
            }


            let field_value;


            if (
                fieldname
                === "output"
                || fieldname
                === "adjusted_bcm"
            ) {

                field_value = (
                    data.adjusted_bcm !== undefined
                    && data.adjusted_bcm !== null
                    && data.adjusted_bcm !== ""
                )
                    ? data.adjusted_bcm
                    : data.output;

            } else {

                field_value =
                    data[
                        fieldname
                    ];
            }


            const display =
                zero_decimal(
                    field_value
                );


            if (
                display === ""
            ) {

                return "";
            }


            return right_value(
                display,
                is_total_row(
                    data
                )
            );
        };

})();

// END KOSI_PRODUCTIVITY_ZERO_DECIMALS_DISPLAY_V83


// ============================================================
// KOSI_PRODUCTIVITY_BOLD_ONLY_V84
//
// ONLY THESE ROWS ARE BOLD:
//
// 1. CATEGORY
//
//      Excavator
//      ADT
//      Dozer
//
// 2. MACHINE TOTAL
//
//      EX01
//      IS0312
//      IS0330
//      etc.
//
// 3. EXCAVATOR MATERIAL SUBTOTAL
//
//      Coal
//      Hards
//      Softs
//
// EVERYTHING UNDER MATERIAL TOTAL:
//      Ramp 2 - 3 2#Coal
//      Ramp 1 - 2 2#Coal
//      Ramp 1 Overburden
//      Ramp 3 Overburden
//      Topsoil Dump
//
// stays NORMAL weight.
//
// V83 zero-decimal display remains unchanged.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );

    if (!report) {
        return;
    }


    const previous_formatter =
        report.formatter;


    function text_value(
        value
    ) {

        if (
            value === null
            || value === undefined
        ) {

            return "";
        }

        return String(
            value
        ).trim();
    }


    function is_excavator_detail(
        data
    ) {

        return (
            Number(
                data.productivity_excavator_detail_v71
                || 0
            ) === 1
        );
    }


    function is_category_total(
        data
    ) {

        if (!data) {
            return false;
        }


        if (
            Number(
                data.is_category_total
                || 0
            ) === 1
        ) {

            return true;
        }


        return (
            text_value(
                data.productivity_summary_tree_level_v38
            ) === "category"
        );
    }


    function is_machine_total(
        data
    ) {

        if (!data) {
            return false;
        }


        if (
            is_excavator_detail(
                data
            )
        ) {

            return false;
        }


        const label =
            text_value(
                data.label
            );


        const material =
            text_value(
                data.material
            );


        // Machine rows have machine name in Label and no
        // material in the Material column.
        if (
            !label
            || material
        ) {

            return false;
        }


        return Boolean(

            Number(
                data.is_machine_total
                || 0
            ) === 1

            ||

            (
                text_value(
                    data.productivity_summary_tree_level_v38
                ) === "machine"

                &&

                Number(
                    data.productivity_summary_tree_machine_v38
                    || 0
                ) === 1
            )
        );
    }


    function is_material_subtotal(
        data
    ) {

        if (!data) {
            return false;
        }


        if (
            is_excavator_detail(
                data
            )
        ) {

            return false;
        }


        // V77 explicitly marks the Excavator material totals.
        if (
            Number(
                data.productivity_excavator_material_total_v77
                || 0
            ) === 1
        ) {

            return true;
        }


        return false;
    }


    function should_be_bold(
        data
    ) {

        return Boolean(
            is_category_total(
                data
            )
            ||
            is_machine_total(
                data
            )
            ||
            is_material_subtotal(
                data
            )
        );
    }


    function apply_weight(
        html,
        bold
    ) {

        const class_name = (
            bold
                ? "productivity-v84-bold"
                : "productivity-v84-normal"
        );


        return (
            '<span class="'
            + class_name
            + '" style="display:contents;">'
            + (
                html === null
                || html === undefined
                    ? ""
                    : html
            )
            + '</span>'
        );
    }


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {

            let formatted_value;


            if (
                typeof previous_formatter
                === "function"
            ) {

                formatted_value =
                    previous_formatter.call(
                        this,
                        value,
                        row,
                        column,
                        data,
                        default_formatter
                    );

            } else {

                formatted_value =
                    default_formatter(
                        value,
                        row,
                        column,
                        data
                    );
            }


            if (
                !data
                || !column
            ) {

                return formatted_value;
            }


            const fieldname =
                text_value(
                    column.fieldname
                );


            // Only control the report display columns.
            //
            // Area input boxes remain completely untouched.
            if (
                ![
                    "label",
                    "working_hours",
                    "output",
                    "adjusted_bcm",
                    "productivity",
                    "productivity_bcm_hd",
                    "material"
                ].includes(
                    fieldname
                )
            ) {

                return formatted_value;
            }


            return apply_weight(
                formatted_value,
                should_be_bold(
                    data
                )
            );
        };


    // ========================================================
    // FINAL FONT-WEIGHT OVERRIDE
    //
    // !important is intentional because older formatter
    // versions may already have added bold inline styles.
    // ========================================================

    if (
        !document.getElementById(
            "productivity-v84-bold-only-style"
        )
    ) {

        const style =
            document.createElement(
                "style"
            );


        style.id =
            "productivity-v84-bold-only-style";


        style.innerHTML = `

            .productivity-v84-bold,
            .productivity-v84-bold * {

                font-weight:
                    700 !important;
            }


            .productivity-v84-normal,
            .productivity-v84-normal * {

                font-weight:
                    400 !important;
            }

        `;


        document.head.appendChild(
            style
        );
    }

})();

// END KOSI_PRODUCTIVITY_BOLD_ONLY_V84


// ============================================================
// KOSI_PRODUCTIVITY_ADT_DOZER_MATCH_EXCAVATOR_DISPLAY_V87
//
// ADT + DOZER now use the same final interaction as Excavator.
//
// MATERIAL SUBTOTAL:
//     bold
//     no yellow area input
//
// DETAIL:
//     normal weight
//     editable yellow From Area
//     editable yellow To Area
//     editable yellow Hauling Distance
//
// IMPORTANT:
//     typing does NOT save.
//
// User must click:
//     SAVE OVERRIDE
//
// Existing zero-decimal display remains.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );


    if (!report) {
        return;
    }


    const previous_formatter =
        report.formatter;


    function clean(
        value
    ) {

        if (
            value === null
            || value === undefined
        ) {

            return "";
        }


        return String(
            value
        );
    }


    function escape_html(
        value
    ) {

        return frappe.utils.escape_html(
            clean(
                value
            )
        );
    }


    function find_row(
        row_key
    ) {

        const data = (
            frappe.query_report
            && Array.isArray(
                frappe.query_report.data
            )
                ? frappe.query_report.data
                : []
        );


        for (
            const item
            of data
        ) {

            const row = (
                item
                && (
                    item.__data
                    || item
                )
            );


            if (!row) {
                continue;
            }


            const key = clean(
                row.productivity_edit_key
                || row.row_key
                || ""
            ).trim();


            if (
                key
                === row_key
            ) {

                return row;
            }
        }


        return null;
    }


    function sync_draft(
        input
    ) {

        if (!input) {
            return;
        }


        const row_key = clean(
            input.dataset.rowKey
            || ""
        ).trim();


        const field = clean(
            input.dataset.field
            || ""
        ).trim();


        if (
            !row_key
            || ![
                "from_area",
                "to_area",
                "hauling_distance_m"
            ].includes(
                field
            )
        ) {

            return;
        }


        const value = clean(
            input.value
        );


        const row = (
            find_row(
                row_key
            )
        );


        if (!row) {

            console.warn(
                "Productivity V87 row not found:",
                row_key
            );

            return;
        }


        row[
            field
        ] = value;


        window.productivity_editable_rows = (
            window.productivity_editable_rows
            || {}
        );


        window.productivity_editable_rows[
            row_key
        ] = row;


        window.productivity_dirty_overrides = (
            window.productivity_dirty_overrides
            || {}
        );


        window.productivity_dirty_overrides[
            row_key
        ] = true;


        input.classList.add(
            "productivity-v87-dirty"
        );


        if (
            typeof productivity_update_override_button
            === "function"
        ) {

            productivity_update_override_button();
        }
    }


    // No auto-save.
    window.productivityAllMachineAreaV87Input =
        function (
            input
        ) {

            sync_draft(
                input
            );
        };


    // onchange / blur also only synchronize the draft.
    window.productivityAllMachineAreaV87Change =
        function (
            input
        ) {

            sync_draft(
                input
            );
        };


    function input_html(
        data,
        fieldname
    ) {

        const row_key = clean(
            data.productivity_edit_key
            || data.row_key
            || ""
        ).trim();


        if (!row_key) {
            return "";
        }


        const value = clean(
            data[
                fieldname
            ]
            || ""
        );


        return (
            '<input'
            + ' type="text"'
            + ' class="productivity-v87-area-input"'
            + ' data-row-key="'
            + escape_html(
                row_key
            )
            + '"'
            + ' data-field="'
            + escape_html(
                fieldname
            )
            + '"'
            + ' value="'
            + escape_html(
                value
            )
            + '"'
            + ' oninput="productivityAllMachineAreaV87Input(this)"'
            + ' onchange="productivityAllMachineAreaV87Change(this)"'
            + ' onblur="productivityAllMachineAreaV87Change(this)"'
            + '>'
        );
    }


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {

            let formatted_value;


            if (
                typeof previous_formatter
                === "function"
            ) {

                formatted_value = (
                    previous_formatter.call(
                        this,
                        value,
                        row,
                        column,
                        data,
                        default_formatter
                    )
                );

            } else {

                formatted_value = (
                    default_formatter(
                        value,
                        row,
                        column,
                        data
                    )
                );
            }


            if (
                !data
                || !column
            ) {

                return formatted_value;
            }


            const fieldname = clean(
                column.fieldname
                || ""
            ).trim();


            const is_detail = (
                Number(
                    data.productivity_all_machine_detail_v87
                    || 0
                )
                === 1
            );


            const is_material_total = (
                Number(
                    data.productivity_all_machine_material_total_v87
                    || 0
                )
                === 1
            );


            // =================================================
            // ADT / DOZER MATERIAL TOTAL
            // =================================================

            if (is_material_total) {

                if (
                    [
                        "from_area",
                        "to_area",
                        "hauling_distance_m",
                        "productivity_bcm_hd"
                    ].includes(
                        fieldname
                    )
                ) {

                    return "";
                }


                if (
                    [
                        "label",
                        "working_hours",
                        "output",
                        "adjusted_bcm",
                        "productivity",
                        "material"
                    ].includes(
                        fieldname
                    )
                ) {

                    return (
                        '<span class="productivity-v87-bold">'
                        + formatted_value
                        + '</span>'
                    );
                }


                return formatted_value;
            }


            // =================================================
            // ADT / DOZER DETAIL
            // =================================================

            if (is_detail) {

                if (
                    [
                        "from_area",
                        "to_area",
                        "hauling_distance_m"
                    ].includes(
                        fieldname
                    )
                ) {

                    return input_html(
                        data,
                        fieldname
                    );
                }


                if (
                    [
                        "label",
                        "working_hours",
                        "output",
                        "adjusted_bcm",
                        "productivity",
                        "productivity_bcm_hd",
                        "material"
                    ].includes(
                        fieldname
                    )
                ) {

                    return (
                        '<span class="productivity-v87-normal">'
                        + formatted_value
                        + '</span>'
                    );
                }
            }


            return formatted_value;
        };


    if (
        !document.getElementById(
            "productivity-v87-style"
        )
    ) {

        const style =
            document.createElement(
                "style"
            );


        style.id =
            "productivity-v87-style";


        style.innerHTML = `

            .productivity-v87-bold,
            .productivity-v87-bold * {
                font-weight: 700 !important;
            }


            .productivity-v87-normal,
            .productivity-v87-normal * {
                font-weight: 400 !important;
            }


            .productivity-v87-area-input {
                width: 100% !important;
                min-width: 90px !important;
                height: 25px !important;
                padding: 2px 6px !important;
                box-sizing: border-box !important;
                border: 1px solid #e5a100 !important;
                background: #fff8d8 !important;
                border-radius: 0 !important;
                font-weight: 400 !important;
            }


            .productivity-v87-area-input.productivity-v87-dirty {
                border: 2px solid #d69000 !important;
                background: #fff0b3 !important;
            }

        `;


        document.head.appendChild(
            style
        );
    }

})();

// END KOSI_PRODUCTIVITY_ADT_DOZER_MATCH_EXCAVATOR_DISPLAY_V87


// ============================================================
// KOSI_PRODUCTIVITY_SURVEY_SOURCE_DISPLAY_V89
//
// FINAL UI RULE:
//
// From Area
// To Area
// Hauling Distance
//
// are READ-ONLY values populated directly from Survey.
//
// No yellow input boxes.
// No manual override saving.
//
// Existing Save Override button is converted to:
//
//     Save Snapshot
//
// so permanent Productivity snapshots remain available.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    const previous_formatter = (
        report.formatter
    );


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {

            if (
                data
                && column
                && Number(
                    data.productivity_manual_disabled_v89
                    || 0
                ) === 1
                && [
                    "from_area",
                    "to_area",
                    "hauling_distance_m"
                ].includes(
                    String(
                        column.fieldname
                        || ""
                    )
                )
            ) {

                // --------------------------------------------
                // IMPORTANT:
                //
                // Bypass all older manual-input formatters.
                // Render Survey value as plain report text.
                // --------------------------------------------

                return default_formatter(
                    value,
                    row,
                    column,
                    data
                );
            }


            if (
                typeof previous_formatter
                === "function"
            ) {

                return previous_formatter.call(
                    this,
                    value,
                    row,
                    column,
                    data,
                    default_formatter
                );
            }


            return default_formatter(
                value,
                row,
                column,
                data
            );
        };


    // ========================================================
    // MANUAL OVERRIDE IS NO LONGER USED.
    // ========================================================

    window.productivity_dirty_overrides = {};


    // ========================================================
    // KEEP SNAPSHOT FUNCTIONALITY
    //
    // Existing button becomes "Save Snapshot".
    // ========================================================


    function v89_snapshot_button() {

        if (
            typeof productivity_override_button
            !== "function"
        ) {

            return null;
        }


        const button = (
            productivity_override_button()
        );


        if (
            !button
            || !button.length
        ) {

            return null;
        }


        button
            .show()
            .prop(
                "disabled",
                false
            )
            .text(
                __(
                    "Save Snapshot"
                )
            );


        return button;
    }


    if (
        typeof productivity_update_override_button
        === "function"
    ) {

        productivity_update_override_button =
            function () {

                v89_snapshot_button();
            };
    }


    if (
        typeof productivity_save_all_overrides
        === "function"
    ) {

        productivity_save_all_overrides =
            async function () {

                const button = (
                    v89_snapshot_button()
                );


                if (
                    !button
                    || !button.length
                ) {

                    return;
                }


                button
                    .prop(
                        "disabled",
                        true
                    )
                    .text(
                        __(
                            "Saving Snapshot..."
                        )
                    );


                try {

                    if (
                        typeof productivity_create_permanent_snapshot
                        !== "function"
                    ) {

                        throw new Error(
                            "Productivity snapshot function not found."
                        );
                    }


                    const snapshot = await (
                        productivity_create_permanent_snapshot()
                    );


                    frappe.msgprint({

                        title:
                            __(
                                "Save Snapshot"
                            ),

                        indicator:
                            "green",

                        message:
                            __(
                                "Productivity snapshot saved successfully."
                            )
                    });


                    return snapshot;


                } catch (
                    error
                ) {

                    console.error(
                        "Productivity snapshot save failed:",
                        error
                    );


                    frappe.msgprint({

                        title:
                            __(
                                "Save Snapshot"
                            ),

                        indicator:
                            "red",

                        message:
                            __(
                                "The Productivity snapshot could not be saved."
                            )
                    });


                    throw error;


                } finally {

                    button
                        .prop(
                            "disabled",
                            false
                        )
                        .text(
                            __(
                                "Save Snapshot"
                            )
                        );
                }
            };
    }


    // ========================================================
    // Older scripts may update the button after report refresh.
    // Keep the final label correct.
    // ========================================================

    setTimeout(
        v89_snapshot_button,
        200
    );


    setTimeout(
        v89_snapshot_button,
        1000
    );


    setTimeout(
        v89_snapshot_button,
        2500
    );


    // ========================================================
    // REMOVE YELLOW MANUAL INPUT STYLING IF ANY OLD VIRTUAL
    // GRID CELL EXISTS MOMENTARILY DURING REFRESH.
    // ========================================================

    if (
        !document.getElementById(
            "productivity-survey-source-v89-style"
        )
    ) {

        const style =
            document.createElement(
                "style"
            );


        style.id =
            "productivity-survey-source-v89-style";


        style.innerHTML = `

            .productivity-v89-survey-value {
                font-weight: 400 !important;
            }

        `;


        document.head.appendChild(
            style
        );
    }

})();

// END KOSI_PRODUCTIVITY_SURVEY_SOURCE_DISPLAY_V89


// ============================================================
// KOSI_PRODUCTIVITY_SURVEY_READONLY_ALL_V90
//
// FINAL DISPLAY RULE:
//
// Excavator
// ADT
// Dozer
//
// From Area
// To Area
// Hauling Distance
//
// are ALWAYS plain read-only values.
//
// The values still come from Survey through Python V89.
//
// This final formatter intentionally bypasses ALL older
// Productivity manual-input formatters for these 3 columns.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );


    if (!report) {
        return;
    }


    const previous_formatter =
        report.formatter;


    const survey_fields = [
        "from_area",
        "to_area",
        "hauling_distance_m"
    ];


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {

            const fieldname = String(
                column
                && column.fieldname
                || ""
            ).trim();


            // =================================================
            // IMPORTANT:
            //
            // Route fields NEVER pass through the older
            // Excavator / ADT / Dozer editable formatters.
            //
            // Use the normal Frappe formatter directly.
            // =================================================

            if (
                survey_fields.includes(
                    fieldname
                )
            ) {

                return default_formatter(
                    value,
                    row,
                    column,
                    data
                );
            }


            if (
                typeof previous_formatter
                === "function"
            ) {

                return previous_formatter.call(
                    this,
                    value,
                    row,
                    column,
                    data,
                    default_formatter
                );
            }


            return default_formatter(
                value,
                row,
                column,
                data
            );
        };


    // ========================================================
    // FALLBACK CSS
    //
    // In case the report virtual grid briefly reuses older
    // rendered input HTML, make those legacy inputs look and
    // behave as read-only text.
    // ========================================================

    if (
        !document.getElementById(
            "productivity-survey-readonly-v90-style"
        )
    ) {

        const style =
            document.createElement(
                "style"
            );


        style.id =
            "productivity-survey-readonly-v90-style";


        style.innerHTML = `

            input.productivity-inline-edit[data-field="from_area"],
            input.productivity-inline-edit[data-field="to_area"],
            input.productivity-inline-edit[data-field="hauling_distance_m"],

            input.productivity-v87-area-input[data-field="from_area"],
            input.productivity-v87-area-input[data-field="to_area"],
            input.productivity-v87-area-input[data-field="hauling_distance_m"] {

                pointer-events: none !important;
                border: none !important;
                outline: none !important;
                box-shadow: none !important;
                background: transparent !important;
                padding-left: 0 !important;
                padding-right: 0 !important;
                font-weight: 400 !important;
            }

        `;


        document.head.appendChild(
            style
        );
    }

})();

// END KOSI_PRODUCTIVITY_SURVEY_READONLY_ALL_V90


// ============================================================
// KOSI_PRODUCTIVITY_COAL_TONNES_DISPLAY_V106
//
// FINAL DISPLAY RULE FOR:
//
//     output_coal_tonnes
//
// BACKEND IS ALREADY CORRECT:
//
//     Non-Coal:
//         output_coal_tonnes = ""
//
//     Coal:
//         output_coal_tonnes = whole-number tonnes
//
// Browser display:
//
//     Non-Coal -> BLANK
//     Coal     -> 192 180
//
// No decimals.
// Does not change BCM, Hours, BCM/Hr, BCM/HD or Tallies.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );

    if (!report) {
        return;
    }


    const previous_formatter =
        report.formatter;


    function raw_number(value) {

        if (
            value === null
            || value === undefined
            || value === ""
        ) {
            return null;
        }

        const parsed = Number(
            String(value)
                .replace(/,/g, "")
                .replace(/\s/g, "")
                .trim()
        );

        return Number.isFinite(parsed)
            ? parsed
            : null;
    }


    function format_whole_number(value) {

        const number =
            raw_number(value);

        if (number === null) {
            return "";
        }

        const rounded =
            Math.round(number);


        // Prefer Frappe's formatter when available.
        if (
            typeof frappe !== "undefined"
            && frappe.utils
            && typeof frappe.utils.format_number === "function"
        ) {

            try {

                return frappe.utils.format_number(
                    rounded,
                    null,
                    0
                );

            } catch (error) {
                // Fall through to browser locale formatting.
            }
        }


        // South African locale normally uses grouped thousands
        // and no decimal places.
        try {

            return rounded.toLocaleString(
                "en-ZA",
                {
                    minimumFractionDigits: 0,
                    maximumFractionDigits: 0,
                }
            );

        } catch (error) {

            return String(
                rounded
            );
        }
    }


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        const fieldname = (
            column
            && column.fieldname
        )
            ? String(
                column.fieldname
            )
            : "";


        // ====================================================
        // COAL TONNES - FINAL OVERRIDE
        // ====================================================

        if (
            fieldname
            === "output_coal_tonnes"
        ) {

            const raw = (
                data
                ? data.output_coal_tonnes
                : null
            );


            // Non-Coal rows are blank in the backend.
            // Do NOT allow Float formatting to turn blank into
            // 0.000.
            if (
                raw === null
                || raw === undefined
                || raw === ""
            ) {
                return "";
            }


            // Coal rows:
            // whole-number tonnes, no .000.
            return format_whole_number(
                raw
            );
        }


        // ====================================================
        // EVERYTHING ELSE REMAINS EXACTLY AS BEFORE
        // ====================================================

        if (
            typeof previous_formatter
            === "function"
        ) {

            return previous_formatter.call(
                this,
                value,
                row,
                column,
                data,
                default_formatter
            );
        }


        return default_formatter(
            value,
            row,
            column,
            data
        );
    };

})();

// END KOSI_PRODUCTIVITY_COAL_TONNES_DISPLAY_V106


// ============================================================
// KOSI_PRODUCTIVITY_BOLD_COAL_TONNES_TOTAL_V108
//
// ONLY:
//     Bold the Coal TOTAL in:
//         Output Actual (Coal Tonnes)
//
// KEEP NORMAL:
//     2COAL detail rows
//
// No calculation changes.
// No column changes.
// No layout changes.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports["Productivity"]
    );

    if (!report) {
        return;
    }


    const previous_formatter =
        report.formatter;


    report.formatter = function (
        value,
        row,
        column,
        data,
        default_formatter
    ) {

        let formatted_value;


        if (
            typeof previous_formatter
            === "function"
        ) {

            formatted_value =
                previous_formatter.call(
                    this,
                    value,
                    row,
                    column,
                    data,
                    default_formatter
                );

        } else {

            formatted_value =
                default_formatter(
                    value,
                    row,
                    column,
                    data
                );
        }


        if (
            !data
            || !column
            || column.fieldname
                !== "output_coal_tonnes"
        ) {

            return formatted_value;
        }


        const label = String(
            data.label
            || ""
        ).trim();


        const material = String(
            data.material
            || ""
        ).trim();


        // ====================================================
        // COAL SUBTOTAL/TOTAL ROW
        //
        // Hours and Material:
        //     label = Coal
        //
        // Summary Per Machine:
        //     material = Coal
        // ====================================================

        const is_coal_total = (
            label === "Coal"
            || material === "Coal"
        );


        if (!is_coal_total) {
            return formatted_value;
        }


        // Keep blank values blank.
        if (
            formatted_value === null
            || formatted_value === undefined
            || String(
                formatted_value
            ).trim() === ""
        ) {

            return "";
        }


        return (
            '<span style="font-weight:700;">'
            + formatted_value
            + '</span>'
        );
    };

})();

// END KOSI_PRODUCTIVITY_BOLD_COAL_TONNES_TOTAL_V108


// ============================================================
// KOSI_PRODUCTIVITY_RECONCILED_HOURS_DISPLAY_V110
//
// DISPLAY ONLY.
//
// Backend:
//     working_hours stays precise.
//
// Display:
//     productivity_display_working_hours_v110
//
// This formatter feeds the reconciled integer into the
// EXISTING formatter chain so all existing:
//
//     bold totals
//     indentation
//     formatting
//
// remain unchanged.
// ============================================================

(function () {

    const report = (
        frappe.query_reports
        && frappe.query_reports[
            "Productivity"
        ]
    );


    if (!report) {

        return;
    }


    const previous_formatter =
        report.formatter;


    report.formatter =
        function (
            value,
            row,
            column,
            data,
            default_formatter
        ) {


            if (
                data
                && column
                && String(
                    column.fieldname
                    || ""
                ).trim()
                === "working_hours"

                && data[
                    "productivity_display_working_hours_v110"
                ]
                !== undefined

                && data[
                    "productivity_display_working_hours_v110"
                ]
                !== null

                && data[
                    "productivity_display_working_hours_v110"
                ]
                !== ""
            ) {

                const display_value =
                    Number(
                        data[
                            "productivity_display_working_hours_v110"
                        ]
                    );


                const display_data =
                    Object.assign(
                        {},
                        data,
                        {
                            working_hours:
                                display_value,
                        }
                    );


                if (
                    typeof previous_formatter
                    === "function"
                ) {

                    return (
                        previous_formatter.call(
                            this,
                            display_value,
                            row,
                            column,
                            display_data,
                            default_formatter
                        )
                    );
                }


                return (
                    default_formatter(
                        display_value,
                        row,
                        column,
                        display_data
                    )
                );
            }


            if (
                typeof previous_formatter
                === "function"
            ) {

                return (
                    previous_formatter.call(
                        this,
                        value,
                        row,
                        column,
                        data,
                        default_formatter
                    )
                );
            }


            return (
                default_formatter(
                    value,
                    row,
                    column,
                    data
                )
            );
        };

})();

// END KOSI_PRODUCTIVITY_RECONCILED_HOURS_DISPLAY_V110
