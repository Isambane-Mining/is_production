"""Operational-day BCM matrix and proportions of hourly performance categories."""
from collections import defaultdict
from datetime import datetime, time, timedelta

import frappe
from frappe.utils import getdate, now_datetime

from is_production.production.controllers.production_summary_periods import source_hour
from is_production.production.controllers.hourly_productivity_schedule import hour_segments, shift_windows
from is_production.production.page.hourly_productivity_summary import hourly_productivity_summary as summary

CATEGORIES = ("green", "amber", "red", "bright_red", "grey")


def validate_range(from_date, to_date):
    start, end = getdate(from_date), getdate(to_date)
    if start > end:
        raise ValueError("From Date must be on or before To Date")
    if (end - start).days >= 366:
        raise ValueError("Select at most 366 operational days")
    return start, end


def category(hour):
    target, actual = hour["target_bcm"], hour["actual_bcm"]
    if target is None:
        raise ValueError("Hourly target is unavailable; a covering MPP is required")
    if target == 0:
        return "red"  # No deployed machines does not prove a scheduled non-working period.
    if actual == 0 and hour["completed"] and (hour["excavator_available"] or 0) > 0:
        return "bright_red"
    if actual >= target:
        return "green"
    if actual >= target * .85:
        return "amber"
    return "red"


def planning_error(first, last, site, plans, *, permission_denied=False):
    """Only inspect accessible plans; never probe for hidden record existence."""
    months = {}
    for offset in range((last - first).days + 1):
        date = first + timedelta(days=offset)
        if permission_denied or summary.select_plan(plans, site, date) is None:
            months.setdefault(date.strftime("%Y-%m"), date.strftime("%B %Y"))
    if not months:
        return None
    issues = []
    for key, month in months.items():
        message = (f"You do not have permission to access Monthly Production Planning for {site} for {month}. "
                   "Please ask your administrator for access." if permission_denied else
                   f"No accessible production plan was found for {site} for {month}. Please check that the "
                   "Monthly Production Planning exists and that you have permission to access it.")
        issues.append({"month_key": key, "month": month, "status": "permission_denied" if permission_denied
                       else "missing_or_inaccessible", "message": message})
    return {"rows": [], "planning_error": {
        "title": "Monthly Production Planning Access Denied" if permission_denied
                 else "Monthly Production Planning Not Found", "issues": issues}}


def _schedule_document(doctype, name, fields):
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    before = doc.as_dict()
    doc.apply_fieldlevel_read_permissions()
    result = doc.as_dict()
    if any(before.get(field) != result.get(field) for field in fields):
        raise frappe.PermissionError("Access to scheduling calculation fields is required")
    return result


def load_plans(site, first, last):
    summary._read_document("Location", site)
    if not frappe.has_permission("Monthly Production Planning", "read"):
        return None
    fields = ("location", "prod_month_start_date", "prod_month_end_date", "num_excavators",
              "excavator_truck_assignments", "month_prod_days")
    plans = []
    for row in summary._permitted_rows("Monthly Production Planning", {
            "location": site, "prod_month_start_date": ["<=", last],
            "prod_month_end_date": [">=", first], "docstatus": ["<", 2]},
            order_by="modified desc, name desc"):
        try:
            plans.append(_schedule_document("Monthly Production Planning", row.name, fields))
        except frappe.PermissionError:
            # A list-visible plan may still deny document or scheduling-field access.
            # Keep readable months and report uncovered months without revealing this record.
            continue
    return plans


def load_fatigue(site):
    return summary.load_fatigue(site)


def non_working_windows(plan, date, shift, site, fatigue, warnings):
    return shift_windows(plan, date, shift, site, fatigue, warnings)[0]



def mpp_adt_scope(plan, machine_scope):
    """Resolve saved MPP ADT assignments for one operational date."""
    if machine_scope == "all":
        return None

    if machine_scope not in ("production", "spare"):
        raise ValueError("Invalid machine scope")

    production = set()
    spare = set()

    for row in plan.get("excavator_truck_assignments", []):
        truck = row.get("truck")
        if not truck:
            continue

        if row.get("excavator"):
            production.add(truck)
        else:
            spare.add(truck)

    # Conflicting assignments cannot safely be classified.
    ambiguous = production & spare
    production -= ambiguous
    spare -= ambiguous

    return production if machine_scope == "production" else spare


def build_analysis(from_date, to_date, site, plans, assets, breakdowns, documents, *, now=None, fatigue=(), machine_scope="all"):
    first, last = validate_range(from_date, to_date)
    error = planning_error(first, last, site, plans)
    if error:
        return error
    now = now or now_datetime()
    by_date = defaultdict(list)
    warnings = []
    seen = set()
    for doc in documents:
        if doc.get("location") != site or doc.get("docstatus", 0) >= 2 or doc["name"] in seen:
            continue
        seen.add(doc["name"])
        try:
            dt = source_hour(doc["prod_date"], doc["hour_slot"])
            if doc.get("shift") == "Night" and dt.hour < 6:
                dt += timedelta(days=1)
            operational_date = (dt - timedelta(hours=6)).date()
        except (ValueError, TypeError):
            warnings.append("An invalid source hour was excluded.")
            continue
        if first <= operational_date <= last:
            by_date[operational_date].append(doc)
    rows = []
    counts = dict.fromkeys(CATEGORIES, 0)
    missing = pending = 0
    for offset in range((last - first).days + 1):
        date = first + timedelta(days=offset)
        plan = summary.select_plan(plans, site, date)
        adt_scope = mpp_adt_scope(plan, machine_scope)
        cells = []
        load_times = {}
        for shift in ("Day", "Night"):
            shift_data = summary.build_summary(date, shift, site, plan, assets, breakdowns, by_date[date],
                                               now=now, estimate_availability=False, adt_scope=adt_scope)
            warnings.extend(shift_data["warnings"])
            load_times[shift] = shift_data["excavators"]
            windows = non_working_windows(plan, date, shift, site, fatigue, warnings)
            for hour in shift_data["hours"]:
                colour = category(hour)
                segments, minutes = hour_segments(hour, windows, colour)
                counts["grey"] += minutes / 60
                counts[colour] += 1 - minutes / 60
                missing += int(hour["record_status"] == "missing")
                pending += int(not hour["completed"])
                cells.append({key: hour[key] for key in ("label", "start", "end", "actual_bcm", "target_bcm",
                              "achievement", "has_record", "record_status", "excavator_available",
                              "excavator_utilised", "excavator_unavailable", "completed", "available", "utilised")} | {"category": "grey" if minutes == 60 else colour,
                              "non_working_minutes": minutes, "segments": segments})
        rows.append({"date": date.isoformat(), "day": date.strftime("%a"), "month": date.strftime("%B %Y"),
                     "month_key": date.strftime("%Y-%m"), "plan_name": plan["name"], "cells": cells, "load_times": load_times})
    total = len(rows) * 24
    if missing:
        warnings.append(f"{missing} completed hourly cells have no production record. Displayed zero is unverified; missing cells remain in the pie denominator.")
    if pending:
        warnings.append(f"{pending} hourly cells are unfinished or future. They remain in the pie denominator; bright-red availability claims require a completed hour.")
    return {"site": site, "from_date": first.isoformat(), "to_date": last.isoformat(), "rows": rows,
            "hours": [cell["label"] for cell in rows[0]["cells"]], "warnings": list(dict.fromkeys(warnings)),
            "pie": {"total_hours": total, "categories": {key: {"hours": value, "percent": value / total * 100}
                                                        for key, value in counts.items()}},
            "missing_hours": missing, "pending_hours": pending}


@frappe.whitelist()
def get_defaults():
    if frappe.session.user == "Guest":
        raise frappe.PermissionError("Login required")
    # Default to fully completed operational days for meaningful historical analysis.
    now = now_datetime()
    end = now.date() - timedelta(days=1 if now.hour >= 6 else 2)
    return {"from_date": end.replace(day=1).isoformat(), "to_date": end.isoformat()}


@frappe.whitelist()
def get_analysis(site, from_date, to_date, machine_scope="all"):
    if machine_scope not in ("all", "production", "spare"):
        raise ValueError("Invalid machine scope")
    if frappe.session.user == "Guest":
        raise frappe.PermissionError("Login required")
    if not site or not from_date or not to_date:
        raise ValueError("Select Site, From Date and To Date")
    first, last = validate_range(from_date, to_date)
    start = datetime.combine(first, time(6))
    end = datetime.combine(last + timedelta(days=1), time(6))
    try:
        plans = load_plans(site, first, last)
    except frappe.PermissionError:
        # Location access must succeed before revealing even site/month diagnostics.
        summary._read_document("Location", site)
        return planning_error(first, last, site, [], permission_denied=True)
    error = planning_error(first, last, site, plans or [], permission_denied=plans is None)
    if error:
        return error
    _, documents, assets, breakdowns = summary.load_sources(site, start, end, plans=plans)
    try:
        fatigue = load_fatigue(site)
    except frappe.PermissionError:
        return {"rows": [], "configuration_error": {
            "title": "Fatigue Configuration Access Required",
            "message": "You need read access to Startup and Fatigue Specification and its scheduling fields "
                       "to classify fatigue periods. Please ask your administrator for access."}}
    return build_analysis(
        first, last, site, plans, assets, breakdowns, documents,
        fatigue=fatigue, machine_scope=machine_scope
    )


@frappe.whitelist()
def export_excel(site, from_date, to_date):
    """Download the same permission-checked data shown on the dashboard."""
    from frappe.utils import cint
    from is_production.production.page.hourly_productivity_analysis.hourly_productivity_excel import make_workbook

    data = get_analysis(site, from_date, to_date)
    if not data.get("rows"):
        frappe.throw("No exportable analysis. Check planning and configuration access.")

    safe_site = "".join(
        character if character.isalnum() or character in "-_" else "_"
        for character in site
    )[:60]

    frappe.response["filename"] = (
        f"{safe_site}_Hourly_Productivity_{from_date}_to_{to_date}.xlsx"
    )
    frappe.response["filecontent"] = make_workbook(data)
    frappe.response["type"] = "download"



@frappe.whitelist()
def export_machine_excel(site, from_date, to_date, machine_scope="all"):
    """Download the historically classified hourly ADT matrix."""
    from io import BytesIO
    from collections import OrderedDict
    import xlsxwriter

    data = get_analysis(site, from_date, to_date, machine_scope=machine_scope)
    if not data.get("rows"):
        frappe.throw("No exportable machine data.")

    out = BytesIO()
    book = xlsxwriter.Workbook(out, {"in_memory": True})

    navy = book.add_format({
        "bold": True, "font_size": 17,
        "font_color": "#FFFFFF", "bg_color": "#183153"
    })
    heading = book.add_format({
        "bold": True, "bg_color": "#DFE8F1",
        "align": "center", "border": 1
    })
    shift = book.add_format({
        "bold": True, "font_color": "#FFFFFF",
        "bg_color": "#315574", "align": "center"
    })
    normal = book.add_format({"border": 1})
    blue = book.add_format({"bold": True, "font_color": "#1769AA"})
    purple = book.add_format({"bold": True, "font_color": "#7030A0"})

    backgrounds = {
        "green": "#D9EEDB",
        "yellow": "#FFF1BE",
        "orange": "#FAD9B5",
        "red": "#F5C3C8",
        "unknown": "#FFFFFF",
    }
    formats = {
        key: book.add_format({
            "bg_color": colour, "border": 1,
            "align": "center", "valign": "vcenter"
        })
        for key, colour in backgrounds.items()
    }

    months = OrderedDict()
    for row in data["rows"]:
        months.setdefault(row["month_key"], []).append(row)

    overview = book.add_worksheet("Summary")
    overview.hide_gridlines(2)
    overview.set_column("A:A", 24)
    overview.set_column("B:B", 28)
    overview.merge_range("A1:H2", "MACHINE AVAILABLE VS UTILISED", navy)

    details = [
        ("Site", site),
        ("From Date", from_date),
        ("To Date", to_date),
        ("Machine Scope", machine_scope.title()),
        ("Measurement", "Available / Utilised ADTs"),
    ]

    for index, (label, value) in enumerate(details, 3):
        overview.write(index, 0, label, heading)
        overview.write_string(index, 1, str(value), normal)

    overview.write("A10", "Available", blue)
    overview.write("B10", "PBM-adjusted ADT count", normal)
    overview.write("A11", "Utilised", purple)
    overview.write("B11", "ADTs with recorded loads", normal)
    overview.write("A13", "80%+", formats["green"])
    overview.write("A14", "50-79%", formats["yellow"])
    overview.write("A15", "20-49%", formats["orange"])
    overview.write("A16", "Below 20%", formats["red"])

    for month, rows in months.items():
        sheet = book.add_worksheet(rows[0]["month"][:31])
        sheet.hide_gridlines(2)
        sheet.set_landscape()
        sheet.fit_to_pages(1, 0)
        sheet.freeze_panes(6, 3)
        sheet.set_column("A:A", 14)
        sheet.set_column("B:B", 10)
        sheet.set_column("C:C", 18)
        sheet.set_column(3, 26, 13)

        sheet.merge_range(
            "A1:AA2",
            f"{site} | {rows[0]['month']} | AVAILABLE VS UTILISED",
            navy
        )
        sheet.write("A3", "Machines", heading)
        sheet.write_string("B3", machine_scope.title())
        sheet.write("D3", "Value", heading)
        sheet.write_string("E3", "Available / Utilised")

        sheet.merge_range("A5:C5", "OPERATIONAL DAY", shift)
        sheet.merge_range("D5:O5", "DAY SHIFT 06:00-18:00", shift)
        sheet.merge_range("P5:AA5", "NIGHT SHIFT 18:00-06:00", shift)

        for col, label in enumerate(("Date", "Day", "Month")):
            sheet.write(5, col, label, heading)

        for col, label in enumerate(data["hours"], 3):
            sheet.write(5, col, label, heading)

        for row_index, row in enumerate(rows, 6):
            for col, key in enumerate(("date", "day", "month")):
                sheet.write_string(row_index, col, str(row[key]), normal)

            for hour_index, cell in enumerate(row["cells"]):
                available = cell.get("available")
                utilised = cell.get("utilised")
                verified = cell.get("completed") and cell.get("has_record")

                if not verified or available is None or utilised is None:
                    value = "-"
                    colour = "unknown"
                else:
                    available = float(available)
                    utilised = int(utilised)
                    value = f"{available:g} / {utilised}"
                    ratio = utilised / available if available > 0 else None

                    colour = (
                        "unknown" if ratio is None else
                        "green" if ratio >= 0.80 else
                        "yellow" if ratio >= 0.50 else
                        "orange" if ratio >= 0.20 else "red"
                    )

                sheet.write_string(
                    row_index, hour_index + 3,
                    value, formats[colour]
                )

    book.close()

    safe_site = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in str(site)
    )[:60]

    frappe.response["filename"] = (
        f"{safe_site}_Machine_Available_vs_Utilised_"
        f"{from_date}_to_{to_date}.xlsx"
    )
    frappe.response["filecontent"] = out.getvalue()
    frappe.response["type"] = "download"
