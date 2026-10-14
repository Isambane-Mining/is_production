"""Read-only MPP targets, stored production, and PBM-backed capacity estimates."""

from collections import defaultdict
from datetime import datetime, time, timedelta
from math import isfinite

import frappe
from frappe.utils import get_datetime, getdate, now_datetime

from engineering.engineering.page.adt_hourly_productivity import adt_hourly_productivity as adt
from is_production.production.controllers.production_summary_periods import source_hour


def _roster(plan):
    """Persisted pairs prove production assignment; unpaired identities remain unknown."""
    teams = defaultdict(set)
    listed = set()
    for row in (plan or {}).get("excavator_truck_assignments", []):
        excavator, truck = row.get("excavator"), row.get("truck")
        if excavator:
            listed.add(excavator)
            if truck:
                teams[excavator].add(truck)
    return teams, listed - set(teams)


def _intervals(breakdowns, assets, start, end):
    aliases = {alias: row["name"] for row in assets
               for alias in (row["name"], row.get("asset_name")) if alias}
    intervals = defaultdict(list)
    for row in breakdowns:
        asset = aliases.get(row.get("asset_name"))
        if not asset or not row.get("breakdown_start_datetime"):
            continue
        left = max(start, get_datetime(row["breakdown_start_datetime"]))
        right = min(end, get_datetime(row["resolved_datetime"]) if row.get("resolved_datetime") else end)
        if right > left:
            intervals[asset].append((left, right))
    return intervals


def _capacity_exposure(excavator, trucks, intervals, start, end):
    """Integrate exc-down OR fractional assigned-truck downtime, without overlap."""
    relevant = {asset: [(max(start, a), min(end, b)) for a, b in intervals[asset]
                        if a < end and b > start] for asset in {excavator, *trucks}}
    boundaries = sorted({start, end, *(t for rows in relevant.values() for pair in rows for t in pair)})
    exposure = 0.0
    for left, right in zip(boundaries, boundaries[1:]):
        down = {asset for asset, rows in relevant.items() if any(a <= left < b for a, b in rows)}
        fraction = 1.0 if excavator in down else (len(down.intersection(trucks)) / len(trucks) if trucks else 0.0)
        exposure += fraction * (right - left).total_seconds() / 3600
    return exposure


def _excavator_hour_metrics(ids, intervals, production, index, start, end):
    """Use ADT's minute-overlap calculation; count each loaded excavator once."""
    unavailable_seconds = {asset: adt._unavailable_seconds(intervals[asset], start, end) for asset in ids}
    unavailable = sum(unavailable_seconds.values()) / 3600
    utilised = {row.get("asset_name_shoval") for row in production
                if row.get("asset_name_shoval") in ids and float(row.get("loads") or 0) > 0
                and row["hour_index"] == index}
    return {"available": len(ids) - unavailable, "unavailable": unavailable,
            "pbm_count": sum(seconds > 0 for seconds in unavailable_seconds.values()),
            "utilised": len(utilised)}


def _captured_load_events(production, start, hours):
    """Earliest captured first / latest captured last, ordered within this shift."""
    end = start + timedelta(hours=12)
    grouped = {}
    for row in production:
        asset = row.get("asset_name_shoval")
        if not asset:
            continue
        item = grouped.setdefault(asset, {"label": row.get("excavator_label") or asset, "first": None, "last": None})
        for kind, field in (("first", "exc_start_load_time"), ("last", "exc_end_load_time")):
            clock = adt._clock_time(row.get(field))
            if clock is None or clock.tzinfo is not None:
                continue
            captured = datetime.combine(start.date(), clock)
            if start.hour == 18 and clock < start.time():
                captured += timedelta(days=1)
            # A captured final load at the shift's closing boundary belongs to that shift.
            if captured < start or captured > end or (kind == "first" and captured == end):
                continue
            previous = item[kind]
            if previous is None or (captured < previous if kind == "first" else captured > previous):
                item[kind] = captured
    results = []
    for hour in hours:
        hour["first_load_events"], hour["last_load_events"] = [], []
    for item in sorted(grouped.values(), key=lambda row: row["label"]):
        result = {"excavator": item["label"]}
        for kind in ("first", "last"):
            captured = item[kind]
            result[f"{kind}_load_time"] = captured.strftime("%H:%M") if captured is not None else None
            index = min(int((captured - start).total_seconds() // 3600), 11) if captured is not None else None
            result[f"{kind}_load_hour"] = hours[index]["label"] if index is not None else None
            if index is not None:
                hours[index][f"{kind}_load_events"].append({"excavator": item["label"], "time": result[f"{kind}_load_time"]})
        results.append(result)
    return results


def build_summary(date, shift, site, plan, assets, breakdowns, documents, *, now=None,
                  green_threshold=100, orange_threshold=80, estimate_availability=True):
    """Inputs must already be permission-checked; never modifies source rows."""
    if shift not in ("Day", "Night"):
        raise ValueError("Shift must be Day or Night")
    if not 0 <= orange_threshold < green_threshold:
        raise ValueError("Thresholds must satisfy 0 <= orange < green")
    date = getdate(date)
    start = datetime.combine(date, time(6 if shift == "Day" else 18))
    end = start + timedelta(hours=12)
    now = now or now_datetime()
    labels = {row["name"]: row.get("asset_name") or row["name"] for row in assets}
    production, covered = [], set()
    seen_documents, seen_children = set(), set()
    warnings = []
    for doc in documents:
        if doc.get("location") != site or doc.get("docstatus", 0) >= 2:
            continue
        try:
            dt = source_hour(doc.get("prod_date"), doc.get("hour_slot"))
        except (ValueError, TypeError):
            warnings.append("An invalid source hour was excluded.")
            continue
        if not start <= dt < end:
            continue
        if doc["name"] in seen_documents:
            continue
        seen_documents.add(doc["name"])
        covered.add(dt)
        for row in doc.get("truck_loads", []):
            if row.get("name"):
                key = (doc["name"], row["name"])
                if key in seen_children:
                    continue
                seen_children.add(key)
            production.append(dict(row, hour_slot=f"{dt:%H}:00-{(dt.hour + 1) % 24:02}:00",
                                   hour_index=int((dt - start).total_seconds() // 3600),
                                   hp_name=doc["name"], hp_modified=doc.get("modified"),
                                   hp_creation=doc.get("creation"), truck_idx=row.get("idx"),
                                   truck_row_name=row.get("name"),
                                   excavator_label=labels.get(row.get("asset_name_shoval")) or row.get("excavator_plant_no")))
    teams, unpaired_ids = _roster(plan)
    count = int(plan.get("num_excavators") or 0) if plan and plan.get("name") else None
    if count is not None and count < 0:
        raise ValueError("MPP production excavator count cannot be negative")
    trucks_unique = sum(map(len, teams.values())) == len({truck for trucks in teams.values() for truck in trucks})
    roster_complete = count is not None and count == len(teams) and trucks_unique
    if count is None:
        warnings.append("No accessible covering MPP: targets are unavailable.")
    elif not roster_complete:
        warnings.append(f"MPP saves {count} production excavators but identifies {len(teams)} truck-assigned teams. "
                        "Production assignments without trucks exist only in browser localStorage, so their identities "
                        "cannot be recovered reliably. Production availability and breakdown attribution are unavailable.")
    if unpaired_ids:
        warnings.append("Unpaired MPP excavators are production/spare unknown: the original MPP stores empty production "
                        "assignments in browser localStorage, not on these rows. They are not labelled as confirmed spares.")
    if not trucks_unique:
        warnings.append("An ADT is assigned to multiple MPP teams; availability attribution is disabled.")
    site_assets = [row for row in assets if row.get("location") == site or row["name"] in teams]
    adt_assets = [row for row in site_assets if row.get("asset_category") == "ADT"]
    data = adt.build_report(date, shift, site, adt_assets, breakdowns, production)
    data["excavators"] = _captured_load_events(production, start, data["hours"])
    intervals = _intervals(breakdowns, assets, start, end)
    bcm_by_team_hour = defaultdict(float)
    total_by_excavator = defaultdict(float)
    for row in production:
        index = adt._production_hour(row["hour_slot"], shift)
        bcm = float(row.get("bcms") or 0)
        if not isfinite(bcm) or bcm < 0:
            raise ValueError("Source BCM must be finite and nonnegative; review the production record")
        excavator = row.get("asset_name_shoval") or "Unassigned"
        bcm_by_team_hour[(index, excavator)] += bcm
        total_by_excavator[excavator] += bcm
    completed_target = completed_actual = estimate_total = 0.0
    for index, hour in enumerate(data["hours"]):
        left, right = get_datetime(hour["start"]), get_datetime(hour["end"])
        completed = right <= now
        hour["record_status"] = ("pending" if left > now else
                                 "recorded" if completed and left in covered else
                                 "missing" if completed else
                                 "in_progress" if left in covered else "in_progress_missing")
        hour["has_record"] = left in covered
        hour["completed"] = completed
        target_per_excavator = 55 if index == 0 else 165 if index == 11 else 220
        target = count * target_per_excavator if count is not None else None
        actual = sum(bcm for (i, _), bcm in bcm_by_team_hour.items() if i == index)
        achievement = actual / target * 100 if target else None
        hour.update(actual_bcm=actual, target_bcm=target, achievement=achievement,
                    colour=("neutral" if achievement is None or not completed or left not in covered else
                            "green" if achievement >= green_threshold else
                            "orange" if achievement >= orange_threshold else "red"),
                    difference=hour["available"] - hour["utilised"], availability_estimate_bcm=0.0)
        for prefix, ids in (("excavator", set(teams)), ("unpaired", unpaired_ids)):
            metrics = _excavator_hour_metrics(ids, intervals, production, index, left, right)
            hour.update({f"{prefix}_{key}": value if prefix == "unpaired" or roster_complete else None
                         for key, value in metrics.items()})
        # No saved field proves which unpaired assets are spare. Preserve unknown, not an inferred spare count.
        hour.update({f"spare_{key}": None for key in ("available", "unavailable", "pbm_count", "utilised")})
        if completed:
            completed_actual += actual
        if completed and target is not None:
            completed_target += target
            if estimate_availability and left in covered and roster_complete:
                team_exposure = sum(min(max(target_per_excavator - bcm_by_team_hour[(index, exc)], 0),
                                        target_per_excavator * _capacity_exposure(exc, trucks, intervals, left, right))
                                    for exc, trucks in teams.items())
                hour["availability_estimate_bcm"] = min(max(target - actual, 0), team_exposure)
                estimate_total += hour["availability_estimate_bcm"]
    shortfall = max(completed_target - completed_actual, 0)
    estimate_total = min(estimate_total, shortfall)
    observed_extra = set(total_by_excavator) - set(teams) - unpaired_ids
    # Unplanned output remains visible; never silently promote it into the MPP target.
    recorded_labels = {row["excavator"] for row in data["excavators"]}
    for exc in sorted(set(teams) | unpaired_ids):
        if labels.get(exc, exc) not in recorded_labels:
            data["excavators"].append({"excavator": labels.get(exc, exc), "first_load_time": None,
                                      "last_load_time": None, "first_load_hour": None, "last_load_hour": None})
    data.update(plan_name=plan.get("name") if plan else None, production_excavator_count=count,
                roster_complete=roster_complete, warnings=list(dict.fromkeys(warnings)),
                production_excavators=[{"asset": exc, "label": labels.get(exc, exc),
                                        "actual_bcm": total_by_excavator[exc]} for exc in sorted(teams)],
                spare_excavators=[],
                unpaired_excavators=[{"asset": exc, "label": labels.get(exc, exc),
                                      "actual_bcm": total_by_excavator[exc], "classification": "Production/spare unknown"}
                                     for exc in sorted(unpaired_ids)],
                unplanned_excavators=[{"asset": exc, "label": labels.get(exc, exc),
                                       "actual_bcm": total_by_excavator[exc]} for exc in sorted(observed_extra)],
                thresholds={"green": green_threshold, "orange": orange_threshold},
                pie={"achieved": min(max(completed_actual, 0), completed_target),
                     "availability_estimate": estimate_total, "remaining": shortfall - estimate_total,
                     "target": completed_target})
    data["summary"].update(actual_bcm=sum(h["actual_bcm"] for h in data["hours"]),
                           target_bcm=sum(h["target_bcm"] for h in data["hours"]) if count is not None else None,
                           completed_target_bcm=completed_target if count is not None else None,
                           completed_actual_bcm=completed_actual,
                           achievement=completed_actual / completed_target * 100 if completed_target else None,
                           missing_hours=sum(h["record_status"] == "missing" for h in data["hours"]))
    return data


def _read_document(doctype, name):
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    before = doc.as_dict()
    doc.apply_fieldlevel_read_permissions()
    result = doc.as_dict()
    calculation_fields = {
        "Location": ("name",),
        "Monthly Production Planning": ("location", "prod_month_start_date", "prod_month_end_date",
                                        "num_excavators", "excavator_truck_assignments", "month_prod_days", "shift_system"),
        "Hourly Production": ("location", "prod_date", "hour_slot", "truck_loads"),
        "Asset": ("name", "asset_name", "location", "asset_category"),
        "Plant Breakdown or Maintenance": ("asset_name", "breakdown_start_datetime", "resolved_datetime",
                                           "exclude_from_au", "breakdown_reason"),
        "Hourly Productivity Settings": ("green_threshold", "orange_threshold"),
        "Startup and Fatigue spesification": ("site", "shift", "day_type", "startup_start", "startup_end",
                                               "fatigue_start", "fatigue_end"),
    }
    if any(before.get(field) != result.get(field) for field in calculation_fields.get(doctype, ())):
        raise frappe.PermissionError("Access to calculation fields is required for this summary")
    return result


def _permitted_rows(doctype, filters, *, order_by="name asc", **kwargs):
    """get_list preserves role, user, owner and sharing permissions on parents."""
    result = []
    for offset in range(0, 100000, 500):
        rows = frappe.get_list(doctype, filters=filters, fields=["name"],
                               limit_start=offset, limit_page_length=500,
                               order_by=order_by, **kwargs)
        result.extend(rows)
        if len(rows) < 500:
            return result
    raise ValueError("Too many source records for a single shift")


@frappe.whitelist()
def get_defaults():
    if frappe.session.user == "Guest":
        raise frappe.PermissionError("Login required")
    now = now_datetime()
    # User requested calendar today; Night date is always its start date.
    return {"date": now.date().isoformat(), "shift": "Day" if 6 <= now.hour < 18 else "Night"}


@frappe.whitelist()
def get_summary(date=None, shift="Day", site=None):
    if frappe.session.user == "Guest":
        raise frappe.PermissionError("Login required")
    if shift not in ("Day", "Night"):
        raise ValueError("Shift must be Day or Night")
    date = getdate(date or now_datetime().date())
    if not site:
        raise ValueError("Select a Site")
    start = datetime.combine(date, time(6 if shift == "Day" else 18))
    end = start + timedelta(hours=12)
    plans, documents, assets, breakdowns = load_sources(site, start, end)
    plan = select_plan(plans, site, date)
    settings = _read_document("Hourly Productivity Settings", "Hourly Productivity Settings")
    data = build_summary(date, shift, site, plan, assets, breakdowns, documents,
                         green_threshold=float(settings.get("green_threshold") or 100),
                         orange_threshold=float(settings["orange_threshold"]) if settings.get("orange_threshold") is not None else 80)
    data["warnings"].append("ADT fleet uses current Asset site assignments; historical transfers are not reconstructed.")
    return data


def select_plan(plans, site, date):
    """Same latest-modified coverage rule for each operational date, including drafts."""
    candidates = [plan for plan in plans if plan.get("location") == site and plan.get("docstatus", 0) < 2
                  and plan.get("prod_month_start_date") and plan.get("prod_month_end_date")
                  and getdate(plan["prod_month_start_date"]) <= date <= getdate(plan["prod_month_end_date"])]
    return max(candidates, key=lambda p: (str(p.get("modified") or ""), p["name"])) if candidates else None


def load_sources(site, start, end):
    """Fetch once for a bounded date range; all consumers remain read-only."""
    if frappe.session.user == "Guest":
        raise frappe.PermissionError("Login required")
    _read_document("Location", site)
    for doctype in ("Hourly Production", "Monthly Production Planning", "Asset", "Plant Breakdown or Maintenance"):
        frappe.has_permission(doctype, "read", throw=True)
    first_date = (start - timedelta(hours=6)).date()
    last_date = (end - timedelta(hours=6, microseconds=1)).date()
    plans = [_read_document("Monthly Production Planning", row.name) for row in _permitted_rows(
        "Monthly Production Planning", {"location": site, "prod_month_start_date": ["<=", last_date],
        "prod_month_end_date": [">=", first_date], "docstatus": ["<", 2]}, order_by="modified desc, name desc")]
    documents = [_read_document("Hourly Production", row.name) for row in _permitted_rows(
        "Hourly Production", {"location": site, "prod_date": ["between", [start.date(), end.date()]], "docstatus": ["<", 2]})]
    assets = [_read_document("Asset", row.name) for row in _permitted_rows(
        "Asset", {"location": site, "docstatus": 1, "asset_category": ["in", ["ADT", "Excavator"]]})]
    linked_assets = {r.get(field) for plan in plans for r in plan.get("excavator_truck_assignments", [])
                     for field in ("excavator", "truck") if r.get(field)}
    known_assets = {r["name"] for r in assets}
    for name in sorted(linked_assets - known_assets):
        # Historical MPP teams remain authoritative even after an asset moves.
        # Check document access; never bypass permissions for their labels/PBMs.
        assets.append(_read_document("Asset", name))
    names = sorted({value for row in assets for value in (row["name"], row.get("asset_name")) if value})
    breakdowns = [_read_document("Plant Breakdown or Maintenance", row.name) for row in _permitted_rows(
        "Plant Breakdown or Maintenance", {"location": site, "asset_name": ["in", names],
        "docstatus": ["<", 2], "exclude_from_au": 0, "breakdown_reason": ["!=", ""],
        "breakdown_start_datetime": ["<", end]},
        or_filters=[["resolved_datetime", ">", start], ["resolved_datetime", "is", "not set"]])] if names else []
    return plans, documents, assets, breakdowns


# Range dashboard: retained calculations, now in the standard Page module.
CATEGORIES = ('green', 'amber', 'red', 'bright_red', 'grey')
SPECIFICATION = 'Startup and Fatigue spesification'


def validate_range(from_date, to_date, shift):
    if shift not in ('Day', 'Night', 'Full Day'):
        raise ValueError('Shift must be Day, Night or Full Day')
    first, last = getdate(from_date), getdate(to_date)
    if first > last or (last - first).days >= 366:
        raise ValueError('Select an ordered range of at most 366 production days')
    return first, last


def planned_schedule(plan, date, shift):
    """Saved totals prove zero/full shifts only; never position a shortened shift."""
    if not plan or plan.get('shift_system') != '2x12Hour':
        return 'unknown'
    rows = [r for r in plan.get('month_prod_days', [])
            if r.get('shift_start_date') and getdate(r['shift_start_date']) == date]
    if len(rows) != 1:
        return 'unknown'
    value = rows[0].get('shift_day_hours' if shift == 'Day' else 'shift_night_hours')
    if value is None:
        return 'unknown'
    hours = float(value)
    return 'off' if hours == 0 else 'working' if hours == 12 else 'unknown'


def indicators(specifications, site, date, shift):
    day_type = 'Sunday' if date.weekday() == 6 else 'Saturday' if date.weekday() == 5 else 'Weekday'
    candidates = [s for s in specifications if s.get('site') == site and s.get('shift') == shift]
    matches = [s for s in candidates if s.get('day_type') == day_type]
    # Same unambiguous legacy fallback as Engineering A&U; no arbitrary first row.
    spec = matches[0] if len(matches) == 1 else candidates[0] if not matches and len(candidates) == 1 else None
    if not spec:
        return []
    start = datetime.combine(date, time(6 if shift == 'Day' else 18))
    end = start + timedelta(hours=12)
    result = []
    for kind in ('startup', 'fatigue'):
        left_clock = adt._clock_time(spec.get(kind + '_start'))
        right_clock = adt._clock_time(spec.get(kind + '_end'))
        if left_clock is None or right_clock is None or left_clock.tzinfo or right_clock.tzinfo:
            continue
        left, right = datetime.combine(date, left_clock), datetime.combine(date, right_clock)
        if shift == 'Night' and left < start:
            left += timedelta(days=1)
        if shift == 'Night' and right < start:
            right += timedelta(days=1)
        if right < left:
            right += timedelta(days=1)
        left, right = max(left, start), min(right, end)
        if right > left:
            result.append(dict(shift=shift, label=kind.title(), start=str(left), end=str(right),
                               period=f'{left:%H:%M}–{right:%H:%M}'))
    return result


def _excavator_rows(report, assets, breakdowns, date, shift):
    labels = {r['name']: r.get('asset_name') or r['name'] for r in assets}
    verified = {r['asset'] for r in report['production_excavators']}
    timings = {r['excavator']: r for r in report['excavators']}
    start = datetime.combine(date, time(6 if shift == 'Day' else 18))
    intervals = _intervals(breakdowns, assets, start, start + timedelta(hours=12))
    result = []
    for row in report['production_excavators'] + report['unpaired_excavators'] + report['unplanned_excavators']:
        asset = row['asset']
        label = labels.get(asset, row['label'])
        timing = timings.get(label, {})
        down = sum(adt._unavailable_seconds(intervals[asset], start + timedelta(hours=i),
                                                start + timedelta(hours=i + 1)) for i in range(12))
        status = ('PBM unavailable' if down == 12 * 3600 else 'Partial PBM' if down else 'Available')
        result.append(dict(asset=asset, excavator=label, actual_bcm=row['actual_bcm'], shift=shift,
                           first_load_time=timing.get('first_load_time'), last_load_time=timing.get('last_load_time'),
                           availability_status=status if asset in verified else 'Unverified',
                           verified=asset in verified))
    return result


def build_dashboard(site, from_date, to_date, shift, plans, assets, breakdowns, documents, *, now=None,
                    specifications=()):
    first, last = validate_range(from_date, to_date, shift)
    now = now or now_datetime()
    shifts = ('Day', 'Night') if shift == 'Full Day' else (shift,)
    grouped = defaultdict(list)
    seen = set()
    for doc in documents:
        if doc.get('location') != site or doc.get('docstatus', 0) >= 2:
            continue
        try:
            production_date = (source_hour(doc.get('prod_date'), doc.get('hour_slot')) - timedelta(hours=6)).date()
        except (ValueError, TypeError):
            # Delegate the usual validation notice to the shift builder when possible.
            continue
        if first <= production_date <= last and doc.get('name') not in seen:
            grouped[production_date].append(doc)
            seen.add(doc.get('name'))
    days, counts = [], dict.fromkeys(CATEGORIES, 0)
    excluded = dict(missing=0, unfinished=0, schedule_unknown=0, no_target=0)
    date = first
    while date <= last:
        plan = select_plan(plans, site, date)
        day = dict(date=date.isoformat(), plan_name=plan.get('name') if plan else None,
                   production_excavator_count=plan.get('num_excavators') if plan else None,
                   hours=[], excavator_production=[], indicators=[], timeline=[])
        timeline = defaultdict(list)
        for selected_shift in shifts:
            report = build_summary(date, selected_shift, site, plan, assets, breakdowns, grouped[date],
                                          now=now, orange_threshold=85, estimate_availability=False)
            schedule = planned_schedule(plan, date, selected_shift)
            day['excavator_production'].extend(_excavator_rows(report, assets, breakdowns, date, selected_shift))
            day['indicators'].extend(indicators(specifications, site, date, selected_shift))
            details = {(r['adt'], r['hour']): r for r in report['details']}
            machines = sorted({r['adt'] for r in report['details']})
            for hour in report['hours']:
                target = 0 if schedule == 'off' else hour['target_bcm'] if schedule == 'working' else None
                hour.update(shift=selected_shift, schedule=schedule, target_bcm=target,
                            achievement=hour['actual_bcm'] / target * 100 if target else None, category='neutral')
                if not hour['completed']:
                    excluded['unfinished'] += 1
                    if schedule == 'off':
                        hour['category'] = 'grey'  # Visible plan, excluded from completed percentages.
                elif schedule == 'off':
                    hour['category'] = 'grey'
                elif schedule == 'unknown':
                    excluded['schedule_unknown'] += 1
                elif target is None or target <= 0:
                    excluded['no_target'] += 1
                elif not hour['has_record']:
                    excluded['missing'] += 1
                else:
                    achievement = hour['achievement']
                    available = hour['excavator_available']
                    hour['category'] = ('bright_red' if hour['actual_bcm'] == 0 and available is not None and available > 0
                                        else 'green' if achievement >= 100 else 'amber' if achievement >= 85 else 'red')
                if hour['completed'] and hour['category'] in counts:
                    counts[hour['category']] += 1
                hour['colour'] = hour['category']
                day['hours'].append(hour)
                for machine in machines:
                    detail = details[(machine, hour['label'])]
                    loads = detail['loads'] if hour['has_record'] else None
                    state = ('missing' if not hour['has_record'] or not hour['completed'] else
                             'unavailable' if detail['available_minutes'] == 0 else
                             'partial' if detail['available_minutes'] < 60 else 'used' if loads else 'idle')
                    timeline[machine].append(dict(available_minutes=detail['available_minutes'], loads=loads,
                                                  state=state, record_status=hour['record_status']))
        day['timeline'] = [dict(adt=machine, cells=cells) for machine, cells in sorted(timeline.items())]
        days.append(day)
        date += timedelta(days=1)
    hours = [h for day in days for h in day['hours']]
    completed = [h for h in hours if h['completed']]
    average_hours = [h for h in completed if h['has_record'] and h['schedule'] == 'working']

    def total(rows, field):
        return sum(r[field] for r in rows) if all(r[field] is not None for r in rows) else None

    def average(field):
        values = total(average_hours, field)
        return values / len(average_hours) if average_hours and values is not None else None

    completed_target = total(completed, 'target_bcm')
    completed_actual = total(completed, 'actual_bcm')
    eligible = sum(counts.values())
    summary = dict(actual_bcm=total(hours, 'actual_bcm'), target_bcm=total(hours, 'target_bcm'),
                   completed_target_bcm=completed_target, completed_actual_bcm=completed_actual,
                   achievement=completed_actual / completed_target * 100 if completed_target else None,
                   average_adts_available=average('available'), average_adts_utilised=average('utilised'),
                   average_excavators_available=average('excavator_available'),
                   average_excavators_utilised=average('excavator_utilised'), average_hours=len(average_hours))
    return dict(site=site, from_date=first.isoformat(), to_date=last.isoformat(), shift=shift, days=days,
                summary=summary, excluded=excluded, pie=dict(counts=counts, total_hours=eligible,
                percentages={key: value / eligible * 100 if eligible else 0 for key, value in counts.items()}))


@frappe.whitelist()
def get_dashboard(site, from_date, to_date, shift='Day'):
    if frappe.session.user == 'Guest':
        raise frappe.PermissionError('Login required')
    if not site:
        raise ValueError('Select a Site')
    first, last = validate_range(from_date, to_date, shift)
    start, end = datetime.combine(first, time(6)), datetime.combine(last + timedelta(days=1), time(6))
    plans, documents, assets, breakdowns = load_sources(site, start, end)
    specifications = []
    if frappe.has_permission(SPECIFICATION, 'read'):
        for row in _permitted_rows(SPECIFICATION, {'site': site}):
            specifications.append(_read_document(SPECIFICATION, row.name))
    return build_dashboard(site, first, last, shift, plans, assets, breakdowns, documents,
                           specifications=specifications)
