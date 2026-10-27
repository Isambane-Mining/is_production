# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

import os

import frappe
from frappe.tests import IntegrationTestCase

from is_production.geo_planning.doctype.gis_dataset.test_gis_dataset import make_dataset
from is_production.geo_planning.services import gis_health_service, gis_workstation_service

IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]


class IntegrationTestGISSettings(IntegrationTestCase):
	def test_unreachable_qgis_server_is_reported(self):
		result = gis_health_service.check_qgis_server("http://127.0.0.1:9/ows/")
		self.assertFalse(result["ok"])
		self.assertTrue(result["local"])

	def test_missing_python_is_reported(self):
		result = gis_health_service.run_worker("/nonexistent/python3", "health.py", {})
		self.assertIn("error", result)

	def test_missing_site_project_folder_is_explained(self):
		result = gis_health_service.check_project_directory("/var/lib/qgis-server/no-such-base")
		self.assertFalse(result["ok"])
		self.assertTrue(result["path"].endswith("/" + frappe.local.site))
		self.assertIn("sudo install -d -o", result["message"])

	def test_unreadable_plugin_folder_is_a_warning_not_a_crash(self):
		from unittest.mock import patch

		frappe.db.set_single_value("GIS Plugin Settings", "manage_server_plugins", 1)
		frappe.clear_document_cache("GIS Plugin Settings", "GIS Plugin Settings")
		try:
			with patch("is_production.geo_planning.services.gis_plugin_service.os.listdir", side_effect=PermissionError(13, "Permission denied")):
				warnings = []
				gis_health_service.plugin_status(warnings)
			self.assertTrue(any("cannot be read" in w for w in warnings), warnings)
		finally:
			frappe.db.set_single_value("GIS Plugin Settings", "manage_server_plugins", 0)
			frappe.clear_document_cache("GIS Plugin Settings", "GIS Plugin Settings")

	def test_crs_definition_sends_axis_separately(self):
		lo29 = gis_workstation_service.get_crs_definition("EPSG:2053")
		self.assertEqual(lo29["axis"], "wsu")
		self.assertNotIn("+axis", lo29["proj4"])
		self.assertEqual(gis_workstation_service.get_crs_definition("EPSG:4326")["axis"], "enu")

	def test_layer_features_are_web_mercator_and_in_view_only(self):
		from pyproj import Transformer

		dataset = make_dataset("test_gis_ws_points", geometry_type="Point").name
		for x, y in ((-19500, 2860500), (-5000, 2700000)):
			frappe.get_doc(
				{"doctype": "GIS Feature", "dataset": dataset, "geometry": {"type": "Point", "coordinates": [x, y]}}
			).insert()

		to_web = Transformer.from_crs("EPSG:2053", "EPSG:3857", always_xy=True)
		cx, cy = to_web.transform(-19500, 2860500)
		collection = gis_workstation_service.get_layer_features(dataset, [cx - 500, cy - 500, cx + 500, cy + 500])
		self.assertEqual(len(collection["features"]), 1)
		x, y = collection["features"][0]["geometry"]["coordinates"]
		self.assertAlmostEqual(x, cx, places=3)
		self.assertAlmostEqual(y, cy, places=3)
		self.assertFalse(collection["truncated"])

	def test_workstation_api_needs_dataset_permission(self):
		dataset = make_dataset("test_gis_ws_perm", geometry_type="Point").name
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				gis_workstation_service.get_layer_features(dataset, [0, 0, 1, 1])
			with self.assertRaises(frappe.PermissionError):
				gis_workstation_service.get_workstation_config()
		finally:
			frappe.set_user("Administrator")


class IntegrationTestGISSetup(IntegrationTestCase):
	"""bench gis-setup: everything follows the bench user, site and QGIS Server group it runs with."""

	def test_folders_follow_the_bench_user_site_and_group(self):
		from is_production.geo_planning.services import gis_setup_service as setup

		commands = setup.folder_commands("/srv/qgis/projects", "erp.example.org", "alice", "qgis", "/srv/qgis/plugins")
		self.assertEqual(commands[0], ["install", "-d", "-o", "root", "-g", "qgis", "-m", "0751", "/srv/qgis/projects"])
		self.assertEqual(commands[1], ["install", "-d", "-o", "alice", "-g", "qgis", "-m", "2750", "/srv/qgis/projects/erp.example.org"])
		self.assertEqual(commands[2][-1], "/srv/qgis/plugins")

	def test_existing_folders_need_no_sudo(self):
		import getpass
		import grp
		import tempfile

		from is_production.geo_planning.services import gis_setup_service as setup

		with tempfile.TemporaryDirectory() as base:
			group = grp.getgrgid(os.getgid()).gr_name
			os.chmod(base, 0o2750)
			command = setup.folder_commands("/x", "s", getpass.getuser(), group)[1][:8] + [base]
			self.assertTrue(setup.folder_ready(command))
			os.chmod(base, 0o750)
			self.assertFalse(setup.folder_ready(command))
			self.assertFalse(setup.folder_ready(command[:3] + ["someone-else"] + command[4:]))

	def test_account_is_per_database_and_select_only(self):
		from is_production.geo_planning.services import gis_setup_service as setup

		self.assertEqual(setup.account_name("_1bd3e0294da19198"), "qgis_ro__1bd3e0294da19198")
		with self.assertRaises(setup.SetupError):
			setup.account_name("bad`name")
		sql = setup.account_sql("qgis_ro_x", "localhost", "Ab-c_9", "x")
		self.assertEqual(sql[-1], "GRANT SELECT ON `x`.`tabGIS Feature` TO 'qgis_ro_x'@'localhost'")
		with self.assertRaises(setup.SetupError):
			setup.account_sql("qgis_ro_x", "localhost", "it's", "x")

	def test_print_only_changes_nothing_and_uses_the_running_user(self):
		from unittest.mock import patch

		from is_production.geo_planning.services import gis_setup_service as setup

		before = frappe.get_single("GIS Settings").as_dict()
		lines = []
		with patch.object(setup, "bench_user", return_value="bob"), patch.object(setup, "detect_qgis_group", return_value="qgisgrp"), patch.object(setup.subprocess, "run") as run:
			setup.run_setup(print_only=True, skip_host_check=True, echo=lines.append)
		self.assertFalse([c for c in run.call_args_list if c.args and c.args[0][:1] == ["sudo"]])  # nothing privileged ran
		text = "\n".join(lines)
		self.assertIn(f"-o bob -g qgisgrp -m 2750 ", text)
		self.assertIn(frappe.local.site, text)
		self.assertIn(f"'qgis_ro_{frappe.conf.db_name}'@", text)
		self.assertNotIn("IDENTIFIED BY '" + "a", text.replace("********", ""))  # no password shown
		after = frappe.get_single("GIS Settings").as_dict()
		self.assertEqual((after.qgis_db_user, after.qgis_project_directory), (before.qgis_db_user, before.qgis_project_directory))

	def test_another_bench_owning_the_plugin_folder_stops_setup(self):
		from unittest.mock import patch

		from is_production.geo_planning.services import gis_setup_service as setup

		with patch.object(setup, "plugin_dir_owned_elsewhere", return_value="carol"), patch.object(setup.subprocess, "run"):
			with self.assertRaises(setup.SetupError):
				setup.run_setup(manage_server_plugins=True, print_only=True, skip_host_check=True, skip_account=True, echo=lambda *_: None)
