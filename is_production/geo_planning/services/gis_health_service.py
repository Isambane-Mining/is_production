"""GIS dependency health check: QGIS Server, PyQGIS, GDAL/PROJ, the QGIS database account and the
geometry schema. Results are stored on GIS Settings.

PyQGIS cannot be imported in the bench (different Python), so those checks run in
``qgis_worker/health.py`` under the configured system Python. The database password goes to the
worker on stdin, never on the command line.
"""

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import frappe
import requests
from frappe import _
from frappe.utils import now_datetime

from is_production.geo_planning.services.gis_storage_service import get_geometry_schema_status

SETTINGS = "GIS Settings"
WORKER_DIR = Path(__file__).resolve().parent.parent / "qgis_worker"
WORKER_TIMEOUT = 120
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


def run_health_check():
	settings = frappe.get_single(SETTINGS)
	problems, warnings = [], []

	server = check_qgis_server(settings.qgis_server_url)
	if not server["ok"]:
		problems.append(server["message"])
	elif not server["local"]:
		warnings.append(_("QGIS Server URL is not on this host; it must not be reachable from outside."))

	worker = run_worker(
		settings.qgis_python,
		"health.py",
		{"db": database_account(settings)} if settings.qgis_db_user else {},
	)
	pyqgis = worker.get("pyqgis") or {"ok": False, "message": worker.get("error")}
	gdal = worker.get("gdal") or {}
	processing = worker.get("processing") or {}
	database = worker.get("database")

	if not pyqgis.get("ok"):
		problems.append(_("PyQGIS is not available: {0}").format(pyqgis.get("message")))
	else:
		if not gdal.get("mysql_driver"):
			problems.append(_("GDAL has no MySQL driver, so QGIS cannot read features from MariaDB."))
		if not processing.get("ok"):
			warnings.append(_("QGIS Processing did not load: {0}").format(processing.get("message")))

	if database is None and pyqgis.get("ok"):
		warnings.append(_("No QGIS database account is set."))
	elif database is not None and not database.get("ok"):
		problems.append(_("QGIS database account: {0}").format(database.get("message")))
	elif database is not None and not database.get("limited"):
		warnings.append(_("The QGIS database account can read other Frappe tables; grant it tabGIS Feature only."))

	schema = get_geometry_schema_status()
	if not schema["ok"]:
		problems.append(schema["message"])

	directory = check_project_directory(settings.qgis_project_directory)
	if directory and not directory["ok"]:
		warnings.append(directory["message"])

	if server["ok"] and pyqgis.get("ok") and server.get("version") and pyqgis.get("version"):
		if not server["version"].startswith(pyqgis["version"].split("-")[0]):
			warnings.append(
				_("QGIS Server ({0}) and PyQGIS ({1}) are different versions.").format(
					server["version"], pyqgis["version"]
				)
			)

	if pyqgis.get("ok"):
		refresh_processing_registry_if_outdated(pyqgis.get("version"), warnings)
	plugins = plugin_status(warnings)

	status = "Error" if problems else "Warning" if warnings else "OK"
	report = {
		"qgis_server": server,
		"worker": worker,
		"geometry_schema": schema,
		"project_directory": directory,
		"plugins": plugins,
		"problems": problems,
		"warnings": warnings,
	}
	frappe.db.set_single_value(
		SETTINGS,
		{
			"health_status": status,
			"last_check_on": now_datetime(),
			"health_summary": "\n".join(problems + warnings) or _("No problems found."),
			"qgis_server_status": _("Available") if server["ok"] else _("Unavailable"),
			"qgis_version": server.get("version") or pyqgis.get("version"),
			"pyqgis_status": _("Available (Python {0})").format(worker.get("python"))
			if pyqgis.get("ok")
			else _("Unavailable"),
			"gdal_version": gdal.get("version"),
			"proj_version": (worker.get("proj") or {}).get("version"),
			"ogr_mysql_driver": _("Available") if gdal.get("mysql_driver") else _("Missing"),
			"database_adapter_status": describe_database(database),
			"geometry_schema_status": _("Intact") if schema["ok"] else _("Missing column or index"),
			"processing_providers": "\n".join(
				f"{p['name']} ({p['id']}): {p['algorithms']}" for p in processing.get("providers") or []
			),
			"health_report": json.dumps(report, indent=1, default=str),
		},
	)
	return {"status": status, "problems": problems, "warnings": warnings}


def refresh_processing_registry_if_outdated(qgis_version, warnings):
	"""After a QGIS upgrade the cached Processing algorithm list is rebuilt."""
	from is_production.geo_planning.services import gis_processing_service as proc

	try:
		path = proc.registry_path()
		cached = None
		if os.path.exists(path):
			with open(path) as f:
				cached = json.load(f).get("qgis_version")
		if cached != qgis_version:
			proc.refresh_registry()
	except Exception as e:
		warnings.append(_("The Processing algorithm list could not be refreshed: {0}").format(e))


def plugin_status(warnings):
	if not frappe.db.exists("DocType", "GIS Plugin Settings"):
		return None
	from is_production.geo_planning.services.gis_plugin_service import scan_plugins

	result = scan_plugins()
	if result["errors"]:
		warnings.append(_("Plugins with errors: {0}.").format(", ".join(result["errors"])))
	if frappe.db.get_single_value("GIS Plugin Settings", "server_restart_required"):
		warnings.append(_("A QGIS Server plugin changed: restart QGIS Server (sudo systemctl restart qgis-server)."))
	return result


def scheduled_health_check():
	if frappe.db.exists("DocType", SETTINGS):
		run_health_check()


def check_qgis_server(url):
	"""QGIS Server answers any OWS request, even without a project, with its version in the Server header."""
	result = {"ok": False, "url": url, "local": urlparse(url or "").hostname in LOCAL_HOSTS}
	try:
		response = requests.get(url, params={"SERVICE": "WMS", "REQUEST": "GetCapabilities"}, timeout=10)
	except requests.RequestException as e:
		result["message"] = _("QGIS Server is not reachable at {0}: {1}").format(url, e)
		return result

	header = response.headers.get("Server", "")
	if "QGIS" not in header:
		result["message"] = _("{0} answered, but not as QGIS Server (HTTP {1}).").format(url, response.status_code)
		return result

	match = re.search(r"QGIS version ([\w.\-]+)", header)
	result.update({"ok": True, "version": match.group(1) if match else None, "server_header": header})
	return result


def database_account(settings):
	return {
		"host": settings.qgis_db_host or "localhost",
		"port": settings.qgis_db_port or None,
		"name": settings.qgis_db_name or frappe.conf.db_name,
		"user": settings.qgis_db_user,
		"password": settings.get_password("qgis_db_password", raise_exception=False) or "",
	}


def describe_database(database):
	if database is None:
		return _("Not configured")
	if not database.get("ok"):
		return _("Error: {0}").format(database.get("message"))
	text = _("Connected; {0} features readable; FID column {1}, geometry column {2}.").format(
		database.get("feature_count"), database.get("fid_column"), database.get("geometry_column")
	)
	return text + " " + (_("Account is limited.") if database.get("limited") else _("Account is NOT limited."))


def check_project_directory(path):
	if not path:
		return None
	if not os.path.isdir(path):
		return {"ok": False, "message": _("QGIS Project Directory {0} does not exist.").format(path)}
	if not os.access(path, os.W_OK):
		return {"ok": False, "message": _("The bench user cannot write to {0}.").format(path)}
	return {"ok": True}


def run_worker(python, script, request, timeout=WORKER_TIMEOUT):
	"""Run a qgis_worker script with the system Python; returns its JSON report or {"error": ...}."""
	if not python or not os.path.isfile(python):
		return {"error": _("PyQGIS Python {0} does not exist.").format(python)}
	try:
		completed = subprocess.run(
			[python, "-I", str(WORKER_DIR / script)],
			input=json.dumps(request),
			capture_output=True,
			text=True,
			timeout=timeout,
			env={
				"PATH": "/usr/local/bin:/usr/bin:/bin",
				"HOME": os.environ.get("HOME", "/tmp"),
				"QT_QPA_PLATFORM": "offscreen",
				"LANG": "C.UTF-8",
			},
		)
	except subprocess.TimeoutExpired:
		return {"error": _("The PyQGIS worker did not finish within {0} seconds.").format(timeout)}

	# QGIS prints log lines to stdout too; the report is the last line
	lines = [line for line in completed.stdout.splitlines() if line.strip()]
	try:
		return json.loads(lines[-1])
	except (IndexError, ValueError):
		return {"error": (completed.stderr or completed.stdout or "").strip()[-2000:] or _("No output.")}
