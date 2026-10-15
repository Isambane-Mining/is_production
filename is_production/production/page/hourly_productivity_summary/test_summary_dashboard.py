from copy import deepcopy
from datetime import datetime
from unittest import TestCase
from unittest.mock import patch

from is_production.production.page.hourly_productivity_summary import summary_dashboard as dashboard


class TestSummaryDashboard(TestCase):
    def setUp(self):
        self.plan = dict(name='MPP', location='Koppie', prod_month_start_date='2026-10-01',
                         prod_month_end_date='2026-10-31', num_excavators=1, shift_system='2x12Hour',
                         excavator_truck_assignments=[dict(excavator='EX1', truck='T1')],
                         month_prod_days=[dict(shift_start_date='2026-10-12', shift_day_hours=12, shift_night_hours=12)])
        self.assets = [dict(name=x, asset_name=x, location='Koppie', asset_category=c)
                       for x, c in [('EX1', 'Excavator'), ('T1', 'ADT')]]

    def doc(self, hour=6, bcm=0, loads=0, date='2026-10-12', **times):
        return dict(name=f'{date}-{hour}', location='Koppie', prod_date=date,
                    hour_slot=f'{hour:02}:00-{(hour+1)%24:02}:00',
                    truck_loads=[dict(asset_name_shoval='EX1', asset_name_truck='T1',
                                      bcms=bcm, loads=loads, **times)], dozer_production=[dict(bcms=9999)])

    def build(self, docs=(), pbm=(), **kw):
        return dashboard.build_dashboard('Koppie', kw.pop('from_date', '2026-10-12'),
                                          kw.pop('to_date', '2026-10-12'), kw.pop('shift', 'Full Day'),
                                          kw.pop('plans', [self.plan]), self.assets, pbm, docs,
                                          now=kw.pop('now', datetime(2026, 12, 1)), **kw)

    def test_full_day_midnight_and_no_duplicates(self):
        doc = self.doc(0, 10, 1, '2026-10-13')
        result = self.build([doc, doc, self.doc(6, 999, date='2026-10-13')])
        self.assertEqual(len(result['days'][0]['hours']), 24)
        self.assertEqual(result['summary']['actual_bcm'], 10)
        self.assertEqual(result['summary']['target_bcm'], 4840)
        self.assertEqual(result['days'][0]['hours'][18]['actual_bcm'], 10)

    def test_month_specific_count_and_site(self):
        nov = deepcopy(self.plan)
        nov.update(name='NOV', num_excavators=2, prod_month_start_date='2026-11-01', prod_month_end_date='2026-11-30',
                   month_prod_days=[dict(shift_start_date='2026-11-01', shift_day_hours=12, shift_night_hours=12)])
        self.plan['month_prod_days'][0]['shift_start_date'] = '2026-10-31'
        other = dict(nov, name='OTHER', location='Other', num_excavators=99)
        result = self.build(from_date='2026-10-31', to_date='2026-11-01', plans=[self.plan, nov, other])
        self.assertEqual(result['summary']['target_bcm'], 14520)
        self.assertEqual([d['plan_name'] for d in result['days']], ['MPP', 'NOV'])

    def test_night_at_month_end_uses_start_date_plan(self):
        self.plan['month_prod_days'][0]['shift_start_date'] = '2026-10-31'
        november = deepcopy(self.plan)
        november.update(name='NOV', num_excavators=9, prod_month_start_date='2026-11-01', prod_month_end_date='2026-11-30')
        result = self.build([self.doc(0, 220, 1, '2026-11-01')], shift='Night',
                            from_date='2026-10-31', to_date='2026-10-31', plans=[self.plan, november])
        self.assertEqual(result['summary']['target_bcm'], 2420)
        self.assertEqual(result['days'][0]['hours'][6]['category'], 'green')
        self.assertEqual(result['days'][0]['hours'][-1]['end'], '2026-11-01 06:00:00')

    def test_future_non_working_visible_but_not_in_completed_pie(self):
        self.plan['month_prod_days'][0]['shift_night_hours'] = 0
        result = self.build(now=datetime(2026, 10, 12, 12))
        self.assertTrue(all(h['category'] == 'grey' for h in result['days'][0]['hours'][12:]))
        self.assertEqual(result['pie']['counts']['grey'], 0)
        self.assertEqual(result['excluded']['unfinished'], 18)

    def test_saved_zero_count_is_not_inferred_as_zero_required_hours(self):
        self.plan['num_excavators'] = 0
        self.plan['excavator_truck_assignments'] = []
        result = self.build(shift='Day')
        self.assertEqual(result['excluded']['no_target'], 12)
        self.assertEqual(result['pie']['counts']['grey'], 0)

    def test_all_captured_output_including_unverified_machines_is_retained(self):
        doc = self.doc(6, 40, 1)
        doc['truck_loads'].append(dict(asset_name_shoval='UNPAIRED', asset_name_truck='T1', bcms=15, loads=1))
        result = self.build([doc], shift='Day')
        self.assertEqual(result['summary']['actual_bcm'], 55)
        self.assertEqual(result['days'][0]['hours'][0]['category'], 'green')
        unknown = next(r for r in result['days'][0]['excavator_production'] if r['asset'] == 'UNPAIRED')
        self.assertEqual(unknown['availability_status'], 'Unverified')
        self.assertEqual(result['summary']['target_bcm'], 2420)

    def test_zero_hours_not_weekend_assumptions(self):
        self.plan['month_prod_days'] = [dict(shift_start_date='2026-10-11', shift_day_hours=12, shift_night_hours=0)]
        result = self.build(from_date='2026-10-11', to_date='2026-10-11')
        self.assertEqual(result['summary']['target_bcm'], 2420)
        self.assertEqual(result['pie']['counts']['grey'], 12)
        self.assertEqual(result['pie']['total_hours'], 12)
        self.assertTrue(all(h['category'] == 'neutral' for h in result['days'][0]['hours'][:12]))

    def test_reduced_or_absent_schedule_is_unknown(self):
        self.plan['month_prod_days'][0]['shift_day_hours'] = 6
        result = self.build(shift='Day')
        self.assertIsNone(result['summary']['target_bcm'])
        self.assertEqual(result['pie']['total_hours'], 0)
        self.assertEqual(result['excluded']['schedule_unknown'], 12)
        self.plan['month_prod_days'] = []
        self.assertEqual(self.build(shift='Day')['excluded']['schedule_unknown'], 12)
        self.plan['shift_system'] = '3x8Hour'
        self.assertEqual(self.build()['excluded']['schedule_unknown'], 24)

    def test_five_categories_percentages_and_missing(self):
        self.plan['month_prod_days'][0]['shift_night_hours'] = 0
        docs = [self.doc(6, 55, 1), self.doc(7, 187, 1), self.doc(8, 100, 1), self.doc(9)]
        result = self.build(docs)
        self.assertEqual(result['pie']['counts'], dict(green=1, amber=1, red=1, bright_red=1, grey=12))
        self.assertEqual(result['pie']['total_hours'], 16)
        self.assertAlmostEqual(sum(result['pie']['percentages'].values()), 100)
        self.assertEqual(result['excluded']['missing'], 8)
        self.assertEqual(result['pie']['percentages']['green'], 6.25)

    def test_pbm_does_not_reduce_target_or_claim_zero_with_available_machine(self):
        pbm = [dict(asset_name='EX1', breakdown_start_datetime='2026-10-12 09:00', resolved_datetime='2026-10-12 10:00')]
        result = self.build([self.doc(9)], pbm, shift='Day')
        h = result['days'][0]['hours'][3]
        self.assertEqual(h['category'], 'red')
        self.assertEqual(h['target_bcm'], 220)
        self.assertEqual(h['excavator_unavailable'], 1)
        self.plan['num_excavators'] = 2
        h = self.build([self.doc(9)], shift='Day')['days'][0]['hours'][3]
        self.assertEqual(h['category'], 'red')  # Unknown complete production roster cannot prove availability.
        self.assertIsNone(h['excavator_available'])

    def test_future_and_incomplete_excluded_and_completed_kpi_unchanged(self):
        result = self.build([self.doc(6, 55, 1), self.doc(7, 220, 1)], shift='Day', now=datetime(2026, 10, 12, 7, 30))
        self.assertEqual(result['pie']['total_hours'], 1)
        self.assertEqual(result['summary']['completed_target_bcm'], 55)
        self.assertEqual(result['summary']['completed_actual_bcm'], 55)
        self.assertEqual(result['excluded']['unfinished'], 11)

    def test_average_hourly_utilisation_and_timeline_missing(self):
        result = self.build([self.doc(6, 55, 1), self.doc(7, 0, 0)], shift='Day')
        self.assertEqual(result['summary']['average_adts_utilised'], 0.5)
        self.assertEqual(result['summary']['average_excavators_utilised'], 0.5)
        timeline = result['days'][0]['timeline'][0]['cells']
        self.assertEqual(timeline[0]['state'], 'used')
        self.assertEqual(timeline[1]['state'], 'idle')
        self.assertEqual(timeline[2]['state'], 'missing')
        self.assertIsNone(timeline[2]['loads'])

    def test_timeline_partial_pbm_union(self):
        pbm = [dict(asset_name='T1', breakdown_start_datetime='2026-10-12 06:00', resolved_datetime=end)
               for end in ['2026-10-12 06:15', '2026-10-12 06:30']]
        cells = self.build([self.doc(6, 20, 3)], pbm, shift='Day')['days'][0]['timeline'][0]['cells']
        self.assertEqual(cells[0]['available_minutes'], 30)
        self.assertEqual(cells[0]['loads'], 3)
        self.assertEqual(cells[0]['state'], 'partial')

    def test_captured_timings_separate_shifts_and_restart(self):
        docs = [self.doc(6, 10, 1, exc_start_load_time='06:18', exc_end_load_time='07:00'),
                self.doc(10, 10, 1, exc_start_load_time='10:20', exc_end_load_time='17:34'),
                self.doc(18, 10, 1, exc_start_load_time='18:26', exc_end_load_time='22:00'),
                self.doc(0, 10, 1, '2026-10-13', exc_start_load_time='00:30', exc_end_load_time='05:42')]
        rows = self.build(docs)['days'][0]['excavator_production']
        self.assertEqual([(r['shift'], r['first_load_time'], r['last_load_time']) for r in rows],
                         [('Day', '06:18', '17:34'), ('Night', '18:26', '05:42')])

    def test_startup_specs_dynamic_night_and_no_target_exclusion(self):
        specs = [dict(site='Koppie', shift='Night', day_type='Weekday', startup_start='23:30', startup_end='00:15',
                      fatigue_start='03:20', fatigue_end='03:40'),
                 dict(site='Other', shift='Night', day_type='Weekday', startup_start='18:00', startup_end='19:00')]
        result = self.build(specifications=specs)
        indicators = result['days'][0]['indicators']
        self.assertEqual(indicators[0]['label'], 'Startup')
        self.assertEqual(indicators[0]['start'], '2026-10-12 23:30:00')
        self.assertEqual(indicators[0]['end'], '2026-10-13 00:15:00')
        self.assertEqual(result['summary']['target_bcm'], 4840)

    def test_validation_and_inputs_unchanged(self):
        original = deepcopy(self.plan)
        self.build()
        self.assertEqual(self.plan, original)
        for kw in [dict(from_date='2026-10-13'), dict(shift='Other'), dict(to_date='2028-01-01')]:
            with self.assertRaises(ValueError):
                self.build(**kw)


class TestDashboardPermissions(TestCase):
    def test_guest_denied_before_any_query(self):
        with patch.object(dashboard.frappe, 'session', dashboard.frappe._dict(user='Guest')), \
                patch.object(dashboard.source, 'load_sources') as load:
            with self.assertRaises(dashboard.frappe.PermissionError):
                dashboard.get_dashboard('Koppie', '2026-10-12', '2026-10-12')
            load.assert_not_called()

    def test_optional_specification_access_does_not_bypass_permissions(self):
        fixture = TestSummaryDashboard()
        fixture.setUp()
        with patch.object(dashboard.frappe, 'session', dashboard.frappe._dict(user='user@example.com')), \
                patch.object(dashboard.source, 'load_sources', return_value=([fixture.plan], [], fixture.assets, [])) as load, \
                patch.object(dashboard.frappe, 'has_permission', return_value=False), \
                patch.object(dashboard, 'now_datetime', return_value=datetime(2026, 10, 14)), \
                patch.object(dashboard.source, '_permitted_rows') as rows:
            result = dashboard.get_dashboard('Koppie', '2026-10-12', '2026-10-12', 'Full Day')
            rows.assert_not_called()
            self.assertEqual(result['days'][0]['indicators'], [])
            self.assertEqual(load.call_args.args, ('Koppie', datetime(2026, 10, 12, 6), datetime(2026, 10, 13, 6)))

    def test_masked_mpp_schedule_and_specification_fields_fail_closed(self):
        class Document:
            restricted = False
            def check_permission(self, permission):
                pass
            def apply_fieldlevel_read_permissions(self):
                self.restricted = True
            def as_dict(self):
                return {} if self.restricted else dict(month_prod_days=[dict(shift_day_hours=12)], startup_start='06:00')
        for doctype in ['Monthly Production Planning', dashboard.SPECIFICATION]:
            with patch.object(dashboard.frappe, 'get_doc', return_value=Document()):
                with self.assertRaises(dashboard.frappe.PermissionError):
                    dashboard.source._read_document(doctype, 'restricted')
