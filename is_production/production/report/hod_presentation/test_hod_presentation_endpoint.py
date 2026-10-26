"""Regression coverage for the dashboard endpoint's complete summary contract."""

import importlib
import logging
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from . import availability_grids as grids
from .test_availability_grids import GridParser


class TestHodPresentationEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Import without requiring a bench log directory in standalone tests.
        with patch.object(frappe, 'logger', return_value=logging.getLogger(__name__)):
            cls.report = importlib.import_module(
                'is_production.production.report.hod_presentation.hod_presentation'
            )

    def test_march_uitgevallen_endpoint_consumes_complete_five_field_summary(self):
        filters = {
            'site': 'Uitgevallen', 'start_date': '2026-03-01', 'end_date': '2026-03-31',
            'summary_type': 'Average Per Machine', 'machine_scope': 'Include Swing/Spare',
            'asset_ownership': 'Isambane & Excavo Assets', 'au_target_filter': '85% A & U',
        }
        averages = {'excluding': {'avail': 80, 'util': 70},
                    'including': {'avail': 75, 'util': 65}}
        summary = {
            'machines': [{'machine': 'IS1', 'category': 'ADT', 'avail': 80, 'util': 70},
                         {'machine': 'IS2', 'category': 'ADT', 'avail': 0,
                          'util': None, 'is_spare': True}],
            'averages': averages, 'category_averages': {'ADT': averages},
            'start_date': '2026-03-01', 'end_date': '2026-03-31',
        }
        engine = object()
        with (
            patch.object(self.report, 'check_report_access') as check_access,
            patch.object(frappe, 'db', SimpleNamespace(exists=lambda *args: True), create=True),
            patch.object(self.report, 'now_datetime', return_value=datetime(2026, 10, 21)),
            patch.object(self.report, '_load_daily_availability_module', return_value=engine),
            patch.object(grids, 'build_grid_data', return_value=summary) as build,
        ):
            result = self.report.get_availability_dashboard_html(**filters)

        check_access.assert_called_once_with()
        self.assertEqual(build.call_args.args, (engine,))
        self.assertEqual(str(build.call_args.kwargs['start_date']), '2026-03-01')
        self.assertEqual(str(build.call_args.kwargs['end_date']), '2026-03-31')
        self.assertEqual(build.call_args.kwargs['machine_scope'], 'Production + Swing/Spare Machines')
        self.assertEqual(build.call_args.kwargs['asset_ownership'], 'Isambane & Excavo Assets')
        self.assertEqual(build.call_args.kwargs['au_target_filter'], '85% A & U')
        self.assertEqual(result['site'], 'Uitgevallen')
        parsed = GridParser(result['html'])
        self.assertEqual([r['data-machine'] for r in parsed.rows], ['IS2', 'IS1'])
        self.assertEqual(
            [(r['data-is-metric'], r['data-is-machine'], r['data-rank']) for r in parsed.is_numbers],
            [('avail', 'IS2', '1'), ('avail', 'IS1', ''),
             ('util', 'IS1', '1'), ('util', 'IS2', '')],
        )
        self.assertEqual(parsed.tables, 2)
        self.assertIn('2026-03-01 to 2026-03-31', result['html'])
        self.assertIn('class="hod-au-production">80%</span>', result['html'])
        self.assertIn('class="hod-au-spare">75%</span>', result['html'])
        self.assertIn('>—</td>', result['html'])
        self.assertIn('ADTs · worst 1 of 2', result['html'])
        self.assertEqual(result['html'].count('class="hod-au-grid-page"'), 1)


if __name__ == '__main__':
    unittest.main()
