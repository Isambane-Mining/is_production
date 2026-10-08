"""Read valid production planning coverage for reporting snapshots."""
from collections import defaultdict

import frappe
from frappe.utils import getdate
from ..production_summaries.eligibility import merge_plan_windows


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
    return merge_plan_windows(grouped)
