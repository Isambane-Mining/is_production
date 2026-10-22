function hodGetPreviousSunday(value) {
    const parts = String(value || "")
        .split("-")
        .map(Number);

    if (parts.length !== 3) {
        return value;
    }

    const date = new Date(
        parts[0],
        parts[1] - 1,
        parts[2]
    );

    const weekday = date.getDay();

    const daysBack =
        weekday === 0
            ? 7
            : weekday;

    date.setDate(
        date.getDate() - daysBack
    );

    const year = date.getFullYear();

    const month = String(
        date.getMonth() + 1
    ).padStart(2, "0");

    const day = String(
        date.getDate()
    ).padStart(2, "0");

    return `${year}-${month}-${day}`;
}


const hodPresentationPreviousSunday =
    hodGetPreviousSunday(
        frappe.datetime.get_today()
    );


frappe.query_reports["HOD Presentation"] = {
    filters: [
        {
            fieldname: "end_date",
            label: __("End Date"),
            fieldtype: "Date",
            reqd: 1,
            default: hodPresentationPreviousSunday
        },
        {
            fieldname: "site",
            label: __("Sites"),
            fieldtype: "MultiSelectList",
            reqd: 1,
            get_data: function (txt) {
                return frappe.db.get_link_options("Location", txt);
            }
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
            reqd: 1,
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
            reqd: 1,
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
            reqd: 1,
            default: "Isambane & Excavo Assets"
        },
        {
            fieldname: "au_target_filter",
            label: __("A & U Target"),
            fieldtype: "Select",
            options: [
                "100% A & U",
                "85% A & U"
            ],
            reqd: 1,
            default: "85% A & U"
        }
    ],

    onload(report) {
        frappe.require(
            "/assets/is_production/js/hod_presentation_shared.js"
        ).then(() => {
            const dashboard = window.HODPresentationShared;

            dashboard.injectStyles();
            dashboard.bindTabs(report);
            dashboard.ensureInteractiveReport();

            report.page.add_inner_button(
                __("Download Presentation"),
                () => dashboard.download(report)
            );
        }).catch(error => {
            console.error(
                "Unable to initialise HOD Presentation.",
                error
            );
        });
    },

    after_refresh(report) {
        frappe.require(
            "/assets/is_production/js/hod_presentation_shared.js"
        ).then(() => {
            window.requestAnimationFrame(() => {
                window.HODPresentationShared.render(report);
            });
        }).catch(error => {
            console.error(
                "Unable to render HOD Presentation.",
                error
            );
        });
    },

    formatter(value, row, column, data, default_formatter) {
        value = default_formatter(value, row, column, data);
        if (!data) return value;

        const varianceFields = new Set([
            "forecast_variance_bcm",
            "waste_variance_bcm",
            "coal_variance_tons"
        ]);

        if (varianceFields.has(column.fieldname)) {
            const numericValue = Number(data[column.fieldname] || 0);
            const className = numericValue >= 0 ? "hod-positive" : "hod-negative";
            return `<span class="${className}">${value}</span>`;
        }

        if (column.fieldname === "average_bcm_h") {
            const averageBcmH = Number(data.average_bcm_h || 0);

            const className =
                averageBcmH >= 220
                    ? "hod-kpi-good"
                    : averageBcmH >= 200
                        ? "hod-kpi-warning"
                        : "hod-kpi-bad";

            return `<span class="${className}">${value}</span>`;
        }

        if (column.fieldname === "average_availability") {
            const className = Number(data.average_availability || 0) >= 85
                ? "hod-au-good"
                : "hod-au-bad";
            return `<span class="${className}">${value}</span>`;
        }

        if (column.fieldname === "average_utilisation") {
            const className = Number(data.average_utilisation || 0) >= 80
                ? "hod-au-good"
                : "hod-au-bad";
            return `<span class="${className}">${value}</span>`;
        }

        if (["actual_bcm", "total_excavator_hours"].includes(column.fieldname)) {
            return `<span class="hod-kpi-primary">${value}</span>`;
        }

        if (column.fieldname === "excluded_hour_entries") {
            const className = Number(data.excluded_hour_entries || 0) > 0
                ? "hod-warning"
                : "hod-muted";
            return `<span class="${className}">${value}</span>`;
        }

        return value;
    }
};
