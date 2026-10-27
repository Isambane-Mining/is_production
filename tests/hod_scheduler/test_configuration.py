import json
import runpy
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ConfigurationTests(unittest.TestCase):
    def test_hod_has_no_active_automatic_hook_and_existing_jobs_remain(self):
        hooks = runpy.run_path(str(ROOT / 'is_production/hooks.py'))
        cron = hooks['scheduler_events']['cron']
        self.assertFalse(any('hod_presentation_scheduler' in method
                             for methods in cron.values() for method in methods))
        self.assertIn('is_production.production.doctype.production_efficiency.production_efficiency.update_weekly_records',
                      cron['0 6 * * *'])
        self.assertIn('is_production.production.controllers.notifications.send_ceo_dashboard_daily_emails',
                      cron['55 5 * * *'])
        self.assertIn('Isambane-Mining/engineering', hooks['required_apps'])

    def test_approved_shared_children_are_used(self):
        path = ROOT / 'is_production/production/doctype/hod_presentation_schedule/hod_presentation_schedule.json'
        fields = {field['fieldname']: field for field in json.loads(path.read_text())['fields']}
        self.assertEqual(fields['sites']['options'], 'WearCheck Users')
        self.assertEqual(fields['recipients']['options'], 'Production Notification Recipient')


if __name__ == '__main__':
    unittest.main()
