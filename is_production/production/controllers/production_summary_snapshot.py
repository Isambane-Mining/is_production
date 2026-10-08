"""Internal insertion and immutable controllers for the three snapshot DocTypes."""
import hashlib
import json

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, now_datetime

from .production_summary_sources import load_snapshot
from .production_summary_planning import get_covering_plan
from ..production_summaries.periods import make_period

DOCTYPES = {
    'hourly': 'Hourly Production Summary',
    'shift': 'Shift Production Summary',
    'daily': 'Daily Production Summary',
}
_INSERT_TOKEN = object()


def snapshot_key(site, period):
    # A fixed-length key avoids Location names overflowing Frappe's name column.
    identity = json.dumps([period.kind, site, period.start.isoformat()], separators=(',', ':'))
    return hashlib.sha256(identity.encode()).hexdigest()


class ProductionSnapshot(Document):
    """Permissions protect normal UI/API use; guards also protect Administrator."""
    def _check_internal_insert(self):
        if not self.is_new() or self.flags.get('_production_snapshot_token') is not _INSERT_TOKEN:
            raise frappe.PermissionError('Production summaries are immutable scheduler snapshots.')

    def before_insert(self):
        self._check_internal_insert()

    def before_validate(self):
        # Frappe runs this even when a caller sets flags.ignore_validate.
        self._check_internal_insert()

    def validate(self):
        self._check_internal_insert()
        kind = next(kind for kind, doctype in DOCTYPES.items() if doctype == self.doctype)
        period = make_period(kind, self.period_start)
        if (get_datetime(self.period_end) != period.end or str(self.report_date) != str(period.report_date)
                or self.shift != period.shift or (self.hour_slot or None) != period.hour_slot
                or self.snapshot_key != snapshot_key(self.site, period)):
            raise frappe.ValidationError('Snapshot identity does not match its production period.')

    def autoname(self):
        self.name = self.snapshot_key

    def on_trash(self):
        raise frappe.PermissionError('Production summaries cannot be deleted.')

    def before_rename(self, old, new, merge=False):
        raise frappe.PermissionError('Production summaries cannot be renamed or merged.')


def create_snapshot(site, period):
    """Insert once, including concurrent workers. Caller owns the transaction.

    Savepoints keep one bad site/period from discarding earlier successful
    snapshots. No source refresh, notifications, or source-document saves.
    """
    doctype = DOCTYPES[period.kind]
    key = snapshot_key(site, period)
    if frappe.db.exists(doctype, key):
        return None
    if not get_covering_plan(site, period.report_date):
        return None
    savepoint = f'production_summary_{key[:16]}'
    frappe.db.savepoint(savepoint)
    try:
        values = load_snapshot(site, period)
        generated_at = now_datetime()
        raw = json.loads(values['report_data_json'])
        raw['generated_at'] = str(generated_at)
        values['report_data_json'] = json.dumps(raw, default=str, sort_keys=True)
        doc = frappe.get_doc(dict(values, doctype=doctype, snapshot_key=key, generated_at=generated_at))
        doc.flags._production_snapshot_token = _INSERT_TOKEN
        doc.insert(ignore_permissions=True)
        return doc.name
    except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
        # Unique identity/name violations are successful concurrent generation.
        frappe.db.rollback(save_point=savepoint)
        return None
    except Exception:
        frappe.db.rollback(save_point=savepoint)
        raise
