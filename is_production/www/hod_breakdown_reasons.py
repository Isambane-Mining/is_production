"""Permission-checked HOD breakdown detail website page."""

import frappe
from is_production.production.page.hod_presentation.hod_presentation import (
    get_hod_breakdown_details,
)


def get_context(context):
    if frappe.session.user == "Guest":
        frappe.throw("Please sign in to view breakdown details.", frappe.PermissionError)

    machine = str(frappe.form_dict.get("machine") or "").strip()
    site = str(frappe.form_dict.get("site") or "").strip()
    from_date = str(frappe.form_dict.get("from_date") or "").strip()
    to_date = str(frappe.form_dict.get("to_date") or "").strip()

    if not all((machine, site, from_date, to_date)):
        frappe.throw("Machine, site and reporting dates are required.")

    # The existing HOD endpoint validates report access, site access,
    # dates and readable breakdown records.
    result = get_hod_breakdown_details(
        machine=machine,
        site=site,
        from_date=from_date,
        to_date=to_date,
    )

    context.machine = machine
    context.site = site
    context.from_date = from_date
    context.to_date = to_date
    context.rows = result.get("rows") or []
    context.title = f"Breakdown Reasons - {machine}"
    context.no_cache = 1
    context.no_sidebar = 1
    context.show_navbar = False
    context.show_footer = False
