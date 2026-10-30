import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from . import hod_presentation as dashboard_data


class TestDashboardData(unittest.TestCase):
    def test_rankings_are_metric_specific_full_fleet_and_missing_is_excluded(self):
        machines = [{'machine': f'IS{i}', 'category': 'ADT', 'avail': i, 'util': 100-i,
                     'is_spare': i == 1} for i in range(8)]
        machines += [{'machine': 'EX0', 'category': 'Excavator', 'avail': None, 'util': 0},
                     {'machine': 'D0', 'category': 'Dozer', 'avail': 0, 'util': float('nan')},
                     {'machine': 'L0', 'category': 'Loader', 'avail': 0, 'util': 0}]
        averages = {'ADT': {'including': {'avail': 88, 'util': 77}, 'excluding': {'avail': 90, 'util': 80}}}
        fleet = {'machines': machines, 'category_averages': averages,
                 'averages': {'including': {'avail': 81}}, 'start_date':'2026-03-01','end_date':'2026-03-31'}
        original = deepcopy(fleet)
        engine = SimpleNamespace(get_au_engine_breakdown_reasons=Mock(return_value=[]),
                                 get_au_engine_other_delay_reasons=Mock(return_value=[]))
        result = dashboard_data.build_dashboard(SimpleNamespace(au_engine=engine), fleet, 'Uitgevallen')
        self.assertEqual([r['machine'] for r in result['top']['avail']['ADT']], ['IS7','IS6','IS5','IS4','IS3'])
        self.assertEqual([r['machine'] for r in result['top']['util']['ADT']], ['IS0','IS1','IS2','IS3','IS4'])
        self.assertEqual([r['machine'] for r in result['bottom']['avail']], ['D0','IS0','IS1','IS2','IS3','IS4','IS5','IS6','IS7'])
        self.assertEqual([r['machine'] for r in result['bottom']['util']], ['EX0'])
        self.assertEqual(result['category_averages'], averages)
        self.assertEqual(result['machine_count'], 10)
        self.assertEqual(result['unavailable'], {'avail': 1, 'util': 1})
        self.assertEqual(fleet['machines'][1], original['machines'][1])
        for metric in ('avail','util'):
            self.assertTrue(all(r['explanation']=='Not recorded' for r in result['bottom'][metric]))
        engine.get_au_engine_breakdown_reasons.assert_any_call('D0','2026-03-01','2026-03-31',location='Uitgevallen')

    def test_explanations_require_one_distinct_recorded_reason(self):
        self.assertEqual(dashboard_data.recorded_explanation([{'reason':'Engine overheating'}, {'reason':' Engine overheating '}]), 'Engine overheating')
        self.assertEqual(dashboard_data.recorded_explanation([{'reason':'Engine overheating'}, {'reason':'Hydraulic leak'}]), 'Not recorded')
        self.assertEqual(dashboard_data.recorded_explanation([{'reason':''}, {'reason':'No reason captured'}]), 'Not recorded')
        self.assertEqual(dashboard_data.recorded_explanation([]), 'Not recorded')

    def test_empty_fleet_retains_neutral_averages_and_empty_rankings(self):
        result = dashboard_data.build_dashboard(SimpleNamespace(au_engine=object()), {
            'machines': [], 'averages': {}, 'category_averages': {},
            'start_date': '2026-03-01', 'end_date':'2026-03-31'}, 'Uitgevallen')
        self.assertEqual(result['bottom'], {'avail': [], 'util': []})
        self.assertEqual(result['top']['avail']['Dozer'], [])
        self.assertEqual(result['category_averages'], {})
