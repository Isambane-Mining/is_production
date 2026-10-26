"""Desk Page entry point; the report remains the calculation and permission owner."""
import frappe

from is_production.production.report.hod_presentation import hod_presentation as report
from is_production.production.report.hod_presentation.hod_presentation import build_grid_data, render_grids
from .dashboard_data import build_dashboard


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
            # Both exported slides share the exact same filtered Engine snapshot.
            row['fleet_summary_html'] = render_grids(fleet)
    return result
