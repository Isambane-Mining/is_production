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
            operational_date = (source_hour(doc["prod_date"], doc["hour_slot"]) - timedelta(hours=6)).date()
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
                              "excavator_utilised", "excavator_unavailable", "completed")} | {"category": "grey" if minutes == 60 else colour,
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
