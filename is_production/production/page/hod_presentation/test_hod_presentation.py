"""The Desk Page must use the report's permission and calculation boundary."""
import importlib
import logging
import unittest
from unittest.mock import patch
import frappe


class TestHodPresentationPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(frappe, 'logger', return_value=logging.getLogger(__name__)):
            cls.page = importlib.import_module(
                'is_production.production.page.hod_presentation.hod_presentation')

    def test_page_reuses_report_execution_with_every_filter(self):
        filters = {'start_date': '2026-03-01', 'end_date': '2026-03-31', 'site': ['Uitgevallen'],
                   'summary_type': 'Average Per Machine', 'machine_scope': 'Include Swing/Spare',
                   'asset_ownership': 'Isambane & Excavo Assets', 'au_target_filter': '85% A & U'}
        rows = [{'site': 'Uitgevallen', 'period': 'March 2026', 'start_date':'2026-03-01', 'end_date':'2026-03-31'}]
        calls = []
        with (patch.object(self.page, 'render_grids', return_value='<div>Fleet summary</div>') as summary,
              patch.object(self.page, 'build_grid_data', return_value={'machines': []}) as fleet,
              patch.object(self.page, 'build_dashboard', return_value={'machine_count': 20}),
              patch.object(self.page.report, '_load_daily_availability_module', return_value=object()),
              patch.object(self.page.report, 'check_report_access', side_effect=lambda: calls.append('permission')),
              patch.object(self.page.report, 'execute', side_effect=lambda value: (calls.append(value) or ([], rows, 'Period message', {'data': []}, [])))):
            result = self.page.get_data(frappe.as_json(filters))
        self.assertEqual(calls, ['permission', filters])
        self.assertEqual(rows[0]['engineering']['machine_count'], 20)
        self.assertIn('reliability', rows[0]['engineering'])
        self.assertIn('breakdown_summary', rows[0]['engineering'])
        self.assertIn(
            'top_machines',
            rows[0]['engineering']['breakdown_summary']
        )
        self.assertIn(
            'classifications',
            rows[0]['engineering']['breakdown_summary']
        )
        self.assertEqual(rows[0]['fleet_summary_html'], '<div>Fleet summary</div>')
        summary.assert_called_once_with({'machines': []})
        self.assertEqual(fleet.call_args.kwargs['site'], 'Uitgevallen')
        self.assertEqual(fleet.call_args.kwargs['machine_scope'], 'Production + Swing/Spare Machines')
        self.assertEqual(fleet.call_args.kwargs['au_target_filter'], '85% A & U')
        self.assertEqual(fleet.call_args.kwargs['asset_ownership'], 'Isambane & Excavo Assets')
        self.assertEqual(result, {'columns': [], 'result': rows, 'message': 'Period message',
                                  'chart': {'data': []}, 'report_summary': []})

    def test_denied_page_never_executes_the_report(self):
        with (patch.object(self.page.report, 'check_report_access', side_effect=frappe.PermissionError),
              patch.object(self.page.report, 'execute') as execute):
            with self.assertRaises(frappe.PermissionError):
                self.page.get_data({'site': ['Uitgevallen'], 'end_date': '2026-03-31'})
        execute.assert_not_called()

    def test_empty_page_checks_permissions_without_querying_data(self):
        with (patch.object(self.page.report, 'check_report_access') as access,
              patch.object(self.page.report, 'execute', return_value=([], [])) as execute):
            self.assertEqual(self.page.get_data(), {'columns': [], 'result': []})
        access.assert_called_once_with()
        execute.assert_called_once_with({})

    def test_explicit_start_date_reaches_production_hours_and_engine_together(self):
        from contextlib import ExitStack
        from datetime import date, datetime
        from types import SimpleNamespace
        plan = SimpleNamespace(name='Test plan', prod_month_start_date='2026-03-01',
                               prod_month_end_date='2026-03-31')
        end = date(2026, 3, 31)
        with ExitStack() as stack:
            stack.enter_context(patch.object(self.page.report, '_', side_effect=lambda value: value))
            stack.enter_context(patch.object(self.page.report, 'formatdate', side_effect=str))
            stack.enter_context(patch.object(self.page.report, 'now_datetime', return_value=datetime(2026,3,31)))
            stack.enter_context(patch.object(self.page.report, 'validate_filters', return_value=(
                end, ['Uitgevallen'], 'Average Per Machine', 'Include Swing/Spare',
                '85% A & U', 'Isambane & Excavo Assets')))
            stack.enter_context(patch.object(self.page.report, 'get_latest_hod_monthly_plan', return_value=plan))
            production = stack.enter_context(patch.object(self.page.report, 'build_filtered_production_row', return_value={}))
            hours = stack.enter_context(patch.object(self.page.report, 'get_excavator_hour_summary', return_value={}))
            stack.enter_context(patch.object(self.page.report, 'get_non_production_excavator_hours', return_value=0))
            availability = stack.enter_context(patch.object(self.page.report, 'get_availability_summary', return_value={}))
            stack.enter_context(patch.object(self.page.report, 'get_fullname', return_value='Test'))
            stack.enter_context(patch.object(frappe, 'session', SimpleNamespace(user='Administrator'), create=True))
            data = self.page.report.get_report_payload({'start_date':'2026-03-10'})
        start = date(2026, 3, 10)
        self.assertEqual(data['start_date'], '2026-03-10')
        self.assertEqual(production.call_args.kwargs['start_date'], start)
        self.assertEqual(hours.call_args.args[:2], (start, end))
        self.assertEqual(availability.call_args.kwargs['start_date'], start)

    def test_start_date_outside_selected_plan_is_rejected(self):
        from datetime import date
        from types import SimpleNamespace
        with (patch.object(self.page.report, 'validate_filters', return_value=(
                  date(2026,3,31), ['Uitgevallen'], 'Average Per Machine', 'Include Swing/Spare',
                  '85% A & U', 'Isambane & Excavo Assets')),
              patch.object(self.page.report, 'get_latest_hod_monthly_plan', return_value=SimpleNamespace(
                  prod_month_start_date='2026-03-01', prod_month_end_date='2026-03-31')),
              patch.object(self.page.report, '_', side_effect=lambda value: value),
              patch.object(self.page.report, 'formatdate', side_effect=str),
              patch.object(frappe, 'throw', side_effect=frappe.ValidationError)):
            for invalid in ('2026-02-28','2026-04-01'):
                with self.assertRaises(frappe.ValidationError):
                    self.page.report.get_report_payload({'start_date':invalid})
