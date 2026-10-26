# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import json
import math

import frappe
from frappe.tests import IntegrationTestCase

from is_production.cad_engineering.doctype.cad_import_export.test_cad_import_export import upload
from is_production.cad_engineering.doctype.cad_model.test_cad_model import cleanup, make_model
from is_production.cad_engineering.services import (
	cad_exchange_service,
	cad_job_service,
	cad_model_service,
	cad_storage_service,
	cad_surface_service,
	cad_workstation_service,
)

IGNORE_TEST_RECORD_DEPENDENCIES = ["User", "Employee", "Company", "Branch", "Location", "Geo Project", "CAD Design Revision", "CAD Model", "CAD Processing Job", "CAD Entity", "CAD Design Standard", "CAD Design Validation", "Material Type"]
X0, Y0 = -19500.0, 2860500.0


def job_result(started):
	job = cad_job_service.get_job(started["name"])
	assert job["status"] == "Completed", (job, frappe.db.get_value("CAD Processing Job", started["name"], "log"))
	return job["results"]


def point(eid, x, y, z):
	return {"entity_id": eid, "entity_type": "Point", "geometry": {"p": [X0 + x, Y0 + y, z]}}


def add_surface(model, version, result, name, surface_type="Survey Surface"):
	"""What the workstation does after a Surface job: add the Surface entity and save."""
	eid = f"SRF-{name}".upper().replace(" ", "")
	saved = cad_model_service.save_edits(
		model,
		version,
		{"upserts": [{"entity_id": eid, "entity_type": "Surface", "geometry": {"name": name, "surface_type": surface_type, "source": result["source"]}, "geometry_ref": result["geometry_ref"]}]},
	)
	return saved["edit_version"], eid


def plane_points(model, version, prefix, z_of, n=11, size=100.0):
	rows = [point(f"{prefix}{i}_{j}", i * size / (n - 1), j * size / (n - 1), z_of(i * size / (n - 1), j * size / (n - 1))) for i in range(n) for j in range(n)]
	return cad_model_service.save_edits(model, version, {"upserts": rows})["edit_version"], [r["entity_id"] for r in rows]


class IntegrationTestCADSurface(IntegrationTestCase):
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

	def build(self, model, **params):
		return job_result(cad_surface_service.start_surface(model, "build", params))

	def analysis(self, model, analysis, **params):
		return job_result(cad_surface_service.start_analysis(model, analysis, params))

	def test_surface_from_points_breaklines_and_boundary(self):
		model = make_model("SRF-BUILD").name
		v, ids = plane_points(model, 0, "P", lambda x, y: 100 + x)
		v = cad_model_service.save_edits(
			model,
			v,
			{
				"upserts": [
					{"entity_id": "BL", "entity_type": "Polyline", "geometry": {"points": [[X0 + 10, Y0 + 55, 110], [X0 + 90, Y0 + 55, 190]]}},
					{"entity_id": "BOUND", "entity_type": "Polyline", "geometry": {"points": [[X0, Y0, 0], [X0 + 100, Y0, 0], [X0 + 100, Y0 + 100, 0], [X0, Y0 + 100, 0]], "closed": True}},
					{"entity_id": "HOLE", "entity_type": "Circle", "geometry": {"center": [X0 + 50, Y0 + 30, 0], "radius": 10}},
				]
			},
		)["edit_version"]
		result = self.build(model, points=ids, breaklines=["BL"], boundary="BOUND", holes=["HOLE"])
		stats = result["stats"]
		self.assertTrue(stats["validation"]["ok"], stats["validation"])
		self.assertAlmostEqual(stats["plan_area"], 10000 - circle_polygon_area(10, 128), places=4)
		self.assertTrue(cad_storage_service.blob_exists(model, result["geometry_ref"], "tin"))
		v, eid = add_surface(model, v, result, "Topo")
		surface = frappe.get_doc("CAD Surface", {"cad_model": model, "surface_name": "Topo"})
		self.assertEqual(surface.triangle_count, stats["triangle_count"])
		self.assertEqual(surface.geometry_file_reference, result["geometry_ref"])
		self.assertEqual(surface.validation_status, "Valid")
		self.assertAlmostEqual(surface.min_elevation, 100.0)
		index = json.loads(cad_workstation_service.get_surface_index(model, result["geometry_ref"]).get_data())
		self.assertTrue(index["tiles"])
		tile = cad_workstation_service.get_surface_tile(model, result["geometry_ref"], index["tiles"][0]["levels"][0]["file"])
		self.assertTrue(tile.get_data().startswith(b"ISPCADM1"))
		with self.assertRaises(frappe.ValidationError):
			cad_surface_service.tile_key(model, result["geometry_ref"], "../../etc/passwd")
		# deleting the entity removes the CAD Surface record
		cad_model_service.save_edits(model, v, {"deletes": [eid]})
		self.assertFalse(frappe.db.exists("CAD Surface", {"cad_model": model}))

	def test_exact_volumes_are_recorded_with_method_and_checks(self):
		model = make_model("SRF-VOL").name
		v, base_ids = plane_points(model, 0, "B", lambda x, y: 0.0, n=2)
		base = self.build(model, points=base_ids)
		v, _ = add_surface(model, v, base, "Base")
		v, top_ids = plane_points(model, v, "T", lambda x, y: x - 50, n=2)
		top = self.build(model, points=top_ids)
		v, _ = add_surface(model, v, top, "Tilted", "Design Surface")
		result = self.analysis(model, "volume", geometry_refs=[base["geometry_ref"], top["geometry_ref"]])
		self.assertAlmostEqual(result["cut"], 125000.0, places=6)
		self.assertAlmostEqual(result["fill"], 125000.0, places=6)
		self.assertTrue(result["valid"])
		record = frappe.get_doc("CAD Volume Calculation", result["record"])
		self.assertEqual(record.base_geometry_ref, base["geometry_ref"])
		self.assertEqual(record.comparison_name, "Tilted")
		self.assertEqual(record.units, "m³")
		self.assertIn("Exact TIN-to-TIN", record.method)
		self.assertTrue(json.loads(record.validation)["net_matches_direct_integral"])
		with self.assertRaises(frappe.ValidationError):  # a record of a measurement never changes
			record.cut_volume = 1
			record.save(ignore_permissions=True)
		elevation = self.analysis(model, "volume", geometry_refs=[top["geometry_ref"]], elevation=0)
		self.assertAlmostEqual(elevation["cut"], 125000.0, places=6)  # the surface above elevation 0
		self.assertEqual(frappe.db.get_value("CAD Volume Calculation", elevation["record"], "calculation_type"), "Surface to Elevation")

	def test_contours_profile_sections_intersection_and_analysis(self):
		model = make_model("SRF-ANA").name
		v, ids = plane_points(model, 0, "P", lambda x, y: x)
		surface = self.build(model, points=ids)
		v, _ = add_surface(model, v, surface, "Ramp")
		ref = surface["geometry_ref"]
		contours = self.analysis(model, "contours", geometry_refs=[ref], interval=10, major=50)["contours"]
		self.assertEqual(sorted(round(c["z"]) for c in contours), list(range(10, 101, 10)))  # 100: the surface reaches it along its edge
		self.assertTrue(all(abs(p[0] - X0 - c["z"]) < 1e-9 for c in contours for p in c["points"]))
		profile = self.analysis(model, "profile", geometry_refs=[ref], alignment=[[X0, Y0 + 50, 0], [X0 + 100, Y0 + 50, 0]])
		run = profile["profiles"][ref][0]
		self.assertAlmostEqual(profile["length"], 100.0)
		self.assertTrue(all(abs(p[3] - p[0]) < 1e-9 for p in run))
		sections = self.analysis(model, "sections", geometry_refs=[ref], alignment=[[X0 + 10, Y0 + 10, 0], [X0 + 10, Y0 + 90, 0]], interval=20, left=5, right=5)
		self.assertEqual(len(sections["sections"]), 5)
		v, flat_ids = plane_points(model, v, "F", lambda x, y: 40.0, n=2)
		flat = self.build(model, points=flat_ids)
		v, _ = add_surface(model, v, flat, "Flat 40")
		lines = self.analysis(model, "intersect", geometry_refs=[ref, flat["geometry_ref"]])["lines"]
		self.assertEqual(len(lines), 1)
		self.assertTrue(all(abs(p[0] - X0 - 40) < 1e-9 and abs(p[2] - 40) < 1e-9 for p in lines[0]["points"]))
		stats = self.analysis(model, "analyse", geometry_refs=[ref])
		self.assertAlmostEqual(stats["slope"]["mean"], 45.0, places=6)

	def test_repair_clip_and_smooth_make_new_versions(self):
		model = make_model("SRF-EDIT").name
		v, ids = plane_points(model, 0, "P", lambda x, y: 0.0)
		cad_model_service.save_edits(model, v, {"upserts": [point("SPIKE", 55, 55, 50.0)]})
		v += 1
		surface = self.build(model, points=ids + ["SPIKE"])
		self.assertEqual(surface["stats"]["validation"]["spike_count"], 1)
		fixed = job_result(cad_surface_service.start_surface(model, "repair", {"geometry_ref": surface["geometry_ref"], "remove_spikes": True}))
		self.assertEqual(fixed["stats"]["max_elevation"], 0.0)
		self.assertNotEqual(fixed["geometry_ref"], surface["geometry_ref"])
		v = cad_model_service.save_edits(model, v, {"upserts": [{"entity_id": "CLIP", "entity_type": "Polyline", "geometry": {"points": [[X0, Y0, 0], [X0 + 30, Y0, 0], [X0 + 30, Y0 + 30, 0], [X0, Y0 + 30, 0]], "closed": True}}]})["edit_version"]
		clipped = job_result(cad_surface_service.start_surface(model, "clip", {"geometry_ref": fixed["geometry_ref"], "boundary": "CLIP"}))
		self.assertAlmostEqual(clipped["stats"]["plan_area"], 900.0, places=6)
		smoothed = job_result(cad_surface_service.start_surface(model, "smooth", {"geometry_ref": surface["geometry_ref"], "iterations": 3}))
		self.assertLess(smoothed["stats"]["max_elevation"], 50.0)

	def test_surface_formats_import_and_export(self):
		model = make_model("SRF-IO").name
		landxml = f"""<?xml version="1.0"?>
<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" version="1.2"><Surfaces><Surface name="Pit floor"><Definition surfType="TIN">
<Pnts><P id="1">{Y0} {X0} 1500</P><P id="2">{Y0} {X0 + 100} 1500</P><P id="3">{Y0 + 100} {X0 + 100} 1510</P><P id="4">{Y0 + 100} {X0} 1510</P></Pnts>
<Faces><F>1 2 3</F><F>1 3 4</F></Faces></Definition></Surface></Surfaces>
<CgPoints><CgPoint name="BM1">{Y0 + 5} {X0 + 5} 1501</CgPoint></CgPoints></LandXML>""".encode()
		created = self.import_file(model, landxml, "pit.xml", points_as="points")
		self.assertEqual(created["entity_count"], 2)  # the surface and the control point
		surface = frappe.get_doc("CAD Surface", {"cad_model": model, "surface_name": "Pit floor"})
		self.assertAlmostEqual(surface.plan_area, 10000.0, places=6)
		self.assertAlmostEqual(surface.max_elevation, 1510.0)
		bm = frappe.get_doc("CAD Entity", {"cad_model": model, "entity_type": "Point"})
		self.assertEqual(json.loads(bm.geometry)["p"], [X0 + 5, Y0 + 5, 1501.0])  # LandXML is northing easting

		csv = "id,x,y,z\n" + "\n".join(f"P{i},{X0 + (i % 10) * 10},{Y0 + (i // 10) * 10},{100 + i % 10}" for i in range(100))
		self.import_file(model, csv.encode(), "spot.csv", points_as="surface", surface_type="Terrain Surface")
		self.assertTrue(frappe.db.exists("CAD Surface", {"cad_model": model, "surface_name": "spot", "surface_type": "Terrain Surface"}))

		obj = "v 0 0 0\nv 10 0 0\nv 10 10 1\nv 0 10 1\nf 1 2 3 4\n"
		self.import_file(model, obj.encode(), "pad.obj", source_units="Metres")
		self.assertAlmostEqual(frappe.db.get_value("CAD Surface", {"cad_model": model, "surface_name": "pad"}, "plan_area"), 100.0, places=6)
		stl = "solid cube\n" + "".join(f"facet normal 0 0 0\nouter loop\nvertex {a}\nvertex {b}\nvertex {c}\nendloop\nendfacet\n" for a, b, c in cube_faces()) + "endsolid cube\n"
		self.import_file(model, stl.encode(), "cube.stl")
		mesh = frappe.get_doc("CAD Entity", {"cad_model": model, "entity_type": "Mesh"})
		self.assertAlmostEqual(mesh.volume, 8.0, places=6)  # closed 2×2×2 cube

		ref = surface.geometry_file_reference
		for fmt in ("LandXML", "OBJ", "CSV"):
			io = cad_exchange_service.start_export(model, {"format": fmt, "geometry_refs": [ref]})
			io = cad_exchange_service.get_exchange(io["name"])
			self.assertEqual(io["processing_status"], "Completed", frappe.db.get_value("CAD Processing Job", io["processing_job"], "log"))
			path = frappe.get_doc("File", {"file_url": io["result_file"]}).get_full_path()
			self.assertTrue(open(path, "rb").read(200))
		# LandXML round trip into a second model: identical vertices
		other = make_model("SRF-IO2").name
		io = cad_exchange_service.start_export(model, {"format": "LandXML", "geometry_refs": [ref]})
		data = open(frappe.get_doc("File", {"file_url": cad_exchange_service.get_exchange(io["name"])["result_file"]}).get_full_path(), "rb").read()
		self.import_file(other, data, "again.xml")
		self.assertEqual(frappe.db.get_value("CAD Surface", {"cad_model": other}, "plan_area"), surface.plan_area)

	def test_dxf_3dfaces_become_a_surface_and_surfaces_export_to_dxf(self):
		model = make_model("SRF-DXF").name
		faces = [((0, 0, 0), (10, 0, 0), (10, 10, 5)), ((0, 0, 0), (10, 10, 5), (0, 10, 5))]
		body = "".join(face_dxf(f) for f in faces)
		dxf = f"0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n6\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n{body}0\nENDSEC\n0\nEOF\n"
		self.import_file(model, dxf.encode(), "tin.dxf")
		surface = frappe.get_doc("CAD Surface", {"cad_model": model})
		self.assertEqual(surface.triangle_count, 2)
		self.assertAlmostEqual(surface.plan_area, 100.0, places=6)
		io = cad_exchange_service.start_export(model, {"format": "DXF"})
		data = open(frappe.get_doc("File", {"file_url": cad_exchange_service.get_exchange(io["name"])["result_file"]}).get_full_path(), "rb").read()
		self.assertIn(b"MESH", data)
		other = make_model("SRF-DXF2").name
		self.import_file(other, data, "back.dxf")
		self.assertAlmostEqual(frappe.db.get_value("CAD Surface", {"cad_model": other}, "plan_area"), 100.0, places=6)

	def test_solid_booleans_revolve_loft_sweep_section_and_step(self):
		model = make_model("SRF-SOLID").name
		built = job_result(
			cad_job_service.start_job(
				"Build Geometry",
				model,
				None,
				{"items": [{"key": "box", "op": "box", "origin": [X0, Y0, 0], "size": [10, 10, 10]}, {"key": "cyl", "op": "cylinder", "base": [X0 + 5, Y0 + 5, -1], "radius": 2, "height": 12}]},
			)
		)["items"]
		refs = [b["geometry_ref"] for b in built]
		ops = job_result(
			cad_job_service.start_job(
				"Build Geometry",
				model,
				None,
				{
					"items": [
						{"key": "cut", "op": "boolean", "kind": "subtract", "geometry_refs": refs},
						{"key": "rev", "op": "revolve", "profile": [[X0 + 1, Y0, 0], [X0 + 2, Y0, 0], [X0 + 2, Y0, 3], [X0 + 1, Y0, 3]], "axis_point": [X0, Y0, 0], "axis_direction": [0, 0, 1], "angle": 360},
						{"key": "loft", "op": "loft", "ruled": True, "profiles": [{"points": [[X0, Y0, 0], [X0 + 10, Y0, 0], [X0 + 10, Y0 + 10, 0], [X0, Y0 + 10, 0]]}, {"points": [[X0 + 2, Y0 + 2, 5], [X0 + 8, Y0 + 2, 5], [X0 + 8, Y0 + 8, 5], [X0 + 2, Y0 + 8, 5]]}]},
						{"key": "sweep", "op": "sweep", "profile": [[X0, Y0 - 1, -1], [X0, Y0 + 1, -1], [X0, Y0 + 1, 1], [X0, Y0 - 1, 1]], "path": [[X0, Y0, 0], [X0 + 20, Y0, 0]]},
					]
				},
			)
		)["items"]
		volumes = {o["key"]: o["volume"] for o in ops}
		self.assertAlmostEqual(volumes["cut"], 1000 - math.pi * 4 * 10, places=6)
		self.assertAlmostEqual(volumes["rev"], math.pi * 3 * 3, places=6)
		self.assertAlmostEqual(volumes["loft"], 5 / 3 * (100 + 36 + 60), places=6)
		self.assertAlmostEqual(volumes["sweep"], 80.0, places=6)
		section = self.analysis(model, "section_solids", geometry_refs=[refs[0]], origin=[X0, Y0, 5], normal=[0, 0, 1])
		self.assertEqual(len(section["items"][0]["polylines"]), 4)
		# STEP round trip of the subtracted solid
		v = cad_model_service.save_edits(model, 0, {"upserts": [{"entity_id": "CUT", "entity_type": "Solid", "geometry": {"source": ops[0]["source"]}, "geometry_ref": ops[0]["geometry_ref"]}]})["edit_version"]
		io = cad_exchange_service.start_export(model, {"format": "STEP"})
		data = open(frappe.get_doc("File", {"file_url": cad_exchange_service.get_exchange(io["name"])["result_file"]}).get_full_path(), "rb").read()
		other = make_model("SRF-SOLID2").name
		self.import_file(other, data, "part.step")
		self.assertAlmostEqual(frappe.db.get_value("CAD Entity", {"cad_model": other, "entity_type": "Solid"}, "volume"), volumes["cut"], places=6)

	def import_file(self, model, content, name, **options):
		file = upload(content, name)
		io = cad_exchange_service.start_import(model, file.file_url)
		io = cad_exchange_service.get_exchange(io["name"])
		self.assertEqual(io["processing_status"], "Ready to Commit", frappe.db.get_value("CAD Processing Job", io["processing_job"], "log"))
		cad_exchange_service.commit_import(io["name"], {"source_units": "Metres", **options})
		io = cad_exchange_service.get_exchange(io["name"])
		self.assertEqual(io["processing_status"], "Completed", frappe.db.get_value("CAD Processing Job", io["processing_job"], "log"))
		return io

	def test_fillet_chamfer_shell_and_tapered_extrusions(self):
		"""Phase 6 solid finishing operations and Phase 4 extrusion options against closed-form volumes."""
		model = make_model("SRF-DRESS").name
		box = job_result(cad_job_service.start_job("Build Geometry", model, None, {"items": [{"key": "box", "op": "box", "origin": [X0, Y0, 0], "size": [10, 10, 10]}]}))["items"][0]["geometry_ref"]
		square = [[X0, Y0, 0], [X0 + 20, Y0, 0], [X0 + 20, Y0 + 10, 0], [X0, Y0 + 10, 0]]
		items = [
			{"key": "fillet", "op": "fillet", "geometry_ref": box, "radius": 1, "edges": "all"},
			{"key": "chamfer", "op": "chamfer", "geometry_ref": box, "distance": 1, "edges": "vertical"},
			{"key": "open_shell", "op": "shell", "geometry_ref": box, "thickness": 1, "open": "top"},
			{"key": "closed_shell", "op": "shell", "geometry_ref": box, "thickness": 1, "open": "none"},
			{"key": "taper", "op": "extrude", "profile": square, "vector": [0, 0, 6], "taper": 10},
			{"key": "disc", "op": "extrude", "profile": [[X0 + 5, Y0, 0], [X0 - 5, Y0, 0]], "bulges": [1, 1], "vector": [0, 0, 10]},
		]
		out = {r["key"]: r for r in job_result(cad_job_service.start_job("Build Geometry", model, None, {"items": items}))["items"]}
		a, r = 10, 1
		rounded = (a - 2 * r) ** 3 + 6 * (a - 2 * r) ** 2 * r + 3 * math.pi * (a - 2 * r) * r * r + 4 / 3 * math.pi * r**3
		i = 6 * math.tan(math.radians(10))
		prismatoid = (6 / 6) * (200 + 4 * (20 - i) * (10 - i) + (20 - 2 * i) * (10 - 2 * i))
		for key, expected in (("fillet", rounded), ("chamfer", 980), ("open_shell", 424), ("closed_shell", 488), ("taper", prismatoid), ("disc", math.pi * 25 * 10)):
			self.assertTrue(out[key]["valid"], (key, out[key].get("message")))
			self.assertAlmostEqual(out[key]["volume"], expected, delta=1e-6 * expected, msg=key)


def circle_polygon_area(r, n):
	return 0.5 * n * r * r * math.sin(2 * math.pi / n)


def cube_faces():
	v = [(x, y, z) for x in (0, 2) for y in (0, 2) for z in (0, 2)]
	quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
	for a, b, c, d in quads:
		for t in ((a, b, c), (a, c, d)):
			yield tuple(" ".join(map(str, v[i])) for i in t)


def face_dxf(face):
	codes = ""
	pts = list(face) + [face[-1]]
	for k, p in enumerate(pts):
		codes += f"1{k}\n{X0 + p[0]}\n2{k}\n{Y0 + p[1]}\n3{k}\n{p[2]}\n"
	return f"0\n3DFACE\n8\nTOPO\n{codes}"
