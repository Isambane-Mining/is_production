"""Completed-period generation and bounded recovery across all production sites.

The earliest non-cancelled source/planning date anchors recovery. Saved snapshot
periods are the durable coverage record: no rolling lookback can lose a gap.
Continuation cursors are just an optimization; the next scheduled run retries
failed periods and resumes even if a continuation job was lost.
"""
from datetime import datetime, timedelta
import hashlib
import json

import frappe
from frappe.utils import get_datetime, getdate, now_datetime

from .periods import completed_periods, source_hour
from .snapshot import DOCTYPES, create_snapshot

DEFAULT_BATCH_SIZE = 200


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


def _job_id(kind, as_of, cursor=None):
    identity = json.dumps([kind, str(as_of), cursor], sort_keys=True)
    return 'production-summary-' + hashlib.sha256(identity.encode()).hexdigest()


def recover(kind, as_of=None, batch_size=DEFAULT_BATCH_SIZE, cursor=None):
    """Backfill from source history, returning counts and an optional continuation.

    `as_of` freezes the completed-period cutoff for the entire continuation chain.
    The cursor contains site/start, both serialized for RQ. No open periods are
    generated, even when a cron job runs late.
    """
    if kind not in DOCTYPES:
        raise ValueError('Unknown summary kind')
    batch_size = int(batch_size)
    if not 1 <= batch_size <= 10000:
        raise ValueError('batch_size must be between 1 and 10000')
    as_of = get_datetime(as_of) if as_of else now_datetime()
    created = failed = attempted = 0
    continuation = None
    cursor_start = get_datetime(cursor['start']) if cursor else None
    for site, source_date in sorted(get_site_starts().items()):
        if cursor and site < cursor['site']:
            continue
        start = datetime.combine(getdate(source_date), datetime.min.time()).replace(hour=6)
        if cursor and site == cursor['site']:
            start = max(start, cursor_start)
        rows = frappe.get_all(DOCTYPES[kind], filters={'site': site,
            'period_start': ['between', [start, as_of]]}, fields=['period_start'])
        existing = {get_datetime(row['period_start']) for row in rows}
        for period in completed_periods(kind, start, as_of):
            if period.start in existing:
                continue
            if attempted >= batch_size:
                continuation = dict(site=site, start=period.start.isoformat())
                break
            attempted += 1
            try:
                if create_snapshot(site, period):
                    created += 1
            except Exception:
                failed += 1
                frappe.log_error(title=f'{DOCTYPES[kind]}: {site} {period.start}', message=frappe.get_traceback())
        if continuation:
            break
    if continuation:
        frappe.enqueue(method='is_production.production.production_summaries.scheduler.recover',
                       queue='long', timeout=1500, enqueue_after_commit=True,
                       job_id=_job_id(kind, as_of, continuation),
                       deduplicate=True, kind=kind, as_of=as_of.isoformat(), batch_size=batch_size, cursor=continuation)
    return dict(created=created, failed=failed, attempted=attempted, continuation=continuation)


def create_hourly_summaries():
    return recover('hourly')


def create_shift_summaries():
    return _enqueue_recovery('shift')


def create_daily_summaries():
    return _enqueue_recovery('daily')


def _enqueue_recovery(kind):
    # Cron jobs run on the default queue. Put historical work on long instead.
    as_of = now_datetime().replace(minute=0, second=0, microsecond=0)
    return frappe.enqueue(method='is_production.production.production_summaries.scheduler.recover',
                          queue='long', timeout=1500, enqueue_after_commit=True,
                          job_id=_job_id(kind, as_of), deduplicate=True, kind=kind, as_of=as_of.isoformat())


def recover_all_summaries():
    """Hourly long-queue recovery also repairs missed 06:00/18:00 cron runs."""
    as_of = now_datetime()
    return {kind: recover(kind, as_of=as_of) for kind in DOCTYPES}
