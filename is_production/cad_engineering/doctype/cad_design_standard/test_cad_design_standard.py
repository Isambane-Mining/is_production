# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt
"""Phase 4: design standards (versioning), open-pit bench generation, haul roads, mining blocks, design validation,
design exports — checked against closed-form answers."""

import csv
import json
import math

import frappe
from frappe.tests import IntegrationTestCase

from is_production.cad_engineering.doctype.cad_design_standard.cad_design_standard import activate, new_version
from is_production.cad_engineering.doctype.cad_model.test_cad_model import cleanup, make_model
from is_production.cad_engineering.doctype.cad_surface.test_cad_surface import job_result
from is_production.cad_engineering.services import cad_exchange_service, cad_mining_service, cad_model_service, cad_revision_service, cad_surface_service

IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Employee", "Company", "Branch", "Location", "Geo Project", "CAD Design Revision", "CAD Model", "CAD Processing Job", "CAD Entity", "CAD Design Standard", "CAD Design Validation", "Material Type"]
X0, Y0 = -19500.0, 2860500.0
CODE = "TST-STD-P4"


def make_standard(version_rules=None, activate_it=True):
	frappe.db.delete("CAD Design Validation", {"design_standard": ["like", f"{CODE}%"]})
	frappe.db.delete("CAD Design Standard", {"standard_code": CODE})
	doc = frappe.get_doc(
		{
			"doctype": "CAD Design Standard",
			"standard_code": CODE,
			"standard_name": "Test open pit",
			"bench_height": 10,
			"bench_reference_elevation": 0,
			"face_angle": 60,
			"maximum_slope_angle": 50,
			"berm_width": 8,
			"minimum_bench_width": 6,
			"minimum_floor_width": 20,
			"road_width": 20,
			"minimum_road_width": 18,
			"maximum_gradient": 10,
			"minimum_turning_radius": 40,
			"normal_crossfall": 0,
			"maximum_superelevation": 0,
			"cut_batter_angle": 45,
			"fill_batter_angle": 45,
			"block_length": 40,
			"block_width": 30,
			"mining_direction": 90,
			"design_tolerance": 0.01,
			"angle_tolerance": 0.05,
			**(version_rules or {}),
		}
	).insert()
	if activate_it:
		activate(doc.name)
	return doc.name


def frustum(a, b, h, t):
	"""∫ over a face of height h at slope tan = t of the area of a rectangle a × b growing outwards: (a + 2s/t)(b + 2s/t)."""
	return a * b * h + (a + b) * h * h / t + 4 * h**3 / (3 * t * t)


class IntegrationTestCADMiningDesign(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cleanup()

	@classmethod
	def tearDownClass(cls):
		cleanup()
		frappe.db.delete("CAD Design Standard", {"standard_code": CODE})
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")

	# -- helpers --------------------------------------------------------------

	def model_with_ground(self, code, z=105.0):
		model = make_model(code).name
		pts = [{"entity_id": f"G{i}_{j}", "entity_type": "Point", "geometry": {"p": [X0 - 150 + 20 * i, Y0 - 150 + 20 * j, z]}} for i in range(21) for j in range(21)]
		v = cad_model_service.save_edits(model, 0, {"upserts": pts})["edit_version"]
		built = job_result(cad_surface_service.start_surface(model, "build", {"points": [p["entity_id"] for p in pts]}))
		v = cad_model_service.save_edits(model, v, {"upserts": [{"entity_id": "GROUND", "entity_type": "Surface", "geometry": {"name": "Ground", "surface_type": "Survey Surface", "source": built["source"]}, "geometry_ref": built["geometry_ref"]}]})["edit_version"]
		floor = [[X0, Y0, 60], [X0 + 100, Y0, 60], [X0 + 100, Y0 + 40, 60], [X0, Y0 + 40, 60]]
		v = cad_model_service.save_edits(model, v, {"upserts": [{"entity_id": "FLOOR", "entity_type": "Polyline", "geometry": {"points": floor, "closed": True}}]})["edit_version"]
		return model, v, built["geometry_ref"]

	def add_pit(self, model, v, result):
		"""What the workstation does with a pit job: rings + pit surface (+ post-mining surface) in one save."""
		ups = [
			{"entity_id": f"PIT-{r['kind']}-{r['bench']}", "entity_type": "Polyline", "geometry": {"points": r["points"], "closed": True}, "metadata": {"pit_design": {"stage": result["stage"], "kind": r["kind"], "bench": r["bench"], "elevation": r["elevation"], "standard": result["standard"]}}}
			for r in result["rings"]
		]
		ups.append({"entity_id": "PIT-SURFACE", "entity_type": "Surface", "geometry": {"name": "Pit", "surface_type": "Pit Surface", "source": result["surface"]["source"]}, "geometry_ref": result["surface"]["geometry_ref"]})
		if result.get("post_mining"):
			ups.append({"entity_id": "POST", "entity_type": "Surface", "geometry": {"name": "Post mining", "surface_type": "Design Surface", "source": result["post_mining"]["source"]}, "geometry_ref": result["post_mining"]["geometry_ref"]})
		return cad_model_service.save_edits(model, v, {"upserts": ups})["edit_version"]

	# -- tests ----------------------------------------------------------------

	def test_standard_versions_are_frozen_once_active(self):
		name = make_standard(activate_it=False)
		self.assertEqual(name, f"{CODE}-V1")
		doc = frappe.get_doc("CAD Design Standard", name)
		doc.berm_width = 9
		doc.save()  # a Draft can change
		activate(name)
		doc = frappe.get_doc("CAD Design Standard", name)
		doc.berm_width = 12
		self.assertRaises(frappe.ValidationError, doc.save)
		v2 = new_version(name)
		self.assertEqual(v2, f"{CODE}-V2")
		self.assertEqual(frappe.db.get_value("CAD Design Standard", v2, ["status", "previous_version", "berm_width"]), ("Draft", name, 9))
		activate(v2)
		self.assertEqual(frappe.db.get_value("CAD Design Standard", name, "status"), "Superseded")
		model = make_model("STD-SET").name
		self.assertRaises(frappe.ValidationError, cad_mining_service.set_standard, model, name)  # superseded
		self.assertEqual(cad_mining_service.set_standard(model, v2)["name"], v2)

	def test_pit_benches_volume_and_validation(self):
		std = make_standard({"maximum_slope_angle": 40})  # the pit's overall slope is 40.34°: one check must fail
		model, v, ground_ref = self.model_with_ground("PIT", z=105)
		cad_mining_service.set_standard(model, std)
		res = job_result(cad_mining_service.start(model, "pit", {"floor": "FLOOR", "stage": "S1", "top_elevation": 100, "ground": ground_ref, "corners": "mitre"}))
		kinds = [(r["kind"], r["bench"], r["elevation"]) for r in res["rings"]]
		self.assertEqual(kinds[0], ("floor", 0, 60.0))
		self.assertEqual(kinds[-1], ("crest", 4, 100.0))
		self.assertEqual(len([k for k in kinds if k[0] == "crest"]), 4)
		t = math.tan(math.radians(60))
		face = 10 / t
		for b in res["benches"]:
			self.assertAlmostEqual(b["face_width"], face, places=6)
			self.assertAlmostEqual(b["face_angle"], 60, places=6)
			if "berm_width" in b:
				self.assertAlmostEqual(b["berm_width"], 8, places=6)
		# void below the ground at 105: four frustums plus the footprint × 5 m
		a, bb, expected = 100.0, 40.0, 0.0
		for _k in range(4):
			expected += frustum(a, bb, 10, t)
			a, bb = a + 2 * (face + 8), bb + 2 * (face + 8)
		top = (a - 16) * (bb - 16)  # the top crest: the last toe + face, without the berm
		expected += top * 5
		self.assertAlmostEqual(res["volume"]["cut"], expected, delta=1e-6 * expected)
		self.assertIsNone(res["post_mining"])  # the top crest is below the ground: no daylight line
		self.assertAlmostEqual(res["summary"]["overall_slope_angle"], math.degrees(math.atan2(40, 3 * (face + 8) + face)), places=6)
		v = self.add_pit(model, v, res)

		# validation: benches, faces, berms and floor meet the standard; the overall slope (40.34°) is above 40° → fail
		out = cad_mining_service.validate(model)
		by = {}
		for c in out["checks"]:
			by.setdefault(c["check"], []).append(c["status"])
		self.assertEqual(set(by["Bench height"]), {"pass"})
		self.assertEqual(set(by["Bench face angle (steepest)"]), {"pass"})
		self.assertEqual(set(by["Berm width (narrowest)"]), {"pass"})
		self.assertEqual(by["Overall slope angle (steepest)"], ["fail"])
		self.assertEqual(by["Floor width"], ["pass"])
		self.assertEqual(out["status"], "Fail")
		rec = frappe.get_doc("CAD Design Validation", out["name"])
		self.assertEqual((rec.design_standard, rec.standard_version, rec.status), (std, 1, "Fail"))
		self.assertEqual(frappe.db.get_value("CAD Model", model, "last_design_validation"), rec.name)
		# the submitted revision records which standard version the design was checked against
		revision = cad_revision_service.submit_for_review(model, "pit design")
		results = json.loads(revision.validation_results)
		self.assertEqual((results["design_validation"], results["design_standard"], results["design_standard_version"]), (rec.name, std, 1))

	def test_pit_daylight_post_mining_and_narrow_berm_fails(self):
		std = make_standard()
		model, v, ground_ref = self.model_with_ground("PIT-DAY", z=95)
		cad_mining_service.set_standard(model, std)
		res = job_result(cad_mining_service.start(model, "pit", {"floor": "FLOOR", "stage": "S2", "top_elevation": 100, "ground": ground_ref, "corners": "mitre", "berm_width": 4}))
		self.assertTrue(res["daylight"] and res["daylight"][0]["closed"])
		self.assertTrue(all(abs(p[2] - 95) < 1e-6 for p in res["daylight"][0]["points"]))
		self.assertIsNotNone(res["post_mining"])
		v = self.add_pit(model, v, res)
		# the ground after mining against the original ground: the void from 60 up to 95
		t = math.tan(math.radians(60))
		face = 10 / t
		a, bb, expected = 100.0, 40.0, 0.0
		for k in range(4):
			expected += frustum(a, bb, 10 if k < 3 else 5, t)
			a, bb = a + 2 * (face + 4), bb + 2 * (face + 4)
		vol = job_result(cad_surface_service.start_analysis(model, "volume", {"geometry_refs": [ground_ref, res["post_mining"]["geometry_ref"]]}))
		self.assertAlmostEqual(vol["cut"], expected, delta=1e-6 * expected)
		self.assertAlmostEqual(vol["fill"], 0, delta=1e-6)
		checks = cad_mining_service.validate(model)["checks"]
		berms = [c for c in checks if c["check"] == "Berm width (narrowest)"]
		self.assertTrue(berms and all(c["status"] == "fail" and abs(c["value"] - 4) < 1e-9 for c in berms))

	def test_blocks_sum_to_the_pit_volume_and_subdivide(self):
		std = make_standard()
		model, v, ground_ref = self.model_with_ground("BLOCKS", z=105)
		cad_mining_service.set_standard(model, std)
		pit = job_result(cad_mining_service.start(model, "pit", {"floor": "FLOOR", "stage": "S1", "top_elevation": 100, "ground": ground_ref, "corners": "mitre"}))
		v = self.add_pit(model, v, pit)
		top = next(r for r in pit["rings"] if r["kind"] == "crest" and r["bench"] == 4)
		v = cad_model_service.save_edits(model, v, {"upserts": [{"entity_id": "LIMIT", "entity_type": "Polyline", "geometry": {"points": [[p[0], p[1], 100] for p in top["points"]], "closed": True}}]})["edit_version"]
		res = job_result(cad_mining_service.start(model, "blocks", {"boundary": "LIMIT", "upper": ground_ref, "lower": pit["surface"]["geometry_ref"], "min_volume": 0}))
		total = sum(b["volume"] for b in res["blocks"])
		self.assertAlmostEqual(total, pit["volume"]["cut"], delta=1e-6 * total)
		self.assertEqual(sorted({b["bench_bottom"] for b in res["blocks"]}), [60.0, 70.0, 80.0, 90.0, 100.0])
		self.assertEqual([b["sequence"] for b in res["blocks"]], list(range(1, len(res["blocks"]) + 1)))
		self.assertEqual(res["blocks"][0]["bench_bottom"], 100.0)  # top bench first
		# save the blocks (as the workstation does) and split one 2 × 2: the parts add up to the block
		ups = [
			{"entity_id": f"BLK{n}", "entity_type": "Polyline", "geometry": {"points": b["polygon"], "closed": True}, "metadata": {"mining_block": {**{k: b[k] for k in ("block_id", "bench_bottom", "bench_top", "row", "col", "volume", "plan_area", "sequence")}, **res["common"], "material": "Waste" if n % 2 else ""}}}
			for n, b in enumerate(res["blocks"])
		]
		v = cad_model_service.save_edits(model, v, {"upserts": ups})["edit_version"]
		big = max(range(len(res["blocks"])), key=lambda n: res["blocks"][n]["volume"])
		split = job_result(cad_mining_service.start(model, "subdivide", {"blocks": [f"BLK{big}"], "along": 2, "across": 2}))
		parts = split["replacements"][f"BLK{big}"]
		self.assertEqual(len(parts), 4)
		self.assertAlmostEqual(sum(p["volume"] for p in parts), res["blocks"][big]["volume"], delta=1e-6 * res["blocks"][big]["volume"])
		checks = {c["check"]: c for c in cad_mining_service.validate(model)["checks"] if c["category"] == "Mining blocks"}
		self.assertEqual(checks["No overlaps within a bench"]["status"], "pass")
		self.assertEqual(checks["Material classified"]["status"], "warning")
		self.assertEqual(checks["Benches on the standard's bench levels"]["status"], "pass")
		# exports: block table and design GeoJSON
		for fmt in ("Blocks CSV", "GeoJSON"):
			io = cad_exchange_service.start_export(model, {"format": fmt})
			io = cad_exchange_service.get_exchange(io["name"])
			self.assertEqual(io["processing_status"], "Completed", io)
			path = frappe.get_doc("File", {"file_url": io["result_file"]}).get_full_path()
			if fmt == "Blocks CSV":
				with open(path) as f:
					rows = list(csv.DictReader(f))
				self.assertEqual(len(rows), len(res["blocks"]))
				self.assertAlmostEqual(sum(float(r["volume_m3"]) for r in rows), total, delta=1e-3 * len(rows))
			else:
				with open(path) as f:
					data = json.load(f)
				kinds = {feat["properties"]["design"] for feat in data["features"]}
				self.assertEqual(kinds, {"pit_design", "mining_block"})
				self.assertEqual(data["crs"]["properties"]["name"], "EPSG:2053")

	def test_haul_road_gradient_and_cut_fill_record(self):
		std = make_standard()
		model, v, ground_ref = self.model_with_ground("ROAD", z=100)
		cad_mining_service.set_standard(model, std)
		v = cad_model_service.save_edits(model, v, {"upserts": [{"entity_id": "CL", "entity_type": "Polyline", "geometry": {"points": [[X0 - 100, Y0, 100], [X0 + 100, Y0, 100]], "closed": False}}]})["edit_version"]
		res = job_result(cad_mining_service.start(model, "road", {"centreline": "CL", "name": "Ramp", "ground": ground_ref, "vertical": "grade", "start_elevation": 100, "gradient": 12}))
		self.assertAlmostEqual(res["max_grade"], 12, places=9)
		# 20 m wide, 45° fill batters, rising 12 % over 200 m: fill = ∫ (20h + h²) dc, h = 0.12c
		expected = 20 * 0.12 * 200**2 / 2 + 0.12**2 * 200**3 / 3
		self.assertAlmostEqual(res["volume"]["fill"], expected, delta=1e-6 * expected)
		record = frappe.get_doc("CAD Volume Calculation", res["volume"]["record"])
		self.assertEqual((record.base_name, record.comparison_name), ("Ground", "Ramp"))
		ups = [{"entity_id": "ROAD-CL", "entity_type": "Polyline", "geometry": {"points": res["lines"]["centre"], "closed": False}, "metadata": {"haul_road": {"name": "Ramp", "role": "centreline", "width": res["options"]["width"], "standard": std}}}]
		cad_model_service.save_edits(model, v, {"upserts": ups})
		checks = {c["check"]: c for c in cad_mining_service.validate(model)["checks"]}
		self.assertEqual(checks["Gradient (steepest)"]["status"], "fail")
		self.assertAlmostEqual(checks["Gradient (steepest)"]["value"], 12, places=6)
		self.assertEqual(checks["Turning radius (tightest)"]["status"], "pass")
		self.assertEqual(checks["Road width"]["status"], "pass")

	def test_drill_pattern_layout_validation_and_hole_table(self):
		std = make_standard({"drill_burden": 4, "drill_spacing": 5, "drill_pattern": "Staggered", "drill_crest_standoff": 2, "drill_hole_diameter": 165, "drill_subdrill": 1, "drill_max_inclination": 30, "drill_spacing_tolerance": 10})
		model, v, ground_ref = self.model_with_ground("DRILL", z=100)
		cad_mining_service.set_standard(model, std)
		blast = [[X0, Y0, 100], [X0 + 40, Y0, 100], [X0 + 40, Y0 + 20, 100], [X0, Y0 + 20, 100]]
		v = cad_model_service.save_edits(model, v, {"upserts": [{"entity_id": "BLAST", "entity_type": "Polyline", "geometry": {"points": blast, "closed": True}}]})["edit_version"]
		# rows along the 40 m side (row direction 90° / 270°: east-west whichever way the plan is turned)
		res = job_result(cad_mining_service.start(model, "drill", {"boundary": "BLAST", "name": "P1", "collar_surface": ground_ref, "floor_elevation": 90, "inclination": 10, "row_direction": 90}))
		# 36 × 16 m after the stand-off: 5 rows 4 m apart; rows of 8 holes (5 m apart) and, staggered, of 7
		self.assertEqual(res["summary"]["rows"], 5)
		self.assertEqual(res["summary"]["holes"], 38)
		length = 11 / math.cos(math.radians(10))  # 10 m bench + 1 m subdrill, 10° from vertical
		for h in res["holes"]:
			self.assertAlmostEqual(h["collar"][2], 100, places=6)
			self.assertAlmostEqual(h["toe"][2], 89, places=6)
			self.assertAlmostEqual(h["length"], length, places=6)
			self.assertAlmostEqual(math.dist(h["collar"][:2], h["toe"][:2]), 11 * math.tan(math.radians(10)), places=6)
		self.assertAlmostEqual(res["summary"]["drilled_length"], 38 * length, places=4)
		# save as the workstation does (a line per hole, a point per collar) and validate
		meta = {**res["design"], "role": "hole"}
		ups = []
		for n, h in enumerate(res["holes"]):
			data = {**meta, "hole_id": h["hole_id"], "row": h["row"], "number": h["number"], "length": h["length"]}
			ups.append({"entity_id": f"HOLE{n}", "entity_type": "Line", "geometry": {"p0": h["collar"], "p1": h["toe"]}, "metadata": {"drill_hole": data}})
			ups.append({"entity_id": f"COLLAR{n}", "entity_type": "Point", "geometry": {"p": h["collar"]}, "metadata": {"drill_hole": {**data, "role": "collar"}}})
		v = cad_model_service.save_edits(model, v, {"upserts": ups})["edit_version"]
		checks = {c["check"]: c for c in cad_mining_service.validate(model)["checks"] if c["category"] == "Drill pattern"}
		for name in ("Holes go down to their toes", "Hole inclination", "Collars inside the blast boundary", "Collar spacing (closest)", "Burden", "Spacing", "Hole diameter"):
			self.assertEqual(checks[name]["status"], "pass", (name, checks[name]))
		self.assertAlmostEqual(checks["Collar spacing (closest)"]["value"], math.hypot(4, 2.5), places=6)  # staggered: diagonal neighbours
		# a hole moved next to another: spacing warning; one leaning 40°: inclination failure
		a = res["holes"][0]
		moved = [a["collar"][0] + 1, a["collar"][1], 100]
		steep_toe = [a["collar"][0] + 11 * math.tan(math.radians(40)), a["collar"][1], 89]
		cad_model_service.save_edits(model, v, {"upserts": [{"entity_id": "HOLE1", "entity_type": "Line", "geometry": {"p0": moved, "p1": [moved[0], moved[1], 89]}, "metadata": {"drill_hole": {**meta, "hole_id": "X1"}}}, {"entity_id": "HOLE0", "entity_type": "Line", "geometry": {"p0": a["collar"], "p1": steep_toe}, "metadata": {"drill_hole": {**meta, "hole_id": "X0"}}}]})
		checks = {c["check"]: c for c in cad_mining_service.validate(model)["checks"] if c["category"] == "Drill pattern"}
		self.assertEqual(checks["Collar spacing (closest)"]["status"], "warning")
		self.assertEqual(checks["Hole inclination"]["status"], "fail")
		io = cad_exchange_service.start_export(model, {"format": "Drill Holes CSV"})
		io = cad_exchange_service.get_exchange(io["name"])
		self.assertEqual(io["processing_status"], "Completed", io)
		with open(frappe.get_doc("File", {"file_url": io["result_file"]}).get_full_path()) as f:
			rows = list(csv.DictReader(f))
		self.assertEqual(len(rows), 38)
		self.assertTrue(all(float(r["collar_z"]) == 100.0 for r in rows))

	# -- Phase 6: vertical curves, in-pit ramps, pit stages ---------------------

	def test_road_vertical_curves_and_k_check(self):
		std = make_standard({"minimum_k_value": 5})
		model = make_model("VCURVE").name
		cad_mining_service.set_standard(model, std)
		v = cad_model_service.save_edits(model, 0, {"upserts": [{"entity_id": "CL", "entity_type": "Polyline", "geometry": {"points": [[X0 - 100, Y0, 0], [X0 + 100, Y0, 0]], "closed": False}}]})["edit_version"]
		res = job_result(cad_mining_service.start(model, "road", {"centreline": "CL", "name": "Crest", "vertical": "profile", "pvis": "0:100, 100:110, 200:100", "vertical_curve_length": 40, "fit_radius": 0}))
		(curve,) = res["vertical_curves"]
		self.assertEqual((curve["kind"], curve["length"]), ("crest", 40))
		self.assertAlmostEqual(curve["k"], 2, places=9)  # 40 m over a 20 % grade change
		z = {round(s[0], 6): s[3] for s in res["stations"]}
		self.assertAlmostEqual(z[100.0], 109, places=9)  # the PVI less A·L/8
		self.assertAlmostEqual(z[90.0], 108.75, places=9)  # 108 + 0.1·10 − 0.2/80·10²
		self.assertAlmostEqual(z[80.0], 108, places=9)  # the curve starts on the grade line
		self.assertAlmostEqual(z[150.0], 105, places=9)
		ups = [{"entity_id": "ROAD-CL", "entity_type": "Polyline", "geometry": {"points": res["lines"]["centre"], "closed": False}, "metadata": {"haul_road": {"name": "Crest", "role": "centreline", "width": 20, "standard": std, "min_k": res["min_k"]}}}]
		cad_model_service.save_edits(model, v, {"upserts": ups})
		checks = {c["check"]: c for c in cad_mining_service.validate(model)["checks"]}
		self.assertEqual(checks["Vertical curve K (smallest)"]["status"], "fail")
		self.assertAlmostEqual(checks["Vertical curve K (smallest)"]["value"], 2, places=9)

	def test_in_pit_ramp_is_cut_into_the_wall(self):
		std = make_standard()
		model, v, ground_ref = self.model_with_ground("RAMP", z=95)
		cad_mining_service.set_standard(model, std)
		base = {"floor": "FLOOR", "top_elevation": 100, "ground": ground_ref, "corners": "mitre"}
		plain = job_result(cad_mining_service.start(model, "pit", {**base, "stage": "Plain"}))
		res = job_result(cad_mining_service.start(model, "pit", {**base, "stage": "Ramped", "ramp": 1, "ramp_width": 10, "ramp_gradient": 10, "ramp_bearing": 90, "ramp_direction": "clockwise"}))
		ramp = res["ramp"]
		centre = ramp["lines"]["centre"]
		self.assertAlmostEqual(centre[0][2], 95, places=6)  # enters at the daylight line
		self.assertAlmostEqual(centre[-1][2], 60, places=6)  # reaches the floor
		self.assertAlmostEqual(ramp["max_grade"], 10, delta=0.05)
		self.assertGreater(ramp["extra_cut"], 0)
		self.assertGreater(ramp["left_in_place"], 0)
		# exact bookkeeping: ground → pit-with-ramp = ground → pit + pit → ramp road
		net = lambda vol: vol["cut"] - vol["fill"]  # noqa: E731
		self.assertAlmostEqual(net(res["volume"]), net(plain["volume"]) + ramp["extra_cut"] - ramp["left_in_place"], delta=1e-6 * net(plain["volume"]))
		self.assertTrue(res["post_mining"]["source"]["ramp"])

	def test_pit_stages_incremental_volumes_and_pushback(self):
		std = make_standard()
		model, v, ground_ref = self.model_with_ground("STAGES", z=95)
		cad_mining_service.set_standard(model, std)
		res = job_result(cad_mining_service.start(model, "stages", {"floor": "FLOOR", "top_elevation": 100, "ground": ground_ref, "corners": "mitre", "min_pushback_width": 25, "stages": [{"stage": "A", "floor_elevation": 80}, {"stage": "B", "floor_elevation": 60}]}))
		t = math.tan(math.radians(60))
		face = 10 / t

		def void(benches):
			a, bb, total = 100.0, 40.0, 0.0
			for k in range(benches):
				total += frustum(a, bb, 10 if k < benches - 1 else 5, t)
				a, bb = a + 2 * (face + 8), bb + 2 * (face + 8)
			return total

		a, b = res["table"]
		self.assertAlmostEqual(a["volume"], void(2), delta=1e-6 * void(2))
		self.assertAlmostEqual(b["volume"], void(4), delta=1e-6 * void(4))
		self.assertAlmostEqual(b["increment"], void(4) - void(2), delta=1e-6 * void(4))
		self.assertTrue(b["contains_previous"])
		self.assertAlmostEqual(b["undercut"], 0, delta=1e-6)
		self.assertAlmostEqual(b["pushback_width"], 2 * (face + 8), places=6)
		self.assertTrue(b["pushback_ok"])
		self.assertEqual([s["stage"] for s in res["stages"]], ["A", "B"])
		self.assertTrue(res["run"])
