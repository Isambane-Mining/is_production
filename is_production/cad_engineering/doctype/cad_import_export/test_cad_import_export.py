# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import json
import math
import os
from collections import Counter

import frappe
from frappe.tests import IntegrationTestCase

from is_production.cad_engineering.doctype.cad_model.test_cad_model import PREFIX, cleanup, line, make_model
from is_production.cad_engineering.services import (
	cad_exchange_service,
	cad_geometry,
	cad_job_service,
	cad_model_service,
	cad_revision_service,
)

IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Employee", "Company", "Branch", "Location", "Geo Project", "CAD Design Revision", "CAD Model", "CAD Processing Job", "CAD Design Standard", "CAD Design Validation", "Material Type"]
FIXTURE = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "sample_r2018.dxf")


def upload(content, name="sample.dxf"):
	# an older unattached upload of the same name (an interrupted run) would make Frappe rename this one
	for old in frappe.get_all("File", filters={"file_name": name, "is_private": 1, "attached_to_name": ["is", "not set"]}, pluck="name"):
		frappe.delete_doc("File", old, force=True, ignore_permissions=True)
	return frappe.get_doc({"doctype": "File", "file_name": name, "is_private": 1, "content": content}).insert(ignore_permissions=True)


def import_dxf(model, content=None, **options):
	with open(FIXTURE, "rb") as f:
		file = upload(content or f.read())
	io = cad_exchange_service.start_import(model, file.file_url)
	io = cad_exchange_service.get_exchange(io["name"])
	assert io["processing_status"] == "Ready to Commit", (io, frappe.db.get_value("CAD Processing Job", io["processing_job"], "log"))
	cad_exchange_service.commit_import(io["name"], {"source_units": "Metres", **options})
	return cad_exchange_service.get_exchange(io["name"])


def entities(model):
	rows = frappe.get_all("CAD Entity", filters={"cad_model": model}, fields=["entity_id", "entity_type", "geometry", "validation_status", "validation_message", "length", "area", "cad_layer", "color", "source_id"])
	for row in rows:
		row.geometry = json.loads(row.geometry)
	return rows


def rounded(value, places=6):
	if isinstance(value, float):
		return round(value, places) + 0.0
	if isinstance(value, list):
		return [rounded(v, places) for v in value]
	if isinstance(value, dict):
		return {k: rounded(v, places) for k, v in sorted(value.items())}
	return value


class IntegrationTestCADImportExport(IntegrationTestCase):
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

	# -- geometry ------------------------------------------------------------------

	def test_new_entity_types_normalise_and_index(self):
		semicircle = cad_geometry.normalise("Polyline", {"points": [[0, 0], [2, 0]], "bulges": [1]})
		box = cad_geometry.extent("Polyline", semicircle)
		self.assertAlmostEqual(box[1], -1.0, places=6)  # positive bulge = counter-clockwise: the arc swings south of an eastward chord
		self.assertAlmostEqual(box[4], 0.0, places=9)
		with self.assertRaises(frappe.ValidationError):
			cad_geometry.normalise("Polyline", {"points": [[0, 0, 0], [2, 0, 5]], "bulges": [1]})  # an arc segment must be horizontal
		ellipse = cad_geometry.normalise("Ellipse", {"center": [0, 0], "major_axis": [3, 0, 0], "ratio": 0.5})
		self.assertEqual(cad_geometry.extent("Ellipse", ellipse)[3] > 3 - 1e-9, True)
		with self.assertRaises(frappe.ValidationError):
			cad_geometry.normalise("Spline", {"degree": 3, "control_points": [[0, 0], [1, 1], [2, 0], [3, 1]], "knots": [0, 1, 2]})
		dim = cad_geometry.normalise("Dimension", {"kind": "angular", "center": [0, 0], "p1": [1, 0], "p2": [0, 1], "location": [1, 1]})
		self.assertAlmostEqual(cad_geometry.dimension_measurement(dim), 90.0)
		with self.assertRaises(frappe.ValidationError):
			cad_geometry.normalise("Block Reference", {"block": "NOPE", "insert": [0, 0]}, lambda name: False)

	def test_plan_orientation_follows_the_crs(self):
		lo = make_model("ORIENT-LO", crs="EPSG:2053")
		utm = make_model("ORIENT-UTM", crs="EPSG:32735")
		self.assertTrue(cad_model_service.plan_rotated(lo))
		self.assertFalse(cad_model_service.plan_rotated(utm))
		lo.plan_orientation = "X East, Y North"
		self.assertFalse(cad_model_service.plan_rotated(lo))

	# -- blocks --------------------------------------------------------------------

	def test_blocks_nest_redefine_and_protect_their_references(self):
		model = make_model("BLOCKS").name
		marker = {"block_name": "MK", "base_point": [0, 0, 0], "entities": [{"entity_type": "Circle", "geometry": {"center": [0, 0, 0], "radius": 1}}]}
		beacon = {"block_name": "BC", "base_point": [0, 0, 0], "entities": [{"entity_type": "Block Reference", "geometry": {"block": "MK", "insert": [5, 0, 0]}}]}
		ref = {"entity_id": "R1", "entity_type": "Block Reference", "geometry": {"block": "BC", "insert": [100, 100, 0], "scale": [2, 2, 1], "rotation": math.pi / 2}}
		v = cad_model_service.save_edits(model, 0, {"blocks": [marker, beacon], "upserts": [ref]})["edit_version"]
		box = frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "R1"}, ["min_x", "max_x", "min_y", "max_y"], as_dict=True)
		# MK circle (r 1) at (5, 0) in BC, scaled ×2 and turned 90°: centre (100, 110), radius 2
		self.assertAlmostEqual(box.min_x, 98, places=6)
		self.assertAlmostEqual(box.max_y, 112, places=6)

		with self.assertRaises(frappe.ValidationError):  # MK may not contain BC (which contains MK)
			cad_model_service.save_edits(model, v, {"blocks": [{**marker, "entities": [{"entity_type": "Block Reference", "geometry": {"block": "BC", "insert": [0, 0, 0]}}]}]})
		with self.assertRaises(frappe.ValidationError):
			cad_model_service.save_edits(model, v, {"deleted_blocks": ["BC"]})

		# redefining MK (radius 3) moves the reference's box, through BC
		v = cad_model_service.save_edits(model, v, {"blocks": [{**marker, "entities": [{"entity_type": "Circle", "geometry": {"center": [0, 0, 0], "radius": 3}}]}]})["edit_version"]
		self.assertAlmostEqual(frappe.db.get_value("CAD Entity", {"cad_model": model, "entity_id": "R1"}, "max_y"), 116, places=6)

		# blocks travel with revisions
		revision = cad_revision_service.submit_for_review(model)
		self.assertEqual(len(cad_revision_service.load_snapshot(revision.name)["content"]["blocks"]), 2)
		cad_revision_service.reject(revision.name, "test")
		frappe.db.delete("CAD Block", {"cad_model": model, "block_name": "MK"})
		cad_revision_service.restore(revision.name)
		self.assertTrue(frappe.db.exists("CAD Block", {"cad_model": model, "block_name": "MK"}))

	def test_template_copies_layers_and_blocks(self):
		template = make_model("TPL").name
		cad_model_service.save_edits(template, 0, {"layers": [{"layer_name": "CREST", "color": "#ff0000"}], "blocks": [{"block_name": "NORTH", "entities": [{"entity_type": "Line", "geometry": {"p0": [0, 0], "p1": [0, 5]}}]}]})
		model = make_model("FROM-TPL", drawing_template=template).name
		self.assertEqual(frappe.db.get_value("CAD Layer", {"cad_model": model, "layer_name": "CREST"}, "color"), "#ff0000")
		self.assertTrue(frappe.db.exists("CAD Block", {"cad_model": model, "block_name": "NORTH"}))

	# -- curve operations --------------------------------------------------------

	def test_offset_with_open_cascade(self):
		model = make_model("OFFSET").name
		job = cad_job_service.start_job(
			"Curve Operation",
			model,
			None,
			{"operation": "offset", "items": [{"key": "sq", "points": [[0, 0, 1500], [10, 0, 1500], [10, 10, 1500], [0, 10, 1500]], "closed": True, "distance": -2}, {"key": "bad", "points": [[0, 0, 0], [1, 0, 5]], "distance": 1}]},
		)
		result = cad_job_service.get_job(job["name"])["results"]["items"]
		square = result[0]["polylines"][0]
		self.assertTrue(square["closed"])
		self.assertEqual(sorted(tuple(p) for p in square["points"]), [(2.0, 2.0, 1500.0), (2.0, 8.0, 1500.0), (8.0, 2.0, 1500.0), (8.0, 8.0, 1500.0)])
		self.assertIn("horizontal", result[1]["error"])

	# -- DXF -----------------------------------------------------------------------

	def test_dxf_import_report_commit_and_validation(self):
		model = make_model("DXF-IN").name
		io = import_dxf(model)
		self.assertEqual(io["processing_status"], "Completed", io)
		report = io["report"]
		self.assertEqual(report["units_name"], "Metres")
		self.assertEqual(report["unsupported"], {})
		self.assertEqual([s["name"] for s in report["surfaces"]], ["SURVEY"])  # the MESH entity: a 2.5D face set
		self.assertEqual(report["invalid_count"], 1)  # the zero-length line
		self.assertEqual(report["duplicates"], 1)
		rows = entities(model)
		self.assertEqual(len(rows), report["entity_count"] - 1)  # duplicate skipped (the surface is included)
		types = Counter(r.entity_type for r in rows)
		self.assertEqual(types["Block Reference"], 3)
		self.assertEqual(types["Dimension"], 5)
		self.assertTrue(frappe.db.exists("CAD Block", {"cad_model": model, "block_name": "BEACON"}))
		self.assertEqual(frappe.db.get_value("CAD Layer", {"cad_model": model, "layer_name": "TOE"}, "color"), "#14a05a")
		self.assertTrue(frappe.db.get_value("CAD Layer", {"cad_model": model, "layer_name": "SURVEY"}, "locked"))
		self.assertFalse(frappe.db.get_value("CAD Layer", {"cad_model": model, "layer_name": "HIDDEN_LAYER"}, "visible"))

		circle = next(r for r in rows if r.entity_type == "Circle")
		self.assertEqual(circle.validation_status, "Valid")
		self.assertAlmostEqual(circle.length, 2 * math.pi * 12.5, places=8)
		mirrored = next(r for r in rows if r.entity_type == "Arc" and r.color == "#fa8000")
		self.assertEqual(mirrored.geometry["normal"], [0.0, 0.0, 1.0])
		self.assertAlmostEqual(mirrored.geometry["start_angle"], math.pi / 2)
		self.assertAlmostEqual(mirrored.geometry["end_angle"], math.pi)
		self.assertAlmostEqual(mirrored.length, 8 * math.pi / 2, places=8)
		bulged = next(r for r in rows if r.entity_type == "Polyline" and r.geometry.get("closed") and r.geometry.get("bulges"))
		self.assertAlmostEqual(bulged.area, 40 * 40 + math.pi * 20 * 20 / 2, places=6)  # square + semicircle bulge
		invalid = [r for r in rows if r.validation_status == "Invalid"]
		self.assertFalse(invalid, [(r.entity_type, r.validation_message) for r in invalid])
		history = json.loads(frappe.db.get_value("CAD Model", model, "transformation_history"))
		self.assertEqual(history[-1]["entities"], len(rows))

	def test_dxf_round_trip_keeps_geometry(self):
		source = make_model("DXF-RT-A").name
		import_dxf(source)
		io = cad_exchange_service.start_export(source, {"version": "R2018"})
		io = cad_exchange_service.get_exchange(io["name"])
		self.assertEqual(io["processing_status"], "Completed", frappe.db.get_value("CAD Processing Job", io["processing_job"], "log"))
		with open(frappe.get_doc("File", {"file_url": io["result_file"]}).get_full_path(), "rb") as f:
			exported = f.read()
		target = make_model("DXF-RT-B").name
		import_dxf(target, exported)

		def signature(model):
			out = Counter()
			for r in entities(model):
				if r.entity_type in ("Surface", "Mesh"):  # their source (file name) differs: compare name and shape
					out[(r.entity_type, r.geometry["name"], round(r.area, 6))] += 1
				elif r.entity_type == "Dimension":  # exported dimensions are re-rendered by ezdxf: compare what they measure
					out[("Dimension", round(cad_geometry.dimension_measurement(r.geometry), 6))] += 1
				else:
					out[(r.entity_type, json.dumps(rounded(r.geometry), sort_keys=True))] += 1
			return out

		a, b = signature(source), signature(target)
		self.assertEqual(a - b, Counter(), "lost or changed on the round trip")
		self.assertEqual(b - a, Counter(), "added on the round trip")
		blocks = lambda m: {r.block_name: json.dumps(rounded(json.loads(r.entities)), sort_keys=True) for r in frappe.get_all("CAD Block", filters={"cad_model": m}, fields=["block_name", "entities"])}
		self.assertEqual(blocks(source), blocks(target))

	def test_line_weight_and_fill_survive_save_and_dxf(self):
		model = make_model("DXF-FILL").name
		square = [[0, 0, 5], [10, 0, 5], [10, 10, 5], [0, 10, 5]]
		cad_model_service.save_edits(
			model,
			0,
			{
				"upserts": [
					{"entity_id": "SQ", "entity_type": "Polyline", "line_weight": 0.7, "color": "#ff8800", "geometry": {"points": square, "closed": True, "fill": True}},
					{"entity_id": "CI", "entity_type": "Circle", "geometry": {"center": [20, 5, 5], "radius": 3, "fill": True}},
					{"entity_id": "OPEN", "entity_type": "Polyline", "geometry": {"points": square, "closed": False, "fill": True}},  # an open polyline is never filled
				]
			},
		)
		rows = {r.entity_id: r for r in frappe.get_all("CAD Entity", filters={"cad_model": model}, fields=["entity_id", "geometry", "line_weight"])}
		self.assertEqual(rows["SQ"].line_weight, 0.7)
		self.assertTrue(json.loads(rows["SQ"].geometry)["fill"])
		self.assertTrue(json.loads(rows["CI"].geometry)["fill"])
		self.assertNotIn("fill", json.loads(rows["OPEN"].geometry))
		io = cad_exchange_service.start_export(model, {"version": "R2018"})
		io = cad_exchange_service.get_exchange(io["name"])
		self.assertEqual(io["processing_status"], "Completed", io)
		with open(frappe.get_doc("File", {"file_url": io["result_file"]}).get_full_path(), "rb") as f:
			exported = f.read()
		import io as _io

		import ezdxf

		doc = ezdxf.read(_io.StringIO(exported.decode("utf-8", "replace")))
		msp = doc.modelspace()
		hatches = msp.query("HATCH")
		self.assertEqual(len(hatches), 2)  # the square and the circle
		self.assertTrue(all(h.dxf.solid_fill for h in hatches))
		self.assertEqual([e.dxf.lineweight for e in msp.query("LWPOLYLINE") if e.closed], [70])
		target = make_model("DXF-FILL-B").name
		import_dxf(target, exported)
		weights = sorted(r.line_weight for r in frappe.get_all("CAD Entity", filters={"cad_model": target, "entity_type": "Polyline"}, fields=["line_weight"]))
		self.assertIn(0.7, weights)

	def test_import_units_axes_and_review_lock(self):
		model = make_model("DXF-MM").name
		import_dxf(model, source_units="Millimetres", axis_mapping="Negate X and Y")
		point = next(r for r in entities(model) if r.entity_type == "Point")
		self.assertEqual(point.geometry["p"], [19.5, -2860.5, 1.5])

		locked = make_model("DXF-LOCK").name
		cad_model_service.save_edits(locked, 0, {"upserts": [line("L1", [0, 0, 0], [1, 0, 0])]})
		cad_revision_service.submit_for_review(locked)
		with open(FIXTURE, "rb") as f:
			file = upload(f.read())
		io = cad_exchange_service.start_import(locked, file.file_url)
		with self.assertRaises(frappe.ValidationError):
			cad_exchange_service.commit_import(io["name"], {"source_units": "Metres"})

	def test_only_supported_files_are_accepted(self):
		model = make_model("DXF-BAD").name
		file = upload(b"not a drawing", "notes.docx")
		with self.assertRaises(frappe.ValidationError):
			cad_exchange_service.start_import(model, file.file_url)
		garbage = upload(b"0\nSECTION\n2\nGARBAGE\n", "broken.dxf")
		io = cad_exchange_service.start_import(model, garbage.file_url)
		self.assertIn(cad_exchange_service.get_exchange(io["name"])["processing_status"], ("Failed", "Ready to Commit"))
		self.assertFalse(frappe.db.exists("CAD Entity", {"cad_model": model}))
