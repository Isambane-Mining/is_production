"""Production snapshots require a non-cancelled plan covering the operational date."""
from collections import defaultdict
from datetime import datetime, timedelta

import frappe
from frappe.utils import getdate
from .periods import completed_periods


def get_covering_plan(site, report_date):
    """Draft plans are valid, matching the existing Hourly Dashboard convention."""
    report_date = getdate(report_date)
    rows = frappe.get_all('Monthly Production Planning', filters={
        'location': site, 'prod_month_start_date': ['<=', report_date],
        'prod_month_end_date': ['>=', report_date], 'docstatus': ['<', 2]},
        fields=['name'], order_by='modified desc, name desc', limit=1)
    return rows[0]['name'] if rows else None


def get_plan_windows():
    """Read once per recovery/search; merge overlapping and adjacent coverage."""
    grouped = defaultdict(list)
    for row in frappe.get_all('Monthly Production Planning', filters={'docstatus': ['<', 2]},
            fields=['location', 'prod_month_start_date', 'prod_month_end_date']):
        if not row.location or not row.prod_month_start_date or not row.prod_month_end_date:
            continue
        start, end = getdate(row.prod_month_start_date), getdate(row.prod_month_end_date)
        if start <= end:
            grouped[row.location].append((start, end))
    result = {}
    for site, windows in grouped.items():
        merged = []
        for start, end in sorted(windows):
            if merged and start <= merged[-1][1] + timedelta(days=1):
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        result[site] = merged
    return result


def eligible_periods(kind, start, as_of, windows):
    """Skip uncovered history without iterating each inactive hour; preserve cursors."""
    covered_until = start
    for first, last in sorted(windows):
        window_start = datetime.combine(first, datetime.min.time()).replace(hour=6)
        window_end = datetime.combine(last + timedelta(days=1), datetime.min.time()).replace(hour=6)
        left, right = max(start, window_start, covered_until), min(as_of, window_end)
        if left < right:
            yield from completed_periods(kind, left, right)
        covered_until = max(covered_until, window_end)
