# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt
"""QGIS Processing through the real worker on this host.

Processing jobs commit as they go, so this test cleans up after itself: datasets start with
``test_proc`` and every job it creates is removed with its files.
"""

import json
import math

import frappe
from frappe.tests import IntegrationTestCase

from is_production.geo_planning.services import gis_processing_service as proc

IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]
POINTS = "test_proc_points"
COLLARS = [(-19500, 2860500, 1450.0, "BH1", 40.0), (-19400, 2860600, 1451.0, "BH2", 35.0), (-19300, 2860700, 1452.0, "BH3", 45.0)]


class IntegrationTestGISProcessing(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.get_single_value("GIS Settings", "qgis_python"):
			raise cls.skipTest(cls, "GIS Settings has no PyQGIS Python")
		cls.cleanup()
		frappe.get_doc(
			{
				"doctype": "GIS Dataset",
				"dataset_name": POINTS,
				"title": "Test Processing Collars",
				"geometry_type": "Point",
				"crs": "EPSG:2053",
				"has_z": 1,
				"attributes": [{"field_name": "hole", "data_type": "Text"}, {"field_name": "depth", "data_type": "Float"}],
			}
		).insert()
		cls.ids = []
		for x, y, z, hole, depth in COLLARS:
			cls.ids.append(
				frappe.get_doc(
					{
						"doctype": "GIS Feature",
						"dataset": POINTS,
						"geometry": {"type": "Point", "coordinates": [x, y, z]},
						"attributes": {"hole": hole, "depth": depth},
					}
				).insert().name
			)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls.cleanup()
		super().tearDownClass()

	@classmethod
	def cleanup(cls):
		jobs = set(frappe.get_all(proc.JOB, filters={"parameters": ["like", "%test_proc%"]}, pluck="name"))
		for name in jobs:
			frappe.delete_doc(proc.JOB, name, force=True, ignore_permissions=True)
		for name in frappe.get_all("GIS Dataset", filters={"name": ["like", "test_proc%"]}, pluck="name"):
			frappe.db.delete("GIS Feature", {"dataset": name})
			frappe.delete_doc("GIS Dataset", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def run_alg(self, algorithm_id, parameters):
		job = proc.run_algorithm(algorithm_id, parameters)  # runs inline under tests
		return proc.get_processing_job(job["name"])

	def test_registry(self):
		algorithms = {a["id"]: a for a in proc.list_algorithms()["algorithms"]}
		self.assertGreater(len(algorithms), 500)
		self.assertTrue(algorithms["native:buffer"]["supported"])
		self.assertTrue(any(a["provider"] == "gdal" for a in algorithms.values()))
		buffer = proc.get_algorithm("native:buffer")
		self.assertEqual([p["name"] for p in buffer["parameters"]][:2], ["INPUT", "DISTANCE"])

	def test_buffer_makes_a_new_dataset_and_leaves_the_input_alone(self):
		job = self.run_alg(
			"native:buffer",
			{"INPUT": {"dataset": POINTS}, "DISTANCE": 10, "SEGMENTS": 16, "OUTPUT": {"dataset_name": "test_proc_buffers", "title": "Buffers"}},
		)
		self.assertEqual(job["status"], "Completed", job["log"])
		[output] = job["outputs"]
		self.assertEqual(output["dataset"], "test_proc_buffers")
		dataset = frappe.get_doc("GIS Dataset", "test_proc_buffers")
		self.assertIn("Polygon", dataset.geometry_type)
		self.assertEqual((dataset.crs, dataset.source_type, dataset.source_reference), ("EPSG:2053", "Processing", job["name"]))
		self.assertEqual(dataset.feature_count, 3)
		rows = frappe.get_all("GIS Feature", filters={"dataset": "test_proc_buffers"}, fields=["geometry", "attributes"], order_by="name")
		self.assertEqual(sorted(json.loads(r.attributes)["hole"] for r in rows), ["BH1", "BH2", "BH3"])
		from shapely.geometry import shape

		area = shape(json.loads(rows[0].geometry)).area
		self.assertAlmostEqual(area, math.pi * 100, delta=5)  # a 10 m circle, 16 segments per quarter
		self.assertEqual(frappe.db.count("GIS Feature", {"dataset": POINTS}), 3)  # input unchanged

	def test_selected_features_only(self):
		job = self.run_alg(
			"native:buffer",
			{"INPUT": {"dataset": POINTS, "selected_ids": [self.ids[1]]}, "DISTANCE": 5, "OUTPUT": {"dataset_name": "test_proc_one"}},
		)
		self.assertEqual(job["status"], "Completed", job["log"])
		self.assertEqual(frappe.db.get_value("GIS Dataset", "test_proc_one", "feature_count"), 1)
		self.assertIn("(selected)", job["log"])

	def test_statistics_give_values_and_a_report_file(self):
		job = self.run_alg(
			"native:basicstatisticsforfields",
			{"INPUT_LAYER": {"dataset": POINTS}, "FIELD_NAME": "depth", "OUTPUT_HTML_FILE": {"name": "depth_stats"}},
		)
		self.assertEqual(job["status"], "Completed", job["log"])
		self.assertEqual(job["results"]["COUNT"], 3)
		self.assertAlmostEqual(job["results"]["MEAN"], 40.0)
		[report] = [o for o in job["outputs"] if o["file"]]
		self.assertTrue(report["file"].endswith(".html"))

	def test_never_overwrites_and_checks_definitions(self):
		with self.assertRaises(frappe.ValidationError):  # an existing dataset as output
			proc.run_algorithm("native:buffer", {"INPUT": {"dataset": POINTS}, "DISTANCE": 1, "OUTPUT": {"dataset_name": POINTS}})
		with self.assertRaises(frappe.ValidationError):  # below the minimum of SEGMENTS (1)
			proc.run_algorithm("native:buffer", {"INPUT": {"dataset": POINTS}, "DISTANCE": 1, "SEGMENTS": 0, "OUTPUT": {"dataset_name": "test_proc_x"}})
		blocked = next(a for a in proc.list_algorithms()["algorithms"] if not a["supported"])
		with self.assertRaises(frappe.ValidationError):
			proc.run_algorithm(blocked["id"], {})

	def test_cancel_before_start(self):
		job = frappe.get_doc(
			{"doctype": proc.JOB, "algorithm": "native:buffer", "algorithm_name": "Buffer", "status": "Queued", "parameters": json.dumps({"INPUT": {"dataset": POINTS}})}
		).insert()
		self.assertEqual(proc.cancel_processing(job.name)["status"], "Cancelled")
		proc.execute(job.name)  # a worker picking it up later does nothing
		self.assertEqual(frappe.db.get_value(proc.JOB, job.name, "status"), "Cancelled")

	def test_guest_cannot_run(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				proc.run_algorithm("native:buffer", {})
		finally:
			frappe.set_user("Administrator")
