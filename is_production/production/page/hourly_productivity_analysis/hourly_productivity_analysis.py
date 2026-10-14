"""Operational-day BCM matrix and proportions of hourly performance categories."""
from collections import defaultdict
from datetime import datetime, time, timedelta

import frappe
from frappe.utils import getdate, now_datetime

from is_production.production.controllers.production_summary_periods import source_hour
from is_production.production.page.hourly_productivity_summary import hourly_productivity_summary as summary
from is_production.production.page.hourly_productivity_summary.hourly_productivity_summary import planned_schedule

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
    if hour['schedule'] == 'off':
        return 'grey'
    if (not hour['completed'] or hour['schedule'] != 'working' or not hour['has_record']
            or target is None or target <= 0):
        return 'unclassified'
    if actual == 0 and (hour['excavator_available'] or 0) > 0:
        return 'bright_red'
    if actual >= target:
        return "green"
    if actual >= target * .85:
        return "amber"
    return "red"


def build_analysis(from_date, to_date, site, plans, assets, breakdowns, documents, *, now=None):
    first, last = validate_range(from_date, to_date)
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
    excluded = dict(missing=0, pending=0, schedule_unknown=0, no_target=0)
    for offset in range((last - first).days + 1):
        date = first + timedelta(days=offset)
        plan = summary.select_plan(plans, site, date)
        if plan is None:
            raise ValueError(f"No accessible covering MPP for {site} on {date}. Unknown planning is not zero deployment.")
        cells = []
        load_times = {}
        for shift in ("Day", "Night"):
            shift_data = summary.build_summary(date, shift, site, plan, assets, breakdowns, by_date[date],
                                               now=now, estimate_availability=False)
            warnings.extend(shift_data["warnings"])
            load_times[shift] = shift_data["excavators"]
            schedule = planned_schedule(plan, date, shift)
            for hour in shift_data['hours']:
                target = 0 if schedule == 'off' else hour['target_bcm'] if schedule == 'working' else None
                hour.update(schedule=schedule, target_bcm=target,
                            achievement=hour['actual_bcm'] / target * 100 if target else None)
                colour = category(hour)
                if not hour['completed']:
                    excluded['pending'] += 1
                elif colour in counts:
                    counts[colour] += 1
                elif schedule == 'unknown':
                    excluded['schedule_unknown'] += 1
                elif not target:
                    excluded['no_target'] += 1
                else:
                    excluded['missing'] += 1
                missing += int(hour["record_status"] == "missing")
                pending += int(not hour["completed"])
                cells.append({key: hour[key] for key in ("label", "start", "end", "actual_bcm", "target_bcm",
                              "achievement", "has_record", "record_status", "excavator_available",
                              "excavator_utilised", "excavator_unavailable", "completed", "schedule")} | {"category": colour})
        rows.append({"date": date.isoformat(), "day": date.strftime("%a"), "month": date.strftime("%B %Y"),
                     "month_key": date.strftime("%Y-%m"), "plan_name": plan["name"],
                     "production_excavator_count": shift_data["production_excavator_count"], "cells": cells, "load_times": load_times})
    total = sum(counts.values())
    if missing:
        warnings.append(f"{missing} completed hourly cells have no production record. Displayed zero is unverified; missing working cells are excluded from the pie.")
    if pending:
        warnings.append(f"{pending} hourly cells are unfinished or future. They are excluded from completed performance percentages.")
    return {"site": site, "from_date": first.isoformat(), "to_date": last.isoformat(), "rows": rows,
            "excluded": excluded, "total_cells": len(rows) * 24,
            "hours": [cell["label"] for cell in rows[0]["cells"]], "warnings": list(dict.fromkeys(warnings)),
            "pie": {"total_hours": total, "categories": {key: {"hours": value, "percent": value / total * 100 if total else 0}
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
    plans, documents, assets, breakdowns = summary.load_sources(site, start, end)
    return build_analysis(first, last, site, plans, assets, breakdowns, documents)
