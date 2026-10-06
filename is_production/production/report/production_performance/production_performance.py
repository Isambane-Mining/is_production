# Copyright (c) 2025, Isambane Mining (Pty) Ltd
# For license information, please see license.txt

import frappe
from datetime import datetime
from frappe.utils import strip_html_tags


def get_production_actuals(start_date, end_date, site):
    """Return the same actual production values used by Production Performance."""
    COAL_CONVERSION = 1.5

    # --- Tallies (from Hourly Production) ---
    ts_tallies = frappe.db.sql("""
        SELECT COALESCE(SUM(tl.bcms),0)
        FROM `tabHourly Production` hp
        JOIN `tabTruck Loads` tl ON tl.parent = hp.name
        WHERE hp.prod_date BETWEEN %s AND %s
          AND hp.location = %s
    """, (start_date, end_date, site))[0][0]

    dozing_tallies = frappe.db.sql("""
        SELECT COALESCE(SUM(dp.bcm_hour),0)
        FROM `tabHourly Production` hp
        JOIN `tabDozer Production` dp ON dp.parent = hp.name
        WHERE hp.prod_date BETWEEN %s AND %s
          AND hp.location = %s
    """, (start_date, end_date, site))[0][0]

    tallies_total = (ts_tallies or 0) + (dozing_tallies or 0)

    coal_bcm_rows = frappe.db.sql("""
        SELECT SUM(tl.bcms) AS coal_bcm
        FROM `tabHourly Production` hp
        JOIN `tabTruck Loads` tl ON tl.parent = hp.name
        WHERE hp.prod_date BETWEEN %s AND %s
          AND hp.location = %s
          AND LOWER(tl.mat_type) LIKE '%%coal%%'
    """, (start_date, end_date, site), as_dict=True)

    coal_bcm = coal_bcm_rows[0]["coal_bcm"] or 0
    coal_tons_hp = coal_bcm * COAL_CONVERSION
    coal_bcm_tallies = coal_bcm
    waste_bcm_tallies = tallies_total - coal_bcm_tallies

    # --- Survey Fetch ---
    survey_doc = frappe.get_all(
        "Survey",
        filters={
            "location": site,
            "last_production_shift_start_date": ["<=", f"{end_date} 23:59:59"],
        },
        fields=[
            "name",
            "last_production_shift_start_date",
            "total_ts_bcm",
            "total_dozing_bcm",
            "total_surveyed_coal_tons"
        ],
        order_by="last_production_shift_start_date desc",
        limit_page_length=1
    )

    ts_actual_bcm = 0
    dozing_actual_bcm = 0
    coal_tons_actual = 0
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()

    if survey_doc:
        survey = survey_doc[0]
        survey_date = survey.get("last_production_shift_start_date")

        if isinstance(survey_date, datetime):
            survey_date = survey_date.date()

        if survey_date and start_dt <= survey_date <= end_dt:
            ts_actual_bcm = survey.get("total_ts_bcm") or 0
            dozing_actual_bcm = survey.get("total_dozing_bcm") or 0
            coal_tons_actual = survey.get("total_surveyed_coal_tons") or 0

            ts_after = frappe.db.sql("""
                SELECT COALESCE(SUM(tl.bcms),0)
                FROM `tabHourly Production` hp
                JOIN `tabTruck Loads` tl ON tl.parent = hp.name
                WHERE hp.prod_date > %s AND hp.prod_date <= %s
                  AND hp.location = %s
            """, (survey_date, end_date, site))[0][0]

            dozing_after = frappe.db.sql("""
                SELECT COALESCE(SUM(dp.bcm_hour),0)
                FROM `tabHourly Production` hp
                JOIN `tabDozer Production` dp ON dp.parent = hp.name
                WHERE hp.prod_date > %s AND hp.prod_date <= %s
                  AND hp.location = %s
            """, (survey_date, end_date, site))[0][0]

            coal_after = frappe.db.sql("""
                SELECT COALESCE(SUM(tl.bcms),0)
                FROM `tabHourly Production` hp
                JOIN `tabTruck Loads` tl ON tl.parent = hp.name
                WHERE hp.prod_date > %s AND hp.prod_date <= %s
                  AND hp.location = %s
                  AND LOWER(tl.mat_type) LIKE '%%coal%%'
            """, (survey_date, end_date, site))[0][0]

            ts_actual_bcm += ts_after or 0
            dozing_actual_bcm += dozing_after or 0
            coal_tons_actual += ((coal_after or 0) * COAL_CONVERSION)
        else:
            ts_actual_bcm = ts_tallies or 0
            dozing_actual_bcm = dozing_tallies or 0
            coal_tons_actual = coal_tons_hp
    else:
        ts_actual_bcm = ts_tallies or 0
        dozing_actual_bcm = dozing_tallies or 0
        coal_tons_actual = coal_tons_hp

    actual_bcm = (ts_actual_bcm or 0) + (dozing_actual_bcm or 0)
    coal_bcm_actual = (coal_tons_actual / COAL_CONVERSION) if coal_tons_actual else 0
    waste_bcm_actual = actual_bcm - coal_bcm_actual


    return {
        "ts_tallies": ts_tallies or 0,
        "dozing_tallies": dozing_tallies or 0,
        "tallies_total": tallies_total or 0,
        "coal_bcm_tallies": coal_bcm_tallies or 0,
        "coal_tons_hp": coal_tons_hp or 0,
        "waste_bcm_tallies": waste_bcm_tallies or 0,
        "ts_actual_bcm": ts_actual_bcm or 0,
        "dozing_actual_bcm": dozing_actual_bcm or 0,
        "coal_tons_actual": coal_tons_actual or 0,
        "coal_bcm_actual": coal_bcm_actual or 0,
        "waste_bcm_actual": waste_bcm_actual or 0,
        "actual_bcm": actual_bcm or 0,
    }


def execute(filters=None):
    filters = filters or {}
    start_date = filters.get("start_date")
    end_date = filters.get("end_date")
    site = filters.get("site")

    if not start_date or not end_date or not site:
        frappe.throw("Start Date, End Date and Site are required filters.")

    # --- Get the matching Monthly Production Plan ---
    plans = frappe.get_all(
        "Monthly Production Planning",
        filters={
            "location": site,
            "prod_month_start_date": ["<=", end_date],
            "prod_month_end_date": [">=", start_date],
        },
        fields=[
            "name", "location",
            "monthly_target_bcm", "coal_tons_planned", "coal_planned_bcm", "waste_bcms_planned",
            "total_ts_planned_volumes", "planned_dozer_volumes",
            "num_prod_days", "total_month_prod_hours",
            "month_forecated_bcm",
            "month_actual_coal",
            "month_actual_bcm"
        ],
        order_by="creation desc",
        limit_page_length=1
    )

    if not plans:
        return get_columns(), [{"block1": "No data found", "block2": "", "block3": ""}]

    d = plans[0]

    def fmt_int(val):
        return f"{int(val or 0):,}"

    actuals = get_production_actuals(
        start_date,
        end_date,
        site,
    )

    ts_tallies = actuals["ts_tallies"]
    dozing_tallies = actuals["dozing_tallies"]
    tallies_total = actuals["tallies_total"]
    coal_bcm_tallies = actuals["coal_bcm_tallies"]
    coal_tons_hp = actuals["coal_tons_hp"]
    waste_bcm_tallies = actuals["waste_bcm_tallies"]

    ts_actual_bcm = actuals["ts_actual_bcm"]
    dozing_actual_bcm = actuals["dozing_actual_bcm"]
    coal_tons_actual = actuals["coal_tons_actual"]
    coal_bcm_actual = actuals["coal_bcm_actual"]
    waste_bcm_actual = actuals["waste_bcm_actual"]
    actual_bcm = actuals["actual_bcm"]

    total_days = d.num_prod_days or 0
    total_hours = d.total_month_prod_hours or 0
    completed_days = 0
    completed_hours = 0

    child_rows = frappe.get_all(
        "Monthly Production Days",
        filters={
            "parent": d.name,
            "shift_start_date": ["between", [start_date, end_date]]
        },
        fields=[
            "shift_start_date",
            "shift_day_hours", "shift_night_hours",
            "shift_morning_hours", "shift_afternoon_hours"
        ]
    )

    for r in child_rows:
        dt = r.get("shift_start_date")
        if isinstance(dt, str):
            dt = datetime.strptime(dt, "%Y-%m-%d").date()
        if dt and dt.weekday() != 6:  # exclude Sundays
            hrs = (r.get("shift_day_hours") or 0) + (r.get("shift_night_hours") or 0) \
                  + (r.get("shift_morning_hours") or 0) + (r.get("shift_afternoon_hours") or 0)
            if hrs:
                completed_days += 1
                completed_hours += hrs

    remaining_days = total_days - completed_days
    remaining_hours = total_hours - completed_hours

    def make_block(title, rows):
        html = f"<div style='font-weight:bold; margin-bottom:4px; text-align:center;'>{title}</div>"
        html += "<table style='width:100%; border-collapse:collapse; font-size:12px;'>"
        for metric, val in rows:
            html += (
                f"<tr>"
                f"<td style='padding:2px 4px; border-bottom:1px solid #eee;'>{metric}</td>"
                f"<td style='padding:2px 4px; text-align:right; border-bottom:1px solid #eee;'>{val}</td>"
                f"</tr>"
            )
        html += "</table>"
        return html

    # --- Build blocks ---
    planning = make_block("Planning Targets", [
        ("Target BCM Total", fmt_int(d.monthly_target_bcm)),
        ("Target Truck & Shovel BCM", fmt_int(d.total_ts_planned_volumes)),
        ("Target Dozing BCM", fmt_int(d.planned_dozer_volumes)),
        ("Waste Planned BCM", fmt_int(d.waste_bcms_planned)),
        ("Coal Planned BCM", fmt_int(d.coal_planned_bcm)),
        ("Coal Tons Planned", fmt_int(d.coal_tons_planned)),
    ])

    tallies = make_block("MTD Tallies", [
        ("Total Tallies BCMs", fmt_int(tallies_total)),
        ("TS Tallies BCMs", fmt_int(ts_tallies)),
        ("Dozing Tallies BCMs", fmt_int(dozing_tallies)),
        ("Waste Tallies BCMs", fmt_int(waste_bcm_tallies)),
        ("Coal Tallies BCMs", fmt_int(coal_bcm_tallies)),
        ("Coal Tallies Tons", fmt_int(coal_tons_hp)),
    ])

    actuals = make_block("MTD Actuals", [
        ("Actual BCMs (MPP)", fmt_int(actual_bcm)),
        ("TS Actual BCMs (Survey + HP after survey)", fmt_int(ts_actual_bcm)),
        ("Dozing Actual BCMs (Survey + HP after survey)", fmt_int(dozing_actual_bcm)),
        ("Actual Waste BCMs", fmt_int(waste_bcm_actual)),
        ("Actual Coal BCMs", fmt_int(coal_bcm_actual)),
        ("Actual Coal Tons (Survey + HP after survey)", fmt_int(coal_tons_actual)),
    ])

    forecast = make_block("Forecast", [
        ("Forecast BCM", fmt_int(d.month_forecated_bcm)),
    ])

    cal_days = make_block("Calendar (Days)", [
        ("Available Days", fmt_int(total_days)),
        ("Worked Days", fmt_int(completed_days)),
        ("Remaining Days", fmt_int(remaining_days)),
    ])

    cal_hours = make_block("Calendar (Hours)", [
        ("Available Hours", fmt_int(total_hours)),
        ("Worked Hours", fmt_int(completed_hours)),
        ("Remaining Hours", fmt_int(remaining_hours)),
    ])

    actual_bcms_mtd = actual_bcm
    mtd_target = ((d.monthly_target_bcm or 0) / total_days * completed_days) if total_days else 0
    variance_mtd = actual_bcms_mtd - mtd_target
    actual_daily_bcma = (actual_bcm / completed_days) if completed_days else 0
    daily_target_bcma = ((d.monthly_target_bcm or 0) / total_days) if total_days else 0

    variance_daily = actual_daily_bcma - daily_target_bcma

    overall_bcma = actual_bcm
    overall_target = d.monthly_target_bcm or 0
    remaining_volume_month = overall_bcma - overall_target
    current_strip_ratio = (waste_bcm_actual / coal_tons_actual) if coal_tons_actual else 0
    planned_strip_ratio = (d.waste_bcms_planned / d.coal_tons_planned) if d.coal_tons_planned else 0

    mtd_coal_target_tons = ((d.coal_tons_planned or 0) / total_days * completed_days) if total_days else 0
    coal_variance = coal_tons_actual - mtd_coal_target_tons

    summary_rows = [
        ("MTD Actual BCM (waste & coal)", fmt_int(actual_bcms_mtd)),
        ("MTD Actual BCM Target (waste & coal)", f"{mtd_target:,.0f}"),
        ("Variance", f"{variance_mtd:,.0f}"),

        ("Actual Daily BCM (waste & coal)", f"{actual_daily_bcma:,.0f}"),
        ("Daily Target BCM (waste & coal)", f"{daily_target_bcma:,.0f}"),
        ("Variance", f"{variance_daily:,.0f}"),

        ("Overall Actual BCM (waste & coal)", fmt_int(overall_bcma)),
        ("Overall Target BCM (waste & coal)", fmt_int(overall_target)),
        ("Remaining BCM (waste & coal)", fmt_int(remaining_volume_month)),

        ("Current Strip Ratio", f"{current_strip_ratio:.2f}"),
        ("Planned Strip Ratio", f"{planned_strip_ratio:.2f}"),
    ]

    summary1 = make_block("Summary (Part 1)", summary_rows[0:3])
    summary2 = make_block("Summary (Part 2)", summary_rows[3:6])
    summary3 = make_block("Summary (Part 3)", [
    ("Overall Actual BCM (waste & coal)", fmt_int(overall_bcma)),
    ("Overall Target BCM (waste & coal)", fmt_int(overall_target)),
    ("Remaining BCM (waste & coal)", fmt_int(remaining_volume_month)),
])

    summary4 = make_block("Summary (Part 4)", summary_rows[9:11])
    summary5 = make_block("Summary (Part 5 - Coal)", [
        ("MTD Actual Coal (tons)", fmt_int(coal_tons_actual)),
        ("MTD Coal Target (tons)", f"{mtd_coal_target_tons:,.0f}"),
        ("Variance", f"{coal_variance:,.0f}"),
    ])

    data = [
        {"block1": planning, "block2": tallies, "block3": actuals},
        {"block1": forecast, "block2": cal_days, "block3": cal_hours},
        {"block1": summary1, "block2": summary2, "block3": summary3},
        {"block1": summary4, "block2": summary5, "block3": ""},
    ]

    # detect dashboard vs report view
    is_dashboard = bool(frappe.form_dict.get("ignore_prepared_report"))
    if not is_dashboard:
        data = [
            {
                "block1": strip_html_tags(row.get("block1", "")),
                "block2": strip_html_tags(row.get("block2", "")),
                "block3": strip_html_tags(row.get("block3", "")),
            }
            for row in data
        ]

    return get_columns(), data


def get_columns():
    return [
        {"label": "Block 1", "fieldname": "block1", "fieldtype": "HTML", "width": 350},
        {"label": "Block 2", "fieldname": "block2", "fieldtype": "HTML", "width": 350},
        {"label": "Block 3", "fieldname": "block3", "fieldtype": "HTML", "width": 350},
    ]
