import sys
import unittest
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'is_production'))
sys.modules.setdefault('frappe', SimpleNamespace())
from production.controllers import hod_presentation_scheduler as scheduler
from production.controllers import hod_schedule_policy as policy


class SharedTableTests(unittest.TestCase):
    def test_sites_read_location_from_wearcheck_rows(self):
        self.assertEqual(policy.sites({'sites': [
            {'user': 'runner', 'location': ' Koppie '},
            {'user': 'runner', 'location': 'Koppie'},
            {'user': 'runner', 'location': 'Uitgevallen'},
        ]}), ['Koppie', 'Uitgevallen'])

    def test_blank_location_cannot_silently_remove_one_selected_site(self):
        with self.assertRaises(ValueError):
            policy.sites({'sites': [{'location': 'Koppie'}, {'location': ''}]})

    def test_recipients_resolve_user_email_instead_of_cached_email(self):
        doc = {'recipients': [{'user': 'juan', 'user_email': 'old@example.com'}]}
        lookup = lambda user: {'email': 'juan@example.com', 'enabled': 1}
        self.assertEqual(policy.recipients(doc, lookup, sending=True), ['juan@example.com'])

    def test_disabled_recipient_rows_and_disabled_users_do_not_receive_email(self):
        doc = {'recipients': [
            {'user': 'row-disabled', 'enabled': 0},
            {'user': 'user-disabled', 'enabled': 1},
            {'user': 'active', 'enabled': 1},
            {'user': 'active', 'enabled': 1},
        ]}
        accounts = {'user-disabled': {'email': 'off@example.com', 'enabled': 0},
                    'active': {'email': 'active@example.com', 'enabled': 1}}
        self.assertEqual(policy.recipients(doc, accounts.get, sending=True), ['active@example.com'])

    def test_preview_allows_disabled_lab_recipient_without_sending(self):
        lookup = lambda user: {'email': 'juan@example.com', 'enabled': 0}
        self.assertEqual(policy.recipients({'recipients': [{'user': 'juan'}]}, lookup),
                         ['juan@example.com'])

    def test_all_disabled_recipients_prevent_send(self):
        lookup = lambda user: {'email': 'off@example.com', 'enabled': 0}
        with self.assertRaises(ValueError):
            policy.recipients({'recipients': [{'user': 'off'}]}, lookup, sending=True)


class DeliveryGuardTests(unittest.TestCase):
    def setup_delivery(self, stack, *, slot='2026-10-27T06:00', trigger='Scheduled'):
        entry = SimpleNamespace(schedule='Daily HOD', trigger_type=trigger, scheduled_slot=slot)
        self.doc = SimpleNamespace(name='Daily HOD', enabled=1, modified='original')
        db = Mock()
        db.sql.return_value = [('Queued',)]
        self.fake = SimpleNamespace(db=db, get_doc=Mock(return_value=entry), sendmail=Mock(),
                                    as_json=str, get_traceback=lambda: 'test error', log_error=Mock())
        stack.enter_context(patch.object(scheduler, 'frappe', self.fake))
        stack.enter_context(patch.object(scheduler, '_now', return_value=datetime(2026, 10, 27, 6, 5)))
        self.get_schedule = stack.enter_context(patch.object(scheduler, '_schedule', return_value=self.doc))
        stack.enter_context(patch.object(scheduler, 'check_environment'))
        stack.enter_context(patch.object(scheduler, '_email_ready'))
        self.generate = stack.enter_context(patch.object(scheduler, '_generate', return_value=(
            b'PK', 'preview.pptx', {'site': ['Koppie'], 'end_date': '2026-10-25'})))
        stack.enter_context(patch.object(scheduler.policy, 'recipients', return_value=['juan@example.com']))
        stack.enter_context(patch.object(scheduler, '_save_presentation', return_value=SimpleNamespace(
            name='FILE-1', file_url='/private/files/preview.pptx')))

    def test_worker_rejects_expired_slot_before_generating(self):
        with ExitStack() as stack:
            self.setup_delivery(stack, slot='2026-10-27T05:00')
            result = scheduler.deliver('delivery-key')
            self.assertEqual(result['status'], 'Failed')
            self.generate.assert_not_called()
            self.fake.sendmail.assert_not_called()

    def test_current_locking_read_detects_disable_during_capture(self):
        with ExitStack() as stack:
            self.setup_delivery(stack)
            changed = SimpleNamespace(name='Daily HOD', enabled=0, modified='changed')
            self.get_schedule.side_effect = lambda name, for_update=False: changed if for_update else self.doc
            with self.assertRaisesRegex(ValueError, 'Schedule changed'):
                scheduler.deliver('delivery-key')
            self.fake.sendmail.assert_not_called()

    def test_manual_send_is_not_subject_to_scheduled_cutoff(self):
        with ExitStack() as stack:
            self.setup_delivery(stack, slot='manual-test', trigger='Manual')
            self.assertEqual(scheduler.deliver('delivery-key')['status'], 'Email Queued')
            self.fake.sendmail.assert_called_once()


if __name__ == '__main__':
    unittest.main()
