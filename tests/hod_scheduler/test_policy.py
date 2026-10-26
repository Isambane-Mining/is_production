import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'is_production/production/controllers'))
import hod_schedule_policy as policy


class PolicyTests(unittest.TestCase):
    def test_monday_cutoff_is_yesterday(self):
        self.assertEqual(policy.previous_sunday(date(2026, 10, 26)), date(2026, 10, 25))

    def test_sunday_cutoff_is_previous_week(self):
        self.assertEqual(policy.previous_sunday(date(2026, 10, 25)), date(2026, 10, 18))

    def test_month_and_year_boundaries(self):
        self.assertEqual(policy.previous_sunday(date(2027, 1, 1)), date(2026, 12, 27))
        self.assertEqual(policy.previous_sunday(date(2026, 11, 1)), date(2026, 10, 25))

    def test_filters_retain_plan_start_and_all_assets(self):
        filters = policy.report_filters({'sites':[{'site':'Koppie'},{'site':'Uitgevallen'}]}, date(2026,10,26))
        self.assertEqual(filters, {'site':['Koppie','Uitgevallen'], 'end_date':'2026-10-25',
            'summary_type':'Average Per Machine', 'machine_scope':'Include Swing/Spare',
            'asset_ownership':'All Assets', 'au_target_filter':'85% A & U'})
        self.assertNotIn('start_date', filters)

    def test_each_run_resolves_dynamic_cutoff(self):
        doc={'sites':[{'site':'Koppie'}]}
        self.assertNotEqual(policy.report_filters(doc,date(2026,10,26))['end_date'],
                            policy.report_filters(doc,date(2026,11,2))['end_date'])

    def test_disabled_schedule_not_due(self):
        self.assertIsNone(policy.due_slot({'enabled':0,'send_time':'06:00:00'},datetime(2026,10,26,6)))

    def test_due_at_six_including_sunday(self):
        for day in [25,26]:
            self.assertEqual(policy.due_slot({'enabled':1,'send_time':'06:00:00','frequency':'Daily'},
                datetime(2026,10,day,6)), f'2026-10-{day}T06:00')

    def test_before_and_outside_catchup_window_not_due(self):
        doc={'enabled':1,'send_time':'06:00:00'}
        self.assertIsNone(policy.due_slot(doc,datetime(2026,10,26,5,59)))
        self.assertIsNone(policy.due_slot(doc,datetime(2026,10,26,6,15)))
        self.assertIsNotNone(policy.due_slot(doc,datetime(2026,10,26,6,14)))

    def test_weekly_day(self):
        doc={'enabled':1,'send_time':'06:00:00','frequency':'Weekly','weekday':'Monday'}
        self.assertIsNone(policy.due_slot(doc,datetime(2026,10,25,6)))
        self.assertIsNotNone(policy.due_slot(doc,datetime(2026,10,26,6)))

    def test_recipients_and_sites_deduplicate(self):
        doc={'recipients':[{'email':' Juan@example.com '},{'email':'juan@example.com'}],
             'sites':[{'site':' Koppie '},{'site':'Koppie'}]}
        self.assertEqual(policy.recipients(doc),['Juan@example.com'])
        self.assertEqual(policy.sites(doc),['Koppie'])

    def test_rejects_empty_sites_and_recipients(self):
        for function in [policy.sites, policy.recipients]:
            with self.assertRaises(ValueError): function({})

    def test_rejects_bad_recipient(self):
        with self.assertRaises(ValueError): policy.recipients({'recipients':[{'email':'not-an-email'}]})

    def test_slots_distinguish_schedule_day_and_time(self):
        self.assertEqual(policy.delivery_key('daily','2026-10-26T06:00'),policy.delivery_key('daily','2026-10-26T06:00'))
        self.assertNotEqual(policy.delivery_key('daily','2026-10-26T06:00'),policy.delivery_key('daily','2026-10-27T06:00'))
        self.assertNotEqual(policy.delivery_key('daily','2026-10-26T06:00'),policy.delivery_key('other','2026-10-26T06:00'))

    def test_rejects_unknown_schedule_frequency(self):
        with self.assertRaises(ValueError): policy.due_slot({'enabled':1,'send_time':'06:00:00','frequency':'Hourly'},datetime(2026,10,26,6))


if __name__ == '__main__': unittest.main()
