import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'is_production'))
fake_frappe=SimpleNamespace()
sys.modules.setdefault('frappe',fake_frappe)
from production.controllers import hod_presentation_scheduler as scheduler


class SchedulerTests(unittest.TestCase):
    def test_due_slot_is_enqueued_once_without_recovery_loop(self):
        from datetime import datetime
        fake = SimpleNamespace(db=Mock(), local=SimpleNamespace(site='isambane.co.za'), get_all=lambda *a, **k: ['Daily HOD'],
                               enqueue=Mock(), log_error=Mock(), get_traceback=lambda: 'error')
        doc = {'name': 'Daily HOD', 'enabled': 1, 'delivery_site': 'isambane.co.za'}
        with patch.object(scheduler, 'frappe', fake), patch.object(scheduler, '_schedule', return_value=doc), \
             patch.object(scheduler, '_now', return_value=datetime(2026, 10, 27, 6)), \
             patch.object(scheduler, 'check_environment'), \
             patch.object(scheduler.policy, 'due_slot', return_value='2026-10-27T06:00'), \
             patch.object(scheduler, '_claim_delivery', side_effect=['delivery-key', None]):
            scheduler.dispatch_due()
            scheduler.dispatch_due()
        fake.enqueue.assert_called_once()
        self.assertEqual(fake.enqueue.call_args.kwargs['delivery_name'], 'delivery-key')

    def test_queue_failure_is_recorded_for_manual_review(self):
        from datetime import datetime
        fake = SimpleNamespace(db=Mock(), local=SimpleNamespace(site='isambane.co.za'), get_all=lambda *a, **k: ['Daily HOD'],
            enqueue=Mock(side_effect=RuntimeError('Redis unavailable')), log_error=Mock(),
            get_traceback=lambda: 'Redis unavailable')
        doc = {'enabled': 1, 'delivery_site': 'isambane.co.za'}
        with patch.object(scheduler, 'frappe', fake), patch.object(scheduler, '_schedule', return_value=doc), \
             patch.object(scheduler, '_now', return_value=datetime(2026, 10, 27, 6)), \
             patch.object(scheduler, 'check_environment'), \
             patch.object(scheduler.policy, 'due_slot', return_value='2026-10-27T06:00'), \
             patch.object(scheduler, '_claim_delivery', return_value='delivery-key'):
            scheduler.dispatch_due()
        self.assertEqual(fake.db.set_value.call_args.args[2]['status'], 'Failed')
        fake.log_error.assert_called_once()

    def test_partial_export_stops_before_send(self):
        with self.assertRaises(ValueError):
            scheduler.validate_rows([{'site':'Koppie','engineering':{}}],['Koppie','Uitgevallen'])

    def test_duplicate_site_is_not_a_complete_export(self):
        with self.assertRaises(ValueError):
            scheduler.validate_rows([{'site':'Koppie','engineering':{}},{'site':'Koppie','engineering':{}}],['Koppie','Uitgevallen'])

    def test_complete_export_accepts_all_sites(self):
        scheduler.validate_rows([{'site':'Koppie','engineering':{}},{'site':'Uitgevallen','engineering':{}}],['Koppie','Uitgevallen'])

    def test_missing_engineering_is_rejected(self):
        with self.assertRaises(ValueError): scheduler.validate_rows([{'site':'Koppie'}],['Koppie'])

    def test_background_run_never_repeats_an_email_already_queued(self):
        db=Mock()
        db.sql.return_value=[('Email Queued',)]
        fake=SimpleNamespace(db=db)
        with patch.object(scheduler,'frappe',fake),patch.object(scheduler,'_generate') as generate:
            scheduler.deliver('delivery-key')
        generate.assert_not_called()

    def test_preview_never_invokes_email_path(self):
        doc=Mock(name='schedule')
        doc.name='schedule'
        with patch.object(scheduler,'frappe',SimpleNamespace(db=Mock())),patch.object(scheduler,'_schedule',return_value=doc),patch.object(scheduler,'_generate',return_value=(b'PPTX','test.pptx',{'site':['Koppie']})),patch.object(scheduler,'_save_presentation',return_value=SimpleNamespace(name='FILE-1',file_url='/private/files/test.pptx')),patch.object(scheduler,'_claim_delivery') as claim:
            result=scheduler.run_now('schedule',send=0)
        self.assertEqual(result['status'],'Preview only; no email queued')
        claim.assert_not_called()

    def test_blank_environment_does_not_allow_email(self):
        fake=SimpleNamespace(local=SimpleNamespace(site='juan.isambane.co.za'))
        with patch.object(scheduler,'frappe',fake):
            with self.assertRaises(ValueError): scheduler.check_environment({})
            with self.assertRaises(ValueError): scheduler.check_environment({'delivery_site':'isambane.co.za'})

    def test_matching_environment_allows_email(self):
        fake=SimpleNamespace(local=SimpleNamespace(site='isambane.co.za'))
        with patch.object(scheduler,'frappe',fake):
            scheduler.check_environment({'delivery_site':'isambane.co.za'})


if __name__=='__main__': unittest.main()
