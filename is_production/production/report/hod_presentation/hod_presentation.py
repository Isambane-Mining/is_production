from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from io import BytesIO
import base64
import importlib
import re

import frappe
from frappe import _
from frappe.utils import (
    flt,
    formatdate,
    getdate,
    get_datetime,
    get_fullname,
    now_datetime,
    time_diff_in_hours,
)

from is_production.production.page.production_summary_dashboard.production_summary_dashboard import (
    COAL_CONVERSION,
    get_completed_production_days,
    get_mtd_coal_dynamic,
    get_monthly_plan,
)


REPORT_NAME = "HOD Presentation"
PPTX_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
MAX_VALID_WORKING_HOURS = 24.0
EXCAVATOR_CATEGORY_PATTERN = "%excavat%"

DAILY_AVAILABILITY_MODULE = (
    "engineering.engineering.page.daily_availability_and_utilization_dashboard."
    "daily_availability_and_utilization_dashboard"
)

SUMMARY_TYPES = (
    "Daily Summary",
    "Average Per Machine",
    "Weekly Summary",
    "Monthly Summary",
)
MACHINE_SCOPES = (
    "Production Machines",
    "Swing/Spare Machines",
    "Include Swing/Spare",
)
AU_TARGET_FILTERS = (
    "100% A & U",
    "85% A & U",
)

DEFAULT_SUMMARY_TYPE = "Average Per Machine"
DEFAULT_MACHINE_SCOPE = "Include Swing/Spare"
DEFAULT_AU_TARGET_FILTER = "85% A & U"
DEFAULT_ASSET_OWNERSHIP = "Isambane & Excavo Assets"

AVAILABILITY_TARGET = 85.0
UTILISATION_TARGET = 80.0

AU_CATEGORIES = [
    "ADT",
    "Dozer",
    "Excavator",
]

AU_CATEGORY_TITLES = {
    "ADT": "ADT",
    "Dozer": "DOZER",
    "Excavator": "EXCAVATOR",
    "Grader": "GRADER",
    "Service Truck": "SERVICE TRUCK",
    "TLB": "TLB",
    "Water Bowser": "WATER BOWSER",
    "Diesel Bowsers": "DIESEL BOWSERS",
    "Drills": "DRILLS",
    "Loader": "LOADER",
}




def execute(filters=None):
    columns = get_columns()
    filters = frappe._dict(filters or {})

    if (
        not filters.get("start_date")
        or not filters.get("end_date")
        or not filters.get("site")
    ):
        return columns, []

    (
        start_date,
        end_date,
        sites,
        summary_type,
        machine_scope,
        au_target_filter,
    ) = validate_filters(filters)

    payloads = [
        get_report_payload(
            filters,
            include_au_detail=False,
            site_override=site,
        )
        for site in sites
    ]

    data = []

    for payload in payloads:
        production = payload["production"]
        excavators = payload["excavators"]
        availability = payload["availability"]

        data.append(
            {
                "site": payload["site"],
                "period": payload["period_label"],
                "summary_type": availability.get("summary_type"),
                "machine_scope": availability.get("machine_scope"),
                "au_target_filter": availability.get("au_target_filter"),
                "monthly_target_bcm": production.get("monthly_target_bcm", 0),
                "forecast_bcm": production.get("forecast_bcm", 0),
                "forecast_variance_bcm": production.get(
                    "forecast_variance_bcm", 0
                ),
                "waste_variance_bcm": production.get(
                    "waste_variance_bcm", 0
                ),
                "coal_variance_tons": production.get(
                    "coal_variance_tons", 0
                ),
                "actual_bcm": production.get("actual_bcm", 0),
                "actual_coal_tons": production.get(
                    "actual_coal_tons", 0
                ),
                "daily_required_bcm": production.get(
                    "daily_required_bcm", 0
                ),
                "daily_achieved_bcm": production.get(
                    "daily_achieved_bcm", 0
                ),
                "total_excavator_hours": excavators.get(
                    "total_hours",
                    0,
                ),
                "non_production_excavator_hours": excavators.get(
                    "non_production_hours",
                    0,
                ),
                "production_excavator_hours": excavators.get(
                    "production_hours",
                    0,
                ),
                "average_bcm_h": payload.get(
                    "average_bcm_h",
                    0,
                ),
                "excavator_count": excavators.get(
                    "excavator_count", 0
                ),
                "valid_hour_entries": excavators.get(
                    "valid_entry_count", 0
                ),
                "excluded_hour_entries": excavators.get(
                    "excluded_entry_count", 0
                ),
                "average_availability": availability.get(
                    "overall_availability"
                )
                or 0,
                "average_utilisation": availability.get(
                    "overall_utilisation"
                )
                or 0,
                "au_machine_count": availability.get(
                    "machine_count", 0
                ),
                "days_worked": production.get("days_worked", 0),
                "days_left": production.get("days_left", 0),
                "strip_ratio": production.get("strip_ratio", 0),
                "forecast_delivery_percent": production.get(
                    "forecast_delivery_percent", 0
                ),
            }
        )

    message = _(
        "Production, excavator hours, availability and utilisation "
        "are shown separately for each selected site."
    )

    chart = None

    if len(payloads) == 1:
        chart = get_availability_chart(
            payloads[0]["availability"]
        )

    return columns, data, message, chart, []






def get_columns():
    return [
        {
            "label": _("Site"),
            "fieldname": "site",
            "fieldtype": "Link",
            "options": "Location",
            "width": 145,
        },
        {
            "label": _("Period"),
            "fieldname": "period",
            "fieldtype": "Data",
            "width": 180,
        },
        {
            "label": _("A&U Summary"),
            "fieldname": "summary_type",
            "fieldtype": "Data",
            "width": 145,
        },
        {
            "label": _("Machine Scope"),
            "fieldname": "machine_scope",
            "fieldtype": "Data",
            "width": 165,
        },
        {
            "label": _("A&U Target Mode"),
            "fieldname": "au_target_filter",
            "fieldtype": "Data",
            "width": 125,
        },
        {
            "label": _("Monthly Target"),
            "fieldname": "monthly_target_bcm",
            "fieldtype": "Float",
            "precision": 0,
            "width": 125,
        },
        {
            "label": _("Forecast"),
            "fieldname": "forecast_bcm",
            "fieldtype": "Float",
            "precision": 0,
            "width": 115,
        },
        {
            "label": _("Forecast Variance"),
            "fieldname": "forecast_variance_bcm",
            "fieldtype": "Float",
            "precision": 0,
            "width": 145,
        },
        {
            "label": _("Waste Variance"),
            "fieldname": "waste_variance_bcm",
            "fieldtype": "Float",
            "precision": 0,
            "width": 135,
        },
        {
            "label": _("Coal Variance"),
            "fieldname": "coal_variance_tons",
            "fieldtype": "Float",
            "precision": 0,
            "width": 130,
        },
        {
            "label": _("Actual BCM"),
            "fieldname": "actual_bcm",
            "fieldtype": "Float",
            "precision": 0,
            "width": 115,
        },
        {
            "label": _("Actual Coal"),
            "fieldname": "actual_coal_tons",
            "fieldtype": "Float",
            "precision": 0,
            "width": 115,
        },
        {
            "label": _("Daily Required"),
            "fieldname": "daily_required_bcm",
            "fieldtype": "Float",
            "precision": 1,
            "width": 125,
        },
        {
            "label": _("Daily Achieved"),
            "fieldname": "daily_achieved_bcm",
            "fieldtype": "Float",
            "precision": 1,
            "width": 125,
        },
        {
            "label": _("Excavator Production Hours"),
            "fieldname": "production_excavator_hours",
            "fieldtype": "Float",
            "precision": 1,
            "width": 180,
        },
        {
            "label": _("Average BCM/H"),
            "fieldname": "average_bcm_h",
            "fieldtype": "Float",
            "precision": 1,
            "width": 130,
        },
        {
            "label": _("Average Availability"),
            "fieldname": "average_availability",
            "fieldtype": "Percent",
            "precision": 1,
            "width": 145,
        },
        {
            "label": _("Average Utilisation"),
            "fieldname": "average_utilisation",
            "fieldtype": "Percent",
            "precision": 1,
            "width": 145,
        },
        {
            "label": _("A&U Machines"),
            "fieldname": "au_machine_count",
            "fieldtype": "Int",
            "width": 105,
        },
        {
            "label": _("Excavators"),
            "fieldname": "excavator_count",
            "fieldtype": "Int",
            "width": 95,
        },
        {
            "label": _("Valid Hour Entries"),
            "fieldname": "valid_hour_entries",
            "fieldtype": "Int",
            "width": 130,
        },
        {
            "label": _("Excluded Entries"),
            "fieldname": "excluded_hour_entries",
            "fieldtype": "Int",
            "width": 120,
        },
        {
            "label": _("Days Worked"),
            "fieldname": "days_worked",
            "fieldtype": "Int",
            "width": 105,
        },
        {
            "label": _("Days Left"),
            "fieldname": "days_left",
            "fieldtype": "Int",
            "width": 90,
        },
        {
            "label": _("Strip Ratio"),
            "fieldname": "strip_ratio",
            "fieldtype": "Float",
            "precision": 1,
            "width": 95,
        },
        {
            "label": _("Forecast Delivery %"),
            "fieldname": "forecast_delivery_percent",
            "fieldtype": "Percent",
            "precision": 1,
            "width": 135,
        },
    ]


def get_report_payload(
    filters,
    include_au_detail=True,
    site_override=None,
):
    filters = frappe._dict(filters or {})

    (
        start_date,
        end_date,
        sites,
        summary_type,
        machine_scope,
        au_target_filter,
    ) = validate_filters(filters)

    site = site_override or sites[0]

    monthly_plan = get_monthly_plan(site, end_date)
    if not monthly_plan:
        frappe.throw(
            _("No Monthly Production Planning record was found for {0} on {1}.").format(
                frappe.bold(site), formatdate(end_date)
            )
        )

    plan_start = getdate(monthly_plan.prod_month_start_date)
    plan_end = getdate(monthly_plan.prod_month_end_date)

    if end_date < plan_start or end_date > plan_end:
        frappe.throw(
            _("End Date must be between {0} and {1} for {2}.").format(
                frappe.bold(formatdate(plan_start)),
                frappe.bold(formatdate(plan_end)),
                frappe.bold(site),
            )
        )

    production = build_filtered_production_row(
        site=site,
        start_date=start_date,
        end_date=end_date,
        monthly_plan=monthly_plan,
    )

    excavators = get_excavator_hour_summary(
        start_date,
        end_date,
        site,
    )

    total_hours = round(
        flt(
            excavators.get("total_hours")
        ),
        1,
    )

    non_production_hours = (
        get_non_production_excavator_hours(
            start_date,
            end_date,
            site,
        )
    )

    production_hours = round(
        max(
            total_hours - non_production_hours,
            0.0,
        ),
        1,
    )

    excavators["non_production_hours"] = (
        non_production_hours
    )

    excavators["production_hours"] = (
        production_hours
    )

    actual_bcm = flt(
        production.get("actual_bcm")
    )

    average_bcm_h = (
        round(
            actual_bcm / total_hours,
            1,
        )
        if total_hours
        else 0
    )

    availability = get_availability_summary(
        start_date=start_date,
        end_date=end_date,
        site=site,
        summary_type=summary_type,
        machine_scope=machine_scope,
        au_target_filter=au_target_filter,
        include_detail=include_au_detail,
    )

    generated_at = now_datetime()

    return {
        "site": site,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "period_label": _("{0} to {1}").format(
            formatdate(start_date), formatdate(end_date)
        ),
        "plan_name": monthly_plan.name,
        "plan_start_date": str(plan_start),
        "plan_end_date": str(plan_end),
        "production": production,
        "excavators": excavators,
        "average_bcm_h": average_bcm_h,
        "availability": availability,
        "generated_by": get_fullname(frappe.session.user) or frappe.session.user,
        "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_filtered_production_row(
    *,
    site,
    start_date,
    end_date,
    monthly_plan,
):
    plan_start = getdate(
        monthly_plan.prod_month_start_date
    )

    plan_end = getdate(
        monthly_plan.prod_month_end_date
    )

    selected_start = max(
        getdate(start_date),
        plan_start,
    )

    selected_end = min(
        getdate(end_date),
        plan_end,
    )

    monthly_target = flt(
        monthly_plan.monthly_target_bcm
    )

    waste_bcms_planned = flt(
        monthly_plan.waste_bcms_planned
    )

    coal_tons_planned = flt(
        monthly_plan.coal_tons_planned
    )

    num_prod_days = flt(
        monthly_plan.num_prod_days
    )

    forecast = flt(
        monthly_plan.month_forecated_bcm
    )

    selected_worked_days = (
        get_completed_production_days(
            monthly_plan.name,
            selected_start,
            selected_end,
        )
    )

    month_to_date_worked_days = (
        get_completed_production_days(
            monthly_plan.name,
            plan_start,
            selected_end,
        )
    )

    selected_actual_bcm = flt(
        monthly_plan.month_actual_bcm
    )

    month_to_date_actual_bcm = flt(
        monthly_plan.month_actual_bcm
    )

    selected_actual_coal = flt(
        get_mtd_coal_dynamic(
            site,
            selected_end,
            selected_start,
        )
    )

    selected_actual_waste = (
        selected_actual_bcm
        - (
            selected_actual_coal
            / COAL_CONVERSION
        )
    )

    selected_target_waste = (
        (
            waste_bcms_planned
            / num_prod_days
        )
        * selected_worked_days
        if num_prod_days
        else 0
    )

    selected_target_coal = (
        (
            coal_tons_planned
            / num_prod_days
        )
        * selected_worked_days
        if num_prod_days
        else 0
    )

    waste_variance = (
        selected_actual_waste
        - selected_target_waste
    )

    coal_variance = (
        selected_actual_coal
        - selected_target_coal
    )

    days_left = max(
        num_prod_days
        - month_to_date_worked_days,
        0,
    )

    remaining_volume = (
        monthly_target
        - month_to_date_actual_bcm
    )

    daily_required = (
        remaining_volume
        / max(days_left, 1)
    )

    daily_achieved = (
        selected_actual_bcm
        / selected_worked_days
        if selected_worked_days
        else 0
    )

    strip_ratio = (
        selected_actual_waste
        / selected_actual_coal
        if selected_actual_coal
        else 0
    )

    forecast_variance = (
        forecast
        - monthly_target
    )

    forecast_delivery_percent = (
        (
            forecast
            / monthly_target
        )
        * 100
        if monthly_target
        else 0
    )

    return {
        "site": site,
        "monthly_target_bcm": round(
            monthly_target,
            0,
        ),
        "forecast_bcm": round(
            forecast,
            0,
        ),
        "forecast_variance_bcm": round(
            forecast_variance,
            0,
        ),
        "waste_variance_bcm": round(
            waste_variance,
            0,
        ),
        "coal_variance_tons": round(
            coal_variance,
            0,
        ),
        "actual_bcm": round(
            selected_actual_bcm,
            0,
        ),
        "actual_coal_tons": round(
            selected_actual_coal,
            0,
        ),
        "daily_required_bcm": round(
            daily_required,
            1,
        ),
        "daily_achieved_bcm": round(
            daily_achieved,
            1,
        ),
        "days_worked": int(
            selected_worked_days
        ),
        "days_left": int(
            days_left
        ),
        "strip_ratio": round(
            strip_ratio,
            1,
        ),
        "forecast_delivery_percent": round(
            forecast_delivery_percent,
            1,
        ),
    }



def parse_site_filter(value):
    if isinstance(value, (list, tuple, set)):
        raw_sites = list(value)

    elif isinstance(value, str):
        text = value.strip()

        if not text:
            raw_sites = []

        elif text.startswith("["):
            try:
                parsed = frappe.parse_json(text)

                raw_sites = (
                    parsed
                    if isinstance(parsed, list)
                    else [parsed]
                )

            except Exception:
                raw_sites = text.split(",")

        else:
            raw_sites = text.split(",")

    elif value:
        raw_sites = [value]

    else:
        raw_sites = []

    sites = []

    for item in raw_sites:
        if isinstance(item, dict):
            item = (
                item.get("value")
                or item.get("name")
                or ""
            )

        site = str(item or "").strip()

        if site and site not in sites:
            sites.append(site)

    return sites


def validate_filters(filters):
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    sites = parse_site_filter(filters.get("site"))

    summary_type = (
        filters.get("summary_type")
        or DEFAULT_SUMMARY_TYPE
    ).strip()

    machine_scope = (
        filters.get("machine_scope")
        or DEFAULT_MACHINE_SCOPE
    ).strip()

    au_target_filter = (
        filters.get("au_target_filter")
        or DEFAULT_AU_TARGET_FILTER
    ).strip()

    if not start_date:
        frappe.throw(_("Start Date is required."))

    if not end_date:
        frappe.throw(_("End Date is required."))

    if not sites:
        frappe.throw(_("At least one Site is required."))

    start_date = getdate(start_date)
    end_date = getdate(end_date)

    if start_date > end_date:
        frappe.throw(
            _("Start Date cannot be after End Date.")
        )

    for site in sites:
        if not frappe.db.exists("Location", site):
            frappe.throw(
                _("Location {0} does not exist.").format(
                    frappe.bold(site)
                )
            )

    if summary_type not in SUMMARY_TYPES:
        frappe.throw(
            _("Summary Type must be one of: {0}.").format(
                ", ".join(SUMMARY_TYPES)
            )
        )

    if machine_scope not in MACHINE_SCOPES:
        frappe.throw(
            _("Machine Scope must be one of: {0}.").format(
                ", ".join(MACHINE_SCOPES)
            )
        )

    if au_target_filter not in AU_TARGET_FILTERS:
        frappe.throw(
            _("A & U Target must be one of: {0}.").format(
                ", ".join(AU_TARGET_FILTERS)
            )
        )

    return (
        start_date,
        end_date,
        sites,
        summary_type,
        machine_scope,
        au_target_filter,
    )


def get_excavator_hour_summary(start_date, end_date, site):
    raw_rows = frappe.db.sql(
        """
        SELECT
            p.name AS pre_use_name,
            p.shift_date,
            p.shift,
            a.asset_category,
            a.asset_name,
            a.item_name,
            a.eng_hrs_start,
            a.eng_hrs_end,
            a.working_hours
        FROM `tabPre-Use Hours` p
        INNER JOIN `tabPre-use Assets` a
            ON a.parent = p.name
        WHERE p.shift_date BETWEEN %(start_date)s AND %(end_date)s
          AND p.location = %(site)s
          AND LOWER(COALESCE(a.asset_category, '')) LIKE %(category_pattern)s
        ORDER BY
            a.asset_name,
            p.shift_date,
            p.shift
        """,
        {
            "start_date": start_date,
            "end_date": end_date,
            "site": site,
            "category_pattern": EXCAVATOR_CATEGORY_PATTERN,
        },
        as_dict=True,
    )

    daily_rows = collapse_pre_use_rows(raw_rows)
    valid_rows = []
    excluded_rows = []

    for row in daily_rows:
        working_hours = row.get("working_hours")

        if working_hours is None:
            excluded_rows.append({**row, "exclusion_reason": "Missing hours"})
            continue

        working_hours = round(flt(working_hours), 1)

        if working_hours <= 0:
            excluded_rows.append({**row, "exclusion_reason": "Hours are 0 or negative"})
            continue

        if working_hours > MAX_VALID_WORKING_HOURS:
            excluded_rows.append({**row, "exclusion_reason": "Hours exceed 24"})
            continue

        valid_rows.append({**row, "working_hours": working_hours})

    by_asset = defaultdict(
        lambda: {
            "asset_name": "",
            "item_name": "",
            "asset_category": "",
            "dates": set(),
            "valid_entries": 0,
            "total_hours": 0.0,
        }
    )

    for row in valid_rows:
        asset_name = row.get("asset_name") or _("Unknown Excavator")
        entry = by_asset[asset_name]
        entry["asset_name"] = asset_name
        entry["item_name"] = row.get("item_name") or entry["item_name"]
        entry["asset_category"] = (
            row.get("asset_category") or entry["asset_category"]
        )
        entry["dates"].add(str(row.get("shift_date")))
        entry["valid_entries"] += 1
        entry["total_hours"] += flt(row.get("working_hours"))

    breakdown = []
    for entry in by_asset.values():
        days = len(entry["dates"])
        total_hours = round(entry["total_hours"], 1)
        breakdown.append(
            {
                "asset_name": entry["asset_name"],
                "item_name": entry["item_name"],
                "asset_category": entry["asset_category"],
                "working_days": days,
                "valid_entries": entry["valid_entries"],
                "total_hours": total_hours,
                "average_hours_per_day": round(total_hours / days, 1)
                if days
                else 0,
            }
        )

    breakdown.sort(key=lambda item: (-flt(item.get("total_hours")), item["asset_name"]))

    return {
        "total_hours": round(
            sum(flt(row.get("working_hours")) for row in valid_rows), 1
        ),
        "excavator_count": len(breakdown),
        "raw_record_count": len(raw_rows),
        "collapsed_entry_count": len(daily_rows),
        "valid_entry_count": len(valid_rows),
        "excluded_entry_count": len(excluded_rows),
        "breakdown": breakdown,
        "excluded_rows": excluded_rows,
    }



def get_non_production_excavator_hours(
    start_date,
    end_date,
    site,
):
    rows = frappe.db.sql(
        """
        SELECT
            COALESCE(
                SUM(
                    COALESCE(c.hours, 0)
                ),
                0
            ) AS non_production_hours
        FROM `tabNon-Production Worked Hours` p
        INNER JOIN `tabEquipment Breakdown` c
            ON c.parent = p.name
           AND c.parenttype = 'Non-Production Worked Hours'
           AND c.parentfield = 'equipment_non_production_hours'
        WHERE p.docstatus < 2
          AND p.shift_date BETWEEN %(start_date)s
                               AND %(end_date)s
          AND p.site = %(site)s
          AND EXISTS (
              SELECT 1
              FROM `tabAsset` asset
              WHERE (
                    asset.name = c.machine
                    OR asset.asset_name = c.machine
              )
                AND LOWER(
                    COALESCE(
                        asset.asset_category,
                        ''
                    )
                ) LIKE %(category_pattern)s
          )
        """,
        {
            "start_date": start_date,
            "end_date": end_date,
            "site": site,
            "category_pattern": EXCAVATOR_CATEGORY_PATTERN,
        },
        as_dict=True,
    )

    if not rows:
        return 0.0

    return round(
        flt(
            rows[0].get(
                "non_production_hours"
            )
        ),
        1,
    )





def collapse_pre_use_rows(rows):
    """Build one working-hours entry per excavator per date.

    This mirrors the Pre-Use Report's no-shift behaviour: Day/Morning supplies the
    beginning meter and Night/Afternoon supplies the ending meter. If one side is
    unavailable, valid per-shift differences are summed as a fallback.
    """

    grouped = {}

    for row in rows:
        key = (
            row.get("asset_category"),
            row.get("asset_name"),
            row.get("shift_date"),
        )

        if key not in grouped:
            grouped[key] = {
                "asset_category": row.get("asset_category"),
                "shift_date": row.get("shift_date"),
                "asset_name": row.get("asset_name"),
                "item_name": row.get("item_name"),
                "start_hours": None,
                "end_hours": None,
                "shift_hour_candidates": [],
            }

        entry = grouped[key]

        if row.get("item_name"):
            entry["item_name"] = row.get("item_name")

        shift = (row.get("shift") or "").strip().lower()
        start_hours = row.get("eng_hrs_start")
        end_hours = row.get("eng_hrs_end")

        if shift in {"day", "morning"} and start_hours is not None:
            start_value = flt(start_hours)

            entry["start_hours"] = (
                start_value
                if entry["start_hours"] is None
                else min(entry["start_hours"], start_value)
            )

        if shift in {"night", "afternoon"} and end_hours is not None:
            end_value = flt(end_hours)

            entry["end_hours"] = (
                end_value
                if entry["end_hours"] is None
                else max(entry["end_hours"], end_value)
            )

        candidate = calculate_shift_hours(row)

        if candidate is not None:
            entry["shift_hour_candidates"].append(candidate)

    collapsed = []

    for entry in grouped.values():
        start_hours = entry.get("start_hours")
        end_hours = entry.get("end_hours")
        working_hours = None

        if start_hours is not None and end_hours is not None:
            working_hours = (
                0
                if end_hours == 0
                else round(end_hours - start_hours, 1)
            )

        elif entry["shift_hour_candidates"]:
            working_hours = round(
                sum(entry["shift_hour_candidates"]),
                1,
            )

        collapsed.append(
            {
                "asset_category": entry.get("asset_category"),
                "shift_date": entry.get("shift_date"),
                "asset_name": entry.get("asset_name"),
                "item_name": entry.get("item_name"),
                "start_hours": start_hours,
                "end_hours": end_hours,
                "working_hours": working_hours,
            }
        )

    collapsed.sort(
        key=lambda item: (
            item.get("asset_name") or "",
            str(item.get("shift_date") or ""),
        )
    )

    return collapsed


def calculate_shift_hours(row):
    start_hours = row.get("eng_hrs_start")
    end_hours = row.get("eng_hrs_end")

    if start_hours is not None and end_hours is not None:
        end_value = flt(end_hours)

        if end_value == 0:
            return 0

        return round(
            end_value - flt(start_hours),
            1,
        )

    if row.get("working_hours") is not None:
        return round(
            flt(row.get("working_hours")),
            1,
        )

    return None


def get_availability_summary(
    *,
    start_date,
    end_date,
    site,
    summary_type,
    machine_scope,
    au_target_filter,
    include_detail=True,
):
    module = _load_daily_availability_module()
    _require_daily_availability_functions(module)

    start_text = str(start_date)
    end_text = str(end_date)

    filters = frappe._dict(
        {
            "start_date": start_text,
            "end_date": end_text,
            "from_date": start_text,
            "to_date": end_text,
            "location": site,
            "site": site,
            "summary_type": summary_type,
            "machine_scope": machine_scope,
            "au_target_filter": au_target_filter,
        }
    )

    try:
        dashboard_scope = _dashboard_machine_scope(machine_scope)
        filters["machine_scope"] = dashboard_scope
        filters["asset_ownership"] = DEFAULT_ASSET_OWNERSHIP

        source_rows = module.fetch_grouped_data(
            site,
            start_text,
            end_text,
            dashboard_scope,
            au_target_filter,
            DEFAULT_ASSET_OWNERSHIP,
        ) or []

        spare_map = module.get_spare_swing_asset_map(
            filters
        ) or {}

        source_rows = (
            module.apply_machine_scope_filter_to_dashboard_rows(
                source_rows,
                filters,
                spare_map,
            )
            or []
        )

        category_averages = (
            module.build_summary_averages_from_source_rows(
                source_rows
            )
            or {}
        )

        machine_series = (
            module.build_machine_series_from_source_rows(
                source_rows
            )
            or {}
        )

        category_averages, machine_series = (
            _apply_au_target_filter(
                module,
                category_averages,
                machine_series,
                filters,
            )
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "HOD Presentation Daily Availability Data",
        )

        frappe.throw(
            _(
                "Daily Availability Dashboard data could not be loaded for {0}. "
                "Check the selected filters and the Engineering dashboard configuration."
            ).format(
                frappe.bold(site)
            )
        )

    categories = []
    normalised_machine_series = {}

    for category in AU_CATEGORIES:
        values = category_averages.get(category) or {}
        machines = []

        for row in machine_series.get(category) or []:
            if not isinstance(row, dict):
                continue

            machine = str(
                row.get("machine") or ""
            ).strip()

            if not machine:
                continue

            spare_reason = _get_spare_reason(
                module,
                machine,
                spare_map,
            )

            is_spare = (
                dashboard_scope == "Swing/Spare Machines"
                or bool(spare_reason)
            )

            machines.append(
                {
                    "machine": machine,
                    "availability": _percentage_or_none(
                        row.get("avail")
                    ),
                    "utilisation": _percentage_or_none(
                        row.get("util")
                    ),
                    "is_spare": bool(is_spare),
                    "spare_reason": spare_reason,
                }
            )

        machines.sort(
            key=lambda item: item["machine"]
        )

        normalised_machine_series[category] = machines

        categories.append(
            {
                "category": category,
                "title": AU_CATEGORY_TITLES.get(
                    category,
                    category.upper(),
                ),
                "availability": _percentage_or_none(
                    values.get("avail")
                ),
                "utilisation": _percentage_or_none(
                    values.get("util")
                ),
                "machine_count": len(machines),
            }
        )

    availability_values = [
        row["availability"]
        for row in categories
        if row.get("availability") is not None
    ]

    utilisation_values = [
        row["utilisation"]
        for row in categories
        if row.get("utilisation") is not None
    ]

    daily_series = {}

    if include_detail and summary_type == "Daily Summary":
        daily_series = _build_daily_availability_series(
            module=module,
            start_date=start_date,
            end_date=end_date,
            site=site,
            machine_scope=dashboard_scope,
            au_target_filter=au_target_filter,
            source_rows=source_rows,
        )

    return {
        "summary_type": summary_type,
        "machine_scope": machine_scope,
        "au_target_filter": au_target_filter,
        "target_multiplier": (
            0.85
            if au_target_filter == "85% A & U"
            else 1.0
        ),
        "availability_target": AVAILABILITY_TARGET,
        "utilisation_target": UTILISATION_TARGET,
        "overall_availability": _average_or_none(
            availability_values
        ),
        "overall_utilisation": _average_or_none(
            utilisation_values
        ),
        "machine_count": sum(
            row["machine_count"]
            for row in categories
        ),
        "source_row_count": len(source_rows),
        "categories": categories,
        "machine_series": normalised_machine_series,
        "daily_series": daily_series,
    }


def _build_daily_availability_series(
    *,
    module,
    start_date,
    end_date,
    site,
    machine_scope,
    au_target_filter,
    source_rows,
):
    """Build every daily point from the one Engine query already loaded."""
    output = {category: [] for category in AU_CATEGORIES}
    shift_rows = [
        row
        for row in (source_rows or [])
        if isinstance(row, dict)
        and int(row.get("indent") or 0) == 3
        and not row.get("is_formula_row")
    ]

    rows_by_date = defaultdict(list)
    for row in shift_rows:
        row_date = (
            row.get("shift_date")
            or row.get("date")
            or row.get("production_date")
        )
        if row_date:
            rows_by_date[str(getdate(row_date))].append(row)

    current_date = getdate(start_date)
    final_date = getdate(end_date)

    while current_date <= final_date:
        date_text = str(current_date)
        date_shift_rows = rows_by_date.get(date_text, [])
        date_rows = (
            module.au_engine.build_tree_rows(date_shift_rows)
            if date_shift_rows
            else []
        )
        averages = (
            module.build_summary_averages_from_source_rows(date_rows)
            if date_rows
            else {}
        )

        for category in AU_CATEGORIES:
            values = averages.get(category) or {}
            output[category].append({
                "date": date_text,
                "day": current_date.strftime("%d"),
                "availability": _percentage_or_none(values.get("avail")),
                "utilisation": _percentage_or_none(values.get("util")),
            })

        current_date += timedelta(days=1)

    return output


def _dashboard_machine_scope(machine_scope):
    if machine_scope == "Include Swing/Spare":
        return "Production + Swing/Spare Machines"

    return machine_scope



def _load_daily_availability_module():
    try:
        return importlib.import_module(
            DAILY_AVAILABILITY_MODULE
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "HOD Presentation Daily Availability Import",
        )

        frappe.throw(
            _(
                "The Daily Availability Dashboard backend could not be imported. "
                "Confirm that the Engineering app and daily-availability-dashboard page are installed."
            )
        )


def _require_daily_availability_functions(module):
    required = (
        "fetch_grouped_data",
        "get_spare_swing_asset_map",
        "apply_machine_scope_filter_to_dashboard_rows",
        "build_summary_averages_from_source_rows",
        "build_machine_series_from_source_rows",
    )

    missing = [
        name
        for name in required
        if not callable(
            getattr(module, name, None)
        )
    ]

    if missing:
        frappe.throw(
            _(
                "The Daily Availability Dashboard backend is missing required functions: {0}."
            ).format(
                ", ".join(missing)
            )
        )


def _apply_au_target_filter(
    module,
    averages,
    machine_series,
    filters,
):
    averages = deepcopy(averages or {})
    machine_series = deepcopy(
        machine_series or {}
    )

    apply_method = getattr(
        module,
        "apply_au_target_to_values",
        None,
    )

    if callable(apply_method):
        result = apply_method(
            averages,
            machine_series,
            filters,
        )

        if (
            isinstance(result, (list, tuple))
            and len(result) >= 2
        ):
            return (
                result[0] or {},
                result[1] or {},
            )

        return averages, machine_series

    if (
        (filters or {}).get("au_target_filter")
        != "85% A & U"
    ):
        return averages, machine_series

    multiplier = 0.85

    for values in averages.values():
        if not isinstance(values, dict):
            continue

        for field in ("avail", "util"):
            if values.get(field) is not None:
                values[field] = round(
                    flt(values.get(field))
                    * multiplier,
                    1,
                )

    for rows in machine_series.values():
        for row in rows or []:
            if not isinstance(row, dict):
                continue

            for field in ("avail", "util"):
                if row.get(field) is not None:
                    row[field] = round(
                        flt(row.get(field))
                        * multiplier,
                        1,
                    )

    return averages, machine_series


def _get_spare_reason(
    module,
    machine,
    spare_map,
):
    get_reason = getattr(
        module,
        "get_spare_swing_reason",
        None,
    )

    if callable(get_reason):
        try:
            return str(
                get_reason(
                    machine,
                    spare_map,
                )
                or ""
            )

        except Exception:
            pass

    return str(
        (spare_map or {}).get(machine)
        or ""
    )


def _percentage_or_none(value):
    if value in (None, ""):
        return None

    try:
        value = float(
            str(value)
            .replace("%", "")
            .replace(",", "")
            .strip()
        )

    except (TypeError, ValueError):
        return None

    return round(
        max(
            0.0,
            min(100.0, value),
        ),
        1,
    )


def _average_or_none(values):
    values = [
        float(value)
        for value in values
        if value is not None
    ]

    if not values:
        return None

    return round(
        sum(values) / len(values),
        1,
    )


def get_availability_chart(availability):
    categories = availability.get(
        "categories"
    ) or []

    rows = [
        row
        for row in categories
        if (
            row.get("availability") is not None
            or row.get("utilisation") is not None
        )
    ]

    if not rows:
        return None

    return {
        "data": {
            "labels": [
                row.get("title")
                for row in rows
            ],
            "datasets": [
                {
                    "name": _("Availability %"),
                    "values": [
                        flt(row.get("availability"))
                        for row in rows
                    ],
                },
                {
                    "name": _("Utilisation %"),
                    "values": [
                        flt(row.get("utilisation"))
                        for row in rows
                    ],
                },
            ],
        },
        "type": "bar",
        "height": 300,
    }


def get_report_summary(payload):
    production = payload["production"]
    availability = payload["availability"]

    forecast_variance = flt(
        production.get(
            "forecast_variance_bcm"
        )
    )

    average_availability = flt(
        availability.get(
            "overall_availability"
        )
    )

    average_utilisation = flt(
        availability.get(
            "overall_utilisation"
        )
    )

    return [
        {
            "value": flt(
                production.get("actual_bcm")
            ),
            "label": _("Actual BCM"),
            "datatype": "Float",
            "indicator": "Blue",
        },
        {
            "value": flt(
                payload.get("average_bcm_h")
            ),
            "label": _("Average BCM/H"),
            "datatype": "Float",
            "indicator": (
                "Green"
                if flt(payload.get("average_bcm_h")) >= 220
                else (
                    "Orange"
                    if flt(payload.get("average_bcm_h")) >= 200
                    else "Red"
                )
            ),
        },
        {
            "value": average_availability,
            "label": _("Average Availability %"),
            "datatype": "Percent",
            "indicator": (
                "Green"
                if (
                    average_availability
                    >= AVAILABILITY_TARGET
                )
                else "Red"
            ),
        },
        {
            "value": average_utilisation,
            "label": _("Average Utilisation %"),
            "datatype": "Percent",
            "indicator": (
                "Green"
                if (
                    average_utilisation
                    >= UTILISATION_TARGET
                )
                else "Red"
            ),
        },
        {
            "value": forecast_variance,
            "label": _("Forecast Variance BCM"),
            "datatype": "Float",
            "indicator": (
                "Green"
                if forecast_variance >= 0
                else "Red"
            ),
        },
    ]

def get_hod_downtime_summary(
    start_date,
    end_date,
    site,
):
    start_date = getdate(start_date)
    end_date = getdate(end_date)

    range_start = get_datetime(
        f"{start_date} 00:00:00"
    )
    range_end = get_datetime(
        f"{end_date + timedelta(days=1)} 00:00:00"
    )

    rows = frappe.db.sql(
        """
        SELECT
            pbm.name,
            pbm.asset_name AS plant_no,
            pbm.asset_category,
            pbm.breakdown_reason,
            pbm.breakdown_start_datetime,
            pbm.resolved_datetime,
            pbm.open_closed
        FROM `tabPlant Breakdown or Maintenance` pbm
        WHERE pbm.location = %(site)s
          AND pbm.breakdown_start_datetime IS NOT NULL
          AND pbm.breakdown_start_datetime < %(range_end)s
          AND (
                pbm.resolved_datetime IS NULL
                OR pbm.resolved_datetime = ''
                OR pbm.resolved_datetime > %(range_start)s
          )
        ORDER BY pbm.breakdown_start_datetime ASC
        """,
        {
            "site": site,
            "range_start": range_start,
            "range_end": range_end,
        },
        as_dict=True,
    )

    current_time = now_datetime()
    processed_rows = []

    for row in rows:
        actual_start = get_datetime(
            row.get("breakdown_start_datetime")
        )

        actual_end = (
            get_datetime(row.get("resolved_datetime"))
            if row.get("resolved_datetime")
            else current_time
        )

        clipped_start = max(
            actual_start,
            range_start,
        )
        clipped_end = min(
            actual_end,
            range_end,
        )

        if clipped_end <= clipped_start:
            continue

        hours = round(
            float(
                time_diff_in_hours(
                    clipped_end,
                    clipped_start,
                )
            ),
            2,
        )

        if hours <= 0:
            continue

        status = (
            row.get("open_closed")
            or (
                "Closed"
                if row.get("resolved_datetime")
                else "Open"
            )
        )

        processed_rows.append(
            {
                "plant_no": (
                    row.get("plant_no")
                    or "Unknown"
                ),
                "asset_category": (
                    row.get("asset_category")
                    or "Unknown"
                ),
                "reason": (
                    row.get("breakdown_reason")
                    or "No reason captured"
                ),
                "start": clipped_start,
                "end": clipped_end,
                "hours": hours,
                "status": status,
            }
        )

    day_count = (
        end_date - start_date
    ).days + 1

    if day_count <= 7:
        grouping = "Daily"
    elif day_count <= 31:
        grouping = "Weekly"
    else:
        grouping = "Monthly"

    grouped = {}

    machine_hours = defaultdict(float)
    category_hours = defaultdict(float)
    reason_hours = defaultdict(float)

    for row in processed_rows:
        row_date = getdate(row["start"])

        if grouping == "Daily":
            key = str(row_date)
            label = formatdate(row_date)

        elif grouping == "Weekly":
            week_start = (
                row_date
                - timedelta(
                    days=row_date.weekday()
                )
            )

            week_end = min(
                week_start + timedelta(days=6),
                end_date,
            )

            key = str(week_start)
            label = "{0} to {1}".format(
                formatdate(week_start),
                formatdate(week_end),
            )

        else:
            key = row_date.strftime("%Y-%m")
            label = row_date.strftime("%B %Y")

        bucket = grouped.setdefault(
            key,
            {
                "label": label,
                "hours": 0.0,
                "records": 0,
                "open": 0,
                "closed": 0,
                "machine_hours": defaultdict(float),
                "reason_hours": defaultdict(float),
            },
        )

        bucket["hours"] += row["hours"]
        bucket["records"] += 1

        if (
            str(row["status"])
            .strip()
            .lower()
            == "open"
        ):
            bucket["open"] += 1
        else:
            bucket["closed"] += 1

        bucket["machine_hours"][
            row["plant_no"]
        ] += row["hours"]

        bucket["reason_hours"][
            row["reason"]
        ] += row["hours"]

        machine_hours[
            row["plant_no"]
        ] += row["hours"]

        category_hours[
            row["asset_category"]
        ] += row["hours"]

        reason_hours[
            row["reason"]
        ] += row["hours"]

    grouped_rows = []

    for key in sorted(grouped):
        bucket = grouped[key]

        top_machine = (
            max(
                bucket["machine_hours"],
                key=bucket["machine_hours"].get,
            )
            if bucket["machine_hours"]
            else "-"
        )

        top_reason = (
            max(
                bucket["reason_hours"],
                key=bucket["reason_hours"].get,
            )
            if bucket["reason_hours"]
            else "-"
        )

        grouped_rows.append(
            {
                "label": bucket["label"],
                "hours": round(
                    bucket["hours"],
                    2,
                ),
                "records": bucket["records"],
                "open": bucket["open"],
                "closed": bucket["closed"],
                "top_machine": top_machine,
                "top_reason": top_reason,
            }
        )

    total_hours = round(
        sum(
            row["hours"]
            for row in processed_rows
        ),
        2,
    )

    open_count = sum(
        1
        for row in processed_rows
        if (
            str(row["status"])
            .strip()
            .lower()
            == "open"
        )
    )

    return {
        "grouping": grouping,
        "total_hours": total_hours,
        "record_count": len(processed_rows),
        "open_count": open_count,
        "closed_count": (
            len(processed_rows)
            - open_count
        ),
        "top_machine": (
            max(
                machine_hours,
                key=machine_hours.get,
            )
            if machine_hours
            else "-"
        ),
        "top_category": (
            max(
                category_hours,
                key=category_hours.get,
            )
            if category_hours
            else "-"
        ),
        "top_reason": (
            max(
                reason_hours,
                key=reason_hours.get,
            )
            if reason_hours
            else "-"
        ),
        "rows": grouped_rows,
    }


def build_hod_downtime_html(
    summary,
    site,
    start_date,
    end_date,
):
    summary = summary or {}
    rows = summary.get("rows") or []

    table_rows = []

    for row in rows:
        table_rows.append(
            """
            <tr>
                <td>{label}</td>
                <td>{hours:.2f}</td>
                <td>{records}</td>
                <td>{open_count}</td>
                <td>{closed_count}</td>
                <td>{top_machine}</td>
                <td>{top_reason}</td>
            </tr>
            """.format(
                label=frappe.utils.escape_html(
                    str(
                        row.get("label")
                        or ""
                    )
                ),
                hours=flt(
                    row.get("hours")
                ),
                records=int(
                    row.get("records")
                    or 0
                ),
                open_count=int(
                    row.get("open")
                    or 0
                ),
                closed_count=int(
                    row.get("closed")
                    or 0
                ),
                top_machine=frappe.utils.escape_html(
                    str(
                        row.get("top_machine")
                        or "-"
                    )
                ),
                top_reason=frappe.utils.escape_html(
                    str(
                        row.get("top_reason")
                        or "-"
                    )
                ),
            )
        )

    if not table_rows:
        table_rows.append(
            """
            <tr>
                <td
                    colspan="7"
                    class="hod-downtime-empty"
                >
                    No downtime records found for this period.
                </td>
            </tr>
            """
        )

    return """
        <div class="hod-downtime-summary">
            <div class="hod-downtime-header">
                <div class="hod-downtime-title">
                    Downtime Summary - {site}
                </div>

                <div class="hod-downtime-period">
                    {start_date} to {end_date}
                    | Grouped {grouping}
                </div>
            </div>

            <div class="hod-downtime-kpis">
                <div class="hod-downtime-kpi">
                    <span>Total Downtime</span>
                    <strong>
                        {total_hours:.2f} HRS
                    </strong>
                </div>

                <div class="hod-downtime-kpi">
                    <span>Records</span>
                    <strong>{record_count}</strong>
                </div>

                <div class="
                    hod-downtime-kpi
                    hod-downtime-open
                ">
                    <span>Open</span>
                    <strong>{open_count}</strong>
                </div>

                <div class="
                    hod-downtime-kpi
                    hod-downtime-closed
                ">
                    <span>Closed</span>
                    <strong>{closed_count}</strong>
                </div>

                <div class="hod-downtime-kpi">
                    <span>Top Machine</span>
                    <strong>{top_machine}</strong>
                </div>

                <div class="hod-downtime-kpi">
                    <span>Top Category</span>
                    <strong>{top_category}</strong>
                </div>
            </div>

            <table class="hod-downtime-table">
                <thead>
                    <tr>
                        <th>Period</th>
                        <th>Hours</th>
                        <th>Records</th>
                        <th>Open</th>
                        <th>Closed</th>
                        <th>Top Machine</th>
                        <th>Top Reason</th>
                    </tr>
                </thead>

                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </div>
    """.format(
        site=frappe.utils.escape_html(
            str(site or "")
        ),
        start_date=frappe.utils.escape_html(
            str(start_date)
        ),
        end_date=frappe.utils.escape_html(
            str(end_date)
        ),
        grouping=frappe.utils.escape_html(
            str(
                summary.get("grouping")
                or "Daily"
            )
        ),
        total_hours=flt(
            summary.get("total_hours")
        ),
        record_count=int(
            summary.get("record_count")
            or 0
        ),
        open_count=int(
            summary.get("open_count")
            or 0
        ),
        closed_count=int(
            summary.get("closed_count")
            or 0
        ),
        top_machine=frappe.utils.escape_html(
            str(
                summary.get("top_machine")
                or "-"
            )
        ),
        top_category=frappe.utils.escape_html(
            str(
                summary.get("top_category")
                or "-"
            )
        ),
        table_rows="".join(table_rows),
    )


@frappe.whitelist()
def get_availability_dashboard_html(
    start_date=None,
    end_date=None,
    site=None,
    summary_type=None,
    machine_scope=None,
    au_target_filter=None,
):
    check_report_access()

    if not start_date:
        frappe.throw(
            _("Start Date is required.")
        )

    if not end_date:
        frappe.throw(
            _("End Date is required.")
        )

    site = str(site or "").strip()

    if not site:
        frappe.throw(
            _("Site is required.")
        )

    start_date = getdate(start_date)
    end_date = getdate(end_date)

    if start_date > end_date:
        frappe.throw(
            _("Start Date cannot be after End Date.")
        )

    if not frappe.db.exists(
        "Location",
        site,
    ):
        frappe.throw(
            _("Location {0} does not exist.").format(
                frappe.bold(site)
            )
        )

    summary_type = (
        summary_type
        or DEFAULT_SUMMARY_TYPE
    )

    machine_scope = (
        machine_scope
        or DEFAULT_MACHINE_SCOPE
    )

    au_target_filter = (
        au_target_filter
        or DEFAULT_AU_TARGET_FILTER
    )

    module = _load_daily_availability_module()

    dashboard_filters = frappe._dict(
        {
            "start_date": str(start_date),
            "end_date": str(end_date),
            "from_date": str(start_date),
            "to_date": str(end_date),
            "location": site,
            "site": site,
            "summary_type": summary_type,
            "machine_scope": machine_scope,
            "au_target_filter": au_target_filter,
            "asset_ownership": DEFAULT_ASSET_OWNERSHIP,
            "hours_display": "Hours Average per Category",
        }
    )
    dashboard_filters["machine_scope"] = _dashboard_machine_scope(machine_scope)

    try:
        result = module.execute(
            dashboard_filters
        )

    except Exception:
        frappe.log_error(
            frappe.get_traceback(),
            "HOD Presentation Availability Dashboard HTML",
        )

        frappe.throw(
            _(
                "The Availability and Utilisation dashboard "
                "could not be generated for {0}."
            ).format(
                frappe.bold(site)
            )
        )

    dashboard_html = ""

    if isinstance(
        result,
        (list, tuple),
    ):
        if len(result) >= 3:
            dashboard_html = result[2] or ""

    elif isinstance(result, dict):
        dashboard_html = (
            result.get("html")
            or result.get("message")
            or ""
        )

    if not dashboard_html:
        frappe.throw(
            _(
                "No Availability and Utilisation dashboard "
                "HTML was returned for {0}."
            ).format(
                frappe.bold(site)
            )
        )

    downtime_summary = get_hod_downtime_summary(
        start_date=start_date,
        end_date=end_date,
        site=site,
    )

    downtime_html = build_hod_downtime_html(
        downtime_summary,
        site,
        start_date,
        end_date,
    )

    return {
        "site": site,
        "html": dashboard_html + downtime_html,
    }


@frappe.whitelist()
def ensure_interactive_report():
    """Keep HOD Presentation out of Frappe's prepared-report queue."""
    check_report_access()

    values = frappe.db.get_value(
        "Report",
        REPORT_NAME,
        [
            "prepared_report",
            "disable_prepared_report_automation",
        ],
        as_dict=True,
    ) or {}

    needs_update = (
        bool(values.get("prepared_report"))
        or not bool(
            values.get(
                "disable_prepared_report_automation"
            )
        )
    )

    if not needs_update:
        return False

    frappe.db.set_value(
        "Report",
        REPORT_NAME,
        {
            "prepared_report": 0,
            "disable_prepared_report_automation": 1,
        },
        update_modified=False,
    )
    frappe.clear_cache(doctype="Report")

    return True


@frappe.whitelist()
def download_presentation(
    start_date=None,
    end_date=None,
    site=None,
    summary_type=None,
    machine_scope=None,
    au_target_filter=None,
):
    check_report_access()

    filters = {
        "start_date": start_date,
        "end_date": end_date,
        "site": site,
        "summary_type": summary_type,
        "machine_scope": machine_scope,
        "au_target_filter": au_target_filter,
    }

    sites = parse_site_filter(site)

    if not sites:
        frappe.throw(
            _("At least one Site is required.")
        )

    payloads = [
        get_report_payload(
            filters,
            include_au_detail=True,
            site_override=selected_site,
        )
        for selected_site in sites
    ]

    if len(payloads) == 1:
        payload = payloads[0]

    else:
        first_payload = payloads[0]

        payload = {
            "site": " / ".join(sites),
            "start_date": first_payload["start_date"],
            "end_date": first_payload["end_date"],
            "period_label": first_payload["period_label"],
            "generated_by": first_payload["generated_by"],
            "generated_at": first_payload["generated_at"],
            "availability": first_payload["availability"],
            "site_payloads": payloads,
        }

    try:
        from .presentation_builder import (
            build_hod_presentation,
        )

    except ModuleNotFoundError as exc:
        if exc.name == "pptx":
            frappe.throw(
                _(
                    "PowerPoint export requires python-pptx. "
                    "Install python-pptx==1.0.2 in the bench environment "
                    "and restart Bench."
                )
            )

        raise

    output = BytesIO()

    build_hod_presentation(
        payload,
        output,
    )

    output.seek(0)

    safe_site = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        payload["site"],
    ).strip("_")

    filename = (
        "HOD_Presentation_{0}_{1}_to_{2}.pptx"
    ).format(
        safe_site or "Sites",
        payload["start_date"],
        payload["end_date"],
    )

    frappe.local.response["type"] = "download"
    frappe.local.response["filename"] = filename
    frappe.local.response["filecontent"] = (
        output.getvalue()
    )
    frappe.local.response["content_type"] = (
        PPTX_CONTENT_TYPE
    )
    frappe.local.response[
        "display_content_as"
    ] = "attachment"



@frappe.whitelist()
def download_captured_presentation(
    captured_slides=None,
    site=None,
    start_date=None,
    end_date=None,
    period_label=None,
):
    check_report_access()

    slides = frappe.parse_json(
        captured_slides or "[]"
    )

    if not isinstance(slides, list) or not slides:
        frappe.throw(
            _("No report sections were captured.")
        )

    payload = {
        "site": site or "HOD Presentation",
        "start_date": start_date or "",
        "end_date": end_date or "",
        "period_label": period_label or "",
        "generated_by": (
            get_fullname(frappe.session.user)
            or frappe.session.user
        ),
        "generated_at": now_datetime().strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "captured_slides": slides,
    }

    from .presentation_builder import (
        build_hod_presentation,
    )

    output = BytesIO()

    build_hod_presentation(
        payload,
        output,
    )

    output.seek(0)

    safe_site = re.sub(
        r"[^A-Za-z0-9_-]+",
        "_",
        str(site or "Sites"),
    ).strip("_")

    filename = (
        f"HOD_Presentation_{safe_site}_"
        f"{start_date}_to_{end_date}.pptx"
    )

    return {
        "filename": filename,
        "content": base64.b64encode(
            output.getvalue()
        ).decode("ascii"),
    }




def check_report_access():
    report = frappe.get_doc(
        "Report",
        REPORT_NAME,
    )

    if not report.is_permitted():
        frappe.throw(
            _(
                "You do not have access to the {0} report."
            ).format(
                REPORT_NAME
            ),
            frappe.PermissionError,
        )

    if not frappe.has_permission(
        report.ref_doctype,
        "report",
    ):
        frappe.throw(
            _(
                "You do not have report permission on {0}."
            ).format(
                report.ref_doctype
            ),
            frappe.PermissionError,
        )

    if not (
        frappe.has_permission(
            "Pre-Use Hours",
            "report",
        )
        or frappe.has_permission(
            "Pre-Use Hours",
            "read",
        )
    ):
        frappe.throw(
            _(
                "You do not have permission to report on Pre-Use Hours."
            ),
            frappe.PermissionError,
        )
