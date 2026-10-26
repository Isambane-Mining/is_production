import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import frappe
from . import hod_presentation


class TestHodNavigation(unittest.TestCase):
    def test_only_hod_destinations_change_and_sidebar_presets_survive(self):
        workspace = frappe._dict(name='workspace-hod', parent='Production')
        sidebar = frappe._dict(name='sidebar-hod', parent='Production', parenttype='Sidebar',
                              filters='{"site":["Uitgevallen"],"end_date":"2026-03-31"}',
                              route_options='{"summary_type":"Average Per Machine"}')
        db = SimpleNamespace(exists=Mock(return_value=True), set_value=Mock())
        def links(doctype, **kwargs):
            self.assertEqual(kwargs['filters'], {'link_type': 'Report', 'link_to': 'HOD Presentation'})
            return [workspace] if doctype == 'Workspace Link' else [sidebar]
        with (patch.object(frappe, 'db', db, create=True),
              patch.object(frappe, 'get_all', side_effect=links),
              patch.object(frappe, 'clear_document_cache')):
            hod_presentation.sync_navigation()
        self.assertEqual(db.set_value.call_count, 2)
        update = db.set_value.call_args_list[1].args[2]
        self.assertEqual(update['link_type'], 'Page')
        self.assertEqual(update['link_to'], 'hod-presentation')
        self.assertEqual(frappe.parse_json(update['route_options']), {
            'site': ['Uitgevallen'], 'end_date': '2026-03-31',
            'summary_type': 'Average Per Machine',
        })

    def test_native_filter_rows_and_explicit_options_precedence(self):
        item = frappe._dict(
            filters=frappe.as_json([
                ['Monthly Production Planning', 'site', '=', ['Uitgevallen']],
                ['Monthly Production Planning', 'end_date', '=', '2026-03-01'],
            ]),
            route_options='{"end_date":"2026-03-31"}',
        )
        self.assertEqual(hod_presentation._page_route_options(item), {
            'site': ['Uitgevallen'], 'end_date': '2026-03-31',
        })
