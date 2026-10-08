"""Scheduling, serialization, mutual exclusion and bounded recovery contracts."""
import unittest
from datetime import datetime, date
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from is_production.production.controllers import production_summary_scheduler as scheduler
from is_production import hooks


class TestSummarySchedule(unittest.TestCase):
    def test_exact_staggered_cron_layout(self):
        expected = {'22 * * * *': 'create_hourly_summaries',
                    '27 6,18 * * *': 'create_shift_summaries',
                    '37 6 * * *': 'create_daily_summaries',
                    '37 2 * * *': 'recover_hourly_summaries',
                    '43 2 * * *': 'recover_shift_summaries',
                    '49 2 * * *': 'recover_daily_summaries'}
        prefix='is_production.production.controllers.production_summary_scheduler.'
        actual={cron:method[len(prefix):] for cron,methods in hooks.scheduler_events['cron'].items()
                for method in methods if method.startswith(prefix)}
        self.assertEqual(actual,expected)
        self.assertFalse(any('production_summaries.scheduler' in method for methods in hooks.scheduler_events.values()
                             if isinstance(methods,list) for method in methods))

    def test_held_lock_skips_without_reading_or_saving(self):
        lock=MagicMock()
        lock.acquire.return_value=False
        with patch.object(scheduler,'_execution_lock',return_value=lock), \
             patch.object(scheduler,'get_site_starts') as sources, \
             patch.object(scheduler,'create_snapshot') as create:
            result=scheduler.recover('hourly',as_of=datetime(2026,10,1,7))
        self.assertTrue(result['skipped'])
        sources.assert_not_called()
        create.assert_not_called()
        lock.release.assert_not_called()

    def test_hard_attempt_cap(self):
        with self.assertRaises(ValueError):
            scheduler.recover('hourly',batch_size=201)

    def test_budget_stops_and_returns_cursor_without_enqueuing(self):
        now=datetime(2026,10,1,9)
        lock=MagicMock()
        lock.acquire.return_value=True
        with patch.object(scheduler,'_execution_lock',return_value=lock), \
             patch.object(scheduler,'get_site_starts',return_value={'Koppie':date(2026,10,1)}), \
             patch.object(scheduler,'get_plan_windows',return_value={'Koppie':[(date(2026,10,1),date(2026,10,1))]}), \
             patch.object(scheduler.frappe,'get_all',return_value=[]), \
             patch.object(scheduler,'create_snapshot',return_value='saved') as create, \
             patch.object(scheduler,'monotonic',side_effect=[0,0,181]), \
             patch.object(scheduler.frappe,'enqueue') as enqueue, \
             patch.object(scheduler,'_commit_and_release') as finish:
            result=scheduler.recover('hourly',as_of=now,time_budget=180)
        self.assertEqual(result['attempted'],1)
        self.assertEqual(result['continuation'],{'site':'Koppie','start':'2026-10-01T07:00:00'})
        enqueue.assert_not_called()
        finish.assert_called_once_with(lock)

    def test_lock_released_on_exception(self):
        lock=MagicMock()
        lock.acquire.return_value=True
        with patch.object(scheduler,'_execution_lock',return_value=lock), \
             patch.object(scheduler,'get_plan_windows',side_effect=RuntimeError('database unavailable')), \
             patch.object(scheduler.frappe,'db',SimpleNamespace(rollback=MagicMock())):
            with self.assertRaises(RuntimeError):
                scheduler.recover('hourly',as_of=datetime(2026,10,1,7))
        lock.release.assert_called_once()

    def test_success_commits_before_releasing_lock(self):
        events=[]
        lock=MagicMock()
        lock.acquire.return_value=True
        lock.release.side_effect=lambda:events.append('release')
        database=SimpleNamespace(commit=lambda:events.append('commit'))
        with patch.object(scheduler,'_execution_lock',return_value=lock), \
             patch.object(scheduler.frappe,'db',database):
            scheduler._run_locked(lambda:events.append('action'))
        self.assertEqual(events,['action','commit','release'])

    def test_all_cron_workers_enqueue_one_bounded_job_at_frozen_cutoff(self):
        queued=[]
        methods=[('create_hourly_summaries','generate','hourly'),
                 ('create_shift_summaries','generate','shift'),
                 ('create_daily_summaries','generate','daily'),
                 ('recover_hourly_summaries','run_recovery','hourly'),
                 ('recover_shift_summaries','run_recovery','shift'),
                 ('recover_daily_summaries','run_recovery','daily')]
        with patch.object(scheduler,'now_datetime',return_value=datetime(2026,10,1,6,27)), \
             patch.object(scheduler.frappe,'enqueue',side_effect=lambda **kw:queued.append(kw)):
            for name,action,kind in methods:
                getattr(scheduler,name)()
        self.assertEqual(len(queued),6)
        for job,(_,action,kind) in zip(queued,methods):
            self.assertEqual(job['method'],scheduler.METHOD_PREFIX+action)
            self.assertEqual(job['kind'],kind)
            self.assertEqual(job['timeout'],300)
            self.assertTrue(job['deduplicate'])
            self.assertTrue(job['enqueue_after_commit'])
            self.assertEqual(job['as_of'],'2026-10-01T06:00:00')
            self.assertNotIn('now',job)
        self.assertEqual(len({job['job_id'] for job in queued}),6)
