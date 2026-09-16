# Copyright (c) 2025, Isambane Mining (Pty) Ltd
# For license information, please see license.txt

import frappe
from frappe.utils import flt, format_date, getdate
from datetime import datetime


def execute(filters=None):
    if not filters:
        filters = {}

    site = filters.get("site")
    end_date = filters.get("end_date")
    formatted_date = format_date(end_date, "dd/MM/yyyy") if end_date else ""

    mpp = get_monthly_plan(site, end_date)
    month_start = getdate(mpp.prod_month_start_date) if mpp else None

    data = {
        "monthly_target": 0,
        "waste_bcms_planned": 0,
        "coal_tons_planned": 0,
        "num_prod_days": 0,
        "num_prod_days_completed": 0,
        "month_remaining_prod_days": 0,
        "mtd_actual_bcms": 0,
        "mtd_prog_actual_coal": 0,
        "mtd_prog_actual_waste": 0,
        "mtd_prog_target_waste": 0,
        "forecast_waste": 0,
        "short_over_waste": 0,
        "mtd_prog_target_coal": 0,
        "forecast_coal": 0,
        "short_over_coal": 0,
        "remaining_volume": 0,
        "daily_required": 0,
        "actual_daily": 0,
        "days_left": 0,
        "forecast": 0,
        "short_over_forecast": 0,
        "strip_ratio": 0,
    }

    actual_ts_day = get_actual_ts_for_day(site, end_date)
    actual_dozer_day = get_actual_dozer_for_day(site, end_date)
    data["actual_daily"] = (actual_ts_day or 0) + (actual_dozer_day or 0)

    if mpp:
        data.update({
            "monthly_target": flt(mpp.monthly_target_bcm),
            "waste_bcms_planned": flt(mpp.waste_bcms_planned),
            "coal_tons_planned": flt(mpp.coal_tons_planned),
            "num_prod_days": flt(mpp.num_prod_days),
        })

        completed_days = 0
        if month_start:
            child_rows = frappe.get_all(
                "Monthly Production Days",
                filters={
                    "parent": mpp.name,
                    "shift_start_date": ["between", [month_start, end_date]]
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

                if dt and dt.weekday() != 6:
                    hrs = (
                        (r.get("shift_day_hours") or 0)
                        + (r.get("shift_night_hours") or 0)
                        + (r.get("shift_morning_hours") or 0)
                        + (r.get("shift_afternoon_hours") or 0)
                    )
                    if hrs:
                        completed_days += 1

        # --------------------------------------------------------
        # HOURS-BASED PRODUCTION DAYS
        #
        # Monthly Production Planning already converts the production
        # calendar to hour-equivalent days:
        #       18 planned hours = 1.00 production day
        #
        # Example:
        #       456 total planned hours / 18 = 25.33 days
        #       360 worked hours       / 18 = 20.00 days
        #        96 remaining hours    / 18 =  5.33 days
        # --------------------------------------------------------
        monthly_available_days = float(mpp.num_prod_days or 0)
        remaining_days = float(mpp.month_remaining_production_days or 0)
        worked_days = max(monthly_available_days - remaining_days, 0)

        total_planned_hours = monthly_available_days * 18.0
        worked_hours = worked_days * 18.0
        remaining_hours = remaining_days * 18.0

        data["num_prod_days_completed"] = worked_days
        data["month_remaining_prod_days"] = remaining_days

        # Changed: MTD Prog Actual BCM’s now comes directly from Monthly Production Planning
        mtd_actual_bcms = flt(mpp.month_actual_bcm)

        progress_factor = (
            worked_hours / total_planned_hours
            if total_planned_hours
            else 0
        )

        mtd_prog_actual_waste = (
            waste_bcms_planned * progress_factor
        )

        mtd_prog_actual_coal = (
            coal_tons_planned * progress_factor
        )

        data["mtd_actual_bcms"] = mtd_actual_bcms
        data["mtd_prog_actual_coal"] = mtd_prog_actual_coal
        data["mtd_prog_actual_waste"] = mtd_prog_actual_waste

        # Forecast formula requested:
        # (MTD Actual / Worked Days) * Monthly Available Days + MTD Actual
        if worked_days:
            data["forecast_waste"] = (
                (data["mtd_prog_actual_waste"] / worked_days)
                * data["month_remaining_prod_days"]
            ) + data["mtd_prog_actual_waste"]

            data["forecast_coal"] = (
                (data["mtd_prog_actual_coal"] / worked_days)
                * data["month_remaining_prod_days"]
            ) + data["mtd_prog_actual_coal"]
        else:
            data["forecast_waste"] = data["mtd_prog_actual_waste"]
            data["forecast_coal"] = data["mtd_prog_actual_coal"]

        data["mtd_prog_target_waste"] = (
            (data["waste_bcms_planned"] / data["num_prod_days"]) * data["num_prod_days_completed"]
            if data["num_prod_days"] else 0
        )
        data["short_over_waste"] = data["mtd_prog_target_waste"] - data["mtd_prog_actual_waste"]

        data["mtd_prog_target_coal"] = (
            (data["coal_tons_planned"] / data["num_prod_days"]) * data["num_prod_days_completed"]
            if data["num_prod_days"] else 0
        )
        data["short_over_coal"] = data["mtd_prog_target_coal"] - data["mtd_prog_actual_coal"]

        data["remaining_volume"] = data["monthly_target"] - data["mtd_actual_bcms"]
        data["daily_required"] = data["remaining_volume"] / max((data["month_remaining_prod_days"], 1))
        data["forecast"] = (
            mtd_actual_bcms
            + (
                (mtd_actual_bcms / worked_hours)
                * remaining_hours
            )
            if worked_hours
            else mtd_actual_bcms
        )

        data["days_left"] = remaining_days

        data["forecast"] = flt(mpp.month_forecated_bcm) if mpp else 0
        data["short_over_forecast"] = data["monthly_target"] - data["forecast"]

        data["strip_ratio"] = round(
            (data["mtd_prog_actual_waste"] / data["mtd_prog_actual_coal"])
            if data["mtd_prog_actual_coal"] else 0,
            1
        )

    html = build_html(site, formatted_date, data)
    return [], None, html


def get_monthly_plan(site, date):
    if not site or not date:
        return None

    plan_name = frappe.db.get_value(
        "Monthly Production Planning",
        {
            "location": site,
            "prod_month_start_date": ["<=", date],
            "prod_month_end_date": [">=", date]
        },
        "name",
    )

    return frappe.get_doc("Monthly Production Planning", plan_name) if plan_name else None


def get_mtd_coal_dynamic(site, end_date, month_start):
    if not site or not end_date:
        return 0

    COAL_CONVERSION = 1.5

    survey_doc = frappe.get_all(
        "Survey",
        filters={
            "location": site,
            "last_production_shift_start_date": ["<=", f"{end_date} 23:59:59"],
        },
        fields=["last_production_shift_start_date", "total_surveyed_coal_tons"],
        order_by="last_production_shift_start_date desc",
        limit_page_length=1
    )

    coal_tons_actual = 0
    end_dt = getdate(end_date)
    start_dt = getdate(month_start)

    if survey_doc:
        survey = survey_doc[0]
        survey_date = survey.get("last_production_shift_start_date")

        if isinstance(survey_date, datetime):
            survey_date = survey_date.date()

        if survey_date and start_dt <= survey_date <= end_dt:
            coal_tons_actual = survey.get("total_surveyed_coal_tons") or 0

            coal_after = frappe.db.sql("""
                SELECT COALESCE(SUM(tl.bcms),0)
                FROM `tabHourly Production` hp
                JOIN `tabTruck Loads` tl ON tl.parent = hp.name
                WHERE hp.prod_date > %s AND hp.prod_date <= %s
                  AND hp.location = %s
                  AND LOWER(tl.mat_type) LIKE '%%coal%%'
            """, (survey_date, end_date, site))[0][0]

            coal_tons_actual += (coal_after or 0) * COAL_CONVERSION
        else:
            coal_tons_actual = get_coal_from_hourly(month_start, end_date, site, COAL_CONVERSION)
    else:
        coal_tons_actual = get_coal_from_hourly(month_start, end_date, site, COAL_CONVERSION)

    return coal_tons_actual


def get_coal_from_hourly(start_date, end_date, site, COAL_CONVERSION):
    coal_bcm = frappe.db.sql("""
        SELECT COALESCE(SUM(tl.bcms),0)
        FROM `tabHourly Production` hp
        JOIN `tabTruck Loads` tl ON tl.parent = hp.name
        WHERE hp.prod_date BETWEEN %s AND %s
          AND hp.location = %s
          AND LOWER(tl.mat_type) LIKE '%%coal%%'
    """, (start_date, end_date, site))[0][0]

    return (coal_bcm or 0) * COAL_CONVERSION


def get_actual_ts_for_day(site, date):
    if not site or not date:
        return 0

    result = frappe.db.sql(
        """
        SELECT SUM(total_ts_bcm) AS total_bcm
        FROM `tabHourly Production`
        WHERE location = %s AND prod_date = %s
        """,
        (site, date),
        as_dict=True
    )

    return result[0].total_bcm or 0


def get_actual_dozer_for_day(site, date):
    if not site or not date:
        return 0

    result = frappe.db.sql(
        """
        SELECT SUM(total_dozing_bcm) AS total_bcm
        FROM `tabHourly Production`
        WHERE location = %s AND prod_date = %s
        """,
        (site, date),
        as_dict=True
    )

    return result[0].total_bcm or 0


def build_html(site, formatted_date, d):
    def fmt(value, decimals=0):
        return f"{flt(value, decimals):,.{decimals}f}"

    def color_num(value):
        val = fmt(abs(value))
        if value > 0:
            return f"<span style='color:red;'>{val}</span>"
        elif value < 0:
            return f"<span style='color:green;'>{val}</span>"
        else:
            return val

    style = """
    <style>
        @page { size: portrait; margin: 10mm; }
        body { font-family: Arial, sans-serif; font-size: 11.5px; }
        .report-container {
            width: 70%;
            margin: 0 auto;
            border: 1px solid #BFBFBF;
            padding-bottom: 5px;
        }
        table {
            border-collapse: collapse;
            width: 100%;
            table-layout: fixed;
        }
        th, td {
            border: 1px solid #BFBFBF;
            padding: 4px 6px;
        }
        th {
            background-color: #F9F9F9;
            text-align: left;
            font-weight: bold;
        }
        td.label {
            text-align: left;
            width: 60%;
            word-wrap: break-word;
        }
        td.num {
            text-align: right;
            width: 25%;
        }
        td.unit {
            width: 15%;
            text-align: left;
        }
        .bold { font-weight: bold; }
        .header-title {
            background-color: #4FA7FF;
            color: white;
            font-weight: bold;
            text-align: center;
            padding: 6px;
            font-size: 14px;
        }
        .week-input {
            width: 2cm;
            height: 1cm;
            border: 1px solid black;
            text-align: center;
            font-weight: bold;
            background-color: #fff;
            margin-left: 5px;
        }
    </style>
    """

    site_label = site.upper() if site else ""

    return f"""
    {style}
    <div class="report-container">
        <div class="header-title">
            {site_label}<br>
            PRODUCTION SUMMARY – {formatted_date}
            <input type="text" class="week-input" placeholder="" />
        </div>

        <table>
            <tr><th>Description</th><th>Unit</th><th class="num">Value</th></tr>

            <tr><td class="label bold">Monthly Target</td><td class="unit">BCM</td><td class="num">{fmt(d["monthly_target"])}</td></tr>
            <tr><td colspan="3" style="height:12px; border:none;"></td></tr>

            <tr><td class="label bold">Monthly Waste Target</td><td class="unit">BCM</td><td class="num">{fmt(d["waste_bcms_planned"])}</td></tr>
            <tr><td class="label bold">Forecast Waste</td><td class="unit">BCM</td><td class="num">{fmt(d["forecast_waste"])}</td></tr>
            <tr><td class="label">MTD Prog Actual Waste</td><td class="unit">BCM</td><td class="num">{fmt(d["mtd_prog_actual_waste"])}</td></tr>
            <tr><td class="label">MTD Prog Target Waste</td><td class="unit">BCM</td><td class="num">{fmt(d["mtd_prog_target_waste"])}</td></tr>
            <tr><td class="label bold">SHORT / OVER</td><td class="unit">BCM</td><td class="num">{color_num(d["short_over_waste"])}</td></tr>
            <tr><td colspan="3" style="height:12px; border:none;"></td></tr>

            <tr><td class="label bold">Monthly Coal Target</td><td class="unit">TONS</td><td class="num">{fmt(d["coal_tons_planned"])}</td></tr>
            <tr><td class="label bold">Forecast Coal</td><td class="unit">TONS</td><td class="num">{fmt(d["forecast_coal"])}</td></tr>
            <tr><td class="label">MTD Prog Actual COAL</td><td class="unit">TONS</td><td class="num">{fmt(d["mtd_prog_actual_coal"])}</td></tr>
            <tr><td class="label">MTD Prog Target COAL</td><td class="unit">TONS</td><td class="num">{fmt(d["mtd_prog_target_coal"])}</td></tr>
            <tr><td class="label bold">SHORT / OVER</td><td class="unit">TONS</td><td class="num">{color_num(d["short_over_coal"])}</td></tr>
            <tr><td colspan="3" style="height:12px; border:none;"></td></tr>

            <tr><td class="label">MTD Prog Actual BCM’s</td><td class="unit">BCM</td><td class="num">{fmt(d["mtd_actual_bcms"])}</td></tr>
            <tr><td class="label bold">Remaining Volume</td><td class="unit">BCM</td><td class="num">{fmt(d["remaining_volume"])}</td></tr>
            <tr><td class="label">Daily required to reach Target</td><td class="unit">BCM</td><td class="num">{fmt(d["daily_required"])}</td></tr>
            <tr><td class="label">Actual Daily Achieved</td><td class="unit">BCM</td><td class="num">{fmt(d["actual_daily"])}</td></tr>
            <tr><td colspan="3" style="height:12px; border:none;"></td></tr>

            <tr><td class="label">Monthly Available Days</td><td class="unit"></td><td class="num">{float(d["num_prod_days"] or 0):,.2f}</td></tr>
            <tr><td class="label">Worked Days</td><td class="unit"></td><td class="num">{float(d["num_prod_days_completed"] or 0):,.2f}</td></tr>
            <tr><td class="label">Days Left</td><td class="unit"></td><td class="num">{float(d["days_left"] or 0):,.2f}</td></tr>
            <tr><td colspan="3" style="height:12px; border:none;"></td></tr>

            <tr><td class="label bold">Forecast on Current Rate</td><td class="unit">BCM</td><td class="num">{fmt(d["forecast"])}</td></tr>
            <tr><td class="label bold">SHORT / OVER</td><td class="unit">BCM</td><td class="num">{color_num(d["short_over_forecast"])}</td></tr>
            <tr><td colspan="3" style="height:12px; border:none;"></td></tr>

            <tr><td class="label bold">Strip Ratio</td><td class="unit"></td><td class="num">{fmt(d["strip_ratio"], 1)}</td></tr>
        </table>
    </div>
    """