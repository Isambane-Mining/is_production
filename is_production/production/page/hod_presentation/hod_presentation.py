"""Desk Page entry point; the report remains the calculation and permission owner."""
import frappe

from is_production.production.report.hod_presentation import hod_presentation as report
from is_production.production.report.hod_presentation.hod_presentation import build_grid_data, render_grids


# HOD_HR_STATS_V3_DYNAMIC_GENDERS
def get_hod_hr_stats(site, start_date, end_date):
    """Return active headcount and period activity for every recorded gender."""
    report.check_hod_site_access(site)

    records = frappe.db.sql(
        """
        SELECT
            COALESCE(NULLIF(TRIM(gender), ''), 'Not specified') AS gender,
            SUM(CASE WHEN status = 'Active' THEN 1 ELSE 0 END)
                AS total_employees,
            SUM(
                CASE WHEN relieving_date BETWEEN %(start)s AND %(end)s
                THEN 1 ELSE 0 END
            ) AS terminations,
            SUM(
                CASE WHEN date_of_joining BETWEEN %(start)s AND %(end)s
                THEN 1 ELSE 0 END
            ) AS recruitment
        FROM `tabEmployee`
        WHERE branch = %(site)s
        GROUP BY COALESCE(NULLIF(TRIM(gender), ''), 'Not specified')
        ORDER BY gender
        """,
        {
            "site": site,
            "start": start_date,
            "end": end_date,
        },
        as_dict=True,
    )

    categories = (
        "total_employees",
        "terminations",
        "recruitment",
    )

    stats = {
        category: {"total": 0, "genders": []}
        for category in categories
    }

    for record in records:
        gender = str(record.get("gender") or "Not specified")

        for category in categories:
            count = int(record.get(category) or 0)
            stats[category]["total"] += count

            if count:
                stats[category]["genders"].append({
                    "label": gender,
                    "count": count,
                })

    # Same branch/status population as BCM/Man.
    stats["total_employees"]["total"] = frappe.db.count(
        "Employee",
        filters={
            "branch": site,
            "status": "Active",
        },
    )

    return stats


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
            count_plan = report.get_latest_hod_monthly_plan(
                row['site'], row['end_date']
            )
            if not count_plan:
                frappe.throw(
                    "No Monthly Production Planning record found "
                    "for the selected site and End Date."
                )

            excluded_keys = get_hod_excluded_machine_keys(count_plan)
            row['engineering'] = build_dashboard(
                module, fleet, row['site'],
                excluded_machine_keys=excluded_keys,
            )
            # Excluded machines must not contribute to the
            # HOD downtime rankings or contributor summaries.
            eligible_downtime_machines = [
                machine
                for machine in fleet['machines']
                if str(machine.get('machine') or '').strip().casefold()
                not in excluded_keys
            ]

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
                machines=eligible_downtime_machines,
            )
            # Production graph: entire monthly planning period
            # through the selected HOD End Date.
            monthly_plan = report.get_latest_hod_monthly_plan(
                row['site'], row['end_date']
            )

            if not monthly_plan:
                frappe.throw(
                    "No Monthly Production Planning record found "
                    "for the selected site and End Date."
                )

            row['engineering']['downtime_contributors'] = (
                get_hod_breakdown_summary(
                    site=row['site'],
                    start_date=monthly_plan.prod_month_start_date,
                    end_date=row['end_date'],
                    machines=eligible_downtime_machines,
                    all_classifications=True,
                )
            )

            # Engineering Bottom 10 uses effective A&U lost hours.
            effective_summary = row["engineering"][
                "downtime_contributors"
            ]

            breakdown_summary = (
                row["engineering"].get("breakdown_summary")
                or {}
            )

            breakdown_summary["top_machines"] = (
                effective_summary["top_machines"]
            )

            # HOD_SPARE_DOWNTIME_HIGHLIGHT_V1
            # Display-only classification for the downtime table.
            spare_machine_keys = {
                str(machine.get("machine") or "").strip().casefold()
                for machine in eligible_downtime_machines
                if machine.get("is_spare")
            }

            for machine in breakdown_summary.get("top_machines") or []:
                machine["is_spare"] = (
                    str(machine.get("fleet") or "").strip().casefold()
                    in spare_machine_keys
                )

            row["engineering"]["breakdown_summary"] = (
                breakdown_summary
            )

            # Both exported slides share the exact same filtered Engine snapshot.

            # HOD_BCM_PER_MAN_V1
            # Count currently active employees assigned to site Branch.
            active_employees = frappe.db.count(
                "Employee",
                filters={
                    "branch": row["site"],
                    "status": "Active",
                },
            )

            row["active_employee_count"] = active_employees

            row["bcm_per_man"] = (
                round(
                    frappe.utils.flt(row.get("actual_bcm"))
                    / active_employees,
                    1,
                )
                if active_employees > 0
                else None
            )

            # HOD_HR_STATS_V1: site and reporting-period statistics.
            row["hr_stats"] = get_hod_hr_stats(
                site=row["site"],
                start_date=row["start_date"],
                end_date=row["end_date"],
            )

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


def get_hod_excluded_machine_keys(monthly_plan):
    """Machine identifiers excluded by the selected monthly plan."""
    asset_ids = sorted({
        str(row.asset or "").strip()
        for row in monthly_plan.get("excluded_production_machines") or []
        if row.asset
    })

    if not asset_ids:
        return set()

    keys = {asset.casefold() for asset in asset_ids}

    assets = frappe.get_all(
        "Asset",
        filters={"name": ["in", asset_ids]},
        fields=["name", "asset_name"],
        limit_page_length=0,
    )

    for asset in assets:
        for value in (asset.name, asset.asset_name):
            if value:
                keys.add(str(value).strip().casefold())

    return keys


def build_dashboard(module, fleet, site, excluded_machine_keys=None):
    # Excluded machines must not enter HOD rankings.
    excluded_machine_keys = excluded_machine_keys or set()

    machines = [
        row for row in fleet['machines']
        if row['category'] in CATEGORY_LABELS
        and str(row.get('machine') or '').strip().casefold()
        not in excluded_machine_keys
    ]

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
    excluded_machine_keys = excluded_machine_keys or set()
    count_machines = [
        row for row in machines
        if str(row.get('machine') or '').strip().casefold()
        not in excluded_machine_keys
    ]

    return {
        'category_averages': fleet['category_averages'], 'averages': fleet['averages'],
        'machine_count': len(machines), 'unavailable': unavailable,
        'fleet_counts': {
            category: len({
                row['machine']
                for row in count_machines
                if row['category'] == category
            })
            for category in ('Excavator', 'Dozer', 'ADT')
        },
        'fleet_scope_counts': {
            scope: {
                category: len({
                    str(row['machine'])
                    for row in count_machines
                    if row['category'] == category
                    and bool(row.get('is_spare')) == (scope == 'swing')
                })
                for category in ('Excavator', 'Dozer', 'ADT')
            }
            for scope in ('production', 'swing')
        },
        'top': top, 'bottom': bottom,
    }




def _get_hod_effective_downtime_summary(site, start_date, end_date, machines):
    """Production graph: effective downtime using A&U shift rules."""
    from collections import defaultdict
    from datetime import timedelta
    from frappe.utils import (
        add_days, flt, getdate, get_datetime, now_datetime
    )
    from engineering.engineering.doctype.availability_and_utilisation import (
        availability_and_utilisation as au
    )

    report.check_hod_site_access(site)

    start = get_datetime(f"{getdate(start_date)} 06:00:00")
    end = get_datetime(
        f"{add_days(getdate(end_date), 1)} 06:00:00"
    )
    if end <= start:
        frappe.throw("Invalid HOD downtime reporting period.")

    machine_names = sorted({
        str(m.get("machine") or "").strip()
        for m in machines
        if m.get("machine")
    })
    empty = {"top_machines": [], "classifications": []}
    if not machine_names:
        return empty

    # Apply PBM document permissions before reading breakdown data.
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
        SELECT name, asset_name, asset_category, breakdown_reason,
               failure_classification, downtime_type,
               breakdown_start_datetime, resolved_datetime
        FROM `tabPlant Breakdown or Maintenance`
        WHERE name IN %(names)s
          AND location = %(site)s
          AND IFNULL(breakdown_reason, '') != ''
          AND breakdown_start_datetime < %(end)s
          AND (
              resolved_datetime IS NULL
              OR resolved_datetime > %(start)s
          )
        ORDER BY breakdown_start_datetime, name
        """,
        {
            "names": tuple(allowed_names),
            "site": site,
            "start": start,
            "end": end,
        },
        as_dict=True,
    )

    # Load shift records once, matching the optimised A&U engine.
    au_rows = frappe.db.sql(
        """
        SELECT asset_name, location, shift_date, shift,
               shift_system, shift_required_hours
        FROM `tabAvailability and Utilisation`
        WHERE asset_name IN %(machines)s
          AND location = %(site)s
          AND shift_date >= DATE(%(start)s) - INTERVAL 1 DAY
          AND shift_date <= DATE(%(end)s) + INTERVAL 1 DAY
        ORDER BY asset_name, location, shift_date,
                 FIELD(shift, 'Day', 'Morning',
                       'Afternoon', 'Night')
        """,
        {
            "machines": tuple(machine_names),
            "site": site,
            "start": start,
            "end": end,
        },
        as_dict=True,
    )

    shifts_by_machine = defaultdict(list)
    for shift_row in au_rows:
        shifts_by_machine[
            (str(shift_row.asset_name or "").strip(), site)
        ].append(shift_row)

    exclusion_cache = {}

    def overlap(a_start, a_end, b_start, b_end):
        return max(
            (min(a_end, b_end) - max(a_start, b_start))
            .total_seconds() / 3600.0,
            0.0,
        )

    def shift_exclusion_windows(shift_row, shift_start, shift_end):
        key = (
            site, shift_row.shift,
            str(shift_start), str(shift_end)
        )
        if key not in exclusion_cache:
            exclusion_cache[key] = list(
                au._exclusion_windows(
                    site, shift_row.shift, shift_start, shift_end
                )
            )
        return exclusion_cache[key]

    def calculate_segment(machine, segment_start, segment_end):
        shift_results = {}

        for shift_row in shifts_by_machine.get((machine, site), []):
            shift_date = getdate(shift_row.shift_date)

            if (
                shift_date < getdate(segment_start) - timedelta(days=1)
                or shift_date > getdate(segment_end) + timedelta(days=1)
            ):
                continue

            shift_start, shift_end = au.get_shift_timings(
                shift_row.shift_system,
                shift_row.shift,
                str(shift_row.shift_date),
            )
            if not shift_start or not shift_end:
                continue

            raw = overlap(
                segment_start, segment_end, shift_start, shift_end
            )
            if raw <= 0:
                continue

            excluded = sum(
                overlap(
                    segment_start, segment_end,
                    window_start, window_end
                )
                for window_start, window_end in
                shift_exclusion_windows(
                    shift_row, shift_start, shift_end
                )
            )
            excluded = min(excluded, raw)
            valid = max(raw - excluded, 0.0)

            required = max(flt(shift_row.shift_required_hours), 0.0)
            sunday = (
                valid
                if required <= 0 and shift_date.weekday() == 6
                else 0.0
            )
            effective = (
                min(valid, required) if required > 0 else 0.0
            )

            key = (str(shift_date), shift_row.shift)
            bucket = shift_results.setdefault(
                key,
                {
                    "total_minutes": 0.0,
                    "startup_fatigue_minutes": 0.0,
                    "sunday_minutes": 0.0,
                    "au_minutes": 0.0,
                },
            )
            bucket["total_minutes"] += raw * 60
            bucket["startup_fatigue_minutes"] += excluded * 60
            bucket["sunday_minutes"] += sunday * 60
            bucket["au_minutes"] += effective * 60

        # Match Engine largest-remainder allocation, separately
        # for each measured field.
        rounded = {
            key: {field: 0 for field in (
                "total_minutes", "startup_fatigue_minutes",
                "sunday_minutes", "au_minutes"
            )}
            for key in shift_results
        }

        for field in (
            "total_minutes", "startup_fatigue_minutes",
            "sunday_minutes", "au_minutes"
        ):
            parts = []
            total_exact = 0.0

            for key, values in shift_results.items():
                exact = max(float(values[field]), 0.0)
                whole = int(exact)
                parts.append([key, whole, exact - whole])
                total_exact += exact

            target = int(round(total_exact))
            extras = max(target - sum(p[1] for p in parts), 0)
            parts.sort(key=lambda part: (-part[2], str(part[0])))

            for part in parts[:extras]:
                part[1] += 1

            for key, whole, remainder in parts:
                rounded[key][field] = whole

        return {
            field: sum(values[field] for values in rounded.values())
            for field in (
                "total_minutes", "startup_fatigue_minutes",
                "sunday_minutes", "au_minutes"
            )
        }

    totals = defaultdict(int)
    details = defaultdict(list)
    seen_intervals = set()
    seen_segments = set()
    now = now_datetime()

    for row in rows:
        machine = str(row.asset_name or "").strip()
        actual_start = get_datetime(row.breakdown_start_datetime)
        actual_end = (
            get_datetime(row.resolved_datetime)
            if row.resolved_datetime else now
        )

        clipped_start = max(start, actual_start)
        clipped_end = min(end, actual_end)

        if clipped_end <= clipped_start:
            continue

        interval_key = (
            machine, site,
            str(clipped_start), str(clipped_end)
        )
        if interval_key in seen_intervals:
            continue
        seen_intervals.add(interval_key)

        classification = (
            "Planned Maintenance"
            if str(row.downtime_type or "").strip().casefold()
            == "planned maintenance"
            else str(row.failure_classification or "").strip()
            or "Unclassified"
        )

        current_date = getdate(clipped_start)
        if clipped_start < get_datetime(
            f"{current_date} 06:00:00"
        ):
            current_date -= timedelta(days=1)

        while True:
            day_start = get_datetime(
                f"{current_date} 06:00:00"
            )
            day_end = day_start + timedelta(days=1)

            if day_start >= clipped_end:
                break

            segment_start = max(clipped_start, day_start)
            segment_end = min(clipped_end, day_end)

            if segment_end > segment_start:
                segment_key = (
                    machine, site, str(current_date),
                    str(segment_start)[:16],
                    str(segment_end)[:16],
                )

                if segment_key not in seen_segments:
                    seen_segments.add(segment_key)

                    values = calculate_segment(
                        machine, segment_start, segment_end
                    )

                    for field, minutes in values.items():
                        totals[(classification, field)] += minutes

                    if any(values.values()):
                        details[classification].append({
                            "plant_no": machine,
                            "asset_category": str(
                                row.asset_category or ""
                            ).strip(),
                            "start_datetime": str(segment_start)[:16],
                            "resolved_datetime": str(segment_end)[:16],
                            "reason": str(
                                row.breakdown_reason or ""
                            ).strip(),
                            **values,
                        })

            current_date += timedelta(days=1)

    categories = sorted(
        {
            classification
            for classification, field in totals
            if field == "au_minutes"
            and totals[(classification, field)] > 0
        },
        key=lambda classification: (
            -totals[(classification, "au_minutes")],
            classification,
        ),
    )

    classifications = []

    for classification in categories:
        minutes = totals[(classification, "au_minutes")]
        classifications.append({
            "classification": classification,
            "hours": round(minutes / 60.0, 3),
            "au_minutes": minutes,
            "total_minutes": totals[
                (classification, "total_minutes")
            ],
            "startup_fatigue_minutes": totals[
                (classification, "startup_fatigue_minutes")
            ],
            "sunday_minutes": totals[
                (classification, "sunday_minutes")
            ],
            "details": sorted(
                details[classification],
                key=lambda item: (
                    item["plant_no"],
                    item["start_datetime"],
                    item["reason"],
                ),
            ),
        })

    # HOD_BOTTOM10_EFFECTIVE_AU_V1
    # Rank Engineering Bottom 10 using the same verified
    # effective A&U minutes as the Production popup.
    machine_totals = defaultdict(int)

    for classification, entries in details.items():
        for entry in entries:
            effective_minutes = int(
                entry.get("au_minutes") or 0
            )

            if effective_minutes <= 0:
                continue

            key = (
                entry["plant_no"],
                entry["asset_category"],
                classification,
                entry["reason"],
            )

            machine_totals[key] += effective_minutes

    top_machines = [
        {
            "fleet": fleet,
            "category": category,
            "classification": classification,
            "reason": reason,
            "hours": round(minutes / 60.0, 1),
        }
        for (
            fleet, category, classification, reason
        ), minutes in machine_totals.items()
    ]

    top_machines.sort(
        key=lambda item: (
            -item["hours"],
            item["fleet"],
            item["classification"],
            item["reason"],
        )
    )

    return {
        "top_machines": top_machines[:10],
        "classifications": classifications,
    }

def get_hod_breakdown_summary(site, start_date, end_date, machines, all_classifications=False):
    """Permission-filtered PBM totals for the existing HOD dashboard."""
    from collections import defaultdict
    from datetime import timedelta
    from frappe.utils import getdate, get_datetime, now_datetime

    report.check_hod_site_access(site)

    if all_classifications:
        return _get_hod_effective_downtime_summary(
            site, start_date, end_date, machines
        )


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
    detail_entries = defaultdict(list)
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

        if all_classifications:
            detail_entries[classification].append({
                "plant_no": fleet,
                "reason": reason,
                "start_time": actual_start.strftime("%d-%m-%Y %H:%M"),
                "back_in_production": (
                    get_datetime(row["resolved_datetime"]).strftime(
                        "%d-%m-%Y %H:%M"
                    )
                    if row.get("resolved_datetime")
                    else ""
                ),
                "hours": round(hours, 2),
            })

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

    # The Production graph includes all classified downtime hours.
    # The Engineering table retains its existing Bottom 10 aggregation.
    classification_source = (
        classification_totals
        if all_classifications
        else bottom_ten_classifications
    )

    classifications = [
        {"classification": name, "hours": round(hours, 2)}
        for name, hours in classification_source.items()
    ]
    classifications.sort(
        key=lambda row: (-row["hours"], row["classification"])
    )

    # Detail records follow the same permission, period, fleet
    # and duplicate filtering as classification_totals.
    if all_classifications:
        for item in classifications:
            item["details"] = sorted(
                detail_entries[item["classification"]],
                key=lambda detail: (
                    -detail["hours"],
                    detail["plant_no"],
                    detail["start_time"],
                    detail["reason"],
                ),
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
