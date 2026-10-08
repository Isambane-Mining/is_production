# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import base64
import copy
import gzip
import json

import frappe
from frappe.tests import IntegrationTestCase

from is_production.geo_planning.services import mining_simulation_service as svc

IGNORE_TEST_RECORD_DEPENDENCIES = ["Geo Project"]


def sample_project(pid):
	"""A small simulator project with the shapes the mapping has to keep."""
	return {
		"schema": 1,
		"id": pid,
		"name": "Test Pit",
		"description": "",
		"created": "2026-10-05T09:25:00.046Z",
		"modified": "2026-10-07T11:22:21.634Z",
		"startDate": "2027-01-01",
		"crs": {"preset": "seriti", "proj4": "", "axis": "EN"},
		"calendar": {"hoursPerDay": 18, "workDays": [1, 1, 1, 1, 1, 0, 0], "saHolidays": True, "maxYears": 30},
		"grid": {"x0": 16965, "y0": -2866717.5, "nx": 4, "ny": 3, "cell": 7.5},
		"source": {"folder": "Test", "surfaceFiles": ["Topo.tif"], "dxf": None},
		"surfaces": [{"id": "srf_a", "name": "TOPO", "file": "Topo.tif"}, {"id": "srf_b", "name": "FLOOR", "file": "F.tif"}],
		"layers": [
			{"id": "lay_a", "name": "Topsoil", "type": "coal", "top": "srf_a", "bottom": "srf_b", "density": 1.5,
				"swell": 1.35, "color": "#4d844d", "densityField": "", "qualityFields": [], "minThick": 0.3},
		],
		"blocks": [
			{"id": "1", "pts": [[0, 0], [10, 0], [10, 20], [0, 20]], "attrs": {"fid": 1, "S5 RDmean": 1.60477649248563}},
			{"id": "2", "pts": [[10, 0], [20, 0], [20, 20], [10, 20]], "attrs": {"fid": 2}},
		],
		"attrColumns": ["fid", "S5 RDmean"],
		"idColumn": "fid",
		"activities": [
			{"id": "act_a", "name": "Topsoil", "layerId": "lay_a", "method": "truck_shovel", "destMode": "dumps",
				"dests": [{"dump": "dmp_a", "from": "2027-01-01"}], "pushBearing": 270, "lookahead": 1,
				"startDate": "", "path": ["1", "2"], "color": "#57a876", "maxRate": None,
				"dep": {"on": "below", "lead": 3, "unit": "strip"}, "pitId": "pit_a",
				"futureOption": {"x": 1}},  # a key this version does not map
			{"id": "act_b", "name": "Coal", "layerId": "lay_a", "method": "new_method", "destMode": "void",
				"dests": [], "pushBearing": 90, "lookahead": 2, "startDate": "2027-02-01", "path": []},
		],
		"fleet": [
			{"id": "eq_a", "kind": "excavator", "activityId": "act_a", "name": "Ex 70t", "qty": 1, "bucket": 4,
				"fill": 0.9, "cycle": 90, "prodMode": "calc", "prod": 1200, "avail": 0.85, "util": 0.3},
			{"id": "eq_b", "kind": "truck", "activityId": "act_a", "name": "ADT", "qty": 6, "payload": 40,
				"body": 24, "spot": 0.7, "dump": 1, "vLoadFlat": 30, "vLoadRamp": 12, "vEmptyFlat": 42,
				"vEmptyRamp": 25, "avail": 0.85, "util": 0.8},
		],
		"roads": [],
		"dumps": [
			{"id": "dmp_a", "name": "Topsoil", "kind": "topsoil", "pts": [[0, 0], [1, 0], [1, 1]], "maxHeight": 12,
				"crestElev": None, "liftHeight": 2, "faceAngle": 37, "entry": None, "color": "#c9a86a"},
		],
		"overlays": [],
		"imagery": {"provider": "esri", "googleKey": "", "customUrl": "", "fileName": "", "opacity": 1},
		"view": {"vex": 1},
		"gridVersion": 2,
		"isDemo": False,
		"limits": [],
		"cutoff": {"by": "cell", "stop": True},
		"targets": {"coal": 120000, "waste": None, "wasteUnit": "bcm", "basis": "working", "mode": "limit"},
		"attrReport": {"S5_ADC Tons": {"mode": "adc", "layer": "lay_a"}},
		"voidTip": {"on": True, "ref": "top", "afterStrips": 2, "roads": True, "dozeAfterStrips": 1},
		"pits": [{"id": "pit_a", "name": "Main Pit", "color": "#ff5fa2", "pts": [[0, 0], [30, 0], [30, 30]]}],
		"newTopLevel": {"kept": True},
	}


class IntegrationTestMiningSimulationProject(IntegrationTestCase):
	def setUp(self):
		self.pid = "prj_test_" + frappe.generate_hash(length=8).lower()

	def tearDown(self):
		frappe.db.rollback()

	def test_round_trip_is_lossless(self):
		project = sample_project(self.pid)
		svc.save_from_project(copy.deepcopy(project), name=self.pid, insert=True)
		doc = frappe.get_doc(svc.DOCTYPE, self.pid)

		# the parts are real fields and rows
		self.assertEqual(doc.project_name, "Test Pit")
		self.assertEqual(doc.hours_per_day, 18)
		self.assertEqual((doc.work_friday, doc.work_saturday), (1, 0))
		self.assertEqual(doc.target_coal, 120000)
		self.assertEqual(len(doc.blocks), 2)
		self.assertEqual(doc.blocks[0].block_area, 200)
		self.assertEqual((doc.blocks[0].centroid_x, doc.blocks[0].centroid_y), (5, 10))
		self.assertEqual(doc.activities[0].depends_on, "below")
		self.assertEqual(doc.fleet[1].speed_empty_flat, 42)
		self.assertEqual(doc.block_count, 2)
		self.assertEqual(doc.equipment_count, 2)
		# a Select value this version does not offer is kept, not rejected
		self.assertFalse(doc.activities[1].mining_method)

		self.assertEqual(svc.build_project(doc), project)

	def test_saving_again_keeps_rows(self):
		project = sample_project(self.pid)
		svc.save_from_project(copy.deepcopy(project), name=self.pid, insert=True)
		before = {r.block_id: r.name for r in frappe.get_doc(svc.DOCTYPE, self.pid).blocks}

		project["blocks"][1]["attrs"]["fid"] = 22
		project["fleet"].pop()
		svc.save_project(json.dumps(project))
		doc = frappe.get_doc(svc.DOCTYPE, self.pid)

		self.assertEqual({r.block_id: r.name for r in doc.blocks}, before)
		self.assertEqual(json.loads(doc.blocks[1].attributes)["fid"], 22)
		self.assertEqual(len(doc.fleet), 1)
		self.assertEqual(svc.build_project(doc), project)

	def test_desk_edits_and_desk_rows(self):
		svc.save_from_project(sample_project(self.pid), name=self.pid, insert=True)
		doc = frappe.get_doc(svc.DOCTYPE, self.pid)
		doc.dumps[0].crest_elevation = 1550
		doc.append("pits", {"pit_name": "North Pit", "points": "[[0,0],[5,0],[5,5]]"})
		doc.save()

		project = svc.build_project(frappe.get_doc(svc.DOCTYPE, self.pid))
		self.assertEqual(project["dumps"][0]["crestElev"], 1550)
		north = project["pits"][1]
		self.assertTrue(north["id"].startswith("pit_"))
		self.assertEqual(north["name"], "North Pit")
		self.assertEqual(north["pts"], [[0, 0], [5, 0], [5, 5]])

	def test_bundle_import_and_blobs(self):
		grid = bytes(range(256)) * 10
		bundle = {
			"format": svc.BUNDLE_FORMAT,
			"version": 1,
			"project": sample_project(self.pid),
			"blobs": {"grid": base64.b64encode(gzip.compress(grid)).decode()},
		}
		project, blobs = svc.read_bundle(json.dumps(bundle))
		svc.save_from_project(project, blobs, name=self.pid, insert=True)
		doc = frappe.get_doc(svc.DOCTYPE, self.pid)

		self.assertTrue(doc.grid_file)
		self.assertEqual(gzip.decompress(base64.b64decode(svc.get_blob(self.pid, "grid"))), grid)
		self.assertEqual(svc.bundle_for(doc)["project"], sample_project(self.pid))

		svc.delete_blob(self.pid, "grid")
		self.assertIsNone(svc.get_blob(self.pid, "grid"))

	def test_importing_a_file_again_makes_a_named_copy(self):
		bundle = json.dumps({"format": svc.BUNDLE_FORMAT, "version": 1, "project": sample_project(self.pid), "blobs": {}})
		upload = lambda: frappe.get_doc(
			{"doctype": "File", "file_name": "test.rollover.json", "is_private": 1, "content": bundle}
		).insert(ignore_permissions=True).file_url

		first = svc.import_simulation_file(upload())
		self.assertEqual(first, self.pid)
		self.assertEqual(svc.check_import_file(upload())["existing"], self.pid)
		copy_name = svc.import_simulation_file(upload(), mode="new")
		self.assertNotEqual(copy_name, self.pid)
		self.assertEqual(frappe.db.get_value(svc.DOCTYPE, copy_name, "project_name"), "Test Pit (imported)")

	def test_rejects_other_files(self):
		with self.assertRaises(frappe.ValidationError):
			svc.read_bundle(json.dumps({"format": "something-else", "project": {}}))
		with self.assertRaises(frappe.ValidationError):
			svc.read_bundle("not json")
