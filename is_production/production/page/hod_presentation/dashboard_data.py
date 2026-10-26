"""Rank existing Engine period results; never recompute percentages from daily rows."""
from is_production.production.report.hod_presentation.availability_grids import (
    CATEGORY_LABELS, percentage_value,
)


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
