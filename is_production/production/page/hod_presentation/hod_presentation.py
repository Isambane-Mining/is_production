"""Desk Page entry point; the report remains the calculation and permission owner."""
import frappe

from is_production.production.report.hod_presentation import hod_presentation as report
from is_production.production.report.hod_presentation.hod_presentation import build_grid_data, render_grids


@frappe.whitelist()
def get_data(filters=None):
    report.check_report_access()
    filters = frappe.parse_json(filters) if isinstance(filters, str) else filters
    # Frappe Script Reports return columns/result plus optional metadata. The
    # empty HOD result has two fields; a populated result has five. Keep both.
    fields = ('columns', 'result', 'message', 'chart', 'report_summary', 'skip_total_row')
    result = dict(zip(fields, report.execute(filters or {})))
    if result.get('result'):
        module = report._load_daily_availability_module()
        for row in result['result']:
            fleet = build_grid_data(
                module, site=row['site'], start_date=row['start_date'], end_date=row['end_date'],
                machine_scope=report._dashboard_machine_scope(filters.get('machine_scope') or report.DEFAULT_MACHINE_SCOPE),
                au_target_filter=filters.get('au_target_filter') or report.DEFAULT_AU_TARGET_FILTER,
                asset_ownership=filters.get('asset_ownership') or report.DEFAULT_ASSET_OWNERSHIP,
            )
            row['engineering'] = build_dashboard(module, fleet, row['site'])
            row['engineering']['reliability'] = get_hod_reliability(
                site=row['site'],
                start_date=row['start_date'],
                end_date=row['end_date'],
                fleet=fleet,
            )
            row['engineering']['breakdown_summary'] = get_hod_breakdown_summary(
                site=row['site'],
                start_date=row['start_date'],
                end_date=row['end_date'],
                machines=fleet['machines'],
            )
            # Both exported slides share the exact same filtered Engine snapshot.
            row['fleet_summary_html'] = render_grids(fleet)
    return result


# HOD dashboard rankings and explanations
CATEGORY_LABELS = report.CATEGORY_LABELS
percentage_value = report.percentage_value

def recorded_explanation(records):
    """Without duration evidence, multiple explanations do not establish a main cause."""
    placeholders = {'', '-', '—', 'not recorded', 'no reason captured'}
    reasons = {str(row.get('reason') or '').strip() for row in records}
    reasons = {reason for reason in reasons if reason.lower() not in placeholders}
    return next(iter(reasons)) if len(reasons) == 1 else 'Not recorded'


def build_dashboard(module, fleet, site):
    machines = [row for row in fleet['machines'] if row['category'] in CATEGORY_LABELS]
    top, bottom, unavailable = {}, {}, {}
    for metric, reason_method in (
        ('avail', 'get_au_engine_breakdown_reasons'),
        ('util', 'get_au_engine_other_delay_reasons'),
    ):
        valid = [row for row in machines if percentage_value(row.get(metric)) is not None]
        unavailable[metric] = len(machines) - len(valid)
        top[metric] = {
            category: sorted(
                [row for row in valid if row['category'] == category],
                key=lambda row: (-percentage_value(row[metric]), str(row['machine'])),
            )[:5] for category in CATEGORY_LABELS
        }
        bottom[metric] = []
        get_reasons = getattr(module.au_engine, reason_method, None)

        # Bottom 10 is a maximum; exclude machines meeting the target.
        threshold = 85 if metric == 'avail' else 80
        below_target = [
            row for row in valid
            if percentage_value(row[metric]) < threshold
        ]

        for row in sorted(
            below_target,
            key=lambda row: (
                percentage_value(row[metric]),
                str(row['machine'])
            )
        )[:10]:
            reasons = get_reasons(row['machine'], fleet['start_date'], fleet['end_date'], location=site) if get_reasons else []
            bottom[metric].append({**row, 'explanation': recorded_explanation(reasons)})
    return {
        'category_averages': fleet['category_averages'], 'averages': fleet['averages'],
        'machine_count': len(machines), 'unavailable': unavailable,
        'fleet_counts': {
            category: len({
                row['machine']
                for row in machines
                if row['category'] == category
            })
            for category in ('Excavator', 'Dozer', 'ADT')
        },
        'top': top, 'bottom': bottom,
    }



def get_hod_breakdown_summary(site, start_date, end_date, machines):
    """Permission-filtered PBM totals for the existing HOD dashboard."""
    from collections import defaultdict
    from datetime import timedelta
    from frappe.utils import getdate, get_datetime, now_datetime

    report.check_hod_site_access(site)

    start = get_datetime(f"{getdate(start_date)} 00:00:00")
    end = get_datetime(
        f"{getdate(end_date) + timedelta(days=1)} 00:00:00"
    )

    if end <= start:
        frappe.throw("Invalid HOD reporting period.")

    machine_names = sorted({
        str(machine.get("machine") or "").strip()
        for machine in machines
        if machine.get("machine")
    })

    empty = {"top_machines": [], "classifications": []}

    if not machine_names:
        return empty

    # get_list applies the current user's PBM record permissions.
    allowed_names = frappe.get_list(
        "Plant Breakdown or Maintenance",
        filters={
            "location": site,
            "asset_name": ["in", machine_names],
            "exclude_from_au": 0,
            "breakdown_start_datetime": ["<", end],
        },
        pluck="name",
        limit_page_length=0,
    )

    if not allowed_names:
        return empty

    rows = frappe.db.sql(
        """
        SELECT
            name,
            asset_name,
            asset_category,
            breakdown_reason,
            failure_classification,
            downtime_type,
            breakdown_start_datetime,
            resolved_datetime
        FROM `tabPlant Breakdown or Maintenance`
        WHERE name IN %(allowed_names)s
          AND location = %(site)s
          AND breakdown_start_datetime IS NOT NULL
          AND breakdown_start_datetime < %(end)s
          AND (
              resolved_datetime IS NULL
              OR resolved_datetime > %(start)s
          )
        ORDER BY name
        """,
        {
            "allowed_names": tuple(allowed_names),
            "site": site,
            "start": start,
            "end": end,
        },
        as_dict=True,
    )

    current_time = now_datetime()
    machine_totals = defaultdict(float)
    classification_totals = defaultdict(float)
    seen_intervals = set()

    for row in rows:
        actual_start = get_datetime(
            row["breakdown_start_datetime"]
        )
        actual_end = (
            get_datetime(row["resolved_datetime"])
            if row.get("resolved_datetime")
            else current_time
        )

        clipped_start = max(start, actual_start)
        clipped_end = min(end, actual_end)

        if clipped_end <= clipped_start:
            continue

        fleet = str(row.get("asset_name") or "").strip()
        category = str(row.get("asset_category") or "").strip()
        downtime_type = str(
            row.get("downtime_type") or ""
        ).strip()

        if downtime_type.casefold() == "planned maintenance":
            classification = "Planned Maintenance"
        else:
            classification = (
                str(row.get("failure_classification") or "").strip()
                or "Unclassified"
            )
        reason = (
            str(row.get("breakdown_reason") or "").strip()
            or "No reason captured"
        )

        # Do not count an identical machine/failure/time interval twice.
        interval_key = (
            fleet, actual_start, actual_end,
            classification, reason,
        )
        if interval_key in seen_intervals:
            continue
        seen_intervals.add(interval_key)

        hours = (clipped_end - clipped_start).total_seconds() / 3600

        if hours <= 0:
            continue

        machine_totals[
            (fleet, category, classification, reason)
        ] += hours

        classification_totals[classification] += hours

    top_machines = [
        {
            "fleet": fleet,
            "category": category,
            "classification": classification,
            "reason": reason,
            "hours": round(hours, 2),
        }
        for (fleet, category, classification, reason), hours
        in machine_totals.items()
    ]

    top_machines.sort(
        key=lambda row: (
            -row["hours"],
            row["fleet"],
            row["classification"],
            row["reason"],
        )
    )

    # The classification graph follows the exact Bottom 10 table.
    bottom_ten_classifications = defaultdict(float)

    for machine in top_machines[:10]:
        bottom_ten_classifications[machine["classification"]] += (
            float(machine["hours"])
        )

    classifications = [
        {"classification": name, "hours": round(hours, 2)}
        for name, hours in bottom_ten_classifications.items()
    ]
    classifications.sort(
        key=lambda row: (-row["hours"], row["classification"])
    )

    return {
        "top_machines": top_machines[:10],
        "classifications": classifications,
    }



def get_hod_reliability(site, start_date, end_date, fleet):
    """MTBF and full-restoration MTTR for the selected HOD fleet."""
    from collections import defaultdict
    from datetime import timedelta
    from frappe.utils import getdate, get_datetime

    report.check_hod_site_access(site)

    period_start = get_datetime(
        f"{getdate(start_date)} 00:00:00"
    )
    period_end = get_datetime(
        f"{getdate(end_date) + timedelta(days=1)} 00:00:00"
    )

    if period_end <= period_start:
        frappe.throw("Invalid HOD reporting period.")

    categories = ("Excavator", "Dozer", "ADT")

    result = {
        category: {"mtbf": None, "mttr": None, "downtime": None, "planned": {"mtbf": None, "mttr": None, "utilisation": None, "downtime": None}}
        for category in categories
    }

    machine_categories = {
        str(row.get("machine")): row.get("category")
        for row in fleet["machines"]
        if row.get("machine") and row.get("category") in categories
    }

    if not machine_categories:
        return result

    # Permission-filtered Frappe query. No unrestricted SQL.
    records = frappe.get_list(
        "Plant Breakdown or Maintenance",
        filters=[
            ["location", "=", site],
            ["asset_name", "in", list(machine_categories)],
            ["exclude_from_au", "=", 0],
            ["breakdown_start_datetime", ">=", period_start],
            ["breakdown_start_datetime", "<", period_end],
        ],
        fields=[
            "name",
            "asset_name",
            "downtime_type",
            "breakdown_start_datetime",
            "resolved_datetime",
        ],
        limit_page_length=0,
    )

    failures = defaultdict(int)
    completed_repairs = defaultdict(int)
    repair_hours = defaultdict(float)
    planned_events = defaultdict(int)
    planned_repairs = defaultdict(int)
    planned_repair_hours = defaultdict(float)
    seen = set()

    for row in records:
        if row.name in seen:
            continue
        seen.add(row.name)

        category = machine_categories.get(row.asset_name)
        if category not in categories:
            continue

        downtime_type = str(
            row.get("downtime_type") or ""
        ).strip().casefold()

        breakdown_start = row.get(
            "breakdown_start_datetime"
        )

        if not breakdown_start:
            continue

        breakdown_start = get_datetime(breakdown_start)

        if downtime_type == "planned maintenance":
            planned_events[category] += 1
            service_end = row.get("resolved_datetime")
            if service_end:
                service_end = get_datetime(service_end)
                if breakdown_start < service_end <= period_end:
                    planned_repairs[category] += 1
                    planned_repair_hours[category] += (
                        service_end - breakdown_start
                    ).total_seconds() / 3600
            continue

        # Count each qualifying PBM failure event once.
        failures[category] += 1

        resolved = row.get("resolved_datetime")

        if not resolved:
            continue

        resolved = get_datetime(resolved)

        # MTTR uses only completed repairs within the period.
        if not breakdown_start < resolved <= period_end:
            continue

        duration = (
            resolved - breakdown_start
        ).total_seconds() / 3600

        if duration <= 0:
            continue

        completed_repairs[category] += 1
        repair_hours[category] += duration

    work_hours = fleet.get("reliability_work_hours") or {}
    downtime_hours = fleet.get("reliability_downtime_hours") or {}

    for category in categories:
        hours = float(work_hours.get(category) or 0)

        if hours > 0 and planned_events[category] > 0:
            result[category]["planned"]["mtbf"] = round(
                hours / planned_events[category], 1
            )

        category_downtime = downtime_hours.get(category) or {}
        required = float(category_downtime.get("required") or 0)
        breakdown = float(category_downtime.get("breakdown") or 0)
        planned = float(category_downtime.get("planned") or 0)

        # HOD Downtime card only: exclude planned maintenance
        # from the required-hours denominator.
        adjusted_required = required - planned
        result[category]["adjusted_utilisation"] = (
            round(
                float(category_downtime.get("work") or 0)
                / adjusted_required * 100,
                1,
            )
            if adjusted_required > 0 else None
        )

        result[category]["planned"]["hours"] = round(planned, 1)
        planned_work = float(category_downtime.get("work") or 0)

        adjusted_required = required - planned

        if adjusted_required > 0:
            result[category]["planned"]["utilisation"] = round(
                planned_work / adjusted_required * 100, 1
            )

        if required > 0:
            result[category]["planned"]["downtime"] = round(
                planned / required * 100, 2
            )

        if planned_repairs[category] > 0:
            result[category]["planned"]["mttr"] = round(
                planned_repair_hours[category]
                / planned_repairs[category],
                1,
            )

        if required > 0:
            result[category]["downtime"] = round(
                breakdown / required * 100, 1
            )

        if hours > 0 and failures[category] > 0:
            result[category]["mtbf"] = round(
                hours / failures[category], 1
            )

        if completed_repairs[category] > 0:
            result[category]["mttr"] = round(
                repair_hours[category]
                / completed_repairs[category],
                1,
            )

    return result


@frappe.whitelist()
def get_hod_breakdown_details(machine, site, from_date, to_date):
    """Read authorised PBM details using the existing Month End calculations."""
    from frappe.utils import getdate
    from engineering.engineering.report.availability_and_utilisation_month_end_report import (
        availability_and_utilisation_month_end_report as month_end,
    )

    report.check_report_access()

    machine = str(machine or "").strip()
    site = str(site or "").strip()

    if not machine or not site:
        frappe.throw("Machine and site are required.")

    start = getdate(from_date)
    end = getdate(to_date)

    if start > end:
        frappe.throw("Invalid date range.")

    report.check_hod_site_access(site)

    # Only records the current user is permitted to read.
    names = frappe.get_list(
        "Plant Breakdown or Maintenance",
        filters={
            "asset_name": machine,
            "location": site,
        },
        pluck="name",
        limit_page_length=0,
    )

    if not names:
        return {"rows": []}

    filters = frappe._dict({
        "from_date": str(start),
        "to_date": str(end),
        "location": site,
    })

    details = month_end.get_plant_breakdown_reason_details(
        filters,
        [machine],
        allowed_names=names,
    ).get(machine) or []

    if hasattr(month_end, "clean_reason_details"):
        details = month_end.clean_reason_details(details)

    return {"rows": details}
