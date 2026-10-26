"""Single-slide fleet summaries using the existing A&U Engine's period maths."""

from collections import defaultdict
from html import escape
from math import ceil, isfinite
from typing import TypedDict


CATEGORY_LABELS = {'ADT': 'ADTs', 'Dozer': 'Dozers', 'Excavator': 'Excavators'}


class FleetSummary(TypedDict):
    """All eligible machines and averages, before display ranking."""

    machines: list[dict]
    averages: dict[str, dict[str, float | None]]
    category_averages: dict[str, dict[str, dict[str, float | None]]]
    start_date: str
    end_date: str


def percentage_value(value):
    if value is None or value == '':
        return None
    try:
        value = float(value)
        return value if isfinite(value) else None
    except (TypeError, ValueError):
        return None


def percentage_band(metric, value):
    value = percentage_value(value)
    if value is None:
        return 'neutral'
    low, high = (75, 85) if metric == 'avail' else (70, 80)
    return 'red' if value < low else 'amber' if value < high else 'green'


def build_grid_data(module, *, site, start_date, end_date, machine_scope,
                    au_target_filter, asset_ownership) -> FleetSummary:
    """Fetch once, retain Engine machine results and aggregate the same shift rows.

    All averages are hour-weighted Engine summaries, not averages of displayed
    machine percentages or daily percentages. The selected scope/ownership also
    applies to both average cohorts; including = all filtered production + spare.
    Only the three eligible categories enter this population. Display ranking
    happens later in the renderer, so it cannot bias these averages.
    """
    source_rows = module.fetch_grouped_data(
        site, str(start_date), str(end_date), machine_scope,
        au_target_filter, asset_ownership,
    ) or []
    spare_map = module.get_engine_spare_swing_asset_map(source_rows)
    period_series = module.build_machine_series_from_source_rows(source_rows)
    machines = [
        {**row, 'category': category, 'is_spare': row['machine'] in spare_map}
        for category, rows in period_series.items() if category in CATEGORY_LABELS
        for row in rows
    ]
    included = {(m['category'], m['machine']) for m in machines}
    shifts = [
        row for row in source_rows
        if int(row.get('indent') or 0) == 3 and not row.get('is_formula_row')
        and (module.get_category(row), module.get_machine(row)) in included
    ]

    def averages(rows):
        output = {}
        for cohort in ('excluding', 'including'):
            selected = [row for row in rows if cohort == 'including'
                        or module.get_machine(row) not in spare_map]
            summary = module.au_engine.build_summary_row(selected, indent=0)
            module.au_engine.apply_au_percentage_basis([summary], au_target_filter)
            output[cohort] = {
                'avail': summary.get('availability_percentage'),
                'util': summary.get('utilisation_percentage'),
            }
        return output

    return {
        'machines': machines,
        'averages': averages(shifts),
        'category_averages': {
            category: averages([row for row in shifts if module.get_category(row) == category])
            for category, rows in period_series.items() if rows and category in CATEGORY_LABELS
        },
        'start_date': str(start_date), 'end_date': str(end_date),
    }


def _percent(value):
    value = percentage_value(value)
    return '—' if value is None else f'{value:.1f}'.rstrip('0').rstrip('.') + '%'


def _cell(metric, value):
    value = percentage_value(value)
    return (f'<td class="hod-au-{percentage_band(metric, value)}" '
            f'data-value="{value if value is not None else ""}" '
            f'title="{value if value is not None else "Unavailable"}">{_percent(value)}</td>')


def _average_pair(averages, metric):
    output = []
    for cohort, colour in (('excluding', 'production'), ('including', 'spare')):
        value = percentage_value(averages.get(cohort, {}).get(metric))
        style = colour if value is not None else 'neutral-text'
        output.append(f'<span class="hod-au-{style}">{_percent(value)}</span>')
    return ' / '.join(output)


def _ranked_metric_rows(machines, metric):
    """Keep the full fleet; only the worst half of valid values receives ranks."""
    groups = {}
    for category in ('ADT', 'Excavator', 'Dozer'):
        rows = [row for row in machines if row['category'] == category]
        if not rows:
            continue
        valid = sorted(
            (row for row in rows if percentage_value(row.get(metric)) is not None),
            key=lambda row: (percentage_value(row[metric]), str(row['machine'])),
        )
        limit = (len(valid) + 1) // 2
        groups[category] = [
            {**row, 'rank': index if index <= limit else None}
            for index, row in enumerate(valid, 1)
        ] + [
            {**row, 'rank': None}
            for row in sorted(rows, key=lambda row: str(row['machine']))
            if percentage_value(row.get(metric)) is None
        ]
    return groups


def _ordered_machines(machines):
    categories = defaultdict(list)
    for machine in machines:
        if machine['category'] in CATEGORY_LABELS:
            categories[machine['category']].append(machine)
    ordered = []
    for category, rows in categories.items():
        valid = sorted(
            (row for row in rows if percentage_value(row.get('avail')) is not None),
            key=lambda m: (percentage_value(m['avail']), str(m['machine'])),
        )
        count = (len(valid) + 1) // 2
        label = f'{CATEGORY_LABELS[category]} · worst {count} of {len(valid)}'
        for position, row in enumerate(valid, 1):
            ordered.append({**row, 'rank': position if position <= count else None,
                            'group_label': label})
        unavailable = sorted(
            (row for row in rows if percentage_value(row.get('avail')) is None),
            key=lambda m: str(m['machine']),
        )
        for row in unavailable:
            ordered.append({**row, 'rank': None,
                            'group_label': f'{CATEGORY_LABELS[category]} · unavailable ({len(unavailable)})'})
    return ordered


def _group_key(row):
    return row['category'], percentage_value(row.get('avail')) is None


def _table_units(rows):
    """Count machine and category-header rows to balance the actual table heights."""
    return len(rows) + sum(i == 0 or _group_key(row) != _group_key(rows[i - 1])
                           for i, row in enumerate(rows))


def _split_tables(ordered):
    if len(ordered) < 2:
        return ordered, []
    split = min(range(1, len(ordered)), key=lambda index: (
        max(_table_units(ordered[:index]), _table_units(ordered[index:])),
        abs(index - len(ordered) / 2),
    ))
    return ordered[:split], ordered[split:]


def _table(rows, category_averages, continued_group=None):
    output = []
    previous = None
    for machine in rows:
        category = machine['category']
        group = _group_key(machine)
        if group != previous:
            continued = ' <span class="hod-au-continued">(continued)</span>' if group == continued_group else ''
            averages = category_averages.get(category, {})
            output.append(
                f'<tr class="hod-au-category" data-category-header="{escape(str(category), quote=True)}">'
                f'<th colspan="2" scope="rowgroup">{escape(machine["group_label"])}{continued}</th>'
                f'<td>{_average_pair(averages, "avail")}</td><td>{_average_pair(averages, "util")}</td></tr>'
            )
            previous = group
        fleet = escape(str(machine['machine']), quote=True)
        rank = machine['rank']
        rank_label = f'<span>{rank}</span>' if rank is not None else ''
        colour = 'spare' if machine.get('is_spare') else 'production'
        output.append(
            f'<tr data-machine="{fleet}" data-category="{escape(str(category), quote=True)}" '
            f'data-rank="{rank if rank is not None else ""}">'
            f'<td class="hod-au-rank">{rank_label}</td>'
            f'<th scope="row" class="hod-au-machine hod-au-{colour}">{fleet}</th>'
            f'{_cell("avail", machine.get("avail"))}{_cell("util", machine.get("util"))}</tr>'
        )
    return (
        '<table class="hod-au-fleet-table"><colgroup><col class="hod-au-rank-col">'
        '<col class="hod-au-fleet-col"><col><col></colgroup>'
        '<thead><tr><th scope="col">Rank</th><th scope="col">Fleet number</th>'
        '<th scope="col">Availability %</th><th scope="col">Utilisation %</th></tr></thead>'
        f'<tbody>{"".join(output)}</tbody></table>'
    )


def _is_number_section(machines):
    """Show every eligible fleet number, with metric-specific worst-half ranks."""
    columns = []
    max_lines = 0
    for metric, title in (('avail', 'Availability · all machines'), ('util', 'Utilisation · all machines')):
        groups = []
        lines = 0
        ranked = _ranked_metric_rows(machines, metric)
        for category in ('ADT', 'Excavator', 'Dozer'):
            rows = ranked.get(category, [])
            numbers = []
            for row in rows:
                fleet = escape(str(row['machine']).strip(), quote=True)
                colour = 'spare' if row.get('is_spare') else 'production'
                rank = row['rank']
                unavailable = percentage_value(row.get(metric)) is None
                marker = '<small class="hod-au-neutral-text">—</small>' if unavailable else ''
                numbers.append(
                    f'<span class="hod-au-{colour}" data-is-metric="{metric}" '
                    f'data-is-category="{category}" data-is-machine="{fleet}" '
                    f'data-rank="{rank if rank is not None else ""}" '
                    f'data-unavailable="{str(unavailable).lower()}">'
                    f'<b class="hod-au-list-rank">{rank if rank is not None else ""}</b>{fleet}{marker}</span>'
                )
            # Estimate conservatively, including the blank rank slot and gaps.
            lines += max(1, ceil(sum(len(str(row['machine'])) * 9 + 34 for row in rows) / 600))
            content = ''.join(numbers) or '<span class="hod-au-neutral-text">—</span>'
            groups.append(
                f'<div class="hod-au-is-group"><strong>{CATEGORY_LABELS[category]}</strong>'
                f'<div class="hod-au-is-numbers">{content}</div></div>'
            )
        max_lines = max(max_lines, lines)
        columns.append(f'<section><h4>{title} <small>Only worst 50% ranked</small></h4>{"".join(groups)}</section>')
    height = 54 + max_lines * 20
    return (f'<div class="hod-au-is-section" style="height:{height}px">'
            f'{"".join(columns)}</div>'), height


def render_grids(data: FleetSummary) -> str:
    ordered = _ordered_machines(data['machines'])
    left, right = _split_tables(ordered)
    continued = _group_key(left[-1]) if left and right and _group_key(left[-1]) == _group_key(right[0]) else None
    tables = _table(left, data['category_averages']) + _table(right, data['category_averages'], continued)
    is_section, is_section_height = _is_number_section(data['machines'])
    units = max(_table_units(left), _table_units(right), 1)
    row_height = min(22, (588 - is_section_height - 7) // units)
    # Around 50 machines fit with 16px text. Denser sites retain a single
    # summary slide, with the height distributed across every filtered row.
    font_size = min(16, max(12, row_height - 2))
    period = f'{escape(data["start_date"])} to {escape(data["end_date"])}'
    overall = data.get('averages', {})
    ranked_count = sum(row['rank'] is not None for row in ordered)
    unavailable_count = sum(percentage_value(row.get('avail')) is None for row in ordered)
    shown = f'{len(ordered)} machines shown · {ranked_count} ranked'
    if unavailable_count:
        shown += f' · {unavailable_count} unavailable shown separately'
    empty = '' if ordered else '<div class="hod-au-empty">No machines match the selected filters.</div>'
    body = (
        f'<div class="hod-au-page-period">{period} · {shown}</div>'
        '<div class="hod-au-overall"><strong>Overall averages (all eligible)</strong>'
        f'<span>Availability {_average_pair(overall, "avail")}</span>'
        f'<span>Utilisation {_average_pair(overall, "util")}</span></div>'
        f'<div class="hod-au-grid-pair">{tables}</div>{empty}{is_section}'
        '<div class="hod-au-legend">'
        '<div><span><i class="hod-au-dot hod-au-production-dot"></i>Production fleet</span>'
        '<span><i class="hod-au-dot hod-au-spare-dot"></i>Spare fleet</span>'
        '<span>Average key: <b class="hod-au-production">Excluding spares</b> / '
        '<b class="hod-au-spare">Including spares</b></span>'
        '<span>List ranks: worst half per category / metric; unavailable last</span></div>'
        '<div><span>Availability: <i class="hod-au-dot hod-au-red-dot"></i>&lt;75% '
        '<i class="hod-au-dot hod-au-amber-dot"></i>75–&lt;85% '
        '<i class="hod-au-dot hod-au-green-dot"></i>≥85%</span>'
        '<span>Utilisation: <i class="hod-au-dot hod-au-red-dot"></i>&lt;70% '
        '<i class="hod-au-dot hod-au-amber-dot"></i>70–&lt;80% '
        '<i class="hod-au-dot hod-au-green-dot"></i>≥80%</span>'
        '<span>— Unavailable / not applicable</span></div></div>'
    )
    return (f'<style>{GRID_CSS}</style><div class="hod-au-grids"><div class="hod-au-grid-page" '
            f'style="--row-height:{row_height}px;--row-font:{font_size}px">{body}</div></div>')


GRID_CSS = """
.hod-au-grids { max-width:100%; }
.hod-au-grid-page { box-sizing:border-box; width:1500px; height:760px; padding:10px 12px;
    display:flex; flex-direction:column; gap:7px; background:#fff; color:#172033;
    font-family:Arial,sans-serif; line-height:1.1; }
.hod-au-page-period { font-size:15px; color:#526071; line-height:18px; }
.hod-au-overall { display:flex; align-items:center; gap:36px; font-size:18px;
    line-height:25px; border-bottom:1px solid #d5d9df; padding-bottom:5px; }
.hod-au-overall span { white-space:nowrap; }
.hod-au-grid-pair { display:grid; grid-template-columns:1fr 1fr; gap:18px; align-items:start; flex:1; min-height:0; }
.hod-au-is-section { box-sizing:border-box; flex-shrink:0; display:grid; grid-template-columns:1fr 1fr;
    gap:18px; padding:8px 10px; border:1px solid #d5d9df; border-radius:4px; background:#f7f8fa; }
.hod-au-is-section > section { min-width:0; }
.hod-au-is-section h4 { font-family:Arial,sans-serif; font-size:16px; font-weight:700;
    line-height:20px; margin:0 0 4px; }
.hod-au-is-group { display:grid; grid-template-columns:96px minmax(0,1fr); gap:8px;
    margin-top:4px; font-size:15px; line-height:20px; }
.hod-au-is-numbers { display:flex; flex-wrap:wrap; gap:0 10px; align-content:start; }
.hod-au-is-numbers > span { display:inline-flex; align-items:baseline; white-space:nowrap; font-weight:700; }
.hod-au-is-section h4 small { font-size:12px; font-weight:normal; margin-left:8px; }
.hod-au-list-rank { display:inline-block; min-width:18px; margin-right:3px; color:#526071;
    font-size:11px; text-align:right; }
.hod-au-is-numbers small { margin-left:3px; font-size:12px; }
.hod-au-fleet-table { width:100%; table-layout:fixed; border-collapse:collapse;
    font-size:var(--row-font); line-height:1; margin:0; }
.hod-au-rank-col { width:8%; }
.hod-au-fleet-col { width:36%; }
.hod-au-fleet-table th, .hod-au-fleet-table td { box-sizing:border-box; border:1px solid #d5d9df;
    padding:0 5px; text-align:center; height:var(--row-height); white-space:nowrap; }
.hod-au-fleet-table thead th { background:#17365d; color:white; height:26px; font-size:16px; }
.hod-au-fleet-table .hod-au-machine { text-align:left; font-weight:700; }
.hod-au-category th, .hod-au-category td { background:#e9edf3; font-weight:700; }
.hod-au-category th { text-align:left; }
.hod-au-continued { font-size:0.85em; font-weight:normal; }
.hod-au-rank span { box-sizing:border-box; display:inline-flex; align-items:center; justify-content:center;
    width:calc(var(--row-height) - 2px); height:calc(var(--row-height) - 2px);
    border:1px solid #7c8594; border-radius:50%; font-size:0.8em; }
.hod-au-production { color:#17365d; }
.hod-au-spare { color:#7030a0; }
.hod-au-red { background:#f8d8d5; color:#9f211b; }
.hod-au-amber { background:#ffebba; color:#805400; }
.hod-au-green { background:#d6efdf; color:#176239; }
.hod-au-neutral { background:#edf0f4; color:#6b7280; }
.hod-au-neutral-text { color:#6b7280; }
.hod-au-legend { font-size:14px; line-height:19px; border-top:1px solid #d5d9df; padding-top:6px; }
.hod-au-legend > div { display:flex; align-items:center; gap:24px; margin:2px 0; }
.hod-au-legend span { display:inline-flex; align-items:center; gap:5px; white-space:nowrap; }
.hod-au-dot { display:inline-block; width:10px; height:10px; border-radius:50%; flex-shrink:0; }
.hod-au-production-dot { background:#17365d; }
.hod-au-spare-dot { background:#7030a0; }
.hod-au-red-dot { background:#d63830; }
.hod-au-amber-dot { background:#d99400; }
.hod-au-green-dot { background:#229451; }
.hod-au-empty { font-size:18px; }
"""
