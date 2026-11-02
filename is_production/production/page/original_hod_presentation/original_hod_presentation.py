import frappe

from is_production.production.report.original_hod_presentation.original_hod_presentation import (
    execute,
    check_report_access,
)


@frappe.whitelist()
def get_page_data(filters=None):
    check_report_access()

    if isinstance(filters, str):
        filters = frappe.parse_json(filters)

    if not isinstance(filters, dict):
        frappe.throw("Invalid Original HOD Presentation filters.")

    result = execute(filters)

    return {
        "columns": result[0],
        "rows": result[1],
    }
