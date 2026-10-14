"""Generate synthetic browser input without connecting to a site/database.

Usage: bench-env-python -m is_production.production.page.hourly_productivity_summary.dashboard_browser_fixture /tmp/hps-dashboard.json
"""
import json
import sys
from datetime import datetime
from pathlib import Path

from .summary_dashboard import build_dashboard


def browser_fixture():
    plan = dict(name='2026-10-Koppie', location='Koppie', prod_month_start_date='2026-10-01',
                prod_month_end_date='2026-10-31', shift_system='2x12Hour', num_excavators=2,
                excavator_truck_assignments=[dict(excavator='EX1', truck='T1'), dict(excavator='EX2', truck='T2')],
                month_prod_days=[dict(shift_start_date='2026-10-12', shift_day_hours=12, shift_night_hours=12),
                                 dict(shift_start_date='2026-10-13', shift_day_hours=12, shift_night_hours=0)])
    assets = [dict(name=asset, asset_name=label, location='Koppie', asset_category=category)
              for asset, label, category in [('EX1', 'IS0309', 'Excavator'), ('EX2', 'IS0310', 'Excavator'),
                                            ('T1', 'IS0501', 'ADT'), ('T2', 'IS0502', 'ADT')]]
    documents = []
    for day in [12, 13]:
        for index in range(24):
            if index in [8, 9]:
                continue
            hour = (6 + index) % 24
            calendar_date = f'2026-10-{day + (index >= 18):02}'
            target = 55 if index % 12 == 0 else 165 if index % 12 == 11 else 220
            bcm = target * [1, .9, .65, 0, 1.05][index % 5]
            rows = [dict(asset_name_shoval=excavator, asset_name_truck=truck, bcms=bcm, loads=3 if bcm else 0,
                         exc_start_load_time='06:18' if index < 12 else '18:26',
                         exc_end_load_time='17:34' if index < 12 else '05:42')
                    for excavator, truck in [('EX1', 'T1'), ('EX2', 'T2')]]
            documents.append(dict(name=f'HP-{day}-{index}', location='Koppie', prod_date=calendar_date,
                                  hour_slot=f'{hour:02}:00-{(hour + 1) % 24:02}:00', truck_loads=rows))
    breakdowns = [dict(asset_name='T1', breakdown_start_datetime='2026-10-12 06:00', resolved_datetime='2026-10-12 06:15'),
                  dict(asset_name='T2', breakdown_start_datetime='2026-10-12 09:00', resolved_datetime='2026-10-12 10:00')]
    specs = [dict(site='Koppie', shift='Day', day_type='Weekday', startup_start='06:00', startup_end='06:15',
                  fatigue_start='17:15', fatigue_end='18:00'),
             dict(site='Koppie', shift='Night', day_type='Weekday', startup_start='18:00', startup_end='18:15',
                  fatigue_start='05:15', fatigue_end='06:00')]
    return build_dashboard('Koppie', '2026-10-12', '2026-10-13', 'Full Day', [plan], assets, breakdowns, documents,
                           now=datetime(2026, 10, 14, 7), specifications=specs)


if __name__ == '__main__':
    Path(sys.argv[1]).write_text(json.dumps(browser_fixture()))
