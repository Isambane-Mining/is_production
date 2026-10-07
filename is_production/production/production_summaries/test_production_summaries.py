"""Run with the bench Python through unittest; no live site writes."""
import json
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

import frappe

from is_production.production.production_summaries import periods, calculations


class TestPeriods(unittest.TestCase):
    def test_completed_hour_and_operational_rollover(self):
        cases = [
            (datetime(2026, 10, 1, 0, 12), date(2026, 9, 30), 'Night', '23:00-00:00'),
            (datetime(2026, 10, 1, 1), date(2026, 9, 30), 'Night', '00:00-01:00'),
            (datetime(2026, 10, 1, 6), date(2026, 9, 30), 'Night', '05:00-06:00'),
            (datetime(2026, 10, 1, 7), date(2026, 10, 1), 'Day', '06:00-07:00'),
            (datetime(2027, 1, 1, 1), date(2026, 12, 31), 'Night', '00:00-01:00'),
        ]
        for now, report_date, shift, slot in cases:
            with self.subTest(now=now):
                p = periods.completed_hour(now)
                self.assertEqual((p.report_date, p.shift, p.hour_slot), (report_date, shift, slot))
                self.assertEqual(p.end - p.start, timedelta(hours=1))

    def test_day_night_and_full_daily_boundaries(self):
        day = periods.make_period('shift', datetime(2026, 9, 30, 6))
        night = periods.make_period('shift', datetime(2026, 9, 30, 18))
        daily = periods.make_period('daily', datetime(2026, 9, 30, 6))
        self.assertEqual((day.end, day.shift), (datetime(2026, 9, 30, 18), 'Day'))
        self.assertEqual((night.end, night.report_date, night.shift),
                         (datetime(2026, 10, 1, 6), date(2026, 9, 30), 'Night'))
        self.assertEqual((daily.end, daily.shift), (datetime(2026, 10, 1, 6), 'Full Daily'))
        self.assertEqual(list(periods.completed_periods('daily', daily.start, datetime(2026, 10, 1, 5, 59))), [])
        self.assertEqual(len(list(periods.completed_periods('daily', daily.start, daily.end))), 1)

    def test_normalizes_legacy_slots_and_rejects_ambiguous_drill_slots(self):
        self.assertEqual(periods.source_hour('2026-10-01', '0:00-1:00'), datetime(2026, 10, 1))
        self.assertEqual(periods.source_hour('2026-09-30', '23:00-24:00'), datetime(2026, 9, 30, 23))
        for slot in ['00:00-12:00', '12:00-01:00', 'garbage', '25:00-26:00', '6:30-7:30']:
            with self.subTest(slot=slot), self.assertRaises(ValueError):
                periods.source_hour('2026-10-01', slot)

    def test_unaligned_periods_are_rejected(self):
        for kind, start in [('hourly', datetime(2026, 10, 1, 6, 15)),
                            ('shift', datetime(2026, 10, 1, 7)), ('daily', datetime(2026, 10, 1, 18))]:
            with self.assertRaises(ValueError):
                periods.make_period(kind, start)


def hourly(name, stamp, bcm, shift='Night', location='Koppie', docstatus=0):
    return dict(name=name, location=location, prod_date=stamp.date(),
                hour_slot=f'{stamp.hour:02}:00-{(stamp.hour + 1) % 24:02}:00', shift=shift,
                docstatus=docstatus, hour_total_bcm=bcm, total_ts_bcm=bcm - 20,
                total_dozing_bcm=20, truck_loads=[dict(asset_name_shoval='EX-1', mat_type='Coal', bcms=bcm - 20)],
                dozer_production=[dict(plant_no='DZ-1', bcm_hour=20, dozer_service='Production Dozing-50m')])


class TestCalculations(unittest.TestCase):
    def test_hour_shift_and_daily_accumulations_stop_at_period_end(self):
        docs = [hourly('day', datetime(2026, 9, 30, 6), 100, 'Day'),
                hourly('night', datetime(2026, 9, 30, 18), 200),
                hourly('midnight', datetime(2026, 10, 1, 0), 300),
                hourly('future', datetime(2026, 10, 1, 1), 900)]
        plan = dict(name='MPP', modified='2026-10-07', monthly_target_bcm=10000,
                    month_actual_bcm=4000, target_bcm_day=1000, target_bcm_hour=50,
                    mtd_bcm_hour=40, mtd_bcm_day=800, month_remaining_prod_hours=120,
                    month_remaining_production_days=6, split_ratio=2.5,
                    monthly_act_tally_survey_variance=700, month_forecated_bcm=8000)
        p = periods.make_period('hourly', datetime(2026, 10, 1))
        result = calculations.build_snapshot('Koppie', p, docs, [], plan)
        self.assertEqual((result['hour_bcm'], result['period_bcm'], result['shift_accumulated_bcm'], result['daily_bcm']),
                         (300, 300, 500, 600))
        self.assertEqual((result['excavator_bcm'], result['dozer_bcm'], result['coal_bcm'], result['coal_tons']),
                         (280, 20, 280, 420))
        self.assertEqual((result['remaining_bcm'], result['required_hourly_rate'], result['required_daily_rate']),
                         (6000, 50, 1000))
        self.assertEqual((result['strip_ratio'], result['survey_variance_bcm']), (2.5, 700))
        raw = json.loads(result['report_data_json'])
        self.assertEqual(raw['monthly_values_basis'], 'planning_at_generation')
        self.assertEqual(raw['period_source_names'], ['midnight'])
        self.assertNotIn('future', raw['accumulation_source_names'])

    def test_full_day_excludes_adjacent_days_cancelled_and_other_sites(self):
        p = periods.make_period('daily', datetime(2026, 9, 30, 6))
        docs = [hourly('before', p.start - timedelta(hours=1), 900),
                hourly('day', p.start, 100, 'Day'), hourly('night', p.start + timedelta(hours=12), 200),
                hourly('last', p.end - timedelta(hours=1), 300), hourly('next', p.end, 900),
                hourly('cancelled', p.start, 999, docstatus=2), hourly('elsewhere', p.start, 999, location='Gwab')]
        result = calculations.build_snapshot('Koppie', p, docs, [], None)
        self.assertEqual((result['period_bcm'], result['daily_bcm']), (600, 600))
        self.assertEqual(result['source_hour_count'], 3)
        self.assertEqual(json.loads(result['report_data_json'])['missing_hour_slots'].__len__(), 21)
        self.assertIn('monthly_planning', json.loads(result['report_data_json'])['missing_data'])
        night = calculations.build_snapshot('Koppie', periods.make_period('shift', p.start + timedelta(hours=12)), docs, [], None)
        self.assertEqual((night['period_bcm'], night['shift_accumulated_bcm'], night['daily_bcm']), (500, 500, 600))

    def test_drill_meters_and_invalid_slots_are_traceable(self):
        p = periods.make_period('hourly', datetime(2026, 10, 1))
        drills = [dict(name='HDR', site='Koppie', date='2026-10-01', docstatus=0,
                       hourly_entries=[dict(drill='DR-1', hourly_slot='00:00-01:00', meters=15),
                                       dict(drill='DR-1', hourly_slot='00:00-12:00', meters=999)])]
        result = calculations.build_snapshot('Koppie', p, [], drills, None)
        self.assertEqual(result['drill_meters'], 15)
        raw = json.loads(result['report_data_json'])
        self.assertEqual(len(raw['invalid_drilling_slots']), 1)
        self.assertEqual(raw['drilling_reports'][0]['name'], 'HDR')

    def test_empty_period_and_zero_divisors(self):
        p = periods.make_period('hourly', datetime(2026, 10, 1, 6))
        result = calculations.build_snapshot('Koppie', p, [], [], dict(monthly_target_bcm=100))
        self.assertEqual(result['period_bcm'], 0)
        self.assertIsNone(result['required_hourly_rate'])
        self.assertIsNone(json.loads(result['report_data_json'])['metrics']['required_hourly_rate'])
        self.assertEqual(json.loads(result['report_data_json'])['missing_hour_slots'], ['06:00-07:00'])


class TestSnapshotIdentity(unittest.TestCase):
    def test_site_kind_and_period_have_distinct_stable_keys(self):
        from . import snapshot
        p = periods.make_period('hourly', datetime(2026, 10, 1, 6))
        self.assertEqual(snapshot.snapshot_key('Koppie', p), snapshot.snapshot_key('Koppie', p))
        keys = {snapshot.snapshot_key('Koppie', p), snapshot.snapshot_key('Gwab', p),
                snapshot.snapshot_key('Koppie', periods.make_period('shift', p.start)),
                snapshot.snapshot_key('Koppie', periods.make_period('hourly', p.end))}
        self.assertEqual(len(keys), 4)

    def test_existing_summary_is_not_recalculated(self):
        from . import snapshot
        from types import SimpleNamespace
        p = periods.make_period('hourly', datetime(2026, 10, 1, 6))
        with patch.object(snapshot.frappe, 'db', SimpleNamespace(exists=lambda *a: True)), \
             patch.object(snapshot, 'load_snapshot', side_effect=AssertionError('must not recalculate')):
            self.assertIsNone(snapshot.create_snapshot('Koppie', p))


class TestScheduler(unittest.TestCase):
    """Only storage/queue IO is replaced; run the real recovery loop."""
    def run_recovery(self, kind, now, sources, existing=None, fail=None, batch_size=200, cursor=None):
        from . import scheduler
        from types import SimpleNamespace
        saved = existing if existing is not None else set()
        created, jobs, errors = [], [], []

        def create(site, period):
            if fail and fail(site, period):
                raise RuntimeError('source failure')
            key = (site, period.start)
            if key not in saved:
                saved.add(key)
                created.append(key)
                return str(key)

        def get_all(doctype, **kwargs):
            site = kwargs['filters']['site']
            return [frappe._dict(period_start=start) for found_site, start in saved if found_site == site]

        with patch.object(scheduler, 'get_plan_windows', return_value={site: [(getdate, now.date())] for site, getdate in sources.items()}), \
             patch.object(scheduler, 'get_site_starts', return_value=sources), \
             patch.object(scheduler, 'create_snapshot', side_effect=create), \
             patch.object(scheduler.frappe, 'get_all', side_effect=get_all), \
             patch.object(scheduler.frappe, 'enqueue', side_effect=lambda **kw: jobs.append(kw)), \
             patch.object(scheduler.frappe, 'log_error', side_effect=lambda **kw: errors.append(kw)), \
             patch.object(scheduler.frappe, 'get_traceback', return_value='trace'):
            result = scheduler.recover(kind, as_of=now, batch_size=batch_size, cursor=cursor)
        return created, jobs, errors, result

    def test_outage_longer_than_24_hours_and_existing_holes(self):
        sources = {'Koppie': date(2026, 9, 30), 'Gwab': date(2026, 9, 30)}
        existing = {('Koppie', datetime(2026, 9, 30, 7)), ('Koppie', datetime(2026, 10, 1, 7))}
        created, jobs, errors, _ = self.run_recovery('hourly', datetime(2026, 10, 2, 6), sources, existing)
        self.assertEqual(len(created), 94)
        self.assertIn(('Koppie', datetime(2026, 9, 30, 6)), created)
        self.assertIn(('Koppie', datetime(2026, 10, 2, 5)), created)
        self.assertEqual((jobs, errors), ([], []))
        rerun, _, _, _ = self.run_recovery('hourly', datetime(2026, 10, 2, 6), sources, existing)
        self.assertEqual(rerun, [])

    def test_batch_continuation_is_bounded_and_resumes_after_commit(self):
        sources, existing = {'Koppie': date(2026, 9, 30)}, set()
        now = datetime(2026, 10, 2, 6)
        created, jobs, _, _ = self.run_recovery('hourly', now, sources, existing, batch_size=24)
        self.assertEqual(len(created), 24)
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0]['enqueue_after_commit'])
        self.assertEqual(jobs[0]['as_of'], '2026-10-02T06:00:00')
        self.assertNotIn('now', jobs[0])
        created2, jobs2, _, _ = self.run_recovery('hourly', now, sources, existing, batch_size=24, cursor=jobs[0]['cursor'])
        self.assertEqual(len(created2), 24)
        self.assertEqual(jobs2, [])
        self.assertEqual(len(existing), 48)

    def test_failed_site_does_not_stop_other_site_and_is_retried(self):
        sources, existing = {'Gwab': date(2026, 10, 1), 'Koppie': date(2026, 10, 1)}, set()
        created, _, errors, _ = self.run_recovery('hourly', datetime(2026, 10, 1, 7), sources, existing,
                                                 fail=lambda site, p: site == 'Gwab')
        self.assertEqual(created, [('Koppie', datetime(2026, 10, 1, 6))])
        self.assertEqual(len(errors), 1)
        retried, _, _, _ = self.run_recovery('hourly', datetime(2026, 10, 1, 7), sources, existing)
        self.assertEqual(retried, [('Gwab', datetime(2026, 10, 1, 6))])

    def test_shift_and_daily_recovery_only_generate_completed_periods(self):
        sources = {'Koppie': date(2026, 9, 30)}
        for kind, now, starts in [
            ('shift', datetime(2026, 9, 30, 17, 59), []),
            ('shift', datetime(2026, 9, 30, 18), [datetime(2026, 9, 30, 6)]),
            ('shift', datetime(2026, 10, 1, 6), [datetime(2026, 9, 30, 6), datetime(2026, 9, 30, 18)]),
            ('daily', datetime(2026, 10, 1, 5, 59), []),
            ('daily', datetime(2026, 10, 1, 6), [datetime(2026, 9, 30, 6)]),
        ]:
            with self.subTest(kind=kind, now=now):
                created, _, _, _ = self.run_recovery(kind, now, sources)
                self.assertEqual([start for site, start in created], starts)

    def test_invalid_batch_size_rejected(self):
        from . import scheduler
        for size in [0, -1, 10001]:
            with self.assertRaises(ValueError):
                scheduler.recover('hourly', as_of=datetime(2026, 10, 1, 7), batch_size=size)


class TestFrappeIntegration(unittest.TestCase):
    """Real schema/document tests on a disposable Frappe SQLite site only.

    Set PRODUCTION_SUMMARY_TEST_SITES to /tmp/production-summary-validation-*/sites.
    This never initializes the working bench's production site.
    """
    @classmethod
    def setUpClass(cls):
        import os
        from pathlib import Path
        sites = os.environ.get('PRODUCTION_SUMMARY_TEST_SITES')
        if not sites:
            raise unittest.SkipTest('Set PRODUCTION_SUMMARY_TEST_SITES for isolated Frappe integration tests')
        sites = Path(sites).resolve()
        if not str(sites).startswith('/tmp/production-summary-validation-'):
            raise ValueError('Integration tests require a disposable /tmp test site')
        config = json.loads((sites / 'summary-test.local' / 'site_config.json').read_text())
        if config['db_type'] != 'sqlite':
            raise ValueError('Integration tests only write to the disposable SQLite database')
        cls.previous_cwd = os.getcwd()
        os.chdir(sites)
        frappe.init('summary-test.local', sites_path=str(sites))
        frappe.connect()
        frappe.db.set_global('installed_apps', json.dumps(['frappe', 'is_production']))
        frappe.local.request_cache.clear()
        frappe.flags.in_test = True
        frappe.flags.mute_emails = True
        frappe.flags.in_import = True
        cls.make_source_schema()
        for role in ['Production Manager', 'Production User', 'Production Supervisor',
                     'Production Foreman', 'Control Clerk', 'Drill Supervisor']:
            if not frappe.db.exists('Role', role):
                frappe.get_doc(dict(doctype='Role', role_name=role)).insert(ignore_permissions=True)
        if not frappe.db.exists('Module Def', 'Production'):
            frappe.get_doc(dict(doctype='Module Def', module_name='Production', app_name='is_production')).insert(ignore_permissions=True)
        for slug in ['hourly_production_summary', 'shift_production_summary', 'daily_production_summary']:
            frappe.reload_doc('production', 'doctype', slug, force=True)
        frappe.flags.in_import = False
        frappe.db.commit()

    @classmethod
    def make_source_schema(cls):
        """Minimal relational source fixtures; no existing source controller runs."""
        defs = {
            'Location': [('location_name', 'Data')],
            'Monthly Production Planning': [('location', 'Data'), ('prod_month_start_date', 'Date'),
                ('prod_month_end_date', 'Date'), ('monthly_target_bcm', 'Float'), ('month_actual_bcm', 'Float'),
                ('target_bcm_day', 'Float'), ('target_bcm_hour', 'Float'), ('mtd_bcm_hour', 'Float'), ('mtd_bcm_day', 'Float'),
                ('month_remaining_prod_hours', 'Float'), ('month_remaining_production_days', 'Float'),
                ('split_ratio', 'Float'), ('monthly_act_tally_survey_variance', 'Float'), ('month_forecated_bcm', 'Float')],
            'Hourly Production': [('location', 'Data'), ('prod_date', 'Date'), ('hour_slot', 'Data'), ('shift', 'Data'),
                ('hour_total_bcm', 'Float'), ('total_ts_bcm', 'Float'), ('total_dozing_bcm', 'Float')],
            'Hourly Drilling Report': [('site', 'Data'), ('date', 'Date')],
            'Truck Loads': [('asset_name_shoval', 'Data'), ('mat_type', 'Data'), ('bcms', 'Float')],
            'Dozer Production': [('asset_name', 'Data'), ('dozer_service', 'Data'), ('bcm_hour', 'Float')],
            'Drills Hourly Entries': [('drill', 'Data'), ('hourly_slot', 'Data'), ('meters', 'Float')],
        }
        for name, fields in defs.items():
            if not frappe.db.exists('DocType', name):
                frappe.get_doc(dict(doctype='DocType', name=name, module='Custom', custom=1,
                    istable=int(name in ['Truck Loads', 'Dozer Production', 'Drills Hourly Entries']),
                    fields=[dict(fieldname=n, fieldtype=t, label=n) for n, t in fields],
                    permissions=[] if name in ['Truck Loads', 'Dozer Production', 'Drills Hourly Entries'] else
                        [dict(role='System Manager', read=1, create=1, write=1, delete=1)])).insert(ignore_permissions=True)

    @classmethod
    def tearDownClass(cls):
        import os
        frappe.db.rollback()
        frappe.destroy()
        os.chdir(cls.previous_cwd)

    def setUp(self):
        # Only disposable fixture and new snapshot tables are cleared.
        from .snapshot import DOCTYPES
        for doctype in [*DOCTYPES.values(), 'Truck Loads', 'Dozer Production', 'Drills Hourly Entries',
                        'Hourly Production', 'Hourly Drilling Report', 'Monthly Production Planning', 'Location']:
            frappe.db.delete(doctype)
        self.fixture('Location', 'Koppie', location_name='Koppie')
        self.fixture('Location', 'Gwab', location_name='Gwab')
        for site in ['Koppie']:
            self.fixture('Monthly Production Planning', 'BASE-'+site, location=site,
                         prod_month_start_date='2026-10-01', prod_month_end_date='2026-10-01')

    def fixture(self, doctype, name, **values):
        doc = frappe.get_doc(dict(doctype=doctype, name=name, **values))
        doc.db_insert()  # Source fixtures bypass all existing workflow hooks.
        return doc

    def source(self, name, when, bcm, site='Koppie', status=0):
        self.fixture('Hourly Production', name, location=site, prod_date=when.date(),
                     hour_slot=f'{when.hour:02}:00-{(when.hour + 1) % 24:02}:00',
                     hour_total_bcm=bcm, total_ts_bcm=bcm-20, total_dozing_bcm=20, docstatus=status)
        self.fixture('Truck Loads', name+'-truck', parent=name, parenttype='Hourly Production',
                     parentfield='truck_loads', bcms=bcm-20, asset_name_shoval='EX-1', mat_type='Coal')
        self.fixture('Dozer Production', name+'-dozer', parent=name, parenttype='Hourly Production',
                     parentfield='dozer_production', bcm_hour=20, asset_name='DZ-1', dozer_service='Production Dozing-50m')

    def test_real_loader_and_all_three_document_insertions(self):
        from . import snapshot
        self.source('day', datetime(2026, 9, 30, 6), 100)
        self.source('night', datetime(2026, 9, 30, 18), 200)
        self.source('last', datetime(2026, 10, 1, 5), 300)
        self.source('next', datetime(2026, 10, 1, 6), 900)
        self.source('cancelled', datetime(2026, 9, 30, 7), 999, status=2)
        self.fixture('Monthly Production Planning', 'PLAN', location='Koppie',
                     prod_month_start_date='2026-09-01', prod_month_end_date='2026-09-30',
                     monthly_target_bcm=10000, month_actual_bcm=4000, split_ratio=2.5)
        self.fixture('Hourly Drilling Report', 'HDR', site='Koppie', date='2026-10-01')
        self.fixture('Drills Hourly Entries', 'DRILL', parent='HDR', parenttype='Hourly Drilling Report',
                     parentfield='hourly_entries', drill='DR-1', hourly_slot='05:00-06:00', meters=12)
        for kind, start, total in [('hourly', datetime(2026, 10, 1, 5), 300),
                                   ('shift', datetime(2026, 9, 30, 18), 500),
                                   ('daily', datetime(2026, 9, 30, 6), 600)]:
            p = periods.make_period(kind, start)
            name = snapshot.create_snapshot('Koppie', p)
            doc = frappe.get_doc(snapshot.DOCTYPES[kind], name)
            self.assertEqual(doc.period_bcm, total)
            self.assertEqual(doc.report_date, date(2026, 9, 30))
            self.assertEqual(doc.drill_meters, 12)
            self.assertEqual(doc.strip_ratio, 2.5)
            self.assertEqual(json.loads(doc.dozer_production_json)[0]['dozer'], 'DZ-1')
            self.assertIsNone(snapshot.create_snapshot('Koppie', p))
            self.assertEqual(frappe.db.count(snapshot.DOCTYPES[kind]), 1)
        self.assertEqual(frappe.db.get_value('Monthly Production Planning', 'PLAN', 'month_actual_bcm'), 4000)
        self.assertEqual(frappe.db.get_value('Hourly Production', 'next', 'hour_total_bcm'), 900)

    def test_database_primary_and_unique_key_protect_against_worker_races(self):
        from . import snapshot
        p = periods.make_period('hourly', datetime(2026, 10, 1, 6))
        name = snapshot.create_snapshot('Koppie', p)
        real_exists = frappe.db.exists
        def race(doctype, *args, **kwargs):
            if doctype == 'Hourly Production Summary':
                return False
            return real_exists(doctype, *args, **kwargs)
        with patch.object(frappe.db, 'exists', side_effect=race):
            self.assertIsNone(snapshot.create_snapshot('Koppie', p))
        self.assertEqual(frappe.db.count('Hourly Production Summary'), 1)
        stored = frappe.get_doc('Hourly Production Summary', name)
        self.assertIsNone(json.loads(stored.report_data_json)['metrics']['required_hourly_rate'])
        duplicate = frappe.get_doc('Hourly Production Summary', name)
        duplicate.name = 'alternate-name'
        with self.assertRaises(frappe.UniqueValidationError):
            duplicate.db_insert()

    def test_real_save_delete_rename_and_manual_insert_are_denied(self):
        from . import snapshot
        from frappe.model.rename_doc import rename_doc
        p = periods.make_period('hourly', datetime(2026, 10, 1, 6))
        name = snapshot.create_snapshot('Koppie', p)
        doc = frappe.get_doc('Hourly Production Summary', name)
        doc.period_bcm = 999
        doc.flags.ignore_validate = True
        with self.assertRaises(frappe.PermissionError):
            doc.save(ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            frappe.delete_doc(doc.doctype, name, ignore_permissions=True)
        with self.assertRaises(frappe.PermissionError):
            rename_doc(doc.doctype, name, 'renamed', ignore_permissions=True)
        values = calculations.load_snapshot('Koppie', periods.make_period('hourly', p.end))
        with self.assertRaises(frappe.PermissionError):
            frappe.get_doc(dict(values, doctype=doc.doctype)).insert(ignore_permissions=True)
        self.assertEqual(frappe.db.get_value(doc.doctype, name, 'period_bcm'), 0)

    def test_real_scheduler_recovery_and_failure_savepoints(self):
        from . import scheduler, snapshot
        self.source('source', datetime(2026, 10, 1, 6), 100)
        self.source('other', datetime(2026, 10, 1, 6), 200, site='Gwab')
        self.fixture('Monthly Production Planning','GWAB',location='Gwab',
                     prod_month_start_date='2026-10-01',prod_month_end_date='2026-10-01')
        original = snapshot.load_snapshot
        def fail(site, p):
            if site == 'Gwab':
                raise ValueError('test bad source')
            return original(site, p)
        with patch.object(snapshot, 'load_snapshot', side_effect=fail):
            result = scheduler.recover('hourly', as_of=datetime(2026, 10, 1, 8))
        self.assertEqual((result['created'], result['failed']), (2, 2))
        self.assertEqual(frappe.db.count('Hourly Production Summary'), 2)
        result = scheduler.recover('hourly', as_of=datetime(2026, 10, 1, 8))
        self.assertEqual((result['created'], result['failed']), (2, 0))
        result = scheduler.recover('hourly', as_of=datetime(2026, 10, 1, 8))
        self.assertEqual(result['created'], 0)

    def test_schema_reload_preserves_snapshots_and_read_only_permissions(self):
        from . import snapshot
        p = periods.make_period('daily', datetime(2026, 9, 30, 6))
        self.fixture('Monthly Production Planning', 'SEP', location='Koppie',
                     prod_month_start_date='2026-09-30', prod_month_end_date='2026-09-30')
        name = snapshot.create_snapshot('Koppie', p)
        frappe.db.commit()
        for slug, kind in [('hourly_production_summary', 'hourly'), ('shift_production_summary', 'shift'),
                           ('daily_production_summary', 'daily')]:
            frappe.reload_doc('production', 'doctype', slug, force=True)
            meta = frappe.get_meta(snapshot.DOCTYPES[kind], cached=False)
            self.assertTrue(meta.get_field('snapshot_key').unique)
            for permission in meta.permissions:
                self.assertTrue(permission.read)
                self.assertFalse(any(permission.get(p) for p in ['create', 'write', 'delete', 'submit', 'cancel', 'amend']))
        self.assertTrue(frappe.db.exists('Daily Production Summary', name))

    def test_earliest_midnight_source_recovers_previous_operational_day(self):
        from . import scheduler
        self.source('earliest', datetime(2026, 10, 1, 0), 100)
        self.fixture('Monthly Production Planning', 'SEP', location='Koppie',
                     prod_month_start_date='2026-09-30', prod_month_end_date='2026-09-30')
        self.assertEqual(scheduler.get_site_starts()['Koppie'], date(2026, 9, 30))
        result = scheduler.recover('hourly', as_of=datetime(2026, 10, 1, 1))
        self.assertEqual(result['created'], 19)
        rows = frappe.get_all('Hourly Production Summary', fields=['period_start', 'report_date'], order_by='period_start desc', limit=1)
        self.assertEqual(rows[0]['period_start'], datetime(2026, 10, 1, 0))
        self.assertEqual(rows[0]['report_date'], date(2026, 9, 30))

    def test_drilling_only_site_early_hours_anchor_previous_operational_day(self):
        from . import scheduler
        self.fixture('Hourly Drilling Report', 'HDR', site='Koppie', date='2026-10-01')
        self.fixture('Drills Hourly Entries', 'DRILL', parent='HDR', parenttype='Hourly Drilling Report',
                     parentfield='hourly_entries', drill='DR-1', hourly_slot='00:00-01:00', meters=12)
        self.assertEqual(scheduler.get_site_starts()['Koppie'], date(2026, 9, 30))

    def test_scheduler_registration_is_idempotent_and_jobs_use_expected_frequencies(self):
        from frappe.core.doctype.scheduled_job_type.scheduled_job_type import sync_jobs
        from is_production import hooks
        sync_jobs(hooks.scheduler_events)
        sync_jobs(hooks.scheduler_events)
        prefix = 'is_production.production.production_summaries.scheduler.'
        for method, frequency, cron in [('recover_all_summaries', 'Hourly Long', ''),
            ('create_shift_summaries', 'Cron', '0 6,18 * * *'),
            ('create_daily_summaries', 'Cron', '0 6 * * *')]:
            rows = frappe.get_all('Scheduled Job Type', filters={'method': prefix + method}, fields=['frequency', 'cron_format'])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['frequency'], frequency)
            if cron:
                self.assertEqual(rows[0]['cron_format'], cron)


    def test_real_enqueue_defers_continuation_until_commit_and_preserves_cutoff(self):
        from types import SimpleNamespace
        from frappe.utils import background_jobs
        from . import scheduler

        self.source('source', datetime(2026, 10, 1, 6), 100)
        queued = []
        # Keep Frappe enqueue real. Only the external RQ boundary is replaced.
        queue = SimpleNamespace(count=0, enqueue_call=lambda *a, **kw: queued.append(kw))
        with patch.object(background_jobs, 'get_queue', return_value=queue), \
             patch.object(background_jobs, 'get_job', return_value=None):
            result = scheduler.recover('hourly', datetime(2026, 10, 1, 8), batch_size=1)
            self.assertEqual(result['created'], 1)
            self.assertEqual(result['attempted'], 1)
            self.assertEqual(frappe.db.count('Hourly Production Summary'), 1)
            self.assertEqual(queued, [])
            self.assertEqual(result['continuation']['start'], '2026-10-01T07:00:00')
            frappe.db.commit()
            self.assertEqual(len(queued), 1)
            payload = queued[0]['kwargs']['kwargs']
            self.assertEqual(payload['as_of'], '2026-10-01T08:00:00')
            self.assertNotIn('now', payload)
            self.assertEqual(payload['cursor'], result['continuation'])
            # Run the captured worker arguments: it must finish just the next
            # hour at the original cutoff, even though wall-clock time is later.
            next_batch = scheduler.recover(**payload)
            self.assertEqual((next_batch['created'], next_batch['attempted']), (1, 1))
            self.assertIsNone(next_batch['continuation'])
            self.assertEqual(frappe.db.count('Hourly Production Summary'), 2)

    def test_real_enqueue_cron_jobs_are_deferred_and_keep_cutoff(self):
        from types import SimpleNamespace
        from frappe.utils import background_jobs
        from . import scheduler

        queued = []
        queue = SimpleNamespace(count=0, enqueue_call=lambda *a, **kw: queued.append(kw))
        with patch.object(background_jobs, 'get_queue', return_value=queue), \
             patch.object(background_jobs, 'get_job', return_value=None), \
             patch.object(scheduler, 'now_datetime', return_value=datetime(2026, 10, 1, 6)):
            scheduler.create_shift_summaries()
            scheduler.create_daily_summaries()
            self.assertEqual(queued, [])
            self.assertEqual(frappe.db.count('Shift Production Summary'), 0)
            self.assertEqual(frappe.db.count('Daily Production Summary'), 0)
            frappe.db.commit()
            self.assertEqual(len(queued), 2)
            self.assertEqual([job['kwargs']['kwargs']['kind'] for job in queued], ['shift', 'daily'])
            for job in queued:
                payload = job['kwargs']['kwargs']
                self.assertEqual(payload['as_of'], '2026-10-01T06:00:00')
                self.assertNotIn('now', payload)

    def test_no_plan_cancelled_plan_and_wrong_site_cannot_generate(self):
        from . import snapshot, scheduler
        from .eligibility import get_covering_plan
        frappe.db.delete('Monthly Production Planning')
        self.source('active-data',datetime(2026,10,1,6),100)
        self.fixture('Monthly Production Planning','CANCELLED',location='Koppie',
            prod_month_start_date='2026-10-01',prod_month_end_date='2026-10-01',docstatus=2)
        self.fixture('Monthly Production Planning','ELSEWHERE',location='Gwab',
            prod_month_start_date='2026-10-01',prod_month_end_date='2026-10-01')
        self.assertIsNone(get_covering_plan('Koppie','2026-10-01'))
        for kind in snapshot.DOCTYPES:
            self.assertIsNone(snapshot.create_snapshot('Koppie',periods.make_period(kind,datetime(2026,10,1,6))))
        result=scheduler.recover('hourly',as_of=datetime(2026,10,1,7))
        self.assertEqual(result['created'],1)  # Only Gwab's valid planning period.
        self.assertEqual(result['attempted'],1)
        self.assertFalse(frappe.db.exists('Hourly Production Summary',{'site':'Koppie'}))



class TestCronQueue(unittest.TestCase):
    def test_cron_jobs_enqueue_long_recovery_after_commit(self):
        from . import scheduler
        queued = []
        with patch.object(scheduler, 'now_datetime', return_value=datetime(2026, 10, 1, 6)), \
             patch.object(scheduler.frappe, 'enqueue', side_effect=lambda **kw: queued.append(kw)):
            scheduler.create_shift_summaries()
            scheduler.create_daily_summaries()
        self.assertEqual([job['kind'] for job in queued], ['shift', 'daily'])
        for job in queued:
            self.assertEqual(job['queue'], 'long')
            self.assertEqual(job['timeout'], 1500)
            self.assertTrue(job['enqueue_after_commit'])
            self.assertEqual(job['as_of'], '2026-10-01T06:00:00')
            self.assertNotIn('now', job)


class TestPlanningEligibility(unittest.TestCase):
    def test_covering_plan_query_uses_operational_date_and_non_cancelled_status(self):
        from .eligibility import get_covering_plan
        p=periods.make_period('hourly',datetime(2026,10,1,2))
        with patch('frappe.get_all',return_value=[frappe._dict(name='SEP')]) as query:
            self.assertEqual(get_covering_plan('Koppie',p.report_date),'SEP')
        self.assertEqual(query.call_args.kwargs['filters'],dict(location='Koppie',
            prod_month_start_date=['<=',date(2026,9,30)],prod_month_end_date=['>=',date(2026,9,30)],docstatus=['<',2]))

    def test_ineligible_direct_creation_never_loads_sources(self):
        from . import snapshot
        from types import SimpleNamespace
        with patch.object(snapshot.frappe,'db',SimpleNamespace(exists=lambda *a:False)), \
             patch.object(snapshot,'get_covering_plan',return_value=None), \
             patch.object(snapshot,'load_snapshot',side_effect=AssertionError('must not load')):
            self.assertIsNone(snapshot.create_snapshot('Inactive',periods.make_period('daily',datetime(2026,10,1,6))))

    def test_eligible_periods_jump_gaps_and_keep_midnight_in_previous_month(self):
        from .eligibility import eligible_periods
        windows=[(date(2026,9,30),date(2026,9,30)),(date(2026,10,2),date(2026,10,2))]
        found=list(eligible_periods('hourly',datetime(2026,9,1,6),datetime(2026,10,3,6),windows))
        self.assertEqual(len(found),48)
        self.assertIn(datetime(2026,10,1,5),[p.start for p in found])
        self.assertNotIn(datetime(2026,10,1,6),[p.start for p in found])
        self.assertEqual(len(list(eligible_periods('daily',datetime(2026,9,1,6),datetime(2026,10,3,6),windows))),2)

    def test_overlapping_plan_ranges_do_not_duplicate_periods(self):
        from .eligibility import eligible_periods
        windows=[(date(2026,9,1),date(2026,9,30)),(date(2026,9,20),date(2026,10,1))]
        found=list(eligible_periods('shift',datetime(2026,9,30,6),datetime(2026,10,2,6),windows))
        self.assertEqual(len(found),4)
