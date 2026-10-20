# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase

from is_production.geo_planning.doctype.gis_dataset.test_gis_dataset import make_dataset
from is_production.geo_planning.services.gis_storage_service import (
	ensure_geometry_schema,
	features_in_bbox,
	get_geometry_schema_status,
)

# GIS Dataset links User; loading User test records pulls in ERPNext records that clash on this site.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]

SQUARE = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
BOWTIE = {"type": "Polygon", "coordinates": [[[0, 0], [10, 10], [10, 0], [0, 10], [0, 0]]]}


def make_feature(dataset, geometry, **kwargs):
	return frappe.get_doc({"doctype": "GIS Feature", "dataset": dataset, "geometry": geometry, **kwargs}).insert()


def native(feature_name):
	return frappe.db.sql(
		"SELECT ST_AsText(geom), ST_SRID(geom) FROM `tabGIS Feature` WHERE name = %s", feature_name
	)[0]


class IntegrationTestGISFeature(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.polygons = make_dataset(
			"test_gis_pits",
			geometry_type="MultiPolygon",
			attributes=[
				{"field_name": "pit", "data_type": "Text", "required": 1, "allowed_values": "North\nSouth"},
				{"field_name": "depth", "data_type": "Float"},
				{"field_name": "benches", "data_type": "Integer", "default_value": "3"},
				{"field_name": "active", "data_type": "Boolean"},
				{"field_name": "surveyed_on", "data_type": "Date"},
			],
		).name

	def test_schema_is_present_and_idempotent(self):
		ensure_geometry_schema()
		self.assertTrue(get_geometry_schema_status()["ok"])

	def test_polygon_is_stored_natively_and_promoted_to_multi(self):
		feature = make_feature(self.polygons, SQUARE, attributes={"pit": "North"})
		self.assertEqual(feature.geometry_type, "MultiPolygon")
		self.assertEqual(json.loads(feature.geometry)["type"], "MultiPolygon")
		self.assertEqual(feature.srid, 2053)
		self.assertEqual(feature.revision, 1)
		self.assertEqual(feature.validation_status, "Valid")
		self.assertEqual((feature.bbox_min_x, feature.bbox_max_y), (0, 10))
		self.assertFalse(feature.has_z)

		wkt, srid = native(feature.name)
		self.assertEqual(wkt, "MULTIPOLYGON(((0 0,10 0,10 10,0 10,0 0)))")
		self.assertEqual(srid, 2053)

	def test_wrong_geometry_type_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_feature(self.polygons, {"type": "Point", "coordinates": [1, 1]}, attributes={"pit": "North"})

	def test_attributes_are_coerced_and_checked(self):
		feature = make_feature(
			self.polygons,
			SQUARE,
			attributes={"pit": "South", "depth": "12.5", "active": "yes", "surveyed_on": "2026-10-01"},
		)
		self.assertEqual(
			json.loads(feature.attributes),
			{"pit": "South", "depth": 12.5, "benches": 3, "active": True, "surveyed_on": "2026-10-01"},
		)
		for bad in ({}, {"pit": "East"}, {"pit": "North", "nope": 1}, {"pit": "North", "benches": 2.5}):
			with self.assertRaises(frappe.ValidationError):
				make_feature(self.polygons, SQUARE, attributes=bad)

	def test_invalid_geometry_flagged_or_rejected(self):
		feature = make_feature(self.polygons, BOWTIE, attributes={"pit": "North"})
		self.assertEqual(feature.validation_status, "Invalid")
		self.assertIn("Self-intersection", feature.validation_message)

		frappe.db.set_value("GIS Dataset", self.polygons, "reject_invalid_geometry", 1)
		frappe.clear_document_cache("GIS Dataset", self.polygons)
		try:
			with self.assertRaises(frappe.ValidationError):
				make_feature(self.polygons, BOWTIE, attributes={"pit": "North"})
		finally:
			frappe.db.set_value("GIS Dataset", self.polygons, "reject_invalid_geometry", 0)
			frappe.clear_document_cache("GIS Dataset", self.polygons)

	def test_edit_bumps_revision_and_rewrites_native_geometry(self):
		feature = make_feature(self.polygons, SQUARE, attributes={"pit": "North"})
		feature.feature_label = "label only"
		feature.save()
		self.assertEqual(feature.revision, 1)

		feature.geometry = {"type": "Polygon", "coordinates": [[[5, 5], [6, 5], [6, 6], [5, 5]]]}
		feature.save()
		self.assertEqual(feature.revision, 2)
		self.assertEqual(native(feature.name)[0], "MULTIPOLYGON(((5 5,6 5,6 6,5 5)))")

		reloaded = frappe.get_doc("GIS Feature", feature.name)
		self.assertNotIn("geom", reloaded.__dict__)

	def test_z_rules(self):
		points3d = make_dataset("test_gis_collars", geometry_type="Point", has_z=1).name
		feature = make_feature(points3d, {"type": "Point", "coordinates": [1, 2, 1450.5]})
		self.assertEqual(json.loads(feature.geometry)["coordinates"], [1, 2, 1450.5])
		self.assertEqual(native(feature.name)[0], "POINT(1 2)")
		self.assertEqual((feature.min_z, feature.max_z), (1450.5, 1450.5))
		make_feature(points3d, {"type": "Point", "coordinates": [3, 4, 1390.25]})
		extent = frappe.db.get_value("GIS Dataset", points3d, ["extent_min_z", "extent_max_z"])
		self.assertEqual(extent, (1390.25, 1450.5))
		with self.assertRaises(frappe.ValidationError):
			make_feature(points3d, {"type": "Point", "coordinates": [1, 2]})

	def test_no_geometry_dataset(self):
		table = make_dataset("test_gis_table", geometry_type="No Geometry").name
		feature = make_feature(table, None)
		self.assertEqual(native(feature.name)[0], "GEOMETRYCOLLECTION EMPTY")
		with self.assertRaises(frappe.ValidationError):
			make_feature(table, {"type": "Point", "coordinates": [1, 2]})

	def test_spatial_query_and_dataset_statistics(self):
		dataset = make_dataset("test_gis_bbox", geometry_type="Point").name
		near = make_feature(dataset, {"type": "Point", "coordinates": [1, 1]})
		make_feature(dataset, {"type": "Point", "coordinates": [100, 100]})

		found = features_in_bbox(dataset, 0, 0, 5, 5)
		self.assertEqual([row.name for row in found], [near.name])
		self.assertEqual(found[0].geometry, {"type": "Point", "coordinates": [1.0, 1.0]})

		stats = frappe.db.get_value(
			"GIS Dataset", dataset, ["feature_count", "extent_min_x", "extent_max_y"], as_dict=True
		)
		self.assertEqual((stats.feature_count, stats.extent_min_x, stats.extent_max_y), (2, 1, 100))

		frappe.delete_doc("GIS Feature", near.name)
		self.assertEqual(frappe.db.get_value("GIS Dataset", dataset, "feature_count"), 1)

	def test_archived_dataset_is_read_only(self):
		dataset = make_dataset("test_gis_archived", geometry_type="Point").name
		feature = make_feature(dataset, {"type": "Point", "coordinates": [1, 1]})
		frappe.db.set_value("GIS Dataset", dataset, "status", "Archived")
		frappe.clear_document_cache("GIS Dataset", dataset)
		feature.feature_label = "x"
		with self.assertRaises(frappe.ValidationError):
			feature.save()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("GIS Feature", feature.name)
