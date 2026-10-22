import frappe

from is_production.production.report.hod_presentation.hod_presentation import (
    check_report_access,
    execute,
)


@frappe.whitelist()
def get_page_data(filters=None):
    """Reuse HOD report calculations and existing site permissions."""
    check_report_access()

    filters = frappe.parse_json(filters) if isinstance(filters, str) else filters

    if not isinstance(filters, dict):
        frappe.throw("Invalid HOD Presentation filters.")

    columns, rows, *_ = execute(filters)

    return {
        "columns": columns,
        "rows": rows,
    }
