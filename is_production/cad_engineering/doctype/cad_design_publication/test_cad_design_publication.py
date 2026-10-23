# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt
"""Phase 5: an approved CAD design published to Geo Planning (traceable), the CAD sequence as a schedule selection,
republishing a changed design, survey reconciliation (exact) with the schedule to date, revision comparison."""

import json
import math

import frappe
from frappe.tests import IntegrationTestCase

from is_production.cad_engineering.doctype.cad_design_standard import test_cad_design_standard as phase4  # module only: its tests are not collected here
from is_production.cad_engineering.doctype.cad_model.test_cad_model import cleanup
from is_production.cad_engineering.doctype.cad_surface.test_cad_surface import job_result
from is_production.cad_engineering.services import cad_mining_service, cad_model_service, cad_planning_service, cad_revision_service, cad_surface_service

IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Employee", "Company", "Branch", "Location", "Geo Project", "CAD Design Revision", "CAD Model", "CAD Processing Job", "CAD Entity", "CAD Design Standard", "CAD Design Validation", "Material Type", "Mining Block", "Geo Pit Layout", "Geo Pit Layout Block", "Geo Pit Layout Material Stack", "Geo Import Batch", "Geo Layout Generation Batch", "Mining Schedule Selection", "Mining Schedule Scenario", "Mine Schedule Scenario", "Mining Schedule Rule Set", "Mining Block Material Calculation Batch", "Project Status Setup", "CAD Design Publication", "CAD Survey Reconciliation", "CAD Surface"]
PROJECT_CODE = "TST-CADP5"
MATERIALS = {"Coal": {"density": 1.45, "status": "Mineable"}, "Overburden": {"density": 2.4, "status": "Waste"}}


def drop_planning():
	layouts = frappe.get_all("Geo Pit Layout", filters={"source_cad_model": ["like", "TST-CAD-%"]}, pluck="name")
	if layouts:
		blocks = frappe.get_all("Mining Block", filters={"source_pit_layout": ["in", layouts]}, pluck="name")
		if blocks:
			frappe.db.delete("Mining Block Material Summary", {"mining_block": ["in", blocks]})
		frappe.db.delete("Mining Block", {"source_pit_layout": ["in", layouts]})
		for s in frappe.get_all("Mining Schedule Selection", filters={"geo_pit_layout": ["in", layouts]}, pluck="name"):
			for scenario in frappe.get_all("Mining Schedule Scenario", filters={"mining_schedule_selection": s}, pluck="name"):
				frappe.delete_doc("Mining Schedule Scenario", scenario, force=True, ignore_permissions=True)  # with its rows
			frappe.delete_doc("Mining Schedule Selection", s, force=True, ignore_permissions=True)
		stacks = frappe.get_all("Geo Pit Layout Material Stack", filters={"geo_pit_layout": ["in", layouts]}, pluck="name")
		for s in stacks:
			frappe.delete_doc("Geo Pit Layout Material Stack", s, force=True, ignore_permissions=True)
		frappe.db.delete("Geo Pit Layout", {"name": ["in", layouts]})
	frappe.db.delete("CAD Reconciliation Block", {"mining_block": ["like", "CAD TST-CAD-%"]})
	for child in ("Mining Schedule Block", "Mining Schedule Period", "Mining Schedule Period Material"):  # left by earlier runs
		frappe.db.delete(child, {"parent": ["like", "TST P5-%"]})
	frappe.db.delete("CAD Design Publication", {"cad_model": ["like", "TST-CAD-%"]})
	frappe.db.delete("CAD Survey Reconciliation", {"cad_model": ["like", "TST-CAD-%"]})
	frappe.db.delete("Geo Project", {"project_code": PROJECT_CODE})
	frappe.db.commit()


def geo_project():
	status = frappe.get_all("Project Status Setup", pluck="name", limit=1)
	doc = frappe.get_doc({"doctype": "Geo Project", "project_code": PROJECT_CODE, "project_name": "CAD Phase 5 test", "crs_name": "EPSG:2053", "project_status": status[0] if status else None})
	doc.insert(ignore_permissions=True, ignore_links=True)
	return doc.name


class IntegrationTestCADPlanning(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		drop_planning()
		cleanup()

	@classmethod
	def tearDownClass(cls):
		drop_planning()
		cleanup()
		super().tearDownClass()

	def design(self, code):
		"""A pit (rectangular, 4 benches from 60 to 100 under ground at 105), blocks with materials, validated."""
		helper = phase4.IntegrationTestCADMiningDesign()
		std = phase4.make_standard()
		model, v, ground_ref = helper.model_with_ground(code, z=105)
		cad_mining_service.set_standard(model, std)
		pit = job_result(cad_mining_service.start(model, "pit", {"floor": "FLOOR", "stage": "S1", "top_elevation": 100, "ground": ground_ref, "corners": "mitre"}))
		v = helper.add_pit(model, v, pit)
		top = next(r for r in pit["rings"] if r["kind"] == "crest" and r["bench"] == 4)
		v = cad_model_service.save_edits(model, v, {"upserts": [{"entity_id": "LIMIT", "entity_type": "Polyline", "geometry": {"points": [[p[0], p[1], 100] for p in top["points"]], "closed": True}}]})["edit_version"]
		res = job_result(cad_mining_service.start(model, "blocks", {"boundary": "LIMIT", "upper": ground_ref, "lower": pit["surface"]["geometry_ref"], "min_volume": 0}))
		ups = [
			{"entity_id": f"BLK{n}", "entity_type": "Polyline", "geometry": {"points": b["polygon"], "closed": True}, "metadata": {"mining_block": {**{k: b[k] for k in ("block_id", "bench_bottom", "bench_top", "row", "col", "volume", "plan_area", "sequence")}, **res["common"], "material": "Coal" if b["bench_bottom"] < 80 else "Overburden"}}}
			for n, b in enumerate(res["blocks"])
		]
		v = cad_model_service.save_edits(model, v, {"upserts": ups})["edit_version"]
		self.assertNotEqual(cad_mining_service.validate(model)["status"], "Fail")
		return model, v, ground_ref, pit, res, top

	def approve(self, model, text):
		revision = cad_revision_service.submit_for_review(model, text)
		cad_revision_service.approve(revision.name, "ok")
		return revision.name

	def test_publish_sequence_republish_reconcile_compare(self):
		model, v, ground_ref, pit, res, top = self.design("P5")
		project = geo_project()
		working = cad_revision_service.working_revision(model)
		self.assertRaises(frappe.ValidationError, cad_planning_service.publish, model, working, project, MATERIALS)  # not approved
		rev1 = self.approve(model, "first design")

		# 1. publish: one Mining Block per CAD block, volumes and tonnes from the design, traceable
		pub = cad_planning_service.publish(model, rev1, project, MATERIALS)
		n = len(res["blocks"])
		self.assertEqual((pub["blocks_published"], pub["blocks_created"], pub["status"]), (n, n, "Published"))
		total = sum(b["volume"] for b in res["blocks"])
		self.assertAlmostEqual(pub["total_volume"], total, delta=1e-6 * total)
		tonnes = sum(b["volume"] * (1.45 if b["bench_bottom"] < 80 else 2.4) for b in res["blocks"])
		self.assertAlmostEqual(pub["total_tonnes"], tonnes, delta=1e-6 * tonnes)
		self.assertEqual(pub["geometry_checksum"], frappe.db.get_value("CAD Design Revision", rev1, "geometry_checksum"))
		self.assertTrue(pub["design_validation"] and pub["design_standard"])
		layout = frappe.get_doc("Geo Pit Layout", pub["geo_pit_layout"])
		self.assertEqual((layout.source_cad_model, layout.source_cad_revision, layout.geo_project), (model, rev1, project))
		blocks = frappe.get_all("Mining Block", filters={"cad_publication": pub["name"]}, fields=["name", "mining_block_code", "design_volume", "cad_entity_id", "cad_sequence", "material", "block_status", "cut_no", "bench_bottom"])
		self.assertEqual(len(blocks), n)
		self.assertEqual({b.cut_no for b in blocks if b.bench_bottom == 100}, {1})  # cut 1 = the top bench
		summaries = frappe.get_all("Mining Block Material Summary", filters={"material_stack": pub["material_stack"]}, fields=["material_seam", "material_status", "volume", "tonnes"])
		self.assertEqual(len(summaries), n)
		self.assertEqual({s.material_seam: s.material_status for s in summaries}, {"Coal": "Mineable", "Overburden": "Waste"})
		# 2. the CAD sequence is the selection's order
		selection = frappe.get_doc("Mining Schedule Selection", pub["schedule_selection"])
		by_name = {b.name: b for b in blocks}
		self.assertEqual([by_name[r.mining_block].cad_sequence for r in selection.blocks], sorted(b.cad_sequence for b in blocks))

		# 3. a revised design: one unscheduled block changes, one scheduled block changes, one block goes
		ids = sorted(blocks, key=lambda b: b.cad_sequence)
		change_free, change_scheduled, drop = ids[0], ids[1], ids[2]
		frappe.db.set_value("Mining Block", change_scheduled.name, "block_status", "Scheduled")
		rows = {r.entity_id: r for r in frappe.get_all("CAD Entity", filters={"cad_model": model, "entity_id": ["in", [change_free.cad_entity_id, change_scheduled.cad_entity_id]]}, fields=["entity_id", "geometry", "metadata"])}
		ups = []
		for eid, r in rows.items():
			meta = json.loads(r.metadata)
			meta["mining_block"]["volume"] += 1.0
			ups.append({"entity_id": eid, "entity_type": "Polyline", "geometry": json.loads(r.geometry), "metadata": meta})
		cad_model_service.save_edits(model, frappe.db.get_value("CAD Model", model, "edit_version"), {"upserts": ups, "deletes": [drop.cad_entity_id]})
		cad_mining_service.validate(model)
		rev2 = self.approve(model, "revised")
		pub2 = cad_planning_service.publish(model, rev2, project, MATERIALS, make_selection=False)
		self.assertEqual((pub2["blocks_published"], pub2["blocks_created"], pub2["blocks_updated"]), (n - 1, 0, 1))
		self.assertEqual(pub2["blocks_flagged"], 2)  # the scheduled block that changed, the block the design dropped
		self.assertEqual(frappe.db.get_value("CAD Design Publication", pub["name"], ["status", "superseded_by"]), ("Superseded", pub2["name"]))
		self.assertEqual(frappe.db.get_value("Mining Block", change_scheduled.name, "planning_status"), "Review")
		self.assertAlmostEqual(frappe.db.get_value("Mining Block", change_scheduled.name, "design_volume"), change_scheduled.design_volume)  # kept
		self.assertAlmostEqual(frappe.db.get_value("Mining Block", change_free.name, "design_volume"), change_free.design_volume + 1.0)
		self.assertEqual(frappe.db.get_value("Mining Block", drop.name, "planning_status"), "Review")

		# 4. revision comparison
		diff = cad_planning_service.compare(model, rev1, rev2)
		self.assertEqual(diff["counts"]["removed"], 1)
		self.assertEqual({r["entity_id"] for r in diff["changed"]} >= {change_free.cad_entity_id, change_scheduled.cad_entity_id}, True)
		self.assertEqual(diff["design"]["older"]["blocks"] - diff["design"]["newer"]["blocks"], 1)

		# 5. a survey flat at 95 under the whole pit: inside the blocks it removed exactly 10 m over the top crest's
		#    area (pre-mining 105); the balance mined − beyond design + remaining = planned holds for every block
		v = frappe.db.get_value("CAD Model", model, "edit_version")
		pts = [{"entity_id": f"SV{i}_{j}", "entity_type": "Point", "geometry": {"p": [phase4.X0 - 150 + 20 * i, phase4.Y0 - 150 + 20 * j, 95.0]}} for i in range(21) for j in range(21)]
		v = cad_model_service.save_edits(model, v, {"upserts": pts})["edit_version"]
		survey = job_result(cad_surface_service.start_surface(model, "build", {"points": [p["entity_id"] for p in pts]}))
		cad_model_service.save_edits(model, v, {"upserts": [{"entity_id": "SURVEY95", "entity_type": "Surface", "geometry": {"name": "Survey 95", "surface_type": "As-Built Surface", "source": survey["source"]}, "geometry_ref": survey["geometry_ref"]}]})
		# a schedule: the first block finished before the survey, the second half done by it
		first, second = sorted(frappe.get_all("Mining Block", filters={"cad_publication": pub2["name"]}, fields=["name", "design_volume", "cad_sequence"]), key=lambda b: b.cad_sequence)[:2]
		scenario = frappe.get_doc(
			{
				"doctype": "Mining Schedule Scenario",
				"scenario_name": "TST P5",
				"mining_schedule_selection": selection.name,
				"geo_project": project,
				"geo_pit_layout": layout.name,
				"period_type": "Weekly",
				"start_date": "2026-01-01",
				"scheduled_blocks": [
					{"period_no": 1, "mining_block": first.name, "planned_start_date": "2026-01-01", "planned_end_date": "2026-01-10", "total_volume": first.design_volume},
					{"period_no": 2, "mining_block": second.name, "planned_start_date": "2026-01-11", "planned_end_date": "2026-01-20", "total_volume": second.design_volume},
				],
			}
		).insert(ignore_permissions=True)
		rec = job_result(cad_mining_service.start(model, "reconcile", {"survey": survey["geometry_ref"], "survey_date": "2026-01-15", "scenario": scenario.name}))
		self.assertTrue(rec["checks"]["balance_holds"])
		# two blocks were given +1 m³ in the revised design without changing any surface: the reconciliation catches it
		self.assertFalse(rec["valid"])
		self.assertAlmostEqual(rec["checks"]["max_design_difference"], 1.0, delta=1e-6)
		crest_area = (top["points"][1][0] - top["points"][0][0]) * (top["points"][2][1] - top["points"][1][1])
		dropped = frappe.db.get_value("Mining Block", drop.name, "design_volume")
		# the dropped block is not in the publication: its share (plan area × its bench's overlap with 95–105) is not reconciled
		area, bottom = frappe.db.get_value("Mining Block", drop.name, ["area", "bench_bottom"])
		dropped_share = area * max(0.0, min(bottom + 10, 105) - max(bottom, 95))
		self.assertAlmostEqual(rec["totals"]["mined"], 10 * abs(crest_area) - dropped_share, delta=1e-3)
		t = rec["totals"]
		self.assertAlmostEqual(t["mined"] - t["over"] + t["remaining"], t["planned"], delta=1e-3)
		self.assertAlmostEqual(t["scheduled"], first.design_volume + second.design_volume * 5 / 10, delta=1e-6)
		doc = frappe.get_doc("CAD Survey Reconciliation", rec["name"])
		self.assertEqual(len(doc.blocks), n - 1)
		self.assertTrue(dropped > 0)
		statuses = frappe.get_all("Mining Block", filters={"cad_publication": pub2["name"]}, fields=["block_status", "percent_mined", "last_reconciliation"])
		self.assertTrue(all(s.last_reconciliation == rec["name"] for s in statuses))
		self.assertTrue(any(s.block_status == "Complete" for s in statuses))  # the top benches lie wholly above 95

		# 6. the planning overlay shows the schedule period and % mined per CAD block
		ov = cad_planning_service.overlay(model, scenario.name)
		first_entity = frappe.db.get_value("Mining Block", first.name, "cad_entity_id")
		self.assertEqual(ov["blocks"][first_entity]["period_no"], 1)

