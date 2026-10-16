"""Read-only schedule intervals shared by hourly productivity pages."""
from datetime import datetime, time, timedelta

from frappe.utils import get_datetime, getdate

from engineering.engineering.page.adt_hourly_productivity import adt_hourly_productivity as adt


def shift_windows(plan, date, shift, site, fatigue, warnings):
    start = datetime.combine(date, time(6 if shift == "Day" else 18))
    end = start + timedelta(hours=12)
    daily = [row for row in ((plan or {}).get("month_prod_days") or [])
             if row.get("shift_start_date") and getdate(row["shift_start_date"]) == date]
    field = "shift_day_hours" if shift == "Day" else "shift_night_hours"
    hours = daily[0].get(field) if len(daily) == 1 else None
    if hours is not None and float(hours) == 0:
        return [(start, end)], "non_working"
    if hours is None:
        warnings.append(f"MPP working hours are unavailable for {date} {shift}; no non-working shift is inferred.")
    elif 0 < float(hours) < 12:
        warnings.append(f"MPP specifies {hours} working hours for {date} {shift}, without clock times. "
                        "Only configured fatigue is shaded; the shorter shift's timing is unknown.")
    day_type = "Saturday" if date.weekday() == 5 else "Sunday" if date.weekday() == 6 else "Weekday"
    candidates = [row for row in fatigue if row.get("site") == site and row.get("shift") == shift]
    matching = [row for row in candidates if row.get("day_type") == day_type]
    # Never substitute another day type: permitted rows may be only part of the configuration.
    config = matching[0] if matching else None
    if config is None:
        warnings.append(f"No accessible fatigue configuration for {shift} {day_type}; fatigue periods are unknown.")
        return [], "fatigue"
    left_clock = adt._clock_time(config.get("fatigue_start"))
    right_clock = adt._clock_time(config.get("fatigue_end"))
    if left_clock is None or right_clock is None:
        warnings.append(f"Invalid fatigue configuration for {shift} {day_type}; fatigue periods are unknown.")
        return [], "fatigue"
    left, right = datetime.combine(date, left_clock), datetime.combine(date, right_clock)
    if shift == "Night":
        if left_clock < start.time():
            left += timedelta(days=1)
        if right_clock < start.time():
            right += timedelta(days=1)
    if right < left:
        right += timedelta(days=1)
    # Equal endpoints mean no fatigue, never an inferred 24-hour exclusion.
    return ([(max(start, left), min(end, right))] if min(end, right) > max(start, left) else []), "fatigue"


def hour_segments(hour, windows, colour, *, excluded_category="grey"):
    start, end = get_datetime(hour["start"]), get_datetime(hour["end"])
    windows = [(max(start, a), min(end, b)) for a, b in windows if a < end and b > start]
    boundaries = sorted({start, end, *(point for pair in windows for point in pair)})
    segments, minutes = [], 0.0
    for left, right in zip(boundaries, boundaries[1:]):
        excluded = any(a <= left < b for a, b in windows)
        if excluded:
            minutes += (right - left).total_seconds() / 60
        segment = {"start_percent": (left - start).total_seconds() / 36,
                   "end_percent": (right - start).total_seconds() / 36,
                   "category": excluded_category if excluded else colour}
        if segments and segments[-1]["category"] == segment["category"]:
            segments[-1]["end_percent"] = segment["end_percent"]
        else:
            segments.append(segment)
    return segments, minutes
