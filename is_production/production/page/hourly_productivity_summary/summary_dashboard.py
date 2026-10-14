"""Range presentation adapter. Reuses the single-shift source calculations without writes."""
from collections import defaultdict
from datetime import datetime, time, timedelta

import frappe
from frappe.utils import getdate, now_datetime

from . import hourly_productivity_summary as source

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
        left_clock = source.adt._clock_time(spec.get(kind + '_start'))
        right_clock = source.adt._clock_time(spec.get(kind + '_end'))
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
    intervals = source._intervals(breakdowns, assets, start, start + timedelta(hours=12))
    result = []
    for row in report['production_excavators'] + report['unpaired_excavators'] + report['unplanned_excavators']:
        asset = row['asset']
        label = labels.get(asset, row['label'])
        timing = timings.get(label, {})
        down = sum(source.adt._unavailable_seconds(intervals[asset], start + timedelta(hours=i),
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
            production_date = (source.source_hour(doc.get('prod_date'), doc.get('hour_slot')) - timedelta(hours=6)).date()
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
        plan = source.select_plan(plans, site, date)
        day = dict(date=date.isoformat(), plan_name=plan.get('name') if plan else None,
                   production_excavator_count=plan.get('num_excavators') if plan else None,
                   hours=[], excavator_production=[], indicators=[], timeline=[])
        timeline = defaultdict(list)
        for selected_shift in shifts:
            report = source.build_summary(date, selected_shift, site, plan, assets, breakdowns, grouped[date],
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
    plans, documents, assets, breakdowns = source.load_sources(site, start, end)
    specifications = []
    if frappe.has_permission(SPECIFICATION, 'read'):
        for row in source._permitted_rows(SPECIFICATION, {'site': site}):
            specifications.append(source._read_document(SPECIFICATION, row.name))
    return build_dashboard(site, first, last, shift, plans, assets, breakdowns, documents,
                           specifications=specifications)
