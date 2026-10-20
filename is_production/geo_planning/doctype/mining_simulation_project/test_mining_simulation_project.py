# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import base64
import copy
import gzip
import json

import frappe
from frappe.tests import IntegrationTestCase

from is_production.geo_planning.services import mining_simulation_service as svc

IGNORE_TEST_RECORD_DEPENDENCIES = ["Geo Project", "Asset Category", "Item"]


def isamiq_v3_parts():
	"""The parts an ISAMIQ v3 project adds (teams replace the fleet, rehab, reserve, ...)."""
	return {
		"teams": {"count": 7, "perTeam": 95000},
		"losses": {"geo": 5, "mining": 5, "contam": 2},
		"topsoilAsWaste": False,
		"startBy": {"coal": "2027-01-20"},
		"bench": {"on": False, "lead": 3},
		"reserve": {"pts": [[0, 0], [50, 0], [50, 50]], "on": True, "name": "Reserve boundary"},
		"rehab": {"on": True, "backfillTo": "topo", "above": 0.5, "topsoil": 1.185, "radius": 100, "pathFall": 0.5,
			"bank": 0.5, "cutDepth": 0.5, "soilDirect": True, "finalVoid": True, "finalRate": None, "lift": False,
			"liftMax": 2, "liftTie": 1, "liftBank": 25},
		"margin": {"cv": {"lay_a": "S5 CV"}, "wasteCost": 49.08, "price": 245, "basis": "ar"},
		"batters": [{"id": "bat_a", "name": "Box cut batter", "pts": [[0, 0], [10, 10]], "angle": 70, "surfId": "srf_b", "side": "left"}],
		"oldDumps": [{"id": "old_a", "name": "OVB Dump", "pts": [[0, 0], [5, 0], [5, 5]], "elev": None, "start": "2026-12-01", "rate": 20000, "dest": "dmp_a"}],
		"rehandles": [{"id": "rh_a", "name": "Softs to the voids", "from": "dmp_a", "to": "voids", "when": "end", "start": "", "rate": 25000, "qty": None, "color": "#baa17d"}],
		"fills": [{"id": "fil_a", "name": "Old Pit", "pts": [[0, 0], [5, 0], [5, 5]], "start": "2027-01-01"}],
		"raises": [{"id": "rse_a", "name": "Raised area 1", "pts": [[0, 0], [5, 0], [5, 5]], "height": 2, "slope": 10}],
		"drains": [],
		"qrasters": [{"id": "qr_a", "file": "S2P TS.tif", "layerId": "lay_a", "kind": "ts", "label": "S2P TS", "res": 10,
			"guessed": False, "seamGuessed": False, "read": "2026-10-16", "col": "Seam 2P TS (raster)",
			"prev": {"hadMargin": False, "margin": None, "quality": []}, "stats": {"n": 108}, "cmp": None, "sig": "16pnhsa.108"}],
	}


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
		self.assertEqual((doc.hours_friday, doc.hours_saturday), (18, 0))
		self.assertEqual(doc.target_coal, 120000)
		self.assertEqual(len(doc.blocks), 2)
		self.assertEqual(doc.blocks[0].block_area, 200)
		self.assertEqual((doc.blocks[0].centroid_x, doc.blocks[0].centroid_y), (5, 10))
		self.assertEqual(doc.activities[0].depends_on, "below")
		self.assertEqual(doc.fleet[1].speed_empty_flat, 42)
		self.assertEqual(doc.block_count, 2)
		self.assertEqual(doc.equipment_count, 0)  # units of linked Items; older fleet rows have none
		# a Select value this version does not offer is kept, not rejected
		self.assertFalse(doc.activities[1].mining_method)

		self.assertEqual(svc.build_project(doc), project)

	def test_isamiq_v3_project_in_fields(self):
		project = sample_project(self.pid)
		project.update(isamiq_v3_parts())
		project["fleet"] = []  # teams replace it
		project["targets"].update({"coalBasis": "ar", "rampMonths": 6, "exposedMonths": 0.5})
		project["pits"][0].update({"startDate": "2027-01-01", "boxcut": {"pts": [[0, 0], [9, 0], [9, 9]], "months": 12}, "priority": True})
		project["layers"][0]["losses"] = {"geo": 3}
		svc.save_from_project(copy.deepcopy(project), name=self.pid, insert=True)
		doc = frappe.get_doc(svc.DOCTYPE, self.pid)

		self.assertEqual((doc.team_count, doc.volume_per_team), (7, 95000))
		self.assertEqual((doc.geological_loss, doc.contamination), (5, 2))
		self.assertEqual(str(doc.start_coal_by), "2027-01-20")
		self.assertEqual((doc.rehab_on, doc.backfill_to, doc.topsoil_thickness), (1, "topo", 1.185))
		self.assertEqual(doc.target_coal_basis, "ar")
		self.assertEqual(json.loads(doc.margin_settings)["price"], 245)
		self.assertEqual((doc.batters[0].side, doc.rehandles[0].destination, doc.quality_rasters[0].label), ("left", "voids", "S2P TS"))
		self.assertEqual((doc.pits[0].boxcut_months, doc.pits[0].priority), (12, 1))
		self.assertEqual(doc.layers[0].geological_loss, 3)
		self.assertEqual(json.loads(doc.json_meta)["extra"], {"newTopLevel": {"kept": True}})

		self.assertEqual(svc.build_project(doc), project)

	def test_weekday_hours(self):
		svc.save_from_project(sample_project(self.pid), name=self.pid, insert=True)
		doc = frappe.get_doc(svc.DOCTYPE, self.pid)
		doc.hours_saturday = 12  # Monday to Friday 18, Saturday 12, Sunday off
		doc.save()
		self.assertEqual(doc.hours_per_day, 17)

		cal = svc.build_project(frappe.get_doc(svc.DOCTYPE, self.pid))["calendar"]
		self.assertEqual(cal["workDays"], [1, 1, 1, 1, 1, 1, 0])
		self.assertEqual(cal["hoursPerDay"], 17)  # same hours a week for the simulator's engine
		self.assertEqual(cal["hoursByDay"], [18, 18, 18, 18, 18, 12, 0])

		# saved back by the simulator unchanged: the weekday hours stay
		project = svc.build_project(frappe.get_doc(svc.DOCTYPE, self.pid))
		svc.save_project(json.dumps(project))
		self.assertEqual(frappe.get_doc(svc.DOCTYPE, self.pid).hours_saturday, 12)

		# hours per day changed in the simulator: every working day gets it
		project["calendar"]["hoursPerDay"] = 20
		svc.save_project(json.dumps(project))
		doc = frappe.get_doc(svc.DOCTYPE, self.pid)
		self.assertEqual([doc.hours_monday, doc.hours_saturday, doc.hours_sunday], [20, 20, 0])

	def test_team_equipment(self):
		adt = frappe.db.get_value("Item", {"asset_category": "ADT", "disabled": 0}, ["name", "item_name"], as_dict=True)
		excavator = frappe.db.get_value("Item", {"asset_category": "Excavator", "disabled": 0})
		if not (adt and excavator):
			self.skipTest("needs an ADT and an Excavator Item")
		project = sample_project(self.pid)
		project["teamList"] = [{"id": "tm_1", "name": "Team 1"}, {"id": "tm_2", "name": "Team 2"}]
		project["fleet"] = [{"id": "eq_t", "kind": "", "name": "", "team": "Team 1", "assetCategory": "ADT", "item": adt.name, "qty": 4}]
		svc.save_from_project(copy.deepcopy(project), name=self.pid, insert=True)
		doc = frappe.get_doc(svc.DOCTYPE, self.pid)
		row = doc.fleet[0]
		self.assertEqual((row.equipment_kind, row.equipment_name, doc.equipment_count, doc.team_rows), ("truck", adt.item_name, 4, 2))
		exported = svc.build_project(doc)
		self.assertEqual(exported["teamList"], project["teamList"])
		self.assertEqual({k: exported["fleet"][0][k] for k in ("team", "assetCategory", "item", "qty")}, {"team": "Team 1", "assetCategory": "ADT", "item": adt.name, "qty": 4})

		for change in ({"item": excavator}, {"team": "Team 9"}, {"asset_category": "Drills"}):
			doc = frappe.get_doc(svc.DOCTYPE, self.pid)
			doc.fleet[0].update(change)
			with self.assertRaises(frappe.ValidationError):
				doc.save()

	def test_tables_filled_in_the_desk_reach_the_simulator(self):
		project = sample_project(self.pid)  # no teamList, as the simulator's own files
		svc.save_from_project(copy.deepcopy(project), name=self.pid, insert=True)
		doc = frappe.get_doc(svc.DOCTYPE, self.pid)
		doc.append("teams", {"team_name": "Team 1"})
		doc.save()

		exported = svc.build_project(frappe.get_doc(svc.DOCTYPE, self.pid))
		self.assertEqual([t["name"] for t in exported["teamList"]], ["Team 1"])
		svc.save_project(json.dumps(exported))  # the simulator saves it back
		self.assertEqual(len(frappe.get_doc(svc.DOCTYPE, self.pid).teams), 1)

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
		doc.append("layers", {"layer_name": "Seam 1", "layer_type": "coal", "density": 1.5})
		doc.save()

		project = svc.build_project(frappe.get_doc(svc.DOCTYPE, self.pid))
		self.assertEqual(project["dumps"][0]["crestElev"], 1550)
		north = project["pits"][1]
		self.assertTrue(north["id"].startswith("pit_"))
		self.assertEqual(north["name"], "North Pit")
		self.assertEqual(north["pts"], [[0, 0], [5, 0], [5, 5]])
		# optional values left blank in the desk stay out of the object (0 would be a loss override)
		self.assertNotIn("losses", project["layers"][1])
		self.assertNotIn("boxcut", north)

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
