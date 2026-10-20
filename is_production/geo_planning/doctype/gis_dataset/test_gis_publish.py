# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt
"""Publishing to QGIS Server and the gateway, against the real QGIS Server of this host.

QGIS Server reads features through its own database connection, so it cannot see this test's
uncommitted rows: the dataset here is committed, and removed (with its project file) afterwards.
"""

import io
import json
import os

import frappe
from frappe.tests import IntegrationTestCase
from pyproj import Transformer

from is_production.geo_planning.services import gis_gateway_service, gis_publish_service

IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]
DATASET = "test_gis_gateway"
COLLARS = [(-19800, 2860200, 1450.5), (-19500, 2860500, 1452.0)]


class IntegrationTestGISPublish(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		settings = frappe.get_single("GIS Settings")
		if not (settings.qgis_db_user and settings.qgis_project_directory):
			raise cls.skipTest(cls, "GIS Settings has no QGIS database account or project directory")
		cls.remove_dataset()
		frappe.get_doc(
			{
				"doctype": "GIS Dataset",
				"dataset_name": DATASET,
				"title": "Test Gateway Collars",
				"geometry_type": "Point",
				"crs": "EPSG:2053",
				"has_z": 1,
				"default_color": "#d0021b",
				"attributes": [
					{"field_name": "hole_id", "data_type": "Text"},
					{"field_name": "depth", "data_type": "Float"},
				],
			}
		).insert()
		cls.ids = []
		for i, (x, y, z) in enumerate(COLLARS):
			feature = frappe.get_doc(
				{
					"doctype": "GIS Feature",
					"dataset": DATASET,
					"feature_label": f"T{i}",
					"geometry": {"type": "Point", "coordinates": [x, y, z]},
					"attributes": {"hole_id": f"T{i}", "depth": 10.5 + i},
				}
			).insert()
			cls.ids.append(feature.name)
		frappe.db.commit()
		cls.report = gis_publish_service.publish_dataset(DATASET)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls.remove_dataset()
		super().tearDownClass()

	@classmethod
	def remove_dataset(cls):
		if frappe.db.exists("GIS Dataset", DATASET):
			gis_publish_service.unpublish(DATASET)
			frappe.db.delete("GIS Feature", {"dataset": DATASET})
			frappe.delete_doc("GIS Dataset", DATASET, force=True)
			frappe.db.commit()

	def get(self, **params):
		return gis_gateway_service.proxy(DATASET, {k.upper(): str(v) for k, v in params.items()})

	def view_box(self, half=1000):
		to_web = Transformer.from_crs("EPSG:2053", "EPSG:3857", always_xy=True)
		x, y = to_web.transform(-19650, 2860350)
		return f"{x - half},{y - half},{x + half},{y + half}"

	def test_publish_writes_a_private_project(self):
		self.assertTrue(self.report["ok"], self.report)
		self.assertEqual(self.report["feature_count"], 2)
		dataset = frappe.get_doc("GIS Dataset", DATASET)
		self.assertEqual(dataset.qgis_publish_status, "Published")
		self.assertEqual(dataset.qgis_publish_signature, gis_publish_service.publish_signature(dataset))
		mode = os.stat(dataset.qgis_project_file).st_mode & 0o777
		self.assertEqual(mode, 0o640)
		leftovers = [f for f in os.listdir(os.path.dirname(dataset.qgis_project_file)) if f.startswith(".publish-")]
		self.assertEqual(leftovers, [])

	def test_getmap_renders_the_features(self):
		from PIL import Image

		status, content, headers = self.get(
			service="WMS", version="1.3.0", request="GetMap", layers=DATASET, crs="EPSG:3857",
			bbox=self.view_box(), width=256, height=256, format="image/png", transparent="true",
		)
		self.assertEqual(status, 200, content[:300])
		self.assertEqual(headers["Content-Type"], "image/png")
		image = Image.open(io.BytesIO(content)).convert("RGBA")
		drawn = [p for p in image.getdata() if p[3] > 0]
		self.assertTrue(drawn, "QGIS drew nothing")
		self.assertTrue(any(r > 150 and g < 80 and b < 80 for r, g, b, a in drawn), "dataset colour not used")

	def test_getfeatureinfo_returns_stable_ids_and_typed_attributes(self):
		status, content, _headers = self.get(
			service="WMS", version="1.3.0", request="GetFeatureInfo", layers=DATASET, query_layers=DATASET,
			crs="EPSG:3857", bbox=self.view_box(), width=101, height=101, i=50, j=50,
			info_format="application/json", feature_count=10, fi_point_tolerance=200,
		)
		self.assertEqual(status, 200, content[:300])
		features = json.loads(content)["features"]
		ids = sorted(int(f["id"].split(".")[-1]) for f in features)
		self.assertEqual(ids, sorted(self.ids))
		props = features[0]["properties"]
		self.assertIsInstance(props["depth"], float)
		self.assertIn("hole_id", props)
		for hidden in ("attributes", "geometry", "owner", "dataset"):
			self.assertNotIn(hidden, props)

	def test_wfs_getfeature_is_capped_and_read_only(self):
		status, content, _headers = self.get(
			service="WFS", version="1.1.0", request="GetFeature", typename=DATASET,
			outputformat="application/json", maxfeatures=999999,
		)
		self.assertEqual(status, 200, content[:300])
		self.assertEqual(len(json.loads(content)["features"]), 2)
		with self.assertRaises(frappe.PermissionError):
			self.get(service="WFS", request="Transaction")
		with self.assertRaises(frappe.PermissionError):
			self.get(service="WMS", request="GetPrint")

	def test_map_parameter_cannot_be_chosen_by_the_client(self):
		status, content, _headers = self.get(
			service="WMS", version="1.3.0", request="GetCapabilities", map="/etc/passwd"
		)
		self.assertEqual(status, 200)
		self.assertIn(DATASET.encode(), content)

	def test_gateway_checks_permission_and_publication(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				self.get(service="WMS", request="GetCapabilities")
		finally:
			frappe.set_user("Administrator")

		frappe.db.set_value("GIS Dataset", DATASET, "qgis_publish_status", "Not Published")
		try:
			with self.assertRaises(frappe.DoesNotExistError):
				self.get(service="WMS", request="GetCapabilities")
		finally:
			frappe.db.set_value("GIS Dataset", DATASET, "qgis_publish_status", "Published")

	def test_schema_change_marks_dataset_for_republishing(self):
		dataset = frappe.get_doc("GIS Dataset", DATASET)
		before = gis_publish_service.publish_signature(dataset)
		dataset.append("attributes", {"field_name": "driller", "data_type": "Text"})
		self.assertNotEqual(gis_publish_service.publish_signature(dataset), before)
