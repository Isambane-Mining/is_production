# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from is_production.geo_planning.doctype.gis_dataset.test_gis_dataset import make_dataset
from is_production.geo_planning.services import gis_health_service, gis_workstation_service

IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]


class IntegrationTestGISSettings(IntegrationTestCase):
	def test_unreachable_qgis_server_is_reported(self):
		result = gis_health_service.check_qgis_server("http://127.0.0.1:9/ows/")
		self.assertFalse(result["ok"])
		self.assertTrue(result["local"])

	def test_missing_python_is_reported(self):
		result = gis_health_service.run_worker("/nonexistent/python3", "health.py", {})
		self.assertIn("error", result)

	def test_crs_definition_sends_axis_separately(self):
		lo29 = gis_workstation_service.get_crs_definition("EPSG:2053")
		self.assertEqual(lo29["axis"], "wsu")
		self.assertNotIn("+axis", lo29["proj4"])
		self.assertEqual(gis_workstation_service.get_crs_definition("EPSG:4326")["axis"], "enu")

	def test_layer_features_are_web_mercator_and_in_view_only(self):
		from pyproj import Transformer

		dataset = make_dataset("test_gis_ws_points", geometry_type="Point").name
		for x, y in ((-19500, 2860500), (-5000, 2700000)):
			frappe.get_doc(
				{"doctype": "GIS Feature", "dataset": dataset, "geometry": {"type": "Point", "coordinates": [x, y]}}
			).insert()

		to_web = Transformer.from_crs("EPSG:2053", "EPSG:3857", always_xy=True)
		cx, cy = to_web.transform(-19500, 2860500)
		collection = gis_workstation_service.get_layer_features(dataset, [cx - 500, cy - 500, cx + 500, cy + 500])
		self.assertEqual(len(collection["features"]), 1)
		x, y = collection["features"][0]["geometry"]["coordinates"]
		self.assertAlmostEqual(x, cx, places=3)
		self.assertAlmostEqual(y, cy, places=3)
		self.assertFalse(collection["truncated"])

	def test_workstation_api_needs_dataset_permission(self):
		dataset = make_dataset("test_gis_ws_perm", geometry_type="Point").name
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				gis_workstation_service.get_layer_features(dataset, [0, 0, 1, 1])
			with self.assertRaises(frappe.PermissionError):
				gis_workstation_service.get_workstation_config()
		finally:
			frappe.set_user("Administrator")
