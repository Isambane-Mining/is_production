// Copyright (c) 2026, Isambane Mining (Pty) Ltd
// Hourly Dashboard – Multi-Site Hourly Production


frappe.query_reports["Hourly Dashboard"] = {
    filters: [
        {
            fieldname: "production_date",
            label: __("Production Date"),
            fieldtype: "Date"
        }
    ],

    onload: function (report) {
        if (!report.get_filter_value("production_date")) {
            frappe.call({
                method: "is_production.production.report.hourly_dashboard.hourly_dashboard.get_operational_day"
            }).then((response) => {
                if (!report.get_filter_value("production_date")) {
                    report.set_filter_value("production_date", response.message);
                }
            });
        }

        if (report._auto_refresh_started) return;

        report._auto_refresh_started = true;
        report._refreshing = false;

        const get_ms_until_next_refresh = () => {
            const now = new Date();
            const minutes = now.getMinutes();
            const seconds = now.getSeconds();
            const ms = now.getMilliseconds();

            let nextMinute;

            if (minutes < 10) {
                nextMinute = 10;
            } else if (minutes < 30) {
                nextMinute = 30;
            } else {
                nextMinute = 70; // next hour + 10 minutes
            }

            return (
                (nextMinute - minutes) * 60 * 1000
                - seconds * 1000
                - ms
            );
        };

        const schedule_next = () => {
            if (report._auto_refresh_timer) {
                clearTimeout(report._auto_refresh_timer);
            }

            report._auto_refresh_timer = setTimeout(auto_refresh, get_ms_until_next_refresh());
        };

        const auto_refresh = () => {
            if (report._refreshing) {
                schedule_next();
                return;
            }

            report._refreshing = true;

            Promise.resolve(report.refresh())
                .then(() => {
                    const time = new Date().toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit"
                    });

                    frappe.show_alert(
                        {
                            message: `Hourly Dashboard data updated at ${time}`,
                            indicator: "green"
                        },
                        5
                    );
                })
                .finally(() => {
                    report._refreshing = false;
                    schedule_next();
                });
        };

        schedule_next();
    },

    onunload: function (report) {
        if (report._auto_refresh_timer) {
            clearTimeout(report._auto_refresh_timer);
            report._auto_refresh_timer = null;
        }

        report._auto_refresh_started = false;
        report._refreshing = false;
    }
};
