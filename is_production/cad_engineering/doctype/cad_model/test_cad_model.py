# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import json
import math

import frappe
from frappe.tests import IntegrationTestCase

from is_production.cad_engineering.services import (
	cad_geometry,
	cad_health_service,
	cad_job_service,
	cad_model_service,
	cad_revision_service,
	cad_storage_service,
	cad_worker_service,
	cad_workstation_service,
)

IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Employee", "Company", "Branch", "Location", "Geo Project", "CAD Design Revision", "CAD Model", "CAD Processing Job"]
PREFIX = "TST-CAD-"
# Lo29-style survey coordinates: large numbers that float32 would round to ~0.25 m
X0, Y0, Z0 = 2500000.0, -2900000.0, 1500.0


def cleanup():
	"""Jobs commit (the worker runs in the background), so test models are removed explicitly — by model
	name pattern, so rows left behind by an interrupted run are removed too."""
	models = frappe.get_all("CAD Model", filters={"name": ["like", f"{PREFIX}%"]}, pluck="name")
	for doctype in ("CAD Entity", "CAD Layer", "CAD Block", "CAD Import Export", "CAD Design Revision", "CAD Processing Job"):
		frappe.db.delete(doctype, {"cad_model": ["like", f"{PREFIX}%"]})
	frappe.db.delete("CAD Model", {"name": ["like", f"{PREFIX}%"]})
	store = cad_storage_service.provider()
	for model in models:
		store.delete_prefix("models", model)
		store.delete_prefix("mesh", model)
	frappe.db.commit()


def make_user(email, role):
	if not frappe.db.exists("User", email):
		frappe.get_doc({"doctype": "User", "email": email, "first_name": role, "send_welcome_email": 0, "roles": [{"role": role}]}).insert(ignore_permissions=True)
	return email


def make_model(code, **values):
	return cad_model_service.create_model({"model_name": f"Test {code}", "model_code": PREFIX + code, "crs": "EPSG:2053", **values})


def line(entity_id, p0, p1, layer=None):
	return {"entity_id": entity_id, "entity_type": "Line", "layer": layer, "geometry": {"p0": p0, "p1": p1}}


class IntegrationTestCADModel(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cleanup()

	@classmethod
	def tearDownClass(cls):
		cleanup()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.flags.cad_batch = False

	# -- geometry -------------------------------------------------------------

	def test_geometry_is_normalised_and_bad_geometry_refused(self):
		self.assertEqual(cad_geometry.normalise("Point", {"p": [1, 2]}), {"p": [1.0, 2.0, 0.0]})
		with self.assertRaises(frappe.ValidationError):
			cad_geometry.normalise("Line", {"p0": [0, 0, float("nan")], "p1": [1, 1, 1]})
		with self.assertRaises(frappe.ValidationError):
			cad_geometry.normalise("Circle", {"center": [0, 0, 0], "radius": -1})
		with self.assertRaises(frappe.ValidationError):
			cad_geometry.normalise("Polyline", {"points": [[0, 0, 0]]})
		with self.assertRaises(frappe.ValidationError):
			cad_geometry.normalise("Spline", {})

	def test_plane_basis_matches_occt_for_the_z_axis(self):
		x, y = cad_geometry.plane_basis([0.0, 0.0, 1.0])
		self.assertEqual(x, [1.0, 0.0, 0.0])
		self.assertEqual(y, [0.0, 1.0, 0.0])
		box = cad_geometry.extent("Arc", cad_geometry.normalise("Arc", {"center": [0, 0, 0], "radius": 2, "start_angle": 0, "end_angle": math.pi / 2}))
		self.assertAlmostEqual(box[0], 0, places=3)
		self.assertAlmostEqual(box[3], 2, places=3)
		self.assertAlmostEqual(box[4], 2, places=3)

	# -- storage --------------------------------------------------------------

	def test_storage_keys_cannot_leave_their_root(self):
		store = cad_storage_service.provider()
		for key in ("models/../../site_config.json", "models/X/../../etc/passwd", "/etc/passwd", "other/X/y"):
			with self.assertRaises(frappe.ValidationError):
				store.path(key)
		with self.assertRaises(frappe.ValidationError):
			cad_storage_service.check_model_code("../evil")
		with self.assertRaises(frappe.ValidationError):
			cad_storage_service.site_directory("/etc", "private/cad/models")

	def test_snapshots_are_write_once(self):
		model = make_model("SNAP").name
		cad_storage_service.write_snapshot(model, f"{model}-R999", b"{}")
		with self.assertRaises(frappe.ValidationError):
			cad_storage_service.write_snapshot(model, f"{model}-R999", b"{}")

	# -- models and edits -----------------------------------------------------

	def test_new_model_has_layer_zero_and_a_working_revision(self):
		model = make_model("NEW")
		self.assertEqual(model.working_revision, f"{model.name}-R001")
		self.assertEqual(frappe.db.get_value("CAD Design Revision", model.working_revision, "revision_status"), "Working")
		self.assertTrue(frappe.db.exists("CAD Layer", {"cad_model": model.name, "layer_name": "0"}))
		self.assertEqual(model.linear_units, "Metres")

	def test_save_edits_indexes_validates_and_measures_with_occt(self):
		model = make_model("EDIT").name
		result = cad_model_service.save_edits(
			model,
			0,
			{
				"layers": [{"layer_name": "Crest", "color": "#ff0000"}],
				"upserts": [
					line("L1", [X0, Y0, Z0], [X0 + 3, Y0 + 4, Z0], layer="Crest"),
					{"entity_id": "P1", "entity_type": "Polyline", "geometry": {"points": [[X0, Y0], [X0 + 10, Y0], [X0 + 10, Y0 + 10], [X0, Y0 + 10]], "closed": True}},
					{"entity_id": "A1", "entity_type": "Arc", "geometry": {"center": [X0, Y0, Z0], "radius": 2, "start_angle": 0, "end_angle": math.pi / 2}},
					{"entity_id": "Z1", "entity_type": "Line", "geometry": {"p0": [X0, Y0, Z0], "p1": [X0, Y0, Z0]}},
				],
			},
		)
		self.assertEqual(result["edit_version"], 1)
		self.assertTrue(result["validation_job"])
		self.assertEqual(frappe.db.get_value("CAD Processing Job", result["validation_job"], "status"), "Completed")

		row = frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "L1"}, ["length", "validation_status", "min_x", "max_y", "revision"], as_dict=True)
		self.assertEqual(row.validation_status, "Valid")
		self.assertAlmostEqual(row.length, 5.0, places=9)
		self.assertEqual(row.min_x, X0)  # survey coordinates keep full precision
		self.assertEqual(row.max_y, Y0 + 4)
		self.assertEqual(row.revision, f"{model}-R001")
		poly = frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "P1"}, ["length", "area"], as_dict=True)
		self.assertAlmostEqual(poly.length, 40.0, places=6)
		self.assertAlmostEqual(poly.area, 100.0, places=6)
		self.assertAlmostEqual(frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "A1"}, "length"), math.pi, places=9)
		self.assertEqual(frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "Z1"}, "validation_status"), "Invalid")

		doc = frappe.get_doc("CAD Model", model)
		self.assertEqual(doc.entity_count, 4)
		self.assertEqual((doc.local_origin_x, doc.local_origin_y), (X0, Y0))
		layer = frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "L1"}, "cad_layer")
		self.assertEqual(frappe.db.get_value("CAD Layer", layer, "layer_name"), "Crest")

		in_view = cad_model_service.get_entities(model, [X0 + 2.5, Y0 + 3.5, X0 + 2.9, Y0 + 3.9])["entities"]
		self.assertIn("L1", [e["entity_id"] for e in in_view])
		self.assertNotIn("A1", [e["entity_id"] for e in in_view])

	def test_tilted_arc_index_box_contains_occts_box(self):
		model = make_model("ARC").name
		normal = [1.0, 2.0, 2.0]
		result = cad_model_service.save_edits(
			model, 0, {"upserts": [{"entity_id": "T1", "entity_type": "Arc", "geometry": {"center": [X0, Y0, Z0], "radius": 30, "start_angle": 0.3, "end_angle": 2.1, "normal": normal}}]}
		)
		ours = result["entities"][0]["bbox"]
		workspace = cad_worker_service.job_workspace("test-arc")
		path = f"{workspace}/in.json"
		with open(path, "w") as f:
			json.dump([{"entity_id": "T1", "entity_type": "Arc", "geometry": json.loads(frappe.db.get_value("CAD Entity", {"cad_model": model}, "geometry"))}], f)
		occt = cad_worker_service.run({"command": "validate", "workspace": workspace, "input": path})["entities"][0]
		for k in range(3):
			self.assertLessEqual(ours[k], occt["bbox"][k] + 1e-6)
			self.assertGreaterEqual(ours[k + 3], occt["bbox"][k + 3] - 1e-6)
			# OCCT's box has a small tolerance gap; ours is padded by one sagitta: both tight
			self.assertAlmostEqual(ours[k], occt["bbox"][k], delta=0.01)
		self.assertAlmostEqual(occt["measures"]["length"], 30 * 1.8, places=9)

	def test_stale_edit_version_is_refused(self):
		model = make_model("CONFLICT").name
		cad_model_service.save_edits(model, 0, {"upserts": [line("L1", [0, 0, 0], [1, 0, 0])]})
		with self.assertRaises(cad_model_service.EditConflict):
			cad_model_service.save_edits(model, 0, {"upserts": [line("L1", [0, 0, 0], [2, 0, 0])]})
		self.assertEqual(json.loads(frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "L1"}, "geometry"))["p1"], [1.0, 0.0, 0.0])

	def test_failed_save_changes_nothing(self):
		model = make_model("ATOMIC").name
		with self.assertRaises(frappe.ValidationError):
			cad_model_service.save_edits(model, 0, {"upserts": [line("L1", [0, 0, 0], [1, 0, 0]), {"entity_id": "bad", "entity_type": "Line", "geometry": {"p0": [0, 0]}}]})
		self.assertFalse(frappe.db.exists("CAD Entity", {"cad_model": model}))
		self.assertEqual(frappe.db.get_value("CAD Model", model, "edit_version"), 0)

	def test_locked_layer_refuses_edits(self):
		model = make_model("LOCK").name
		version = cad_model_service.save_edits(model, 0, {"layers": [{"layer_name": "Survey", "locked": 1}]})["edit_version"]
		with self.assertRaises(frappe.ValidationError):
			cad_model_service.save_edits(model, version, {"upserts": [line("L1", [0, 0, 0], [1, 0, 0], layer="Survey")]})

	def test_unchanged_entities_are_not_rewritten(self):
		model = make_model("SAME").name
		first = cad_model_service.save_edits(model, 0, {"upserts": [line("L1", [0, 0, 0], [1, 0, 0])]})
		again = cad_model_service.save_edits(model, first["edit_version"], {"upserts": [line("L1", [0, 0, 0], [1, 0, 0])]})
		self.assertEqual(again["entities"][0]["entity_version"], 1)
		self.assertIsNone(again["validation_job"])

	# -- solids (CAD worker) --------------------------------------------------

	def build(self, model, items):
		job = cad_job_service.start_job("Build Geometry", model, None, {"items": items})
		job = cad_job_service.get_job(job["name"])
		self.assertEqual(job["status"], "Completed", frappe.db.get_value("CAD Processing Job", job["name"], "log"))
		return job["results"]["items"]

	def test_solids_are_built_stored_and_saved_by_reference(self):
		model = make_model("SOLID").name
		built = self.build(
			model,
			[
				{"key": "b", "op": "box", "origin": [X0, Y0, Z0], "size": [10, 20, 5]},
				{"key": "bad", "op": "cylinder", "base": [X0, Y0, Z0], "radius": 0, "height": 1},
			],
		)
		box, bad = built
		self.assertTrue(box["valid"])
		self.assertAlmostEqual(box["volume"], 1000.0, places=6)
		self.assertFalse(bad["valid"])
		self.assertNotIn("geometry_ref", bad)
		self.assertTrue(cad_storage_service.blob_exists(model, box["geometry_ref"]))
		self.assertTrue(cad_storage_service.provider().exists(cad_storage_service.mesh_key(model, box["geometry_ref"])))

		result = cad_model_service.save_edits(
			model, 0, {"upserts": [{"entity_id": "S1", "entity_type": "Solid", "geometry_ref": box["geometry_ref"], "geometry": {"source": box["source"]}}]}
		)
		self.assertEqual(result["entities"][0]["validation_status"], "Valid")
		self.assertEqual(result["entities"][0]["bbox"], [X0, Y0, Z0, X0 + 10, Y0 + 20, Z0 + 5])
		self.assertAlmostEqual(frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "S1"}, "volume"), 1000.0, places=6)

		moved = self.build(model, [{"key": "m", "op": "transform", "geometry_ref": box["geometry_ref"], "translate": [1, 0, 0]}])[0]
		self.assertEqual(moved["bbox"][0], X0 + 1)
		self.assertNotEqual(moved["geometry_ref"], box["geometry_ref"])

		response = cad_workstation_service.get_mesh(model, box["geometry_ref"])
		self.assertEqual(response.status_code, 200)
		self.assertTrue(response.get_data().startswith(b"ISPCADM1"))

	def test_solid_with_an_unknown_geometry_file_is_refused(self):
		model = make_model("FAKE").name
		with self.assertRaises(frappe.ValidationError):
			cad_model_service.save_edits(model, 0, {"upserts": [{"entity_id": "S1", "entity_type": "Solid", "geometry_ref": "0" * 64, "geometry": {"source": {"op": "box"}}}]})

	# -- revisions ------------------------------------------------------------

	def test_revision_review_approval_and_immutability(self):
		model = make_model("REV").name
		version = cad_model_service.save_edits(model, 0, {"upserts": [line("L1", [X0, Y0, Z0], [X0 + 1, Y0, Z0])]})["edit_version"]

		r1 = cad_revision_service.submit_for_review(model, "Pit shell v1")
		self.assertEqual(r1.revision_status, "In Review")
		self.assertEqual(len(r1.geometry_checksum), 64)
		self.assertEqual(frappe.db.get_value("CAD Model", model, "approval_status"), "In Review")
		with self.assertRaises(frappe.ValidationError):  # locked while in review
			cad_model_service.save_edits(model, version, {"upserts": [line("L2", [0, 0, 0], [1, 1, 0])]})
		with self.assertRaises(frappe.ValidationError):
			cad_revision_service.reject(r1.name, "")

		cad_revision_service.approve(r1.name, "OK")
		self.assertEqual(frappe.db.get_value("CAD Model", model, "current_revision"), r1.name)
		approved = frappe.get_doc("CAD Design Revision", r1.name)
		approved.revision_description = "changed"
		with self.assertRaises(frappe.ValidationError):
			approved.save()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("CAD Design Revision", r1.name)

		# editing after approval opens R002, parent R001; the approved snapshot is untouched
		result = cad_model_service.save_edits(model, version, {"upserts": [line("L1", [X0, Y0, Z0], [X0 + 2, Y0, Z0])]})
		self.assertEqual(result["working_revision"], f"{model}-R002")
		self.assertEqual(frappe.db.get_value("CAD Design Revision", f"{model}-R002", "parent_revision"), r1.name)
		snapshot = cad_revision_service.load_snapshot(r1.name)
		self.assertEqual(snapshot["content"]["entities"][0]["geometry"]["p1"][0], X0 + 1)

		# restore R001 into the working state
		cad_revision_service.restore(r1.name)
		self.assertEqual(json.loads(frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "L1"}, "geometry"))["p1"][0], X0 + 1)
		self.assertEqual(cad_revision_service.checksum(cad_revision_service.current_content(model)), r1.geometry_checksum)

		# the next approval supersedes R001
		r2 = cad_revision_service.submit_for_review(model)
		cad_revision_service.approve(r2.name)
		self.assertEqual(frappe.db.get_value("CAD Design Revision", r1.name, "revision_status"), "Superseded")
		with self.assertRaises(frappe.ValidationError):  # models with approved revisions are archived, not deleted
			frappe.delete_doc("CAD Model", model)

	def test_tampered_snapshot_is_detected(self):
		model = make_model("TAMPER").name
		cad_model_service.save_edits(model, 0, {"upserts": [line("L1", [0, 0, 0], [1, 0, 0])]})
		revision = cad_revision_service.submit_for_review(model)
		payload = json.loads(cad_storage_service.read_snapshot(revision.geometry_snapshot))
		payload["content"]["entities"][0]["geometry"]["p1"] = [9.0, 0.0, 0.0]
		import gzip

		path = cad_storage_service.provider().path(revision.geometry_snapshot)
		with open(path, "wb") as f:
			f.write(gzip.compress(json.dumps(payload).encode()))
		with self.assertRaises(frappe.ValidationError):
			cad_revision_service.approve(revision.name)

	# -- permissions ----------------------------------------------------------

	def test_production_user_edits_but_cannot_approve(self):
		model = make_model("PERM").name
		cad_model_service.save_edits(model, 0, {"upserts": [line("L1", [0, 0, 0], [1, 0, 0])]})
		revision = cad_revision_service.submit_for_review(model).name
		frappe.set_user(make_user("cad.test.user@example.com", "Production User"))
		self.assertTrue(frappe.has_permission("CAD Model", "write", doc=model))
		with self.assertRaises(frappe.PermissionError):
			cad_revision_service.approve(revision)
		self.assertFalse(cad_workstation_service.get_workstation_config()["can_approve"])

	def test_branch_restriction_hides_models_and_their_entities(self):
		branch = frappe.get_all("Branch", limit=2, pluck="name")
		if len(branch) < 2:
			self.skipTest("needs two Branch records")
		allowed = make_model("BR-A", branch=branch[0]).name
		hidden = make_model("BR-B", branch=branch[1]).name
		cad_model_service.save_edits(hidden, 0, {"upserts": [line("H1", [0, 0, 0], [1, 0, 0])]})
		user = make_user("cad.test.branch@example.com", "Production User")
		permission = frappe.get_doc({"doctype": "User Permission", "user": user, "allow": "Branch", "for_value": branch[0]}).insert(ignore_permissions=True)
		try:
			frappe.set_user(user)
			models = frappe.get_list("CAD Model", filters={"name": ["like", f"{PREFIX}BR-%"]}, pluck="name")
			self.assertIn(allowed, models)
			self.assertNotIn(hidden, models)
			self.assertFalse(frappe.get_list("CAD Entity", filters={"cad_model": hidden}))
			with self.assertRaises(frappe.PermissionError):
				cad_workstation_service.get_model(hidden)
		finally:
			frappe.set_user("Administrator")
			permission.delete(ignore_permissions=True)

	def test_guest_cannot_use_the_api(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			cad_workstation_service.get_workstation_config()

	# -- health -----------------------------------------------------------------

	def test_health_check_reports_occt_and_a_missing_worker(self):
		self.assertIn("error", cad_worker_service.run({"command": "health"}, python="/nonexistent/python3"))
		cad_health_service.run_health_check()
		settings = frappe.get_doc("CAD Settings")
		self.assertTrue(settings.occt_version.startswith("7."))
		self.assertEqual(settings.worker_health_status, "Available")
