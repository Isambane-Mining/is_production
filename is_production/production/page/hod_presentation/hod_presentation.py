"""Desk Page entry point; the report remains the calculation and permission owner."""
import frappe

from is_production.production.report.hod_presentation import hod_presentation as report
from is_production.production.report.hod_presentation.hod_presentation import build_grid_data, render_grids


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


# HOD dashboard rankings and explanations
CATEGORY_LABELS = report.CATEGORY_LABELS
percentage_value = report.percentage_value

def recorded_explanation(records):
    """Without duration evidence, multiple explanations do not establish a main cause."""
    placeholders = {'', '-', '—', 'not recorded', 'no reason captured'}
    reasons = {str(row.get('reason') or '').strip() for row in records}
    reasons = {reason for reason in reasons if reason.lower() not in placeholders}
    return next(iter(reasons)) if len(reasons) == 1 else 'Not recorded'


def build_dashboard(module, fleet, site):
    machines = [row for row in fleet['machines'] if row['category'] in CATEGORY_LABELS]
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
        for row in sorted(valid, key=lambda row: (percentage_value(row[metric]), str(row['machine'])))[:5]:
            reasons = get_reasons(row['machine'], fleet['start_date'], fleet['end_date'], location=site) if get_reasons else []
            bottom[metric].append({**row, 'explanation': recorded_explanation(reasons)})
    return {
        'category_averages': fleet['category_averages'], 'averages': fleet['averages'],
        'machine_count': len(machines), 'unavailable': unavailable,
        'top': top, 'bottom': bottom,
    }
