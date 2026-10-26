// Keep saved Query Report links working while the dashboard lives on its Desk Page.
frappe.query_reports["HOD Presentation"] = {
    filters: [],
    onload() {
        const options = Object.fromEntries(new URLSearchParams(window.location.search));
        frappe.route_options = { ...options, ...(frappe.route_options || {}) };
        return frappe.set_route("hod-presentation");
    }
};
