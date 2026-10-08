"""Pure planning-window and operational-period coverage helpers."""
from datetime import datetime, timedelta

from .production_summary_periods import completed_periods


def merge_plan_windows(grouped):
    result = {}
    for site, windows in grouped.items():
        merged = []
        for start, end in sorted(windows):
            if merged and start <= merged[-1][1] + timedelta(days=1):
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        result[site] = merged
    return result


def eligible_periods(kind, start, as_of, windows):
    """Skip uncovered history without iterating each inactive hour; preserve cursors."""
    covered_until = start
    for first, last in sorted(windows):
        window_start = datetime.combine(first, datetime.min.time()).replace(hour=6)
        window_end = datetime.combine(last + timedelta(days=1), datetime.min.time()).replace(hour=6)
        left, right = max(start, window_start, covered_until), min(as_of, window_end)
        if left < right:
            yield from completed_periods(kind, left, right)
        covered_until = max(covered_until, window_end)
