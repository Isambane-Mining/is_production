"""Read-only MPP targets, stored production, and PBM-backed capacity estimates."""

from collections import defaultdict
from datetime import datetime, time, timedelta
from math import isfinite

import frappe
from frappe.utils import get_datetime, getdate, now_datetime

from engineering.engineering.page.adt_hourly_productivity import adt_hourly_productivity as adt
from is_production.production.controllers.production_summary_periods import source_hour
from is_production.production.controllers.hourly_productivity_schedule import hour_segments, shift_windows


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


def apply_schedule_presentation(data, plan, date, shift, site, fatigue):
    """Decorate hours without changing any production, capacity or target calculation."""
    windows, kind = shift_windows(plan, date, shift, site, fatigue, data["warnings"])
    for hour in data["hours"]:
        target = hour["target_bcm"]
        colour = "neutral" if target is None else "green" if hour["actual_bcm"] >= target else "red"
        excluded_colour = "grey" if kind == "non_working" else "blue"
        segments, minutes = hour_segments(hour, windows, colour, excluded_category=excluded_colour)
        hour.update(presentation_segments=segments, presentation_colour=excluded_colour if minutes == 60 else colour,
                    fatigue_minutes=minutes if kind == "fatigue" else 0,
                    non_working_minutes=minutes if kind == "non_working" else 0)
    data["warnings"] = list(dict.fromkeys(data["warnings"]))


def load_fatigue(site, shift=None):
    doctype = "Startup and Fatigue spesification"  # Persisted DocType spelling.
    frappe.has_permission(doctype, "read", throw=True)
    filters = {"site": site}
    if shift:
        filters["shift"] = shift
    return [_read_document(doctype, row.name) for row in
            _permitted_rows(doctype, filters, order_by="modified desc, name desc")]


def _read_document(doctype, name):
    doc = frappe.get_doc(doctype, name)
    doc.check_permission("read")
    before = doc.as_dict()
    doc.apply_fieldlevel_read_permissions()
    result = doc.as_dict()
    calculation_fields = {
        "Location": ("name",),
        "Startup and Fatigue spesification": ("site", "shift", "day_type", "fatigue_start", "fatigue_end"),
        "Monthly Production Planning": ("location", "prod_month_start_date", "prod_month_end_date",
                                        "num_excavators", "excavator_truck_assignments"),
        "Hourly Production": ("location", "prod_date", "hour_slot", "truck_loads"),
        "Asset": ("name", "asset_name", "location", "asset_category"),
        "Plant Breakdown or Maintenance": ("asset_name", "breakdown_start_datetime", "resolved_datetime",
                                           "exclude_from_au", "breakdown_reason"),
        "Hourly Productivity Settings": ("green_threshold", "orange_threshold"),
    }
    if any(before.get(field) != result.get(field) for field in calculation_fields.get(doctype, ())):
        raise frappe.PermissionError("Access to calculation fields is required for this summary")
    if doctype == "Monthly Production Planning" and before.get("month_prod_days") != result.get("month_prod_days"):
        # Redacted child fields cannot establish a zero-hour shift; retain the numeric report.
        result["month_prod_days"] = []
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
    try:
        fatigue = load_fatigue(site, shift)
    except frappe.PermissionError:
        fatigue = []
        data["warnings"].append("Fatigue colouring is unavailable: read access to Startup and Fatigue Specification "
                                "and its scheduling fields is required.")
    apply_schedule_presentation(data, plan, date, shift, site, fatigue)
    data["warnings"].append("ADT fleet uses current Asset site assignments; historical transfers are not reconstructed.")
    return data


def select_plan(plans, site, date):
    """Same latest-modified coverage rule for each operational date, including drafts."""
    candidates = [plan for plan in plans if plan.get("location") == site and plan.get("docstatus", 0) < 2
                  and plan.get("prod_month_start_date") and plan.get("prod_month_end_date")
                  and getdate(plan["prod_month_start_date"]) <= date <= getdate(plan["prod_month_end_date"])]
    return max(candidates, key=lambda p: (str(p.get("modified") or ""), p["name"])) if candidates else None


def load_sources(site, start, end, *, plans=None):
    """Fetch read-only sources; supplied plans must already be permission-checked."""
    if frappe.session.user == "Guest":
        raise frappe.PermissionError("Login required")
    _read_document("Location", site)
    for doctype in ("Hourly Production", "Monthly Production Planning", "Asset", "Plant Breakdown or Maintenance"):
        frappe.has_permission(doctype, "read", throw=True)
    first_date = (start - timedelta(hours=6)).date()
    last_date = (end - timedelta(hours=6, microseconds=1)).date()
    if plans is None:
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
