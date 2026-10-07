"""Read-only Python equivalents of existing truck/shovel and dozer report totals.

Monthly planning figures are captured, never refreshed: its refresh method saves
planning and invokes existing workflows. JSON records the basis and timestamp.
"""
import json
from collections import defaultdict
from datetime import timedelta

from frappe.utils import flt

from .periods import hour_slot, source_hour
from .eligibility import get_covering_plan

COAL_TONS_PER_BCM = 1.5
CALCULATION_VERSION = '1'


def _groups(rows, key_fields, value_field):
    grouped = defaultdict(float)
    for row in rows:
        key = tuple(row.get(field) or '' for field in key_fields)
        grouped[key] += flt(row.get(value_field))
    return [dict(zip(key_fields, key), **{value_field: value}) for key, value in sorted(grouped.items())]


def build_snapshot(site, period, hourly_docs, drilling_docs, plan):
    """Build period totals plus accumulations through this period's exclusive end.

    Source prod_date/date are calendar dates, as in Hourly Dashboard. Never use
    an HP's stored day_total_bcm: it can include later hours and other windows.
    """
    valid = []
    invalid_hours = []
    for doc in hourly_docs:
        if doc.get('location') != site or doc.get('docstatus', 0) >= 2:
            continue
        try:
            start = source_hour(doc.get('prod_date'), doc.get('hour_slot'))
        except (ValueError, TypeError):
            invalid_hours.append(doc)
            continue
        if period.day_start <= start < period.end:
            valid.append((start, doc))
    valid.sort(key=lambda pair: (pair[0], pair[1]['name']))
    selected = [doc for start, doc in valid if period.start <= start]
    accumulated = [doc for _, doc in valid]
    shift_docs = [doc for start, doc in valid if period.shift_start <= start]

    def bcm(docs):
        return sum(flt(doc.get('hour_total_bcm')) for doc in docs)

    trucks = [row for doc in selected for row in doc.get('truck_loads', [])]
    dozers = [row for doc in selected for row in doc.get('dozer_production', [])]
    excavator_rows = [dict(excavator=row.get('excavator_plant_no') or row.get('asset_name_shoval') or 'No Excavator',
                           area=row.get('mining_areas_trucks'), material=row.get('geo_mat_layer_truck'),
                           bcms=flt(row.get('bcms'))) for row in trucks]
    dozer_rows = [dict(dozer=row.get('dozer_plant_no') or row.get('plant_no') or row.get('asset_name'),
                       service=row.get('dozer_service'), bcm_hour=flt(row.get('bcm_hour'))) for row in dozers]
    material = defaultdict(float)
    for row in trucks:
        name = str(row.get('mat_type') or '').lower()
        category = ('Coal' if 'coal' in name else 'Hards' if 'hard' in name else
                    'Softs' if 'soft' in name else 'Other' if name else 'Unassigned')
        material[category] += flt(row.get('bcms'))

    drill_rows, invalid_drills, raw_drills = [], [], []
    for doc in drilling_docs:
        if doc.get('site') != site or doc.get('docstatus', 0) >= 2:
            continue
        raw_drills.append(doc)
        for row in doc.get('hourly_entries', []):
            try:
                start = source_hour(doc.get('date'), row.get('hourly_slot'))
            except (ValueError, TypeError):
                invalid_drills.append(dict(report=doc['name'], row=row))
                continue
            if period.start <= start < period.end:
                drill_rows.append(dict(drill=row.get('drill'), area=row.get('area'),
                                       material=row.get('material'), meters=flt(row.get('meters'))))

    covered = {start for start, _ in valid if period.start <= start}
    expected_hours = int((period.end - period.start).total_seconds() / 3600)
    missing_slots = [hour_slot(period.start + timedelta(hours=i)) for i in range(expected_hours)
                     if period.start + timedelta(hours=i) not in covered]
    missing_data = []
    if not selected:
        missing_data.append('hourly_production')
    if not raw_drills:
        missing_data.append('drilling_reports')
    if not plan:
        missing_data.append('monthly_planning')
    plan = plan or {}

    def planning(field):
        return flt(plan[field]) if plan.get(field) is not None else None

    target, actual = planning('monthly_target_bcm'), planning('month_actual_bcm')
    remaining = target - actual if target is not None and actual is not None else None
    remaining_hours = planning('month_remaining_prod_hours')
    remaining_days = planning('month_remaining_production_days')
    coal_bcm = material.get('Coal', 0)
    result = dict(
        site=site, report_date=period.report_date, shift=period.shift, hour_slot=period.hour_slot,
        period_start=period.start, period_end=period.end, calculation_version=CALCULATION_VERSION,
        period_bcm=bcm(selected), hour_bcm=bcm(selected) if period.kind == 'hourly' else None,
        shift_accumulated_bcm=bcm(shift_docs) if period.kind != 'daily' else None,
        daily_bcm=bcm(accumulated), excavator_bcm=sum(flt(d.get('total_ts_bcm')) for d in selected),
        dozer_bcm=sum(flt(d.get('total_dozing_bcm')) for d in selected),
        drill_meters=sum(row['meters'] for row in drill_rows), coal_bcm=coal_bcm,
        coal_tons=coal_bcm * COAL_TONS_PER_BCM,
        waste_bcm=material.get('Softs', 0) + material.get('Hards', 0) + sum(flt(d.get('total_dozing_bcm')) for d in selected),
        source_hour_count=len(selected), expected_hour_count=expected_hours,
        missing_hour_count=len(missing_slots), source_data_missing=int(bool(missing_data or missing_slots or invalid_hours or invalid_drills)),
        monthly_production_planning=plan.get('name'), monthly_target_bcm=target,
        daily_target=planning('target_bcm_day'), hourly_target=planning('target_bcm_hour'),
        production_to_date=actual, remaining_bcm=remaining,
        actual_hourly_rate=planning('mtd_bcm_hour'), actual_daily_rate=planning('mtd_bcm_day'),
        required_hourly_rate=remaining / remaining_hours if remaining is not None and remaining_hours and remaining_hours > 0 else None,
        required_daily_rate=remaining / remaining_days if remaining is not None and remaining_days and remaining_days > 0 else None,
        strip_ratio=planning('split_ratio'), survey_variance_bcm=planning('monthly_act_tally_survey_variance'),
        forecast_bcm=planning('month_forecated_bcm'),
    )
    raw = dict(calculation_version=CALCULATION_VERSION, site=site,
               period=dict(kind=period.kind, start=period.start, end=period.end, report_date=period.report_date),
               source_date_basis='calendar_date', monthly_values_basis='planning_at_generation',
               monthly_planning=plan, monthly_planning_modified=plan.get('modified'),
               period_source_names=[d['name'] for d in selected],
               accumulation_source_names=[d['name'] for d in accumulated],
               hourly_production=accumulated, drilling_reports=raw_drills,
               invalid_hourly_slots=invalid_hours, invalid_drilling_slots=invalid_drills,
               missing_hour_slots=missing_slots, missing_data=missing_data, material_bcm=dict(material),
               strip_ratio_basis='monthly_planning.split_ratio', coal_tons_per_bcm=COAL_TONS_PER_BCM,
               waste_bcm_basis='softs + hards + production dozing; other/unassigned excluded')
    # Frappe's Float columns coerce None to zero. Preserve null applicability
    # and availability here so future reporting can distinguish the two.
    raw['metrics'] = dict(result)
    for field, data in [('excavator_production_json', _groups(excavator_rows, ('excavator', 'area', 'material'), 'bcms')),
                        ('dozer_production_json', _groups(dozer_rows, ('dozer', 'service'), 'bcm_hour')),
                        ('drill_production_json', _groups(drill_rows, ('drill', 'area', 'material'), 'meters')),
                        ('report_data_json', raw)]:
        result[field] = json.dumps(data, default=str, sort_keys=True)
    return result


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
