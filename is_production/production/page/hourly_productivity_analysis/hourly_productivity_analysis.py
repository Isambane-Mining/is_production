"""Operational-day BCM matrix and proportions of hourly performance categories."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from math import isfinite

import frappe
from frappe.utils import getdate, now_datetime

from is_production.production.controllers.production_summary_periods import source_hour
from is_production.production.controllers.hourly_productivity_schedule import hour_segments, shift_windows
from is_production.production.page.hourly_productivity_summary import hourly_productivity_summary as summary

CATEGORIES = ("green", "amber", "red", "grey")


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
    # target_bcm already includes all MPP production excavators.
    # Green = 220 BCM per excavator; Orange = 200 BCM per excavator.
    # Preserve proportional thresholds for partial hours.
    if actual >= target:
        return "green"
    if actual >= target * (200 / 220):
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
    if machine_scope not in ("all", "production", "spare"):
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

    if machine_scope == "all":
        return production | spare
    return production if machine_scope == "production" else spare


def build_analysis(from_date, to_date, site, plans, assets, breakdowns, documents, *, now=None, fatigue=(), machine_scope="all", include_machine_targets=False):
    first, last = validate_range(from_date, to_date)
    error = planning_error(first, last, site, plans)
    if error:
        return error
    # Never turn a missing saved production count into a zero BCM threshold.
    # The machine tab has its own MPP ADT roster and does not use BCM colours.
    if not include_machine_targets:
        for offset in range((last - first).days + 1):
            date = first + timedelta(days=offset)
            plan = summary.select_plan(plans, site, date)
            raw_count = plan.get("num_excavators")
            try:
                count = float(raw_count) if raw_count not in (None, "") else None
            except (TypeError, ValueError):
                count = None
            if count is None or not isfinite(count) or count < 0 or not count.is_integer():
                return {"rows": [], "configuration_error": {
                    "title": "Production Excavator Count Unavailable",
                    "message": f"MPP production excavator count is missing or invalid for {date}. "
                               "Check the saved Number of Production Excavators before classifying BCM hours."}}
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
        fleet_total = len(adt_scope)
        production_scope = mpp_adt_scope(plan, "production") if include_machine_targets else None
        cells = []
        load_times = {}
        for shift in ("Day", "Night"):
            shift_data = summary.build_summary(date, shift, site, plan, assets, breakdowns, by_date[date],
                                               now=now, estimate_availability=False, adt_scope=adt_scope)
            if include_machine_targets:
                production_data = shift_data if adt_scope == production_scope else summary.build_summary(
                    date, shift, site, plan, assets, breakdowns, by_date[date],
                    now=now, estimate_availability=False, adt_scope=production_scope)
                production_hours = production_data["hours"]
            warnings.extend(shift_data["warnings"])
            load_times[shift] = shift_data["excavators"]
            windows = non_working_windows(plan, date, shift, site, fatigue, warnings)
            for hour_index, hour in enumerate(shift_data["hours"]):
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
                if include_machine_targets:
                    production_hour = production_hours[hour_index]
                    cells[-1].update(production_available=production_hour["available"],
                                     production_utilised=production_hour["utilised"])

        rows.append({"date": date.isoformat(), "day": date.strftime("%a"), "month": date.strftime("%B %Y"),
                     "month_key": date.strftime("%Y-%m"), "plan_name": plan["name"],
                     "fleet_total": fleet_total, "cells": cells, "load_times": load_times})
        if include_machine_targets:
            rows[-1]["production_fleet_total"] = (
                len(production_scope) if plan.get("excavator_truck_assignments") is not None else None)

    total = len(rows) * 24
    if missing:
        warnings.append(f"{missing} completed hourly cells have no production record. Displayed zero is unverified; missing cells remain in the pie denominator.")
    if pending:
        warnings.append(f"{pending} hourly cells are unfinished or future. They remain in the pie denominator.")
    return {"site": site, "from_date": first.isoformat(), "to_date": last.isoformat(), "rows": rows,
            "hours": [cell["label"] for cell in rows[0]["cells"]], "warnings": list(dict.fromkeys(warnings)),
            "pie": {"total_hours": total, "categories": {key: {"hours": value, "percent": value / total * 100}
                                                        for key, value in counts.items()}},
            "missing_hours": missing, "pending_hours": pending,
            **({"machine_scope": machine_scope} if include_machine_targets else {})}


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
    return _get_analysis(site, from_date, to_date, machine_scope)


@frappe.whitelist()
def get_machine_analysis(site, from_date, to_date, machine_scope="all"):
    """Add production-only ADT metrics without changing displayed scope counts."""
    return _get_analysis(site, from_date, to_date, machine_scope, include_machine_targets=True)


def _get_analysis(site, from_date, to_date, machine_scope="all", *, include_machine_targets=False):
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
        fatigue=fatigue, machine_scope=machine_scope, include_machine_targets=include_machine_targets
    )


@frappe.whitelist()
def export_excel(site, from_date, to_date, machine_scope="all"):
    """Download the same permission-checked data shown on the dashboard."""
    from frappe.utils import cint


    data = get_analysis(site, from_date, to_date, machine_scope=machine_scope)
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
def export_machine_excel(site, from_date, to_date, machine_scope="all", unavailable_number="", view_mode="utilisation"):
    """Download the same machine data and classifications displayed in the ERP."""
    from io import BytesIO
    from collections import OrderedDict
    import xlsxwriter
    from math import isfinite

    if view_mode not in ("utilisation", "availability"):
        raise ValueError("Invalid machine export view")

    if unavailable_number is None or str(unavailable_number) == "":
        selected_range = None
    else:
        selected_range = int(unavailable_number)
        if selected_range < 0 or selected_range > 10000:
            raise ValueError("Invalid unavailable-machine range")

    data = get_machine_analysis(site, from_date, to_date, machine_scope=machine_scope)
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
    green = book.add_format({"bold": True, "font_color": "#16A05A"})
    average_format = book.add_format({
        "bg_color": "#DDEBF7",
        "border": 1,
        "align": "center",
        "valign": "vcenter",
    })

    backgrounds = {
        "green": "#B9EDC5",
        "orange": "#FFC18A",
        "red": "#F3A6AE",
        "unknown": "#E8EDF2",
    }
    formats = {
        key: book.add_format({
            "bg_color": colour, "border": 1,
            "align": "center", "valign": "vcenter"
        })
        for key, colour in backgrounds.items()
    }

    highlighted_formats = {
        key: book.add_format({
            "bg_color": colour,
            "border": 2,
            "border_color": "#DC2626",
            "align": "center",
            "valign": "vcenter"
        })
        for key, colour in backgrounds.items()
    }

    def valid_nonnegative(value):
        if value is None or str(value).strip() == "":
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if isfinite(number) and number >= 0 else None

    def display_number(value, digits):
        if value is None:
            return "—"
        result = f"{value:,.{digits}f}"
        return result.rstrip("0").rstrip(".") if digits else result

    def utilisation_colour(row, cell, verified):
        if not verified or machine_scope == "spare":
            return "unknown"

        production = valid_nonnegative(row.get("production_fleet_total"))
        available = valid_nonnegative(cell.get("production_available"))
        utilised = valid_nonnegative(cell.get("production_utilised"))

        if production is None or available is None or utilised is None:
            return "unknown"

        if production <= 0 or available <= 0:
            return "unknown"

        expected = min(production, available)
        shortfall = expected - utilised

        if shortfall <= 0:
            return "green"
        if shortfall < 2:
            return "orange"
        return "red"

    months = OrderedDict()
    for row in data["rows"]:
        months.setdefault(row["month_key"], []).append(row)

    overview = book.add_worksheet("Summary")
    overview.hide_gridlines(2)
    overview.set_column("A:A", 24)
    overview.set_column("B:B", 28)
    overview.merge_range("A1:H2", "MACHINE AVAILABILITY" if view_mode == "availability" else "MACHINE UTILISED VS AVAILABLE", navy)

    details = [
        ("Site", site),
        ("From Date", from_date),
        ("To Date", to_date),
        ("Machine Scope", machine_scope.title()),
        ("Measurement", "Available / Total fleet ADTs" if view_mode == "availability" else "Utilised / Available ADTs"),
        ("Unavailable Filter",
         "No highlight" if selected_range is None else
         "0 - Fully available" if selected_range == 0 else
         f"{selected_range - 1}-{selected_range} unavailable"),
    ]

    for index, (label, value) in enumerate(details, 3):
        overview.write(index, 0, label, heading)
        overview.write_string(index, 1, str(value), normal)

    if view_mode == "availability":
        overview.write("A10", "Available", blue)
        overview.write("B10", "PBM-adjusted available ADT count", normal)
        overview.write("A11", "Total fleet", green)
        overview.write("B11", "Total ADTs in selected machine scope", normal)
    else:
        overview.write("A10", "Utilised", purple)
        overview.write("B10", "ADTs with recorded loads", normal)
        overview.write("A11", "Available", blue)
        overview.write("B11", "PBM-adjusted ADT count", normal)
        overview.write("A13", "Green · Target met", formats["green"])
        overview.write("A14", "Orange · Shortfall below 2", formats["orange"])
        overview.write("A15", "Red · Shortfall of 2 or more", formats["red"])
        overview.write("A16", "Grey · Unknown / pending / spare", formats["unknown"])

    for month, rows in months.items():
        sheet = book.add_worksheet(rows[0]["month"][:31])
        sheet.hide_gridlines(2)
        sheet.set_landscape()
        sheet.fit_to_pages(1, 0)
        sheet.freeze_panes(6, 3)
        sheet.set_column("A:A", 14)
        sheet.set_column("B:B", 10)
        sheet.set_column("C:C", 18)
        sheet.set_column(3, 28 if view_mode == 'availability' else 26, 13)

        sheet.merge_range(
            "A1:AC2" if view_mode == "availability" else "A1:AA2",
            f"{site} | {rows[0]['month']} | " + ("AVAILABLE VS TOTAL FLEET" if view_mode == "availability" else "AVAILABLE VS UTILISED"),
            navy
        )
        sheet.write("A3", "Machines", heading)
        sheet.write_string("B3", machine_scope.title())
        sheet.write("D3", "Value", heading)
        sheet.write_string("E3", "Available / Total fleet" if view_mode == "availability" else "Utilised / Available")

        sheet.merge_range("A5:C5", "OPERATIONAL DAY", shift)
        sheet.merge_range(
            "D5:P5" if view_mode == "availability" else "D5:O5",
            "DAY SHIFT 06:00-18:00", shift
        )
        sheet.merge_range(
            "Q5:AC5" if view_mode == "availability" else "P5:AA5",
            "NIGHT SHIFT 18:00-06:00", shift
        )

        for col, label in enumerate(("Date", "Day", "Month")):
            sheet.write(5, col, label, heading)

        for hour_index, label in enumerate(data["hours"]):
            col = hour_index + 3 + (
                1 if view_mode == "availability" and hour_index >= 12 else 0
            )
            sheet.write(5, col, label, heading)

            if view_mode == "availability" and hour_index == 11:
                sheet.write(5, 15, "Day Avg", heading)
            elif view_mode == "availability" and hour_index == 23:
                sheet.write(5, 28, "Night Avg", heading)

        for row_index, row in enumerate(rows, 6):
            for col, key in enumerate(("date", "day", "month")):
                sheet.write_string(row_index, col, str(row[key]), normal)

            for hour_index, cell in enumerate(row["cells"]):
                col = hour_index + 3 + (
                    1 if view_mode == "availability" and hour_index >= 12 else 0
                )
                if view_mode == "availability":
                    raw_available = cell.get("available")
                    raw_total = row.get("fleet_total")
                    verified = cell.get("completed") and cell.get("has_record")

                    try:
                        available_valid = (
                            verified and raw_available is not None
                            and isfinite(float(raw_available))
                            and float(raw_available) >= 0
                        )
                    except (TypeError, ValueError):
                        available_valid = False

                    try:
                        total_valid = (
                            raw_total is not None and isfinite(float(raw_total))
                            and float(raw_total) >= 0
                        )
                    except (TypeError, ValueError):
                        total_valid = False

                    available_text = f"{float(raw_available):g}" if available_valid else "—"
                    total_text = f"{float(raw_total):g}" if total_valid else "—"

                    highlighted = False
                    if selected_range is not None and available_valid and total_valid and float(raw_total) > 0:
                        deficit = float(raw_total) - float(raw_available)
                        epsilon = 1e-9
                        highlighted = (
                            abs(deficit) <= epsilon if selected_range == 0 else
                            deficit > selected_range - 1 + epsilon
                            and deficit <= selected_range + epsilon
                        )

                    cell_format = (
                        highlighted_formats["unknown"] if highlighted
                        else formats["unknown"]
                    )
                    sheet.write_rich_string(
                        row_index, col,
                        blue, available_text, normal, " / ",
                        green, total_text, cell_format
                    )
                    continue

                verified = bool(cell.get("completed") and cell.get("has_record"))

                available_value = (
                    valid_nonnegative(cell.get("available")) if verified else None
                )
                utilised_value = (
                    valid_nonnegative(cell.get("utilised")) if verified else None
                )

                # Match the displayed Utilised / Available values.
                value = (
                    f"{display_number(utilised_value, 0)} / "
                    f"{display_number(available_value, 2)}"
                )

                # Classification uses production-only ADT metrics,
                # exactly as machineColour() does in the dashboard.
                colour = utilisation_colour(row, cell, verified)

                highlighted = False
                total_value = valid_nonnegative(row.get("fleet_total"))

                if (
                    selected_range is not None
                    and verified
                    and available_value is not None
                    and total_value is not None
                    and total_value > 0
                ):
                    deficit = total_value - available_value
                    epsilon = 1e-9

                    if selected_range == 0:
                        highlighted = abs(deficit) <= epsilon
                    else:
                        highlighted = (
                            deficit > selected_range - 1 + epsilon
                            and deficit <= selected_range + epsilon
                        )

                sheet.write_string(
                    row_index, col,
                    value,
                    highlighted_formats[colour] if highlighted
                    else formats[colour]
                )

        if view_mode == "availability":
            for row_index, row in enumerate(rows, 6):
                total = row.get("fleet_total")
                try:
                    total_value = float(total)
                    total_valid = isfinite(total_value) and total_value >= 0
                except (TypeError, ValueError):
                    total_valid = False

                total_text = f"{total_value:g}" if total_valid else "—"

                for shift_cells, col in (
                    (row["cells"][:12], 15),
                    (row["cells"][12:24], 28),
                ):
                    values = []
                    for cell in shift_cells:
                        if not cell.get("completed") or not cell.get("has_record"):
                            continue
                        try:
                            value = float(cell.get("available"))
                        except (TypeError, ValueError):
                            continue
                        if isfinite(value) and value >= 0:
                            values.append(value)

                    average = sum(values) / len(values) if values else None
                    average_text = (
                        f"{average:.2f}" if average is not None else "—"
                    )

                    sheet.write_rich_string(
                        row_index, col,
                        blue, average_text,
                        normal, " / ",
                        green, total_text,
                        average_format
                    )

    book.close()

    safe_site = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in str(site)
    )[:60]

    frappe.response["filename"] = (
        f"{safe_site}_{'Availability' if view_mode == 'availability' else 'Machine_Utilised_vs_Available'}_"
        f"{from_date}_to_{to_date}.xlsx"
    )
    frappe.response["filecontent"] = out.getvalue()
    frappe.response["type"] = "download"


# Consolidated Hourly Productivity Excel generator

from collections import OrderedDict
from io import BytesIO
from math import isfinite
import re

import xlsxwriter

COLOURS = {
    "green": "#00B050",
    "amber": "#FF9F1C",
    "red": "#D63C4B",
    "grey": "#F5F5F5",
}

LABELS = {
    "green": "Green · 220 and above per production excavator",
    "amber": "Orange · 200-219 per production excavator",
    "red": "Red · Below 200 per production excavator",
    "grey": "Fatigue / Non-working",
}



def _shift_bcm_average(cells):
    values = []
    for cell in cells:
        if not cell.get("completed") or not cell.get("has_record"):
            continue
        try:
            value = float(cell["actual_bcm"])
        except (TypeError, ValueError, KeyError):
            continue
        if isfinite(value):
            values.append(value)
    return sum(values) / len(values) if values else None


def make_workbook(data):
    output = BytesIO()
    workbook = xlsxwriter.Workbook(output, {"in_memory": True})

    workbook.set_properties({
        "title": "Hourly Productivity Analysis",
        "subject": "Production performance by operational day",
        "comments": "Generated from permission-checked ERPNext report data",
    })

    title = workbook.add_format({
        "bold": True, "font_size": 18, "font_color": "#FFFFFF",
        "bg_color": "#183153", "valign": "vcenter",
    })
    section = workbook.add_format({
        "bold": True, "font_color": "#FFFFFF",
        "bg_color": "#315574", "border": 1,
        "border_color": "#CAD5E0",
    })
    head = workbook.add_format({
        "bold": True, "bg_color": "#DFE8F1",
        "border": 1, "border_color": "#B8C8D5",
        "align": "center",
    })
    text = workbook.add_format({"font_color": "#253647"})
    integer = workbook.add_format({"num_format": "#,##0"})
    decimal = workbook.add_format({"num_format": "#,##0.0"})
    percentage = workbook.add_format({"num_format": "0.0%"})

    fills = {
        key: workbook.add_format({
            "bg_color": colour,
            "font_color": "#FFFFFF" if key == "red" else "#17212B",
            "border": 1,
            "border_color": "#C7D2DD",
            "align": "center",
            "num_format": "#,##0.0#",
        })
        for key, colour in COLOURS.items()
    }

    average_fill = workbook.add_format({
        "bg_color": "#DDEBF7",
        "font_color": "#1769AA",
        "bold": True,
        "border": 1,
        "border_color": "#B8C8D5",
        "align": "center",
        "num_format": "#,##0.0",
    })

    missing_fills = {
        key: workbook.add_format({
            "bg_color": colour,
            "font_color": "#FFFFFF" if key == "red" else "#17212B",
            "border": 1,
            "border_color": "#C7D2DD",
            "align": "center",
            "bold": True,
        })
        for key, colour in COLOURS.items()
    }

    months = OrderedDict()
    for row in data["rows"]:
        months.setdefault(row["month_key"], []).append(row)

    overview = workbook.add_worksheet("Summary")
    overview.hide_gridlines(2)
    overview.set_tab_color("#183153")
    overview.set_column("A:A", 22)
    overview.set_column("B:B", 18)
    overview.set_column("C:H", 19)
    overview.set_column("I:I", 27)

    overview.merge_range("A1:I2", "HOURLY PRODUCTIVITY | MANAGEMENT SUMMARY", title)
    overview.write("A4", "Site", section)
    overview.write("B4", data["site"], text)
    overview.write("A5", "From date", section)
    overview.write("B5", data["from_date"], text)
    overview.write("A6", "To date", section)
    overview.write("B6", data["to_date"], text)

    headers = [
        "Month", "Days", "Actual BCM", "Target BCM",
        "Achievement", "Green hrs", "Orange hrs",
        "Red hrs", "Fatigue / Non-working hrs",
    ]

    for col, label in enumerate(headers):
        overview.write(8, col, label, head)

    for month_index, (month_key, rows) in enumerate(months.items()):
        month_name = rows[0]["month"]
        sheet = workbook.add_worksheet(month_name[:31])
        sheet.set_tab_color("#00B050")
        sheet.hide_gridlines(2)
        sheet.set_landscape()
        sheet.fit_to_pages(1, 1)
        sheet.set_paper(8)
        sheet.set_margins(0.25, 0.25, 0.4, 0.4)
        sheet.repeat_rows(0, 5)
        sheet.freeze_panes(6, 3)
        sheet.set_column("A:A", 13)
        sheet.set_column("B:B", 9)
        sheet.set_column("C:C", 14)
        sheet.set_column(3, 28, 11)

        sheet.merge_range("A1:AC2", f"{data['site']} | {month_name} | HOURLY PRODUCTIVITY", title)
        sheet.merge_range("A4:C4", "OPERATIONAL DAY 06:00-06:00", section)
        sheet.merge_range("D4:P4", "DAY SHIFT | 06:00-18:00", section)
        sheet.merge_range("Q4:AC4", "NIGHT SHIFT | 18:00-06:00", section)

        for col, label in enumerate(["Date", "Day", "Month"]):
            sheet.write(5, col, label, head)

        hours = [cell["label"] for cell in rows[0]["cells"]]
        for hour_index, label in enumerate(hours):
            col = hour_index + 3 + (1 if hour_index >= 12 else 0)
            sheet.write(5, col, label, head)

            if hour_index == 11:
                sheet.write(5, 15, "Day Avg", head)
            elif hour_index == 23:
                sheet.write(5, 28, "Night Avg", head)

        totals = {
            "actual": 0.0, "target": 0.0,
            **{key: 0.0 for key in COLOURS},
        }

        for day_index, row in enumerate(rows):
            excel_row = day_index + 6
            sheet.write(excel_row, 0, row["date"], text)
            sheet.write(excel_row, 1, row["day"], text)
            sheet.write(excel_row, 2, row["month"], text)

            for hour_index, cell in enumerate(row["cells"]):
                col = hour_index + 3
                category = cell["category"]
                if category not in COLOURS:
                    category = "red"

                actual = float(cell["actual_bcm"] or 0)
                target = float(cell["target_bcm"] or 0)
                totals["actual"] += actual
                totals["target"] += target

                segments = cell.get("segments") or []
                if segments:
                    for segment in segments:
                        key = segment["category"]
                        if key in totals:
                            totals[key] += (
                                float(segment["end_percent"])
                                - float(segment["start_percent"])
                            ) / 100
                else:
                    totals[category] += 1

                if not cell["has_record"]:
                    sheet.write(excel_row, col, f"{actual:g}*", missing_fills[category])
                    sheet.write_comment(
                        excel_row, col,
                        "No hourly production record. Zero is unverified."
                    )
                elif not cell["completed"]:
                    sheet.write(excel_row, col, f"{actual:g}†", missing_fills[category])
                    sheet.write_comment(
                        excel_row, col, "Current or unfinished hour."
                    )
                else:
                    sheet.write_number(excel_row, col, actual, fills[category])

                if len(segments) > 1:
                    sheet.write_comment(
                        excel_row, col,
                        "Mixed classification within this hour: "
                        + ", ".join(
                            f"{s['category']}: "
                            f"{float(s['end_percent'])-float(s['start_percent']):.0f}%"
                            for s in segments
                        )
                    )

        # Dashboard-equivalent averages: completed recorded hours only.
        for day_index, row in enumerate(rows):
            excel_row = day_index + 6

            for shift_cells, col in (
                (row["cells"][:12], 15),
                (row["cells"][12:24], 28),
            ):
                average = _shift_bcm_average(shift_cells)
                if average is None:
                    sheet.write_string(excel_row, col, "—", average_fill)
                else:
                    sheet.write_number(excel_row, col, average, average_fill)

        end_row = 6 + len(rows)
        sheet.autofilter(5, 0, end_row - 1, 28)
        sheet.print_area(0, 0, end_row + 5 + len(COLOURS), 28)

        note_row = end_row + 2
        sheet.merge_range(
            note_row, 0, note_row, 12,
            "* Missing record (unverified zero)  |  † Unfinished hour",
            text,
        )

        sheet.write(note_row + 2, 0, "Performance category", head)
        sheet.write(note_row + 2, 1, "Hours", head)
        for ix, key in enumerate(COLOURS):
            sheet.write(note_row + 3 + ix, 0, LABELS[key], fills[key])
            sheet.write_number(note_row + 3 + ix, 1, totals[key], decimal)
        sheet.set_column("A:A", 48)

        summary_row = 9 + month_index
        values = [
            month_name, len(rows), totals["actual"], totals["target"],
            totals["actual"] / totals["target"] if totals["target"] else 0,
            totals["green"], totals["amber"], totals["red"],
            totals["grey"],
        ]
        for col, value in enumerate(values):
            fmt = percentage if col == 4 else decimal if col >= 5 else integer if col in (1, 2, 3) else text
            if isinstance(value, str):
                overview.write(summary_row, col, value, fmt)
            else:
                overview.write_number(summary_row, col, value, fmt)

    last = 9 + len(months)
    overview.autofilter(8, 0, last - 1, len(headers) - 1)
    overview.freeze_panes(9, 1)

    chart = workbook.add_chart({"type": "column"})
    chart.add_series({
        "name": "Actual BCM",
        "categories": ["Summary", 9, 0, last - 1, 0],
        "values": ["Summary", 9, 2, last - 1, 2],
    })
    chart.add_series({
        "name": "Target BCM",
        "categories": ["Summary", 9, 0, last - 1, 0],
        "values": ["Summary", 9, 3, last - 1, 3],
    })
    chart.set_title({"name": "Monthly Actual vs Target BCM"})
    chart.set_y_axis({"name": "BCM"})
    chart.set_legend({"position": "bottom"})
    chart.set_style(10)
    overview.insert_chart(last + 3, 0, chart, {"x_scale": 1.6, "y_scale": 1.4})

    overview.write(
        last + 27, 0,
        "Performance categories are hour-weighted. "
        "Mixed fatigue hours contribute fractionally. "
        "Thresholds combine excavators and scale for partial hours.",
        text,
    )

    workbook.close()
    return output.getvalue()
