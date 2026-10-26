import unittest
from html.parser import HTMLParser
from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock
from copy import deepcopy

from . import availability_grids as grids


class GridParser(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.cells = []
        self.categories = []
        self.tables = 0
        self.is_numbers = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'table':
            self.tables += 1
        if tag == 'tr' and 'data-machine' in attrs:
            self.rows.append(attrs)
        if tag == 'tr' and 'data-category-header' in attrs:
            self.categories.append(attrs)
        if tag == 'td' and 'data-value' in attrs:
            self.cells.append(attrs)
        if tag == 'span' and 'data-is-machine' in attrs:
            self.is_numbers.append(attrs)


def fixture(machines):
    averages = {'excluding': {'avail': 80, 'util': 60}, 'including': {'avail': 75, 'util': 65}}
    return {'machines': machines, 'averages': averages,
            'category_averages': {m['category']: averages for m in machines},
            'start_date': '2026-08-01', 'end_date': '2026-08-07'}


class TestAvailabilityGrids(unittest.TestCase):
    def test_metric_ranking_keeps_every_eligible_machine_and_ranks_only_worst_half(self):
        machines = [
            {'machine': 'IS001', 'category': 'ADT', 'avail': 10, 'util': 90},
            {'machine': 'IS002', 'category': 'ADT', 'avail': 20, 'util': 80, 'is_spare': True},
            {'machine': 'IS003', 'category': 'ADT', 'avail': 80, 'util': 20},
            {'machine': 'IS004', 'category': 'ADT', 'avail': 90, 'util': 10},
            {'machine': 'EX001', 'category': 'ADT', 'avail': 0, 'util': 0},
            {'machine': 'EX002', 'category': 'ADT', 'avail': None, 'util': 30},
            {'machine': 'IS005', 'category': 'Excavator', 'avail': None, 'util': 0},
            {'machine': 'IS006', 'category': 'Excavator', 'avail': 30, 'util': None},
            {'machine': 'IS007', 'category': 'Dozer', 'avail': 0, 'util': 0},
            {'machine': 'IS008', 'category': 'Loader', 'avail': 0, 'util': 0},
        ]
        original = deepcopy(machines)
        availability = grids._ranked_metric_rows(machines, 'avail')
        utilisation = grids._ranked_metric_rows(machines, 'util')
        self.assertEqual(list(availability), ['ADT', 'Excavator', 'Dozer'])
        self.assertEqual([r['machine'] for r in availability['ADT']],
                         ['EX001', 'IS001', 'IS002', 'IS003', 'IS004', 'EX002'])
        self.assertEqual([r['rank'] for r in availability['ADT']], [1, 2, 3, None, None, None])
        self.assertEqual([r['machine'] for r in utilisation['ADT']],
                         ['EX001', 'IS004', 'IS003', 'EX002', 'IS002', 'IS001'])
        self.assertEqual([r['rank'] for r in utilisation['ADT']], [1, 2, 3, None, None, None])
        self.assertEqual([r['rank'] for r in availability['Excavator']], [1, None])
        self.assertEqual([r['rank'] for r in utilisation['Excavator']], [1, None])
        for groups in (availability, utilisation):
            displayed = [r['machine'] for rows in groups.values() for r in rows]
            self.assertEqual(len(displayed), 9)
            self.assertEqual(set(displayed), {r['machine'] for r in machines if r['category'] != 'Loader'})
        self.assertEqual(machines, original)

    def test_metric_lists_render_all_machines_with_independent_partial_ranks(self):
        machines = [
            {'machine': f'IS{i:03}', 'category': 'ADT', 'avail': i, 'util': 100 - i,
             'is_spare': i == 14}
            for i in reversed(range(15))
        ] + [
            {'machine': 'EX099', 'category': 'ADT', 'avail': None, 'util': 0},
            {'machine': 'EX100', 'category': 'Dozer', 'avail': 0, 'util': None},
            {'machine': 'IS999', 'category': 'Loader', 'avail': 0, 'util': 0},
        ]
        data = fixture(machines)
        original = deepcopy(data)
        html = grids.render_grids(data)
        parsed = GridParser(html)
        for metric, expected in (
            ('avail', [f'IS{i:03}' for i in range(15)] + ['EX099']),
            ('util', ['EX099'] + [f'IS{i:03}' for i in reversed(range(15))]),
        ):
            rows = [r for r in parsed.is_numbers
                    if r['data-is-metric'] == metric and r['data-is-category'] == 'ADT']
            self.assertEqual([r['data-is-machine'] for r in rows], expected)
            self.assertEqual([r['data-rank'] for r in rows],
                             [str(i) for i in range(1, 9)] + [''] * 8)
            self.assertEqual(len({r['data-is-machine'] for r in rows}), 16)
        for row in parsed.is_numbers:
            self.assertNotIn('data-value', row)
            self.assertEqual(row['class'], 'hod-au-spare' if row['data-is-machine'] == 'IS014'
                             else 'hod-au-production')
        missing = [r for r in parsed.is_numbers if r['data-unavailable'] == 'true']
        self.assertEqual([(r['data-is-metric'], r['data-is-machine'], r['data-rank']) for r in missing],
                         [('avail', 'EX099', ''), ('util', 'EX100', '')])
        self.assertEqual(data, original)
        self.assertEqual(parsed.tables, 2)
        self.assertEqual(html.count('class="hod-au-grid-page"'), 1)
        self.assertIn('Availability · all machines', html)
        self.assertIn('Utilisation · all machines', html)
        self.assertIn('Only worst 50% ranked', html)
        self.assertIn('hod-au-list-rank"></b>', html)

    def test_summary_keeps_all_twenty_machines_and_only_eleven_rank_circles(self):
        machines = [
            {'machine': f'{category}{i:02}', 'category': category,
             'avail': i, 'util': 100 - i, 'is_spare': i % 2 == 0}
            for category, count in [('ADT', 13), ('Dozer', 3), ('Excavator', 4)]
            for i in reversed(range(count))
        ]
        html = grids.render_grids(fixture(machines))
        parsed = GridParser(html)
        self.assertEqual(len(parsed.rows), 20)
        self.assertEqual(len({r['data-machine'] for r in parsed.rows}), 20)
        for category, count, ranked in [('ADT', 13, 7), ('Dozer', 3, 2), ('Excavator', 4, 2)]:
            rows = [r for r in parsed.rows if r['data-category'] == category]
            self.assertEqual([r['data-machine'] for r in rows],
                             [f'{category}{i:02}' for i in range(count)])
            self.assertEqual([r['data-rank'] for r in rows],
                             [str(i) for i in range(1, ranked + 1)] + [''] * (count - ranked))
        self.assertEqual(html.count('<td class="hod-au-rank"><span>'), 11)
        self.assertEqual(html.count('<td class="hod-au-rank"></td>'), 9)
        self.assertIn('20 machines shown · 11 ranked', html)
        self.assertEqual(len(parsed.cells), 40)

    def test_threshold_boundaries_and_missing(self):
        for metric, cases in {
            'avail': [(0, 'red'), (74.99, 'red'), (75, 'amber'), (84.99, 'amber'), (85, 'green')],
            'util': [(0, 'red'), (69.99, 'red'), (70, 'amber'), (79.99, 'amber'), (80, 'green')],
        }.items():
            for value, expected in cases:
                with self.subTest(metric=metric, value=value):
                    self.assertEqual(grids.percentage_band(metric, value), expected)
            for value in (None, '', '—', float('nan')):
                self.assertEqual(grids.percentage_band(metric, value), 'neutral')

    def test_worst_half_independently_per_category_with_continuation(self):
        machines = [
            {'machine': f'IS{i:02}', 'category': 'ADT' if i < 30 else 'Dozer',
             'is_spare': i % 2 == 0, 'avail': 100 - i, 'util': i}
            for i in range(50)
        ]
        html = grids.render_grids(fixture(machines))
        parsed = GridParser(html)
        self.assertEqual(parsed.tables, 2)
        self.assertEqual(html.count('class="hod-au-grid-page"'), 1)
        self.assertEqual(len(parsed.rows), 50)
        self.assertEqual(len({r['data-machine'] for r in parsed.rows}), 50)
        self.assertEqual([r['data-machine'] for r in parsed.rows[:15]], [f'IS{i:02}' for i in reversed(range(15, 30))])
        self.assertEqual([int(r['data-rank']) for r in parsed.rows[:15]], list(range(1, 16)))
        self.assertEqual([r['data-rank'] for r in parsed.rows[15:30]], [''] * 15)
        self.assertEqual([r['data-rank'] for r in parsed.rows[30:]],
                         [str(i) for i in range(1, 11)] + [''] * 10)
        self.assertIn('ADTs · worst 15 of 30', html)
        self.assertIn('Dozers · worst 10 of 20', html)
        self.assertEqual(len(parsed.categories), 3)
        self.assertIn('continued', html)
        self.assertNotIn('2026-08-02', html)
        self.assertNotIn('hod-au-label', html)
        self.assertEqual(html.count('class="hod-au-production">80%</span>'), 4)
        self.assertEqual(html.count('class="hod-au-spare">75%</span>'), 4)

    def test_missing_availability_last_and_unranked_and_identity_escaped(self):
        machines = [
            {'machine': '<missing>', 'category': 'ADT', 'avail': None, 'util': 0, 'is_spare': True},
            {'machine': 'B', 'category': 'ADT', 'avail': 85, 'util': None},
            {'machine': 'A', 'category': 'ADT', 'avail': 75, 'util': 70},
        ]
        html = grids.render_grids(fixture(machines))
        parsed = GridParser(html)
        self.assertEqual([r['data-machine'] for r in parsed.rows], ['A', 'B', '<missing>'])
        self.assertEqual([r['data-rank'] for r in parsed.rows], ['1', '', ''])
        self.assertIn('hod-au-spare', html)
        self.assertIn('ADTs · worst 1 of 2', html)
        self.assertIn('ADTs · unavailable (1)', html)
        self.assertNotIn('><missing><', html)
        self.assertIn('>—</td>', html)
        self.assertIn('>0%</td>', html)

    def test_odd_category_counts_round_up_and_other_categories_are_omitted(self):
        machines = [
            {'machine': f'{category}{i:02}', 'category': category, 'avail': i, 'util': 100 - i}
            for category, count in [('ADT', 15), ('Dozer', 5), ('Excavator', 1), ('Loader', 3)]
            for i in reversed(range(count))
        ]
        html = grids.render_grids(fixture(machines))
        rows = GridParser(html).rows
        self.assertEqual(len(rows), 21)
        for category, count, expected in [('ADT', 15, 8), ('Dozer', 5, 3), ('Excavator', 1, 1)]:
            selected = [row for row in rows if row['data-category'] == category]
            self.assertEqual([r['data-machine'] for r in selected],
                             [f'{category}{i:02}' for i in range(count)])
            self.assertEqual([r['data-rank'] for r in selected],
                             [str(i) for i in range(1, expected + 1)] + [''] * (count - expected))
        self.assertIn('ADTs · worst 8 of 15', html)
        self.assertIn('Dozers · worst 3 of 5', html)
        self.assertIn('Excavators · worst 1 of 1', html)
        self.assertNotIn('Loader', html)

    def test_percentage_cell_colours_apply_to_both_metrics(self):
        machines = [
            {'machine': 'A', 'category': 'ADT', 'avail': 75, 'util': 70},
            {'machine': 'B', 'category': 'ADT', 'avail': 85, 'util': 80},
            {'machine': 'C', 'category': 'Dozer', 'avail': None, 'util': 0},
        ]
        cells = GridParser(grids.render_grids(fixture(machines))).cells
        self.assertEqual([(c['data-value'], c['class']) for c in cells], [
            ('75.0', 'hod-au-amber'), ('70.0', 'hod-au-amber'),
            ('85.0', 'hod-au-green'), ('80.0', 'hod-au-green'),
            ('', 'hod-au-neutral'), ('0.0', 'hod-au-red'),
        ])

    def test_unavailable_averages_use_neutral_dashes(self):
        data = fixture([])
        data['averages'] = {'excluding': {'avail': None, 'util': None},
                            'including': {'avail': None, 'util': 0}}
        html = grids.render_grids(data)
        overall = html.split('<div class="hod-au-overall">', 1)[1].split('<div class="hod-au-grid-pair">', 1)[0]
        self.assertEqual(overall.count('class="hod-au-neutral-text">—</span>'), 3)
        self.assertIn('class="hod-au-spare">0%</span>', html)

    def test_averages_use_engine_hours_combine_spares_and_exclude_invalid_rows(self):
        from engineering.engineering.report.availability_and_utilisation_engine import (
            availability_and_utilisation_engine as engine,
        )

        rows = [
            {'indent': 3, 'asset_name': 'P', 'asset_category': 'ADT',
             'required_hours': 10, 'availability_available_hours': 8,
             'utilisation_available_hours': 8, 'work_hours': 4},
            {'indent': 3, 'asset_name': 'P', 'asset_category': 'ADT',
             'required_hours': 30, 'availability_available_hours': 30,
             'utilisation_available_hours': 30, 'work_hours': 15},
            {'indent': 3, 'asset_name': 'S', 'asset_category': 'ADT',
             'required_hours': 10, 'availability_available_hours': 0,
             'utilisation_available_hours': 0, 'work_hours': 0},
            {'indent': 3, 'asset_name': 'D', 'asset_category': 'Dozer',
             'required_hours': 20, 'availability_available_hours': 0,
             'utilisation_available_hours': 20, 'work_hours': 20, 'invalid_pre_use': True},
        ]
        rows.append({'indent': 3, 'asset_name': 'L', 'asset_category': 'Loader',
                     'required_hours': 100, 'availability_available_hours': 0,
                     'utilisation_available_hours': 100, 'work_hours': 0})
        # Substitute the database boundary and already-tested machine series;
        # the averages exercise the real Engine with unequal shift hours.
        module = SimpleNamespace(
            fetch_grouped_data=Mock(return_value=rows), au_engine=engine,
            get_engine_spare_swing_asset_map=lambda source: {'S': 'Spare'},
            get_category=lambda row: row['asset_category'],
            get_machine=lambda row: row['asset_name'],
            build_machine_series_from_source_rows=lambda source: {
                'ADT': [{'machine': 'P', 'avail': 95, 'util': 50},
                        {'machine': 'S', 'avail': 0, 'util': None}],
                'Dozer': [{'machine': 'D', 'avail': None, 'util': None}],
                'Loader': [{'machine': 'L', 'avail': 0, 'util': 0}],
            },
        )
        data = grids.build_grid_data(
            module, site='Test site', start_date=date(2026, 8, 1), end_date=date(2026, 8, 7),
            machine_scope='Production + Swing/Spare Machines', au_target_filter='100% A & U',
            asset_ownership='All Assets',
        )
        module.fetch_grouped_data.assert_called_once_with(
            'Test site', '2026-08-01', '2026-08-07', 'Production + Swing/Spare Machines',
            '100% A & U', 'All Assets',
        )
        expected = {'excluding': {'avail': 95, 'util': 50},
                    'including': {'avail': 76, 'util': 50}}
        self.assertEqual(data['averages'], expected)
        self.assertEqual([m['machine'] for m in data['machines']], ['P', 'S', 'D'])
        self.assertNotIn('Loader', data['category_averages'])
        rendered = grids.render_grids(data)
        self.assertIn('data-machine="P"', rendered)
        self.assertIn('data-machine="S"', rendered)
        self.assertIn('class="hod-au-spare">76%</span>', rendered)
        self.assertEqual(data['category_averages']['ADT'], expected)
        self.assertEqual(data['category_averages']['Dozer'], {
            'excluding': {'avail': None, 'util': None},
            'including': {'avail': None, 'util': None},
        })
        self.assertEqual([m['machine'] for m in data['machines'] if m['is_spare']], ['S'])


if __name__ == '__main__':
    unittest.main()
