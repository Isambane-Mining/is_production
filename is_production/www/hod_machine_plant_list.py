"""Direct, permission-checked Machine Plant List for HOD PowerPoint."""

import re

import frappe

from is_production.production.report.hod_presentation import (
    hod_presentation as hod_report,
)


def _natural_key(row):
    value = str(row.get("name") or "").strip()
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in re.split(r"(\d+)", value)
    ]


def get_context(context):
    site = str(frappe.form_dict.get("site") or "").strip()

    if not site or frappe.session.user == "Guest":
        frappe.throw(
            "Please sign in and select an authorised site.",
            frappe.PermissionError,
        )

    hod_report.check_report_access()
    hod_report.check_hod_site_access(site)

    get_rows = frappe.get_attr(
        "engineering.engineering.report."
        "engineering_legals_monthly_summary."
        "engineering_legals_monthly_summary."
        "get_submitted_machine_rows"
    )

    result = get_rows(machine_type="Total Fleet", site=site)
    rows = result.get("rows") or []

    categories = (
        ("Excavator", {"EXCAVATOR", "EXCAVATORS"}),
        ("Dozer", {"DOZER", "DOZERS"}),
        ("ADT", {
            "ADT",
            "ADTS",
            "ARTICULATED DUMP TRUCK",
            "ARTICULATED DUMP TRUCKS",
        }),
    )

    # Reuse Engineering Month End Swing/Spare classification.
    from datetime import date
    from calendar import monthrange
    from frappe.utils import getdate
    from engineering.engineering.report.availability_and_utilisation_month_end_report import (
        availability_and_utilisation_month_end_report as month_end,
    )

    from_value = frappe.form_dict.get("from_date")
    to_value = frappe.form_dict.get("to_date")

    if bool(from_value) != bool(to_value):
        frappe.throw("Both reporting dates are required.")

    if from_value:
        start_date = getdate(from_value)
        end_date = getdate(to_value)
    else:
        current = date.today()
        start_date = current.replace(day=1)
        end_date = current.replace(
            day=monthrange(current.year, current.month)[1]
        )

    if start_date > end_date:
        frappe.throw("Invalid reporting period.")

    filters = frappe._dict({
        "from_date": str(start_date),
        "to_date": str(end_date),
        "location": site,
    })

    spare_map = month_end.get_spare_swing_asset_map(filters)

    sections = []

    for label, aliases in categories:
        matching = [
            row for row in rows
            if str(row.get("asset_category") or "")
            .strip().upper() in aliases
        ]

        if label == "ADT":
            matching.sort(key=_natural_key)

        production_rows = []
        swing_rows = []

        for row in matching:
            if month_end.is_spare_swing_asset(
                row.get("name"), spare_map
            ):
                swing_rows.append(row)
            else:
                production_rows.append(row)

        sections.append({
            "label": label,
            "rows": matching,
            "production_rows": production_rows,
            "swing_rows": swing_rows,
            "count": len(matching),
        })

    context.site = site
    context.sections = sections
    context.title = "Machine Plant List - Total Fleet"
    context.no_cache = 1
