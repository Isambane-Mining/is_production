# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt
"""Phase 6 geology: seam surfaces from Geo model grids and from drillholes, geology checks and block geology — checked
against closed-form answers (planar seams, linear qualities)."""

import json
import math

import frappe
from frappe.tests import IntegrationTestCase

from is_production.cad_engineering.doctype.cad_model.test_cad_model import PREFIX, cleanup, make_model
from is_production.cad_engineering.doctype.cad_surface.test_cad_surface import job_result
from is_production.cad_engineering.services import cad_geology_service, cad_mining_service, cad_model_service

IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Employee", "Company", "Branch", "Location", "Geo Project", "CAD Design Revision", "CAD Model", "CAD Processing Job", "CAD Entity", "CAD Geology Model", "CAD Pit Optimisation", "Geo Drillhole", "Drillhole Type", "Quality Variable Name"]
X0, Y0 = -19500.0, 2860500.0  # model (westing/southing) corner of the 100 m test square
GRID = "TST-GEO-GRID"
HOLES = "TST-GEO-HOLE"


def seam_a_roof(u):
	return 100 + 0.01 * u


def remove_geo():
	for code in (GRID, HOLES):
		name = frappe.db.get_value("Geo Project", {"project_code": code})
		if name:
			frappe.db.delete("Geo Model Points", {"geo_project": name})
			for hole in frappe.get_all("Geo Drillhole", filters={"geo_project": name}, pluck="name"):
				frappe.delete_doc("Geo Drillhole", hole, force=True, ignore_permissions=True)
			frappe.delete_doc("Geo Project", name, force=True, ignore_permissions=True)
	for g in frappe.get_all("CAD Pit Optimisation", filters={"cad_model": ["like", f"{PREFIX}%"]}, pluck="name"):
		frappe.delete_doc("CAD Pit Optimisation", g, force=True, ignore_permissions=True)
	for g in frappe.get_all("CAD Geology Model", filters={"cad_model": ["like", f"{PREFIX}%"]}, pluck="name"):
		frappe.delete_doc("CAD Geology Model", g, force=True, ignore_permissions=True)
	frappe.db.commit()


def geo_project(code):
	return frappe.get_doc({"doctype": "Geo Project", "project_code": code, "project_name": f"Test {code}", "crs_name": "EPSG:2053", "project_status": "Pre-Planning"}).insert(ignore_permissions=True).name


def grid_project():
	"""Model grids stored easting/northing-style (x = −westing, y = −southing), 10 m spacing over the square."""
	project = geo_project(GRID)
	variables = {
		"SASR - Seam A Roof": seam_a_roof,
		"SASF - Seam A Floor": lambda u: seam_a_roof(u) - 2,
		"SAAS - Seam A Ash": lambda u: 20 + 0.1 * u,
		"SARD - Seam A Relative Density": lambda u: 1.5,
		"SBSR - Seam B Roof": lambda u: 90.0,
		"SBSF - Seam B Floor": lambda u: 87.0,
		"Topo - Topography": lambda u: 110.0,
	}
	rows, n = [], 0
	for variable, f in variables.items():
		for i in range(11):
			for j in range(11):
				u, v = 10.0 * i, 10.0 * j
				n += 1
				rows.append((frappe.generate_hash(length=12), project, variable, n, -(X0 + u), -(Y0 + v), f(u), f"{variable} 1", "Draft"))
	frappe.db.bulk_insert("Geo Model Points", ["name", "geo_project", "variable_name", "row_no", "x", "y", "z", "version_tag", "status"], rows)
	return project


def holes_project():
	"""Nine vertical holes on a 50 m grid, collars at 110; seam A from 10 + 0.01u to 12 + 0.01u deep (roof 100 − 0.01u),
	density 1.5, ash 20 + 0.1u; stored easting/northing-style like the grids."""
	project = geo_project(HOLES)
	for master, field, value in (("Drillhole Type", "define_drillhole_type", "Exploration"), ("Quality Variable Name", "quality_variable_name", "Ash")):
		if not frappe.db.exists(master, value):
			frappe.get_doc({"doctype": master, field: value}).insert(ignore_permissions=True)
	for i in range(3):
		for j in range(3):
			u, v = 50.0 * i, 50.0 * j
			top, bottom = 10 + 0.01 * u, 12 + 0.01 * u
			frappe.get_doc(
				{
					"doctype": "Geo Drillhole",
					"geo_project": project,
					"hole_id": f"TSTGEO-{i}{j}",
					"drillhole_type": "Exploration",
					"easting": -(X0 + u),
					"northing": -(Y0 + v),
					"rl_m": 110,
					"end_depth_m": 20,
					"dip": -90,
					"geo_material_interval": [
						{"from_depth": 0, "to_depth": top, "lithology": "Sandstone"},
						{"from_depth": top, "to_depth": bottom, "seam": "A", "density": 1.5},
						{"from_depth": bottom, "to_depth": 20, "lithology": "Shale"},
					],
					"geo_quality_interval": [{"from_depth": top, "to_depth": bottom, "variable_name": "Ash", "value": 20 + 0.1 * u}],
				}
			).insert(ignore_permissions=True)
	return project


class IntegrationTestCADGeology(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cleanup()
		remove_geo()

	@classmethod
	def tearDownClass(cls):
		remove_geo()
		cleanup()
		super().tearDownClass()

	def test_hole_desurvey(self):
		p = cad_geology_service.hole_point([0, 0, 100], 10, 90, -60)
		self.assertAlmostEqual(p[0], 5, places=9)  # 10·cos 60° east
		self.assertAlmostEqual(p[1], 0, places=9)
		self.assertAlmostEqual(p[2], 100 - 10 * math.sin(math.radians(60)), places=9)
		for got, want in zip(cad_geology_service.hole_point([1, 2, 3], 4, 0, 0), [1, 2, -1]):  # no dip: vertical
			self.assertAlmostEqual(got, want, places=9)
		self.assertEqual(cad_geology_service.parse_variable("S1UAS - 1 Seam Upper Ash")[:2], ("S1U", "AS"))
		self.assertEqual(cad_geology_service.parse_variable("S1AS - 1 Seam Ash")[:2], ("S1", "AS"))
		self.assertEqual(cad_geology_service.parse_variable("S2LSR - 2 Seam Lower Roof")[:2], ("S2L", "SR"))
		self.assertEqual(cad_geology_service.parse_variable("Topo - Topography")[:2], (None, None))

	def test_model_grids_checks_and_block_geology(self):
		project = grid_project()
		model = make_model("GEO").name
		info = cad_geology_service.sources(model, project)
		self.assertEqual([s["key"] for s in info["seams"]], ["SA", "SB"])
		self.assertEqual(info["seams"][0]["label"], "Seam A")
		self.assertEqual(info["seams"][0]["density"], "SARD - Seam A Relative Density")
		self.assertEqual(info["seams"][0]["qualities"], {"Ash": "SAAS - Seam A Ash"})
		self.assertEqual((info["topography"], info["axis"]), ("Topo - Topography", "Negate X and Y"))

		res = job_result(cad_mining_service.start(model, "geology", {"geo_project": project}))
		seams = {s["seam"]: s for s in res["seams"]}
		self.assertAlmostEqual(seams["SA"]["volume"], 2 * 100 * 100, delta=1e-6)
		self.assertAlmostEqual(seams["SA"]["tonnes"], 1.5 * 20000, delta=1e-6)
		self.assertAlmostEqual(json.loads(seams["SA"]["qualities"])["Ash"], 25, places=6)  # mean of 20 + 0.1u, constant thickness
		self.assertAlmostEqual(seams["SB"]["volume"], 3 * 100 * 100, delta=1e-6)
		self.assertAlmostEqual(seams["SA"]["mean_thickness"], 2, places=9)
		by = {(c["check"], c["seam"]): c["status"] for c in res["checks"]}
		self.assertEqual(by[("Floor below roof", "SA")], "pass")
		self.assertEqual(by[("Seams do not cross", "SA / SB")], "pass")
		self.assertEqual(by[("Seam below topography", "SA")], "pass")
		self.assertEqual(by[("Density", "SB")], "warning")  # no RD grid for seam B
		self.assertEqual(res["checks_status"], "Warning")
		roof = next(s for s in res["surfaces"] if s["key"] == "SA:roof")
		bbox = roof["stats"]["bbox"]
		self.assertAlmostEqual(bbox[0], X0, places=6)  # mapped onto the model's westing/southing axes
		self.assertAlmostEqual(bbox[4], Y0 + 100, places=6)
		record = frappe.get_doc("CAD Geology Model", res["geology"])
		self.assertEqual((record.status, record.axis_mapping, len(record.seams)), ("Current", "Negate X and Y", 2))

		# two blocks: W (u 0–50) in the 95–105 bench holds seam A, E (u 50–100) in the 85–95 bench holds seam B
		def block(eid, u0, u1, bottom):
			pts = [[X0 + u0, Y0, bottom], [X0 + u1, Y0, bottom], [X0 + u1, Y0 + 100, bottom], [X0 + u0, Y0 + 100, bottom]]
			return {"entity_id": eid, "entity_type": "Polyline", "geometry": {"points": pts, "closed": True}, "metadata": {"mining_block": {"block_id": eid, "bench_bottom": bottom, "bench_top": bottom + 10, "volume": 50000.0}}}

		cad_model_service.save_edits(model, frappe.db.get_value("CAD Model", model, "edit_version"), {"upserts": [block("BW", 0, 50, 95), block("BE", 50, 100, 85)]})
		out = job_result(cad_mining_service.start(model, "block_geology", {"geology": res["geology"], "min_coal": 20}))
		w, e = out["updates"]["BW"], out["updates"]["BE"]
		self.assertEqual(list(w["geology"]["seams"]), ["SA"])
		self.assertAlmostEqual(w["geology"]["coal_volume"], 2 * 50 * 100, delta=1e-6)
		self.assertAlmostEqual(w["geology"]["coal_tonnes"], 15000, delta=1e-6)
		self.assertAlmostEqual(w["geology"]["seams"]["SA"]["qualities"]["Ash"], 22.5, places=6)  # mean over u 0–50
		self.assertAlmostEqual(w["geology"]["strip_ratio"], 40000 / 15000, places=9)
		self.assertEqual(list(e["geology"]["seams"]), ["SB"])
		self.assertAlmostEqual(e["geology"]["coal_tonnes"], 1.5 * 15000, delta=1e-6)  # default density (no RD grid)
		self.assertEqual((w["material"], e["material"]), ("Coal", "Coal"))  # 20 % and 30 % coal ≥ 20 %
		out = job_result(cad_mining_service.start(model, "block_geology", {"geology": res["geology"], "min_coal": 25, "material_by": "seam"}))
		self.assertEqual((out["updates"]["BW"]["material"], out["updates"]["BE"]["material"]), ("Overburden", "Seam B"))

	def test_drillhole_seams(self):
		project = holes_project()
		model = make_model("GEOH").name
		info = cad_geology_service.sources(model, project)
		self.assertEqual(info["drillholes"]["holes"], 9)
		self.assertEqual([s["seam"] for s in info["drillholes"]["seams"]], ["A"])
		res = job_result(cad_mining_service.start(model, "geology", {"geo_project": project, "source": "Drillholes", "collars_as_topography": 1}))
		(seam,) = res["seams"]
		self.assertAlmostEqual(seam["volume"], 2 * 100 * 100, delta=1e-6)  # roof and floor are planes through the holes
		self.assertAlmostEqual(seam["tonnes"], 30000, delta=1e-6)
		self.assertAlmostEqual(json.loads(seam["qualities"])["Ash"], 25, places=6)
		self.assertEqual(len(res["holes"]), 9)
		hole = next(h for h in res["holes"] if h["hole_id"] == "TSTGEO-20")
		roof, floor = hole["seams"]["A"]
		self.assertAlmostEqual(roof[0], X0 + 100, places=9)
		self.assertAlmostEqual(roof[2], 100 - 1, places=9)  # 110 − (10 + 0.01·100)
		self.assertAlmostEqual(floor[2], 98 - 1, places=9)
		self.assertEqual({c["status"] for c in res["checks"] if c["check"] == "Seam below topography"}, {"pass"})
		# inverse distance: roof and thickness gridded, floor = roof − thickness; a plane through 9 holes is not
		# reproduced exactly by IDW, but the thickness (2 m everywhere) is
		res = job_result(cad_mining_service.start(model, "geology", {"geo_project": project, "source": "Drillholes", "interpolation": "idw", "cell": 10}))
		(seam,) = res["seams"]
		self.assertAlmostEqual(seam["mean_thickness"], 2, places=6)
		self.assertEqual(frappe.db.count("CAD Geology Model", {"cad_model": model, "status": "Current"}), 1)  # the TIN run is superseded

	def test_pit_shell_optimisation(self):
		"""Flat topography 110 over the 100 m square, seam A (2 m, density 1.5) and seam B (3 m, default density 1.5): a
		10 m block model from 110 down to 80 holds 300 000 m³ and 75 000 t; waste 250 000 bcm."""
		project = frappe.db.get_value("Geo Project", {"project_code": GRID}) or grid_project()
		model = make_model("GEO-OPT").name
		geo = job_result(cad_mining_service.start(model, "geology", {"geo_project": project}))["geology"]
		rich = job_result(cad_mining_service.start(model, "pit_shells", {"geology": geo, "cell_size": 10, "bench_height": 10, "slope": 45, "price": 1000, "mining_cost": 1, "rf_from": 0.5, "rf_to": 1.0, "rf_step": 0.5}))
		self.assertAlmostEqual(rich["totals"]["volume"], 300000, delta=1e-6)
		self.assertAlmostEqual(rich["totals"]["tonnes"], 75000, delta=1e-6)
		(grid,) = [rich["grid"]]
		self.assertEqual((grid["nx"], grid["ny"], grid["benches"], grid["top"]), (10, 10, 3, 110))
		sel = rich["selected"]
		self.assertEqual(sel["blocks"], 300)  # everything pays
		self.assertAlmostEqual(sel["tonnes"], 75000, delta=1e-6)
		self.assertAlmostEqual(sel["waste_volume"], 250000, delta=1e-6)
		self.assertAlmostEqual(sel["strip_ratio"], 250000 / 75000, places=9)
		self.assertAlmostEqual(sel["value"], 1000 * 75000 - 300000, delta=1e-3)
		self.assertTrue(rich["shell"] and rich["outline"])
		self.assertEqual(len(rich["outline"]), 1)
		self.assertEqual(frappe.db.get_value("CAD Pit Optimisation", rich["optimisation"], "shell_tonnes"), 75000)
		poor = job_result(cad_mining_service.start(model, "pit_shells", {"geology": geo, "price": 1, "mining_cost": 100, "rf_from": 1, "rf_to": 1, "rf_step": 1}))
		self.assertEqual(poor["selected"]["blocks"], 0)
		self.assertIsNone(poor["shell"])
