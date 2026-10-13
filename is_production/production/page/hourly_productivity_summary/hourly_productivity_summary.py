"""Read-only MPP targets, stored production, and PBM-backed capacity estimates."""

from collections import defaultdict
from datetime import datetime, time, timedelta
from math import isfinite

import frappe
from frappe.utils import get_datetime, getdate, now_datetime

from engineering.engineering.page.adt_hourly_productivity import adt_hourly_productivity as adt
from is_production.production.controllers.production_summary_periods import source_hour


def _roster(plan):
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
        fraction = 1.0 if excavator in down else len(down.intersection(trucks)) / len(trucks)
        exposure += fraction * (right - left).total_seconds() / 3600
    return exposure


def build_summary(date, shift, site, plan, assets, breakdowns, documents, *, now=None,
                  green_threshold=100, orange_threshold=80):
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
        covered.add(dt)
        for row in doc.get("truck_loads", []):
            production.append(dict(row, hour_slot=f"{dt:%H}:00-{(dt.hour + 1) % 24:02}:00",
                                   hp_name=doc["name"], hp_modified=doc.get("modified"),
                                   hp_creation=doc.get("creation"), truck_idx=row.get("idx"),
                                   truck_row_name=row.get("name"),
                                   excavator_label=labels.get(row.get("asset_name_shoval")) or row.get("excavator_plant_no")))
    teams, spare_ids = _roster(plan)
    count = int(plan.get("num_excavators") or 0) if plan and plan.get("name") else None
    if count is not None and count < 0:
        raise ValueError("MPP production excavator count cannot be negative")
    trucks_unique = sum(map(len, teams.values())) == len({truck for trucks in teams.values() for truck in trucks})
    roster_complete = count is not None and count == len(teams) and trucks_unique
    if count is None:
        warnings.append("No accessible covering MPP: targets are unavailable.")
    elif not roster_complete:
        warnings.append(f"MPP saves {count} production excavators but identifies {len(teams)} truck-assigned teams. "
                        "Unpaired excavators may be spare or production; availability attribution is disabled.")
    if not trucks_unique:
        warnings.append("An ADT is assigned to multiple MPP teams; availability attribution is disabled.")
    site_assets = [row for row in assets if row.get("location") == site or row["name"] in teams]
    adt_assets = [row for row in site_assets if row.get("asset_category") == "ADT"]
    data = adt.build_report(date, shift, site, adt_assets, breakdowns, production)
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
        if completed:
            completed_actual += actual
        if completed and target is not None:
            completed_target += target
            if left in covered and roster_complete:
                team_exposure = sum(min(max(target_per_excavator - bcm_by_team_hour[(index, exc)], 0),
                                        target_per_excavator * _capacity_exposure(exc, trucks, intervals, left, right))
                                    for exc, trucks in teams.items())
                hour["availability_estimate_bcm"] = min(max(target - actual, 0), team_exposure)
                estimate_total += hour["availability_estimate_bcm"]
    shortfall = max(completed_target - completed_actual, 0)
    estimate_total = min(estimate_total, shortfall)
    observed_extra = set(total_by_excavator) - set(teams) - spare_ids
    # Unplanned output remains visible; never silently promote it into the MPP target.
    recorded_labels = {row["excavator"] for row in data["excavators"]}
    for exc in sorted(set(teams) | spare_ids):
        if labels.get(exc, exc) not in recorded_labels:
            data["excavators"].append({"excavator": labels.get(exc, exc), "first_load_time": None,
                                      "last_load_time": None, "first_load_hour": None, "last_load_hour": None})
    data.update(plan_name=plan.get("name") if plan else None, production_excavator_count=count,
                roster_complete=roster_complete, warnings=list(dict.fromkeys(warnings)),
                production_excavators=[{"asset": exc, "label": labels.get(exc, exc),
                                        "actual_bcm": total_by_excavator[exc]} for exc in sorted(teams)],
                spare_excavators=[{"asset": exc, "label": labels.get(exc, exc),
                                   "actual_bcm": total_by_excavator[exc],
                                   "classification": "Spare" if roster_complete else "Spare / unresolved"}
                                  for exc in sorted(spare_ids)],
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
        "Monthly Production Planning": ("num_excavators", "excavator_truck_assignments"),
        "Hourly Production": ("location", "prod_date", "hour_slot", "truck_loads"),
        "Asset": ("name", "asset_name", "location", "asset_category"),
        "Plant Breakdown or Maintenance": ("asset_name", "breakdown_start_datetime", "resolved_datetime",
                                           "exclude_from_au", "breakdown_reason"),
        "Hourly Productivity Settings": ("green_threshold", "orange_threshold"),
    }
    if any(before.get(field) != result.get(field) for field in calculation_fields.get(doctype, ())):
        raise frappe.PermissionError("Access to calculation fields is required for this summary")
    return result


def _permitted_rows(doctype, filters, **kwargs):
    """get_list preserves role, user, owner and sharing permissions on parents."""
    result = []
    for offset in range(0, 100000, 500):
        rows = frappe.get_list(doctype, filters=filters, fields=["name"],
                               limit_start=offset, limit_page_length=500,
                               order_by="name asc", **kwargs)
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
    _read_document("Location", site)
    for doctype in ("Hourly Production", "Monthly Production Planning", "Asset", "Plant Breakdown or Maintenance"):
        frappe.has_permission(doctype, "read", throw=True)
    start = datetime.combine(date, time(6 if shift == "Day" else 18))
    end = start + timedelta(hours=12)
    plans = frappe.get_list("Monthly Production Planning", filters={
        "location": site, "prod_month_start_date": ["<=", date],
        "prod_month_end_date": [">=", date], "docstatus": ["<", 2]},
        fields=["name"], order_by="modified desc, name desc", limit_page_length=1)
    plan = _read_document("Monthly Production Planning", plans[0].name) if plans else None
    documents = [_read_document("Hourly Production", row.name) for row in _permitted_rows(
        "Hourly Production", {"location": site, "prod_date": ["between", [date, end.date()]], "docstatus": ["<", 2]})]
    assets = [_read_document("Asset", row.name) for row in _permitted_rows(
        "Asset", {"location": site, "docstatus": 1, "asset_category": ["in", ["ADT", "Excavator"]]})]
    linked_assets = {r.get(field) for r in (plan or {}).get("excavator_truck_assignments", [])
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
    settings = _read_document("Hourly Productivity Settings", "Hourly Productivity Settings")
    data = build_summary(date, shift, site, plan, assets, breakdowns, documents,
                         green_threshold=float(settings.get("green_threshold") or 100),
                         orange_threshold=float(settings["orange_threshold"]) if settings.get("orange_threshold") is not None else 80)
    data["warnings"].append("ADT fleet uses current Asset site assignments; historical transfers are not reconstructed.")
    return data
