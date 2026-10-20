# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

# GIS Dataset links User; loading User test records pulls in ERPNext records that clash on this site.
IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]


def make_dataset(dataset_name, geometry_type="Polygon", crs="EPSG:2053", attributes=None, **kwargs):
	if frappe.db.exists("GIS Dataset", dataset_name):
		frappe.db.delete("GIS Feature", {"dataset": dataset_name})
		frappe.delete_doc("GIS Dataset", dataset_name, force=True)
	return frappe.get_doc(
		{
			"doctype": "GIS Dataset",
			"dataset_name": dataset_name,
			"title": dataset_name.replace("_", " ").title(),
			"geometry_type": geometry_type,
			"crs": crs,
			"attributes": attributes or [],
			**kwargs,
		}
	).insert()


class IntegrationTestGISDataset(IntegrationTestCase):
	def test_crs_is_normalised_to_epsg(self):
		dataset = make_dataset("test_gis_crs", crs="epsg:2053")
		self.assertEqual(dataset.crs, "EPSG:2053")
		self.assertEqual(dataset.srid, 2053)
		self.assertEqual(dataset.qgis_layer_name, "test_gis_crs")

	def test_unknown_crs_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			make_dataset("test_gis_bad_crs", crs="EPSG:999999")

	def test_dataset_name_pattern(self):
		with self.assertRaises(frappe.ValidationError):
			make_dataset("Bad Name")

	def test_attribute_schema_rules(self):
		for bad in (
			[{"field_name": "dataset", "data_type": "Text"}],  # reserved
			[{"field_name": "a", "data_type": "Text"}, {"field_name": "a", "data_type": "Integer"}],
			[{"field_name": "depth", "data_type": "Integer", "default_value": "deep"}],
			[{"field_name": "Depth", "data_type": "Integer"}],
		):
			with self.assertRaises(frappe.ValidationError):
				make_dataset("test_gis_schema", attributes=bad)

	def test_locked_changes_once_features_exist(self):
		dataset = make_dataset(
			"test_gis_locked", geometry_type="Point", attributes=[{"field_name": "hole", "data_type": "Text"}]
		)
		frappe.get_doc(
			{
				"doctype": "GIS Feature",
				"dataset": dataset.name,
				"geometry": {"type": "Point", "coordinates": [1, 2]},
			}
		).insert()

		dataset.reload()
		dataset.crs = "EPSG:4326"
		with self.assertRaises(frappe.ValidationError):
			dataset.save()

		dataset.reload()
		dataset.attributes = []
		with self.assertRaises(frappe.ValidationError):
			dataset.save()

		dataset.reload()
		dataset.append("attributes", {"field_name": "depth", "data_type": "Float"})
		dataset.save()  # adding a field is allowed
