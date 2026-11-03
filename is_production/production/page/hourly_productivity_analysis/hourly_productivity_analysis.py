"""Operational-day BCM matrix and proportions of hourly performance categories."""
from collections import defaultdict
from datetime import datetime, time, timedelta

from collections import OrderedDict
from io import BytesIO
import re

import xlsxwriter

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


def build_analysis(from_date, to_date, site, plans, assets, breakdowns, documents, *, now=None, fatigue=()):
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
        cells = []
        load_times = {}
        for shift in ("Day", "Night"):
            shift_data = summary.build_summary(date, shift, site, plan, assets, breakdowns, by_date[date],
                                               now=now, estimate_availability=False)
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
def get_analysis(site, from_date, to_date):
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
    return build_analysis(first, last, site, plans, assets, breakdowns, documents, fatigue=fatigue)


@frappe.whitelist()
def export_excel(site, from_date, to_date):
    """Download the same permission-checked data shown on the dashboard."""
    from frappe.utils import cint

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


COLOURS = {
    "green": "#00B050",
    "amber": "#FF9F1C",
    "red": "#FFE066",
    "bright_red": "#D63C4B",
    "grey": "#E8EDF2",
}

LABELS = {
    "green": "Target achieved >=100%",
    "amber": "85-99.9% achieved",
    "red": "Below 85%",
    "bright_red": "Zero production, machines available",
    "grey": "Fatigue / Non-working",
}


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
            "font_color": "#FFFFFF" if key == "bright_red" else "#17212B",
            "border": 1,
            "border_color": "#C7D2DD",
            "align": "center",
            "num_format": "#,##0.0#",
        })
        for key, colour in COLOURS.items()
    }

    missing_fills = {
        key: workbook.add_format({
            "bg_color": colour,
            "font_color": "#FFFFFF" if key == "bright_red" else "#17212B",
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
    overview.set_column("C:J", 19)

    overview.merge_range("A1:J2", "HOURLY PRODUCTIVITY | MANAGEMENT SUMMARY", title)
    overview.write("A4", "Site", section)
    overview.write("B4", data["site"], text)
    overview.write("A5", "From date", section)
    overview.write("B5", data["from_date"], text)
    overview.write("A6", "To date", section)
    overview.write("B6", data["to_date"], text)

    headers = [
        "Month", "Days", "Actual BCM", "Target BCM",
        "Achievement", ">=100% hrs", "85-99.9% hrs",
        "Below 85% hrs", "Zero-production hrs", "Non-working hrs",
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
        sheet.set_column(3, 26, 11)

        sheet.merge_range("A1:AA2", f"{data['site']} | {month_name} | HOURLY PRODUCTIVITY", title)
        sheet.merge_range("A4:C4", "OPERATIONAL DAY 06:00-06:00", section)
        sheet.merge_range("D4:O4", "DAY SHIFT | 06:00-18:00", section)
        sheet.merge_range("P4:AA4", "NIGHT SHIFT | 18:00-06:00", section)

        for col, label in enumerate(["Date", "Day", "Month"]):
            sheet.write(5, col, label, head)

        hours = [cell["label"] for cell in rows[0]["cells"]]
        for col, label in enumerate(hours, 3):
            sheet.write(5, col, label, head)

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

        end_row = 6 + len(rows)
        sheet.autofilter(5, 0, end_row - 1, 26)
        sheet.print_area(0, 0, end_row + 10, 26)

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
        sheet.set_column("A:A", 35)

        summary_row = 9 + month_index
        values = [
            month_name, len(rows), totals["actual"], totals["target"],
            totals["actual"] / totals["target"] if totals["target"] else 0,
            totals["green"], totals["amber"], totals["red"],
            totals["bright_red"], totals["grey"],
        ]
        for col, value in enumerate(values):
            fmt = percentage if col == 4 else decimal if col >= 5 else integer if col in (1, 2, 3) else text
            if isinstance(value, str):
                overview.write(summary_row, col, value, fmt)
            else:
                overview.write_number(summary_row, col, value, fmt)

    last = 9 + len(months)
    overview.autofilter(8, 0, last - 1, 9)
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
        "Mixed fatigue hours contribute fractionally.",
        text,
    )

    workbook.close()
    return output.getvalue()
