"""Calendar source hours and 06:00 operational reporting periods."""
from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from frappe.utils import get_datetime, getdate


@dataclass(frozen=True)
class Period:
    kind: str
    start: datetime
    end: datetime

    @property
    def report_date(self):
        return (self.start - timedelta(hours=6)).date()

    @property
    def shift(self):
        if self.kind == 'daily':
            return 'Full Daily'
        return 'Day' if 6 <= self.start.hour < 18 else 'Night'

    @property
    def hour_slot(self):
        return hour_slot(self.start) if self.kind == 'hourly' else None

    @property
    def day_start(self):
        return datetime.combine(self.report_date, datetime.min.time()).replace(hour=6)

    @property
    def shift_start(self):
        return self.day_start + timedelta(hours=12 if self.shift == 'Night' else 0)


def hour_slot(start):
    return f'{start.hour:02}:00-{(start.hour + 1) % 24:02}:00'


def make_period(kind, start):
    start = get_datetime(start)
    if kind not in ('hourly', 'shift', 'daily') or start.minute or start.second or start.microsecond:
        raise ValueError('Invalid or unaligned production period')
    if kind == 'shift' and start.hour not in (6, 18):
        raise ValueError('Shifts start at 06:00 or 18:00')
    if kind == 'daily' and start.hour != 6:
        raise ValueError('Production days start at 06:00')
    hours = {'hourly': 1, 'shift': 12, 'daily': 24}[kind]
    return Period(kind, start, start + timedelta(hours=hours))


def completed_hour(now):
    start = get_datetime(now).replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    return make_period('hourly', start)


def completed_periods(kind, start, now):
    p = make_period(kind, start)
    now = get_datetime(now)
    while p.end <= now:
        yield p
        p = make_period(kind, p.end)


def source_hour(source_date, slot):
    """Accept padded/unpadded one-hour slots; never guess malformed drill slots."""
    match = re.fullmatch(r'(\d{1,2}):00-(\d{1,2}):00', str(slot or '').strip())
    if not match:
        raise ValueError(f'Invalid hourly slot: {slot}')
    start, end = map(int, match.groups())
    if not 0 <= start <= 23 or end != (start + 1) % 24 and not (start == 23 and end == 24):
        raise ValueError(f'Not a one-hour slot: {slot}')
    return datetime.combine(getdate(source_date), datetime.min.time()).replace(hour=start)
