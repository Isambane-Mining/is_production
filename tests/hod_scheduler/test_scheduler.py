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
    def test_redis_failure_keeps_claim_retryable_and_next_tick_can_enqueue(self):
        class Row(dict):
            __getattr__=dict.__getitem__
        row=Row(name='key',schedule='daily',status='Queued',trigger_type='Scheduled',
                scheduled_slot='2026-10-26T06:00',started_at=None)
        fake=SimpleNamespace(get_all=lambda *a,**k:[row],db=Mock(),
            enqueue=Mock(side_effect=[RuntimeError('Redis unavailable'),None]),
            log_error=Mock(),get_traceback=lambda:'Redis unavailable')
        from datetime import datetime
        doc={'enabled':1,'delivery_site':'isambane.co.za'}
        with patch.object(scheduler,'frappe',fake),patch.object(scheduler,'_schedule',return_value=doc),patch.object(scheduler,'check_environment'):
            scheduler.reconcile_pending(datetime(2026,10,26,6,1))
            self.assertEqual(row.status,'Queued')
            scheduler.reconcile_pending(datetime(2026,10,26,6,2))
            self.assertEqual(fake.enqueue.call_count,2)

    def test_worker_interruption_is_visible_without_automatic_resend(self):
        class Row(dict):
            __getattr__=dict.__getitem__
        row=Row(name='key',schedule='daily',status='Generating',trigger_type='Scheduled',
                scheduled_slot='2026-10-26T06:00',started_at='2026-10-26 06:00:00')
        db=Mock()
        db.sql.return_value=[('Generating',)]
        def update(doctype,name,values,**kwargs):
            if doctype==scheduler.DELIVERY: row.update(values)
        db.set_value.side_effect=update
        fake=SimpleNamespace(get_all=lambda *a,**k:[row],db=db,enqueue=Mock(),
            log_error=Mock(),get_traceback=lambda:'error')
        from datetime import datetime
        with patch.object(scheduler,'frappe',fake):
            scheduler.reconcile_pending(datetime(2026,10,26,6,36))
        self.assertEqual(row.status,'Interrupted')
        fake.enqueue.assert_not_called()

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
