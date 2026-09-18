# Copyright (c) 2025, Isambane Mining (Pty) Ltd
# For license information, please see license.txt

import frappe
from frappe.utils import flt, format_date, getdate
from datetime import datetime
from is_production.production.report.daily_reporting import daily_reporting


def execute(filters=None):
    if not filters:
        filters = {}

    site = filters.get("site")
    end_date = filters.get("end_date")
    monthly_production = filters.get("monthly_production")
    formatted_date = format_date(end_date, "dd/MM/yyyy") if end_date else ""

    # Always respect the Monthly Production Planning document selected
    # by the user on the Production Dashboard.
    #
    # Only fall back to date-based lookup when the report is opened
    # directly without a selected monthly_production filter.
    if (
        monthly_production
        and frappe.db.exists(
            "Monthly Production Planning",
            monthly_production,
        )
    ):
        mpp = frappe.get_doc(
            "Monthly Production Planning",
            monthly_production,
        )
    else:
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

        # --------------------------------------------------------
        # SELECTED-END-DATE PRODUCTION HOURS
        #
        # Production day:
        #   Day   06:00 -> 18:00
        #   Night 18:00 -> 06:00 next calendar morning
        #
        # The Monthly Production Days row belongs to its
        # shift_start_date. Selecting 15 September therefore
        # includes the complete 15 September production day,
        # but excludes the row dated 16 September.
        #
        # 18 planned hours = 1.00 production day.
        # --------------------------------------------------------

        monthly_available_days = float(mpp.num_prod_days or 0)
        total_planned_hours = monthly_available_days * 18.0

        worked_hours = 0.0

        if month_start and end_date:
            selected_rows = frappe.get_all(
                "Monthly Production Days",
                filters={
                    "parent": mpp.name,
                    "shift_start_date": [
                        "between",
                        [month_start, getdate(end_date)]
                    ],
                },
                fields=[
                    "shift_start_date",
                    "shift_day_hours",
                    "shift_night_hours",
                    "shift_morning_hours",
                    "shift_afternoon_hours",
                ],
                order_by="shift_start_date asc",
            )

            for r in selected_rows:
                worked_hours += flt(r.get("shift_day_hours"))
                worked_hours += flt(r.get("shift_night_hours"))
                worked_hours += flt(r.get("shift_morning_hours"))
                worked_hours += flt(r.get("shift_afternoon_hours"))

        if total_planned_hours:
            worked_hours = min(worked_hours, total_planned_hours)

        worked_days = worked_hours / 18.0 if worked_hours else 0.0
        remaining_hours = max(total_planned_hours - worked_hours, 0.0)
        remaining_days = remaining_hours / 18.0

        data["num_prod_days_completed"] = worked_days
        data["month_remaining_prod_days"] = remaining_days

        # MTD Actual BCM respects the user-selected production day.
        #
        # Hourly Production.prod_date represents the complete production
        # day, including the Night shift ending at 06:00 next morning.
        # Same MTD Actual BCM source as Daily & Shift Report.
        #
        # This is the source used by the Summary section:
        # get_actual_bcms_for_date(site, end_date, month_start)
        #
        # It respects the selected production date and keeps the
        # Weekly Report aligned with Daily & Shift Report.
        mtd_actual_bcms = daily_reporting.get_actual_bcms_for_date(
            site,
            getdate(end_date),
            month_start,
        )

        # --------------------------------------------------------
        # ACTUAL PRODUCTION THROUGH SELECTED PRODUCTION DATE
        # --------------------------------------------------------
        #
        # Actual BCM:
        #   Truck & Shovel + Dozing from Hourly Production.
        #
        # Actual Coal:
        #   Survey / actual coal production through selected date.
        #
        # Actual Waste:
        #   Actual BCM - Actual Coal BCM
        #
        # Coal conversion:
        #   1.5 tons = 1 BCM
        # --------------------------------------------------------

        mtd_prog_actual_coal = get_mtd_coal_dynamic(
            site,
            getdate(end_date),
            month_start,
        )

        actual_coal_bcm = (
            mtd_prog_actual_coal / 1.5
            if mtd_prog_actual_coal
            else 0
        )

        mtd_prog_actual_waste = (
            mtd_actual_bcms - actual_coal_bcm
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

        # --------------------------------------------------------
        # PROGRESS TARGETS - HOURS BASED
        #
        # MTD Target Waste:
        #   Monthly Waste Target / Total Planned Hours * Worked Hours
        #
        # MTD Target Coal:
        #   Monthly Coal Target / Total Planned Hours * Worked Hours
        # --------------------------------------------------------

        # Same progress-target formulas as Production Summary.
        data["mtd_prog_target_waste"] = (
            (
                data["waste_bcms_planned"]
                / data["num_prod_days"]
            )
            * data["num_prod_days_completed"]
            if data["num_prod_days"]
            else 0
        )

        data["short_over_waste"] = (
            data["mtd_prog_target_waste"]
            - data["mtd_prog_actual_waste"]
        )

        data["mtd_prog_target_coal"] = (
            (
                data["coal_tons_planned"]
                / data["num_prod_days"]
            )
            * data["num_prod_days_completed"]
            if data["num_prod_days"]
            else 0
        )

        data["short_over_coal"] = (
            data["mtd_prog_target_coal"]
            - data["mtd_prog_actual_coal"]
        )

        data["remaining_volume"] = data["monthly_target"] - data["mtd_actual_bcms"]
        data["daily_required"] = data["remaining_volume"] / max((data["month_remaining_prod_days"], 1))

        # Same Forecast source as Production Summary.
        data["forecast"] = flt(mpp.month_forecated_bcm)

        data["days_left"] = remaining_days

        data["short_over_forecast"] = (
            data["monthly_target"]
            - data["forecast"]
        )

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


def get_mtd_actual_bcms_from_days(parent_name, month_start, end_date):
    """
    Same source used by Production Summary.

    Sum Monthly Production Days.total_daily_bcms from the
    monthly plan start through the selected production date.
    """
    if not parent_name or not month_start or not end_date:
        return 0

    rows = frappe.get_all(
        "Monthly Production Days",
        filters={
            "parent": parent_name,
            "shift_start_date": [
                "between",
                [month_start, end_date],
            ],
        },
        fields=["total_daily_bcms"],
        order_by="shift_start_date asc",
    )

    return sum(
        flt(row.get("total_daily_bcms"))
        for row in rows
    )


def get_mtd_actual_bcm(site, start_date, end_date):
    """
    Return Truck and Shovel plus Dozing BCM from the monthly
    production start through the selected production day.

    Hourly Production.prod_date is the production-day key.
    A selected prod_date includes its complete Day and Night shift
    and excludes the following production day.
    """
    if not site or not start_date or not end_date:
        return 0

    result = frappe.db.sql(
        """
        SELECT
            COALESCE(
                SUM(
                    COALESCE(total_ts_bcm, 0)
                    + COALESCE(total_dozing_bcm, 0)
                ),
                0
            ) AS total_bcm
        FROM `tabHourly Production`
        WHERE location = %s
          AND prod_date BETWEEN %s AND %s
        """,
        (site, start_date, end_date),
        as_dict=True,
    )

    return flt(result[0].total_bcm) if result else 0

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

            <tr><td class="label">Monthly Available Days</td><td class="unit"></td><td class="num">{flt(d['num_prod_days']):,.1f}</td></tr>
            <tr><td class="label">Worked Days</td><td class="unit"></td><td class="num">{flt(d['num_prod_days_completed']):,.1f}</td></tr>
            <tr><td class="label">Days Left</td><td class="unit"></td><td class="num">{flt(d['days_left']):,.1f}</td></tr>
            <tr><td colspan="3" style="height:12px; border:none;"></td></tr>

            <tr><td class="label bold">Forecast on Current Rate</td><td class="unit">BCM</td><td class="num">{fmt(d["forecast"])}</td></tr>
            <tr><td class="label bold">SHORT / OVER</td><td class="unit">BCM</td><td class="num">{color_num(d["short_over_forecast"])}</td></tr>
            <tr><td colspan="3" style="height:12px; border:none;"></td></tr>

            <tr><td class="label bold">Strip Ratio</td><td class="unit"></td><td class="num">{fmt(d["strip_ratio"], 1)}</td></tr>
        </table>
    </div>
    """