# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt
"""QGIS plugin management with real (tiny) plugin packages built here.

Enabling a plugin rebuilds the Processing registry and commits, so this test restores GIS Plugin
Settings and removes its plugins, packages, folders and datasets afterwards.
"""

import io
import os
import sys
import zipfile

import frappe
from frappe.tests import IntegrationTestCase

from is_production.geo_planning.services import gis_plugin_service as plugins
from is_production.geo_planning.services import gis_processing_service as proc

IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]
DATASET = "test_plugin_points"

PROCESSING_PLUGIN = {
	"isp_test_tools/__init__.py": "def classFactory(iface):\n    from .plugin import IspTestPlugin\n    return IspTestPlugin(iface)\n",
	"isp_test_tools/plugin.py": (
		"from qgis.core import QgsApplication\n"
		"from .provider import IspTestProvider\n\n"
		"class IspTestPlugin:\n"
		"    def __init__(self, iface):\n        self.iface = iface\n        self.provider = None\n"
		"    def initProcessing(self):\n        self.provider = IspTestProvider()\n        QgsApplication.processingRegistry().addProvider(self.provider)\n"
		"    def initGui(self):\n        self.initProcessing()\n"
		"    def unload(self):\n        QgsApplication.processingRegistry().removeProvider(self.provider)\n"
	),
	"isp_test_tools/provider.py": (
		"from qgis.core import QgsProcessingAlgorithm, QgsProcessingOutputNumber, QgsProcessingParameterFeatureSource, QgsProcessingProvider\n\n"
		"class CountFeatures(QgsProcessingAlgorithm):\n"
		"    def name(self):\n        return 'countfeatures'\n"
		"    def displayName(self):\n        return 'Count features (ISP test)'\n"
		"    def createInstance(self):\n        return CountFeatures()\n"
		"    def initAlgorithm(self, config=None):\n"
		"        self.addParameter(QgsProcessingParameterFeatureSource('INPUT', 'Input'))\n"
		"        self.addOutput(QgsProcessingOutputNumber('COUNT', 'Count'))\n"
		"    def processAlgorithm(self, parameters, context, feedback):\n"
		"        return {'COUNT': self.parameterAsSource(parameters, 'INPUT', context).featureCount()}\n\n"
		"class IspTestProvider(QgsProcessingProvider):\n"
		"    def id(self):\n        return 'isptest'\n"
		"    def name(self):\n        return 'ISP Test'\n"
		"    def loadAlgorithms(self):\n        self.addAlgorithm(CountFeatures())\n"
	),
	"isp_test_tools/metadata.txt": (
		"[general]\nname=ISP Test Tools\nqgisMinimumVersion=3.22\ndescription=Test processing plugin\nversion=1.0\n"
		"author=Isambane Mining\nemail=dev@example.com\nabout=Counts features.\nrepository=https://example.com/isp\n"
		"hasProcessingProvider=yes\nexperimental=False\n"
	),
}
DESKTOP_PLUGIN = {
	"isp_test_desktop/__init__.py": "def classFactory(iface):\n    from .main import Main\n    return Main(iface)\n",
	"isp_test_desktop/main.py": (
		"from qgis.PyQt.QtWidgets import QAction\n\nclass Main:\n    def __init__(self, iface):\n        self.iface = iface\n"
		"    def initGui(self):\n        self.action = QAction('Hello')\n        self.iface.addToolBarIcon(self.action)\n    def unload(self):\n        pass\n"
	),
	"isp_test_desktop/metadata.txt": "[general]\nname=ISP Test Desktop\nqgisMinimumVersion=3.0\ndescription=d\nversion=0.1\nauthor=a\nemail=e@example.com\nabout=a\nrepository=https://example.com\n",
}
SERVER_PLUGIN = {
	"isp_test_server/__init__.py": "def serverClassFactory(serverIface):\n    from .filter import Plugin\n    return Plugin(serverIface)\n",
	"isp_test_server/filter.py": "class Plugin:\n    def __init__(self, serverIface):\n        self.serverIface = serverIface\n",
	"isp_test_server/metadata.txt": "[general]\nname=ISP Test Server\nqgisMinimumVersion=3.0\ndescription=s\nversion=0.2\nauthor=a\nemail=e@example.com\nabout=a\nrepository=https://example.com\nserver=True\n",
}


def package(files):
	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w") as z:
		for name, text in files.items():
			z.writestr(name, text)
	return buffer.getvalue()


def register(title, files):
	plugin = frappe.get_doc({"doctype": plugins.PLUGIN, "title": title}).insert()
	f = frappe.get_doc(
		{"doctype": "File", "file_name": f"test_plugin_{title}.zip", "is_private": 1, "content": package(files), "attached_to_doctype": plugins.PLUGIN, "attached_to_name": plugin.name}
	).insert(ignore_permissions=True)
	plugin.db_set("package", f.file_url)
	return plugin.name


class IntegrationTestGISPlugin(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.get_single_value("GIS Settings", "qgis_python"):
			raise cls.skipTest(cls, "GIS Settings has no PyQGIS Python")
		cls.settings_before = {k: frappe.db.get_single_value(plugins.SETTINGS, k) for k in ("enable_third_party_plugins", "allow_server_plugins", "server_restart_required")}
		cls.cleanup()
		frappe.db.set_single_value(plugins.SETTINGS, {"enable_third_party_plugins": 1, "allow_processing_plugins": 1, "allow_server_plugins": 1, "enforce_compatibility": 1})
		frappe.get_doc({"doctype": "GIS Dataset", "dataset_name": DATASET, "title": "Plugin test", "geometry_type": "Point", "crs": "EPSG:2053"}).insert()
		for x in (1, 2, 3, 4):
			frappe.get_doc({"doctype": "GIS Feature", "dataset": DATASET, "geometry": {"type": "Point", "coordinates": [x, x]}}).insert()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls.cleanup()
		frappe.db.set_single_value(plugins.SETTINGS, cls.settings_before)
		frappe.db.commit()
		proc.refresh_registry()
		super().tearDownClass()

	def tearDown(self):
		self.remove_plugins()
		frappe.db.set_single_value(plugins.SETTINGS, "enable_third_party_plugins", 1)
		frappe.db.commit()

	@classmethod
	def remove_plugins(cls):
		for name in frappe.get_all(plugins.PLUGIN, filters={"title": ["like", "isp%"]}, pluck="name") + frappe.get_all(plugins.PLUGIN, filters={"plugin_id": ["like", "isp_test%"]}, pluck="name"):
			if not frappe.db.exists(plugins.PLUGIN, name):
				continue
			if frappe.db.get_value(plugins.PLUGIN, name, "status") in ("Installed", "Enabled", "Error"):
				plugins.uninstall_plugin(name)
			frappe.delete_doc(plugins.PLUGIN, name, force=True, ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def cleanup(cls):
		cls.remove_plugins()
		for job in frappe.get_all(proc.JOB, filters={"parameters": ["like", f"%{DATASET}%"]}, pluck="name"):
			frappe.delete_doc(proc.JOB, job, force=True, ignore_permissions=True)
		if frappe.db.exists("GIS Dataset", DATASET):
			frappe.db.delete("GIS Feature", {"dataset": DATASET})
			frappe.delete_doc("GIS Dataset", DATASET, force=True, ignore_permissions=True)
		frappe.db.commit()

	def test_processing_plugin_lifecycle(self):
		name = register("isp tools", PROCESSING_PLUGIN)
		self.assertEqual(plugins.analyse_plugin(name), "Processing-compatible")
		plugin = frappe.get_doc(plugins.PLUGIN, name)
		self.assertEqual((plugin.plugin_id, plugin.category, plugin.version), ("isp_test_tools", "QGIS Processing Plugin", "1.0"))
		self.assertEqual(len(plugin.checksum), 64)

		self.assertEqual(plugins.install_plugin(name), "Installed")
		self.assertTrue(os.path.isfile(os.path.join(plugins.processing_dir(), "isp_test_tools", "provider.py")))
		self.assertEqual(plugins.set_plugin_enabled(name, 1), "Enabled")
		self.assertIn("isptest:countfeatures", proc.registry()["by_id"])

		job = proc.run_algorithm("isptest:countfeatures", {"INPUT": {"dataset": DATASET}})
		job = proc.get_processing_job(job["name"])
		self.assertEqual(job["status"], "Completed", job["log"])
		self.assertEqual(job["results"]["COUNT"], 4)
		self.assertNotIn("isp_test_tools", sys.modules)  # the web process never imported the plugin

		plugins.uninstall_plugin(name)
		self.assertNotIn("isptest:countfeatures", proc.registry()["by_id"])
		self.assertFalse(os.path.exists(os.path.join(plugins.processing_dir(), "isp_test_tools")))

	def test_desktop_plugin_is_classified_and_not_installed(self):
		name = register("isp desktop", DESKTOP_PLUGIN)
		self.assertEqual(plugins.analyse_plugin(name), "Requires manual adaptation")
		plugin = frappe.get_doc(plugins.PLUGIN, name)
		self.assertEqual(plugin.category, "QGIS Desktop Plugin")
		self.assertIn("qgis.PyQt.QtWidgets", plugin.compatibility_report)
		with self.assertRaises(frappe.ValidationError):
			plugins.install_plugin(name)

	def test_server_plugin_goes_to_qgis_server(self):
		name = register("isp server", SERVER_PLUGIN)
		self.assertEqual(plugins.analyse_plugin(name), "Native server-compatible")
		plugins.install_plugin(name)
		server_dir = frappe.db.get_single_value(plugins.SETTINGS, "server_plugin_directory")
		plugins.set_plugin_enabled(name, 1)
		self.assertTrue(os.path.isfile(os.path.join(server_dir, "isp_test_server", "__init__.py")))
		self.assertTrue(frappe.db.get_single_value(plugins.SETTINGS, "server_restart_required"))
		plugins.set_plugin_enabled(name, 0)
		self.assertFalse(os.path.exists(os.path.join(server_dir, "isp_test_server")))

	def test_unsafe_and_changed_packages_are_refused(self):
		name = register("isp unsafe", {"isp_bad/__init__.py": "", "isp_bad/metadata.txt": "[general]\nname=x\n", "../evil.py": "print(1)"})
		with self.assertRaises(frappe.ValidationError):
			plugins.analyse_plugin(name)
		good = register("isp tools again", PROCESSING_PLUGIN)
		plugins.analyse_plugin(good)
		frappe.db.set_value(plugins.PLUGIN, good, "checksum", "0" * 64)  # as if the file changed afterwards
		with self.assertRaises(frappe.ValidationError):
			plugins.install_plugin(good)

	def test_plugins_are_switched_off_by_default_policy(self):
		name = register("isp tools off", PROCESSING_PLUGIN)
		plugins.analyse_plugin(name)
		frappe.db.set_single_value(plugins.SETTINGS, "enable_third_party_plugins", 0)
		frappe.clear_document_cache(plugins.SETTINGS, plugins.SETTINGS)
		try:
			with self.assertRaises(frappe.ValidationError):
				plugins.install_plugin(name)
		finally:
			frappe.db.set_single_value(plugins.SETTINGS, "enable_third_party_plugins", 1)

	def test_catalogue_requests_and_personal_toolbox(self):
		user = "test_gis_plugin_user@example.com"
		if not frappe.db.exists("User", user):
			frappe.get_doc({"doctype": "User", "email": user, "first_name": "Plugin", "send_welcome_email": 0, "roles": [{"role": "Production User"}]}).insert(ignore_permissions=True)
		# an approved (enabled) processing plugin
		name = register("isp tools catalogue", PROCESSING_PLUGIN)
		plugins.analyse_plugin(name)
		plugins.install_plugin(name)
		plugins.set_plugin_enabled(name, 1)
		self.assertEqual(frappe.db.get_value(plugins.PLUGIN, name, "processing_providers"), "isptest")

		frappe.set_user(user)
		try:
			catalogue = plugins.plugin_catalogue()
			self.assertFalse(catalogue["is_admin"])
			[mine] = [p for p in catalogue["plugins"] if p.name == name]
			self.assertTrue(mine.in_my_toolbox)
			# switch it off for this user only
			plugins.set_my_plugin(name, 0)
			self.assertNotIn("isptest", {a["provider"] for a in proc.list_algorithms()["algorithms"]})
			with self.assertRaises(frappe.ValidationError):
				proc.run_algorithm("isptest:countfeatures", {"INPUT": {"dataset": DATASET}})
			plugins.set_my_plugin(name, 1)
			self.assertIn("isptest", {a["provider"] for a in proc.list_algorithms()["algorithms"]})

			# ask for a plugin from the repository; users cannot install
			requested = plugins.request_plugin("isp requested", "https://plugins.qgis.org/plugins/isp/version/1.0/download/", "for slope work")
			self.assertEqual(frappe.db.get_value(plugins.PLUGIN, requested, ["status", "requested_by"]), ("Requested", user))
			with self.assertRaises(frappe.PermissionError):
				plugins.install_plugin(requested)
			with self.assertRaises(frappe.ValidationError):
				plugins.request_plugin("isp evil", "https://evil.example.com/x.zip")
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(any(p.name == name for p in plugins.plugin_catalogue()["plugins"]))

		plugins.reject_plugin(requested, "not needed")
		self.assertEqual(frappe.db.get_value(plugins.PLUGIN, requested, "status"), "Rejected")
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": user, "document_name": requested}))
		frappe.db.delete("Notification Log", {"document_type": plugins.PLUGIN})
		frappe.defaults.clear_user_default(plugins.USER_DEFAULT, user)
		frappe.delete_doc("User", user, force=True, ignore_permissions=True)

	def test_only_system_managers(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				plugins.install_plugin("anything")
		finally:
			frappe.set_user("Administrator")
