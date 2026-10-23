# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import json

import frappe
from frappe.tests import IntegrationTestCase
from pyproj import Transformer

from is_production.geo_planning.doctype.gis_dataset.test_gis_dataset import make_dataset
from is_production.geo_planning.services import gis_edit_service as edit

IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]

TO_WEB = Transformer.from_crs("EPSG:2053", "EPSG:3857", always_xy=True)


def web(*points):
	"""Lo29 (westing, southing) points -> EPSG:3857 coordinates, as the browser would send them."""
	return [list(TO_WEB.transform(x, y)) for x, y in points]


def web_polygon(*points):
	return {"type": "Polygon", "coordinates": [web(*points)]}


def stored(name):
	return json.loads(frappe.db.get_value("GIS Feature", name, "geometry"))


class IntegrationTestGISEdit(IntegrationTestCase):
	def setUp(self):
		self.ds = make_dataset(
			"test_gis_edit",
			geometry_type="Polygon",
			attributes=[{"field_name": "block", "data_type": "Text", "required": 1}],
		).name

	def feature(self, points, block="A"):
		return frappe.get_doc(
			{
				"doctype": "GIS Feature",
				"dataset": self.ds,
				"geometry": {"type": "Polygon", "coordinates": [points]},
				"attributes": {"block": block},
			}
		).insert()

	def test_add_update_delete_in_one_save(self):
		keep = self.feature([[0, 0], [100, 0], [100, 100], [0, 0]])
		gone = self.feature([[200, 0], [300, 0], [300, 100], [200, 0]])
		result = edit.save_edits(
			self.ds,
			{
				"adds": [{"ref": "-1", "geometry": web_polygon((0, 500), (50, 500), (50, 550), (0, 500)), "attributes": {"block": "N"}}],
				"updates": [{"id": keep.name, "revision": 1, "attributes": {"block": "B"}, "label": "Renamed"}],
				"deletes": [{"id": gone.name, "revision": 1}],
			},
		)
		self.assertTrue(result["ok"], result)
		new = result["created"]["-1"]
		ring = stored(new)["coordinates"][0]
		self.assertAlmostEqual(ring[1][0], 50, places=6)  # back in Lo29 westing/southing
		self.assertAlmostEqual(ring[1][1], 500, places=6)
		self.assertEqual(frappe.db.get_value("GIS Feature", keep.name, ["feature_label", "revision"]), ("Renamed", 2))
		self.assertFalse(frappe.db.exists("GIS Feature", gone.name))
		self.assertEqual(frappe.db.get_value("GIS Dataset", self.ds, "feature_count"), 2)

	def test_any_failure_rolls_back_everything(self):
		keep = self.feature([[0, 0], [100, 0], [100, 100], [0, 0]])
		result = edit.save_edits(
			self.ds,
			{
				"updates": [{"id": keep.name, "revision": 1, "attributes": {"block": "B"}}],
				"adds": [{"ref": "-7", "geometry": web_polygon((0, 0), (1, 0), (1, 1), (0, 0)), "attributes": {}}],
			},
		)
		self.assertFalse(result["ok"])
		self.assertEqual([e["ref"] for e in result["errors"]], ["-7"])  # block is required
		self.assertEqual(json.loads(frappe.db.get_value("GIS Feature", keep.name, "attributes")), {"block": "A"})

	def test_stale_revision_is_a_conflict(self):
		feature = self.feature([[0, 0], [100, 0], [100, 100], [0, 0]])
		feature.attributes = {"block": "C"}
		feature.save()  # someone else: revision 2
		result = edit.save_edits(self.ds, {"updates": [{"id": feature.name, "revision": 1, "label": "mine"}]})
		self.assertFalse(result["ok"])
		self.assertTrue(result["errors"][0]["conflict"])
		result = edit.save_edits(self.ds, {"deletes": [{"id": 999999999, "revision": 1}]})
		self.assertTrue(result["errors"][0]["conflict"])

	def test_wrong_type_is_refused_by_the_server(self):
		result = edit.save_edits(
			self.ds, {"adds": [{"ref": "-1", "geometry": {"type": "Point", "coordinates": web((1, 1))[0]}, "attributes": {"block": "A"}}]}
		)
		self.assertFalse(result["ok"])
		self.assertIn("Polygon", result["errors"][0]["message"])

	def test_z_is_kept_interpolated_or_required(self):
		lines = make_dataset("test_gis_edit_z", geometry_type="LineString", has_z=1).name
		line = frappe.get_doc(
			{"doctype": "GIS Feature", "dataset": lines, "geometry": {"type": "LineString", "coordinates": [[0, 0, 100], [100, 0, 200]]}}
		).insert()
		# a vertex inserted half way: kept ends keep their Z, the new one is interpolated
		result = edit.save_edits(
			lines, {"updates": [{"id": line.name, "revision": 1, "geometry": {"type": "LineString", "coordinates": web((0, 0), (50, 0), (100, 0))}}]}
		)
		self.assertTrue(result["ok"], result)
		zs = [round(c[2], 6) for c in stored(line.name)["coordinates"]]
		self.assertEqual(zs, [100, 150, 200])

		# a new line with no Z to match needs the editor's Z
		new_line = {"ref": "-1", "geometry": {"type": "LineString", "coordinates": web((500, 0), (600, 0))}}
		self.assertFalse(edit.save_edits(lines, {"adds": [new_line]})["ok"])
		result = edit.save_edits(lines, {"adds": [new_line]}, default_z=1440)
		self.assertEqual([c[2] for c in stored(result["created"]["-1"])["coordinates"]], [1440, 1440])

	def test_moved_point_keeps_its_elevation(self):
		points = make_dataset("test_gis_edit_pz", geometry_type="Point", has_z=1).name
		point = frappe.get_doc(
			{"doctype": "GIS Feature", "dataset": points, "geometry": {"type": "Point", "coordinates": [10, 10, 1452.5]}}
		).insert()
		result = edit.save_edits(
			points, {"updates": [{"id": point.name, "revision": 1, "geometry": {"type": "Point", "coordinates": web((15, 12))[0]}}]}
		)
		self.assertTrue(result["ok"], result)
		x, y, z = stored(point.name)["coordinates"]
		self.assertEqual((round(x, 6), round(y, 6), z), (15, 12, 1452.5))

	def test_split_and_merge(self):
		square = web_polygon((0, 0), (100, 0), (100, 100), (0, 100), (0, 0))
		blade = {"type": "LineString", "coordinates": web((50, -10), (50, 110))}
		parts = edit.geometry_operation(self.ds, "split", [square], blade)["results"][0]
		self.assertEqual(len(parts), 2)
		merged = edit.geometry_operation(self.ds, "merge", parts)["geometry"]
		self.assertEqual(merged["type"], "Polygon")

		apart = web_polygon((500, 500), (510, 500), (510, 510), (500, 500))
		with self.assertRaises(frappe.ValidationError):  # disjoint: a MultiPolygon, not allowed here
			edit.geometry_operation(self.ds, "merge", [square, apart])

	def test_validate_reports_invalid_without_saving(self):
		bowtie = web_polygon((0, 0), (100, 100), (100, 0), (0, 100), (0, 0))
		[result] = edit.validate_geometries(self.ds, [{"ref": "x", "geometry": bowtie}])
		self.assertFalse(result["valid"])
		self.assertIn("Self-intersection", result["message"])
		self.assertEqual(frappe.db.count("GIS Feature", {"dataset": self.ds}), 0)

	def test_guest_cannot_edit(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				edit.save_edits(self.ds, {"adds": []})
			with self.assertRaises(frappe.PermissionError):
				edit.get_edit_schema(self.ds)
		finally:
			frappe.set_user("Administrator")
