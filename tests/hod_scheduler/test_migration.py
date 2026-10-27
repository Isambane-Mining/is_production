import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.modules.setdefault('frappe', SimpleNamespace())
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('hod_reuse_migration',
    ROOT / 'is_production/patches/reuse_hod_schedule_child_tables.py')
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class MigrationTests(unittest.TestCase):
    def test_missing_or_ambiguous_user_is_not_silently_dropped(self):
        with self.assertRaises(ValueError):
            migration.recipient_user('missing@example.com', [])
        with self.assertRaises(ValueError):
            migration.recipient_user('juan@example.com', [
                {'name': 'one', 'email': 'juan@example.com'},
                {'name': 'two', 'email': 'JUAN@example.com'}])

    def test_existing_disabled_lab_user_is_preserved_by_migration(self):
        self.assertEqual(migration.recipient_user('JUAN@example.com',
            [{'name': 'juan', 'email': 'juan@example.com', 'enabled': 0}]), 'juan')

    def fake_site(self, recipient='juan@example.com'):
        saved = {}
        recipient_row = SimpleNamespace(name='old-email', parent='Daily HOD',
            parentfield='recipients', idx=1, email=recipient)
        site_row = SimpleNamespace(name='old-site', parent='Daily HOD', parentfield='sites', idx=1, site='Koppie')
        def exists(doctype, name):
            if doctype == 'DocType': return name == 'WearCheck Users'
            if doctype == 'User': return name == 'Administrator'
            return (doctype, name) in saved
        def child(values):
            def insert(ignore_permissions=False, set_name=None):
                # Frappe hash child names are regenerated unless set_name is explicit.
                actual_name = set_name or 'random-' + str(len(saved))
                saved[(values['doctype'], actual_name)] = dict(values, name=actual_name)
            return SimpleNamespace(insert=insert)
        db = Mock()
        db.table_exists.return_value = True
        db.exists.side_effect = exists
        db.sql.side_effect = lambda query, *args, **kwargs: [recipient_row] if '`email`' in query else [site_row]
        db.get_value.return_value = 'Administrator'
        fake = SimpleNamespace(db=db, reload_doc=Mock(), get_doc=child,
            get_all=lambda *a, **kw: [{'name': 'juan', 'email': 'juan@example.com'}])
        return fake, saved

    def test_repeated_migration_preserves_exactly_one_copy_of_each_row(self):
        fake, saved = self.fake_site()
        with patch.object(migration, 'frappe', fake):
            migration.execute()
            migration.execute()
        self.assertEqual(len(saved), 2)
        site = next(v for (doctype, _), v in saved.items() if doctype == 'WearCheck Users')
        recipient = next(v for (doctype, _), v in saved.items() if doctype == 'Production Notification Recipient')
        self.assertEqual((site['location'], site['user'], site['idx']), ('Koppie', 'Administrator', 1))
        self.assertEqual(recipient['user'], 'juan')
        self.assertTrue(all(call.args[2:] == ('enabled', 0) for call in fake.db.set_value.call_args_list))

    def test_unresolved_recipient_stops_before_any_row_is_written(self):
        fake, saved = self.fake_site(recipient='missing@example.com')
        with patch.object(migration, 'frappe', fake), self.assertRaises(ValueError):
            migration.execute()
        self.assertFalse(saved)
        fake.db.set_value.assert_not_called()


if __name__ == '__main__':
    unittest.main()
