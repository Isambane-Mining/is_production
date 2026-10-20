# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt
"""Import and export through GDAL / QGIS (the real worker on this host).

Import and export jobs commit as they go (per chunk, per layer), so this test cleans up after itself:
every dataset, project, job and file it makes starts with ``test_io`` / is attached to its jobs.
"""

import json
import os
import shutil
import zipfile

import frappe
from frappe.tests import IntegrationTestCase
from pyproj import Transformer

from is_production.geo_planning.services import gis_io_service as io

IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]

PITS = {
	"type": "FeatureCollection",
	"crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::2053"}},
	"features": [
		{
			"type": "Feature",
			"properties": {"Pit Name": "North", "Depth (m)": 42.5, "Benches": 4, "Surveyed": "2026-09-30"},
			"geometry": {"type": "Polygon", "coordinates": [[[-19000, 2860000], [-18000, 2860000], [-18000, 2861000], [-19000, 2860000]]]},
		},
		{
			"type": "Feature",
			"properties": {"Pit Name": "South", "Depth (m)": 18.0, "Benches": 2, "Surveyed": None},
			"geometry": {
				"type": "MultiPolygon",
				"coordinates": [
					[[[-17000, 2862000], [-16500, 2862000], [-16500, 2862500], [-17000, 2862000]]],
					[[[-16000, 2862000], [-15500, 2862000], [-15500, 2862500], [-16000, 2862000]]],
				],
			},
		},
	],
}
# South African survey CSV: Y is the westing (x), X the southing (y); no CRS in the file
COLLARS_CSV = "Hole,Y,X,Z,Depth\nBH1,-19500.25,2860500.75,1452.5,40\nBH2,-19400,2860600,1450,35.5\n"


def make_file(name, content):
	return frappe.get_doc(
		{"doctype": "File", "file_name": name, "is_private": 1, "content": content}
	).insert(ignore_permissions=True)


class IntegrationTestGISImportExport(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.get_single_value("GIS Settings", "qgis_python"):
			raise cls.skipTest(cls, "GIS Settings has no PyQGIS Python")
		cls.cleanup()

	@classmethod
	def tearDownClass(cls):
		cls.cleanup()
		super().tearDownClass()

	@classmethod
	def cleanup(cls):
		jobs = frappe.get_all(io.JOB, filters={"log": ["like", "%test_io%"]}, pluck="name")
		jobs += frappe.get_all(io.JOB, filters={"source_file": ["like", "%test_io%"]}, pluck="name")
		for name in frappe.get_all("GIS Dataset", filters={"name": ["like", "test_io%"]}, pluck="name"):
			jobs += frappe.get_all("GIS Import Export Layer", filters={"dataset": name}, pluck="parent")
			frappe.db.delete("GIS Feature", {"dataset": name})
			frappe.db.delete("GIS Project Layer", {"dataset": name})
			frappe.delete_doc("GIS Dataset", name, force=True, ignore_permissions=True)
		for name in frappe.get_all("GIS Project", filters={"title": ["like", "test_io%"]}, pluck="name"):
			frappe.delete_doc("GIS Project", name, force=True, ignore_permissions=True)
		for name in set(jobs):
			for f in frappe.get_all("File", filters={"attached_to_doctype": io.JOB, "attached_to_name": name}, pluck="name"):
				frappe.delete_doc("File", f, force=True, ignore_permissions=True)
			frappe.delete_doc(io.JOB, name, force=True, ignore_permissions=True)
		for f in frappe.get_all("File", filters={"file_name": ["like", "test_io%"]}, pluck="name"):
			frappe.delete_doc("File", f, force=True, ignore_permissions=True)
		shutil.rmtree(frappe.get_site_path("private", "gis_io", "test_io_pkg"), ignore_errors=True)
		frappe.db.commit()

	def import_file(self, file_name, content, **mapping):
		f = make_file(file_name, content)
		job = io.start_import(f.file_url)
		self.assertEqual(job["status"], "Draft", job["log"])
		layers = job["layers"]
		for layer in layers:
			layer.update(mapping)
		return job, layers

	def features(self, dataset):
		return [
			{"geometry": json.loads(r.geometry), "attributes": json.loads(r.attributes), "source_fid": r.source_fid}
			for r in frappe.get_all("GIS Feature", filters={"dataset": dataset}, fields=["geometry", "attributes", "source_fid"], order_by="name")
		]

	def output_path(self, job):
		return frappe.get_doc("File", {"file_url": job.output_file}).get_full_path()

	# ------------------------------------------------------------------ import

	def test_geojson_import_maps_types_and_fields(self):
		job, layers = self.import_file("test_io_pits.geojson", json.dumps(PITS))
		[layer] = layers
		self.assertEqual(layer["new_geometry_type"], "MultiPolygon")  # one of each found: the Multi type holds both
		self.assertEqual(layer["source_crs"], "EPSG:2053")
		mapping = {m["source"]: (m["field_name"], m["data_type"]) for m in layer["field_mapping"]}
		self.assertEqual(mapping["Pit Name"], ("pit_name", "Text"))
		self.assertEqual(mapping["Depth (m)"], ("depth_m", "Float"))
		self.assertEqual(mapping["Benches"], ("benches", "Integer"))

		io.run_import(job["name"], layers)
		result = io.get_job(job["name"])
		self.assertEqual(result["status"], "Completed", result["log"])
		dataset = frappe.get_doc("GIS Dataset", "test_io_pits")
		self.assertEqual([a.source_field_name for a in dataset.attributes], ["Pit Name", "Depth (m)", "Benches", "Surveyed"])
		features = self.features("test_io_pits")
		self.assertEqual(len(features), 2)
		self.assertEqual(features[0]["attributes"]["depth_m"], 42.5)
		self.assertEqual(features[0]["geometry"]["type"], "MultiPolygon")  # single part promoted
		self.assertEqual(features[0]["geometry"]["coordinates"][0][0][1], [-18000, 2860000])

	def test_csv_with_lo_columns_and_assigned_crs(self):
		job, layers = self.import_file(
			"test_io_collars.csv", COLLARS_CSV, csv_x="Y", csv_y="X", csv_z="Z", dataset_crs="EPSG:2053", new_geometry_type="Point"
		)
		self.assertEqual(layers[0]["source_crs"], "")  # a CSV has no CRS
		layers[0]["has_z"] = 1
		job_doc = frappe.get_doc(io.JOB, job["name"])
		job_doc.layers[0].has_z = 1
		job_doc.save()
		io.run_import(job["name"], layers)
		result = io.get_job(job["name"])
		self.assertIn(result["status"], ("Completed", "Completed with Errors"), result["log"])
		self.assertIn("taken to be EPSG:2053", result["log"])
		first = self.features(result["layers"][0]["dataset"])[0]
		self.assertEqual(first["geometry"]["coordinates"], [-19500.25, 2860500.75, 1452.5])
		self.assertEqual(first["attributes"]["hole"], "BH1")

	def test_reprojection_is_explicit(self):
		wgs84 = json.loads(json.dumps(PITS))
		wgs84["crs"]["properties"]["name"] = "urn:ogc:def:crs:OGC:1.3:CRS84"
		for f in wgs84["features"]:
			f["geometry"] = {"type": "Point", "coordinates": [29.2, -25.86]}
		job, layers = self.import_file("test_io_wgs.geojson", json.dumps(wgs84), dataset_crs="EPSG:2053", new_dataset_name="test_io_wgs")
		with self.assertRaises(frappe.ValidationError):
			io.run_import(job["name"], layers)
		layers[0]["reproject"] = 1
		io.run_import(job["name"], layers)
		x, y = self.features("test_io_wgs")[0]["geometry"]["coordinates"]
		# GDAL (import) must agree with pyproj (the map and editing): 29.2 E is east of Lo29's
		# central meridian, so the westing is negative
		ex, ey = Transformer.from_crs("EPSG:4326", "EPSG:2053", always_xy=True).transform(29.2, -25.86)
		self.assertAlmostEqual(x, ex, places=4)
		self.assertAlmostEqual(y, ey, places=4)
		self.assertLess(x, 0)
		self.assertEqual(frappe.db.get_value("GIS Dataset", "test_io_wgs", "source_crs"), "EPSG:4326")

	# ------------------------------------------------------------------ export

	def imported_pits(self):
		if not frappe.db.exists("GIS Dataset", "test_io_pits"):
			job, layers = self.import_file("test_io_pits.geojson", json.dumps(PITS))
			io.run_import(job["name"], layers)
		return "test_io_pits"

	def test_geopackage_export_opens_with_crs_fields_and_features(self):
		dataset = self.imported_pits()
		job = frappe.get_doc(io.JOB, io.start_export("GeoPackage", datasets=[dataset])["name"])
		self.assertEqual(job.status, "Completed", job.log)
		self.assertTrue(job.output_file.endswith(".gpkg"))
		[layer] = io.worker({"command": "inspect", "path": self.output_path(job)})["layers"]
		self.assertEqual(layer["crs"]["epsg"], "EPSG:2053")
		self.assertEqual(layer["feature_count"], 2)
		names = [f["name"] for f in layer["fields"]]
		self.assertEqual(names, ["gis_feature_id", "label", "Pit Name", "Depth (m)", "Benches", "Surveyed"])

	def test_selected_features_to_shapefile_in_wgs84(self):
		dataset = self.imported_pits()
		first = frappe.get_all("GIS Feature", filters={"dataset": dataset}, pluck="name", order_by="name")[0]
		job = frappe.get_doc(io.JOB, io.start_export("Shapefile", datasets=[dataset], target_crs="EPSG:4326", feature_ids=[first])["name"])
		self.assertIn(job.status, ("Completed", "Completed with Errors"), job.log)
		self.assertTrue(job.output_file.endswith(".zip"))
		with zipfile.ZipFile(self.output_path(job)) as z:
			self.assertTrue({"test_io_pits.shp", "test_io_pits.dbf", "test_io_pits.prj"} <= set(z.namelist()))
		self.assertIn("shortened", job.log)  # "Depth (m)" etc. do not fit 10-character dBASE names
		[layer] = io.worker({"command": "inspect", "path": self.output_path(job)})["layers"]
		self.assertEqual(layer["feature_count"], 1)
		self.assertEqual(layer["crs"]["epsg"], "EPSG:4326")

	def test_qgis_project_round_trip(self):
		dataset = self.imported_pits()
		project = frappe.get_doc(
			{
				"doctype": "GIS Project",
				"title": "test_io round trip",
				"display_crs": "EPSG:2053",
				"layers": [{"dataset": dataset, "visible": 0, "opacity": 0.6, "color": "#d0021b"}],
			}
		).insert()
		job = frappe.get_doc(io.JOB, io.start_export("QGIS Project", project=project.name)["name"])
		self.assertEqual(job.status, "Completed", job.log)
		with zipfile.ZipFile(self.output_path(job)) as z:
			names = set(z.namelist())
			self.assertEqual(names, {"test_io_round_trip.gpkg", "test_io_round_trip.qgz"})
			package = z.read("test_io_round_trip.gpkg"), z.read("test_io_round_trip.qgz")

		# import the package back (as someone would after editing it in QGIS Desktop)
		buffer = os.path.join(io.work_dir("test_io_pkg"), "test_io_package.zip")
		with zipfile.ZipFile(buffer, "w") as z:
			z.writestr("test_io_round_trip.gpkg", package[0])
			z.writestr("test_io_round_trip.qgz", package[1])
		with open(buffer, "rb") as f:
			back, layers = self.import_file("test_io_package.zip", f.read())
		self.assertTrue(frappe.db.get_value(io.JOB, back["name"], "is_project"))
		layers[0]["new_dataset_name"] = "test_io_pits_back"
		io.run_import(back["name"], layers)
		result = io.get_job(back["name"])
		self.assertEqual(result["status"], "Completed", result["log"])

		copy = frappe.get_doc("GIS Project", result["gis_project"])
		self.assertEqual(copy.title, "test_io round trip")
		self.assertEqual(copy.display_crs, "EPSG:2053")
		row = copy.layers[0]
		self.assertEqual((row.dataset, row.visible, round(row.opacity, 2), row.color), ("test_io_pits_back", 0, 0.6, "#d0021b"))
		self.assertEqual(frappe.db.get_value("GIS Dataset", "test_io_pits_back", "crs"), "EPSG:2053")
		original = self.features(dataset)
		returned = self.features("test_io_pits_back")
		self.assertEqual([f["geometry"] for f in returned], [f["geometry"] for f in original])
		strip = lambda a: {k: v for k, v in a.items()}  # noqa: E731
		self.assertEqual([strip(f["attributes"]).get("pit_name") for f in returned], ["North", "South"])

	def test_guest_cannot_export(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				io.start_export("GeoPackage", datasets=["smoke_pits"])
		finally:
			frappe.set_user("Administrator")
