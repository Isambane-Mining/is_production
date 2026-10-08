"""Read production source documents without changing existing workflows."""
from collections import defaultdict

from .production_summary_planning import get_covering_plan
from .production_summary_calculations import build_snapshot


def load_snapshot(site, period):
    """Load the complete operational-day accumulation, including midnight rollover."""
    import frappe

    def documents(doctype, site_field, date_field):
        return frappe.get_all(doctype, filters={site_field: site,
            date_field: ['between', [period.day_start.date(), period.end.date()]],
            'docstatus': ['<', 2]}, fields=['*'], order_by=f'{date_field} asc, name asc')

    hourly = documents('Hourly Production', 'location', 'prod_date')
    drilling = documents('Hourly Drilling Report', 'site', 'date')
    for parents, child_type, parentfield in [(hourly, 'Truck Loads', 'truck_loads'),
                                            (hourly, 'Dozer Production', 'dozer_production'),
                                            (drilling, 'Drills Hourly Entries', 'hourly_entries')]:
        names = [doc['name'] for doc in parents]
        by_parent = defaultdict(list)
        # Bound IN clauses for large imported production days.
        for offset in range(0, len(names), 500):
            rows = frappe.get_all(child_type, filters={'parent': ['in', names[offset:offset + 500]],
                'parenttype': 'Hourly Production' if parents is hourly else 'Hourly Drilling Report',
                'parentfield': parentfield}, fields=['*'], order_by='parent asc, idx asc')
            for row in rows:
                by_parent[row['parent']].append(row)
        for doc in parents:
            doc[parentfield] = by_parent[doc['name']]

    plan_name = get_covering_plan(site, period.report_date)
    plan = frappe.get_doc('Monthly Production Planning', plan_name).as_dict() if plan_name else None
    return build_snapshot(site, period, hourly, drilling, plan)
