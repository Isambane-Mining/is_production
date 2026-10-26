"""Pure scheduling rules, shared by validation, automatic and manual runs."""
import hashlib
import re
from datetime import date, datetime, time, timedelta

WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
DEFAULTS = {
    'summary_type': 'Average Per Machine',
    'machine_scope': 'Include Swing/Spare',
    'asset_ownership': 'All Assets',
    'au_target_filter': '85% A & U',
}


def previous_sunday(today: date) -> date:
    return today - timedelta(days=today.weekday() + 1)


def send_time(value) -> time:
    if isinstance(value, timedelta):
        total = int(value.total_seconds())
        if not 0 <= total < 86400:
            raise ValueError('Send time must be within one day.')
        result = time(total // 3600, total % 3600 // 60, total % 60)
    elif isinstance(value, time):
        result = value
    else:
        result = time.fromisoformat(str(value or '06:00:00'))
    if result.second or result.microsecond:
        raise ValueError('Use a whole minute for send time.')
    return result


def due_slot(doc, now: datetime):
    if not int(doc.get('enabled') or 0):
        return None
    frequency = doc.get('frequency') or 'Daily'
    if frequency not in ('Daily', 'Weekly'):
        raise ValueError('Frequency must be Daily or Weekly.')
    if frequency == 'Weekly':
        weekday = doc.get('weekday')
        if weekday not in WEEKDAYS:
            raise ValueError('Select a valid weekday.')
        if WEEKDAYS[now.weekday()] != weekday:
            return None
    scheduled = datetime.combine(now.date(), send_time(doc.get('send_time')))
    if not 0 <= (now - scheduled).total_seconds() < 900:
        return None
    return scheduled.strftime('%Y-%m-%dT%H:%M')


def sites(doc):
    values = list(dict.fromkeys(str(row.get('site') or '').strip() for row in doc.get('sites', [])))
    values = [value for value in values if value]
    if not values:
        raise ValueError('Select at least one site.')
    return values


def recipients(doc):
    result, seen = [], set()
    for row in doc.get('recipients', []):
        value = str(row.get('email') or '').strip()
        if not re.fullmatch(r'[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+', value):
            raise ValueError('Every recipient must have a valid email address.')
        if value.casefold() not in seen:
            result.append(value)
            seen.add(value.casefold())
    if not result:
        raise ValueError('Add at least one recipient.')
    return result


def report_filters(doc, today):
    return {'site': sites(doc), 'end_date': str(previous_sunday(today)),
            **{key: doc.get(key) or default for key, default in DEFAULTS.items()}}


def delivery_key(name, slot):
    return hashlib.sha256(f'{name}\0{slot}'.encode()).hexdigest()[:40]
