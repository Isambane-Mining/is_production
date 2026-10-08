"""Staggered current-period generation and off-peak bounded history recovery.

Saved snapshots remain the coverage record. Recovery cursors are durable hints:
completed scans restart from source history so failed periods are retried. No
batch queues a continuation; the next off-peak slot resumes saved progress.
"""
from datetime import datetime, timedelta
from time import monotonic
import hashlib
import json

import frappe
from frappe.utils import get_datetime, getdate, now_datetime
from frappe.utils.background_jobs import get_redis_conn

from ..production_summaries.periods import source_hour, make_period, completed_hour
from ..production_summaries.eligibility import eligible_periods
from .production_summary_planning import get_plan_windows
from .production_summary_snapshot import DOCTYPES, create_snapshot

DEFAULT_BATCH_SIZE = 200
DEFAULT_TIME_BUDGET = 180
WORKER_TIMEOUT = 300
# Covers the existing long-queue maximum as well as the new 300-second jobs.
LOCK_TIMEOUT = 1800
METHOD_PREFIX = 'is_production.production.controllers.production_summary_scheduler.'


def get_site_starts():
    rows = frappe.db.sql('''
        SELECT site, source_date, source_kind
        FROM (
            SELECT location AS site, MIN(prod_date) AS source_date, 'hourly' AS source_kind
            FROM `tabHourly Production` WHERE docstatus < 2 GROUP BY location
            UNION ALL
            SELECT location AS site, MIN(prod_month_start_date) AS source_date, 'planning' AS source_kind
            FROM `tabMonthly Production Planning` WHERE docstatus < 2 GROUP BY location
            UNION ALL
            SELECT site, MIN(date) AS source_date, 'drilling' AS source_kind
            FROM `tabHourly Drilling Report` WHERE docstatus < 2 GROUP BY site
        ) sources
        WHERE COALESCE(site, '') != '' AND source_date IS NOT NULL
        ORDER BY site
    ''', as_dict=True)
    starts = {}
    for row in rows:
        site, source_date = row['site'], getdate(row['source_date'])
        if row['source_kind'] == 'hourly':
            hours = frappe.get_all('Hourly Production', filters={'location': site,
                'prod_date': source_date, 'docstatus': ['<', 2]}, fields=['hour_slot'])
            slots = [hour['hour_slot'] for hour in hours]
        elif row['source_kind'] == 'drilling':
            parents = frappe.get_all('Hourly Drilling Report', filters={'site': site,
                'date': source_date, 'docstatus': ['<', 2]}, pluck='name')
            slots = []
            for offset in range(0, len(parents), 500):
                slots.extend(frappe.get_all('Drills Hourly Entries', filters={
                    'parent': ['in', parents[offset:offset + 500]],
                    'parenttype': 'Hourly Drilling Report', 'parentfield': 'hourly_entries'}, pluck='hourly_slot'))
        else:
            slots = None
        if slots is not None:
            operational_dates = []
            for slot in slots:
                try:
                    operational_dates.append((source_hour(source_date, slot) - timedelta(hours=6)).date())
                except (ValueError, TypeError):
                    continue
            # With no valid slots, cover the previous day conservatively.
            source_date = min(operational_dates) if operational_dates else source_date - timedelta(days=1)
        starts[site] = min(starts.get(site, source_date), source_date)
    return starts


def _job_id(action, kind, as_of):
    identity = json.dumps([action, kind, str(as_of)], sort_keys=True)
    return 'production-summary-' + hashlib.sha256(identity.encode()).hexdigest()


def _execution_lock():
    # Queue Redis is separate from Frappe's cache: saving defaults clears site
    # cache, which must never delete an execution lock. Scope to this Frappe
    # site; all production Locations and all summary kinds share the key.
    return get_redis_conn().lock('production-summary-execution:' + frappe.local.site, timeout=LOCK_TIMEOUT)


def _commit_and_release(lock):
    # Visibility must precede unlock, otherwise another worker can race the
    # uncommitted coverage/cursor and calculate the same snapshot again.
    frappe.db.commit()
    lock.release()


def _run_locked(action):
    lock = _execution_lock()
    if not lock.acquire(blocking=False):
        return dict(created=0, failed=0, attempted=0, continuation=None, skipped=True)
    try:
        result = action()
        _commit_and_release(lock)
        return result
    except BaseException:
        frappe.db.rollback()
        if lock.owned():
            lock.release()
        raise


def _validate_limits(kind, batch_size, time_budget):
    if kind not in DOCTYPES:
        raise ValueError('Unknown summary kind')
    batch_size, time_budget = int(batch_size), float(time_budget)
    if not 1 <= batch_size <= DEFAULT_BATCH_SIZE:
        raise ValueError('batch_size must be between 1 and 200')
    if not 0 < time_budget <= DEFAULT_TIME_BUDGET:
        raise ValueError('time_budget must be greater than 0 and at most 180 seconds')
    return batch_size, time_budget


def _recover_batch(kind, as_of, batch_size, cursor, time_budget):
    deadline = monotonic() + time_budget
    created = failed = attempted = 0
    continuation = None
    cursor_start = get_datetime(cursor['start']) if cursor else None
    plan_windows = get_plan_windows()
    for site, source_date in sorted(get_site_starts().items()):
        if site not in plan_windows or (cursor and site < cursor['site']):
            continue
        start = datetime.combine(getdate(source_date), datetime.min.time()).replace(hour=6)
        if cursor and site == cursor['site']:
            start = max(start, cursor_start)
        rows = frappe.get_all(DOCTYPES[kind], filters={'site': site,
            'period_start': ['between', [start, as_of]]}, fields=['period_start'])
        existing = {get_datetime(row['period_start']) for row in rows}
        for period in eligible_periods(kind, start, as_of, plan_windows[site]):
            if attempted >= batch_size or monotonic() >= deadline:
                continuation = dict(site=site, start=period.start.isoformat())
                break
            if period.start in existing:
                continue
            attempted += 1
            try:
                if create_snapshot(site, period):
                    created += 1
            except Exception:
                failed += 1
                frappe.log_error(title=f'{DOCTYPES[kind]}: {site} {period.start}', message=frappe.get_traceback())
        if continuation:
            break
    return dict(created=created, failed=failed, attempted=attempted, continuation=continuation, skipped=False)


def recover(kind, as_of=None, batch_size=DEFAULT_BATCH_SIZE, cursor=None, time_budget=DEFAULT_TIME_BUDGET):
    """Run one manual bounded batch; return a cursor without queuing more work."""
    batch_size, time_budget = _validate_limits(kind, batch_size, time_budget)
    as_of = get_datetime(as_of) if as_of else now_datetime()
    return _run_locked(lambda: _recover_batch(kind, as_of, batch_size, cursor, time_budget))


def _state_key(kind):
    return 'production_summary_recovery_' + kind


def run_recovery(kind, as_of=None):
    """Resume one durable off-peak batch. Cutoff stays frozen across slots."""
    _validate_limits(kind, DEFAULT_BATCH_SIZE, DEFAULT_TIME_BUDGET)
    cutoff = get_datetime(as_of) if as_of else now_datetime()

    def action():
        saved = frappe.db.get_global(_state_key(kind))
        state = json.loads(saved) if saved else {}
        frozen = get_datetime(state['as_of']) if state else cutoff
        result = _recover_batch(kind, frozen, DEFAULT_BATCH_SIZE, state.get('cursor'), DEFAULT_TIME_BUDGET)
        next_state = dict(as_of=frozen.isoformat(), cursor=result['continuation']) if result['continuation'] else None
        frappe.db.set_global(_state_key(kind), json.dumps(next_state) if next_state else None)
        return result

    return _run_locked(action)


def generate(kind, as_of=None):
    """Create the most recently completed period for each eligible site only."""
    _validate_limits(kind, DEFAULT_BATCH_SIZE, DEFAULT_TIME_BUDGET)
    as_of = get_datetime(as_of) if as_of else now_datetime()
    if kind == 'hourly':
        period = completed_hour(as_of)
    else:
        end = as_of.replace(hour=6, minute=0, second=0, microsecond=0)
        if kind == 'shift' and as_of.hour >= 18:
            end += timedelta(hours=12)
        elif as_of < end:
            end -= timedelta(hours=12 if kind == 'shift' else 24)
        period = make_period(kind, end - timedelta(hours=12 if kind == 'shift' else 24))

    def action():
        deadline = monotonic() + DEFAULT_TIME_BUDGET
        created = failed = attempted = 0
        windows = get_plan_windows()
        for site, coverage in sorted(windows.items()):
            if not any(first <= period.report_date <= last for first, last in coverage):
                continue
            if attempted >= DEFAULT_BATCH_SIZE or monotonic() >= deadline:
                break  # Saved coverage lets off-peak recovery repair skipped sites.
            attempted += 1
            try:
                if create_snapshot(site, period):
                    created += 1
            except Exception:
                failed += 1
                frappe.log_error(title=f'{DOCTYPES[kind]}: {site} {period.start}', message=frappe.get_traceback())
        return dict(created=created, failed=failed, attempted=attempted, continuation=None, skipped=False)

    return _run_locked(action)


def _enqueue(action, kind):
    # Cron only queues a single bounded worker; its cutoff is serialized.
    as_of = now_datetime().replace(minute=0, second=0, microsecond=0).isoformat()
    return frappe.enqueue(method=METHOD_PREFIX + action, queue='long', timeout=WORKER_TIMEOUT,
                          enqueue_after_commit=True, job_id=_job_id(action, kind, as_of),
                          deduplicate=True, kind=kind, as_of=as_of)


def create_hourly_summaries():
    return _enqueue('generate', 'hourly')


def create_shift_summaries():
    return _enqueue('generate', 'shift')


def create_daily_summaries():
    return _enqueue('generate', 'daily')


def recover_hourly_summaries():
    return _enqueue('run_recovery', 'hourly')


def recover_shift_summaries():
    return _enqueue('run_recovery', 'shift')


def recover_daily_summaries():
    return _enqueue('run_recovery', 'daily')


def recover_all_summaries(as_of=None):
    """Manual compatibility command at its new controller path; never scheduled."""
    cutoff = get_datetime(as_of) if as_of else now_datetime()
    return {kind: recover(kind, as_of=cutoff) for kind in DOCTYPES}
