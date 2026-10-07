"""Controller immutability tests; no live database required."""
import unittest
import frappe
from .daily_production_summary import DailyProductionSummary


class TestDailyProductionSummary(unittest.TestCase):
    def document(self, **values):
        doc = object.__new__(DailyProductionSummary)
        doc.__dict__.update(doctype='Daily Production Summary', flags=frappe._dict(), **values)
        return doc

    def test_manual_insert_is_denied_even_with_ignore_permissions(self):
        doc = self.document(__islocal=True)
        doc.flags.ignore_permissions = True
        with self.assertRaises(frappe.PermissionError):
            doc.before_insert()
        with self.assertRaises(frappe.PermissionError):
            doc.validate()

    def test_existing_snapshot_cannot_be_saved(self):
        doc = self.document(__islocal=False)
        with self.assertRaises(frappe.PermissionError):
            doc.validate()

    def test_delete_and_rename_are_denied(self):
        doc = self.document()
        with self.assertRaises(frappe.PermissionError):
            doc.on_trash()
        with self.assertRaises(frappe.PermissionError):
            doc.before_rename('old', 'new', True)
