"""QGIS plugin management (GIS Plugin, GIS Plugin Settings).

Lifecycle of a plugin record: Registered (a package is attached or downloaded) → Analysed (the package is
checked and classified by ``qgis_worker/plugin_worker.py`` without running any of its code) → Installed
(unpacked into its directory; the package checksum must still match the analysed one) → Enabled.

- **Processing plugins** are unpacked into the site's private files and loaded only by the processing
  worker (``qgis_worker/processing_worker.py``), so their algorithms appear in the Processing toolbox.
- **QGIS Server plugins** are unpacked into a private staging folder; enabling copies them into QGIS
  Server's plugin directory (QGIS_PLUGINPATH). QGIS Server loads plugins when it starts, so a change
  needs ``sudo systemctl restart qgis-server`` (GIS Plugin Settings shows when).
- **Desktop plugins** (and Frappe GIS plugins) are registered and classified only: a desktop plugin's Qt
  interface cannot run in a browser.

The Frappe web workers never import plugin code. Only System Managers change plugins, and only while
GIS Plugin Settings → Enable Third-Party Plugins is on.
"""

import hashlib
import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET

import frappe
import requests
from frappe import _
from frappe.utils import cint, now_datetime

from is_production.geo_planning.services.gis_health_service import run_worker

SETTINGS = "GIS Plugin Settings"
PLUGIN = "GIS Plugin"
WORKER = "plugin_worker.py"
CATEGORY_FOR = {
	"Native server-compatible": "QGIS Server Plugin",
	"Processing-compatible": "QGIS Processing Plugin",
	"Browser-adapter compatible": "QGIS Desktop Plugin",
	"Requires manual adaptation": "QGIS Desktop Plugin",
}
SERVER_PLUGINS_ELSEWHERE = _(
	"QGIS Server plugins are shared by every site on this host, so only one site manages them. "
	"Turn on Manage QGIS Server Plugins in GIS Plugin Settings only on that site."
)
REQUIRED_COMPATIBILITY = {"QGIS Server Plugin": "Native server-compatible", "QGIS Processing Plugin": "Processing-compatible"}


# ---------------------------------------------------------------------------
# shared
# ---------------------------------------------------------------------------


def ensure_single_defaults():
	"""A Single's JSON defaults only exist once it is saved: fill in missing values (after migrate)."""
	for doctype in ("GIS Settings", SETTINGS):
		if not frappe.db.exists("DocType", doctype):
			continue
		for df in frappe.get_meta(doctype).fields:
			if df.default and frappe.db.get_single_value(doctype, df.fieldname) in (None, ""):
				frappe.db.set_single_value(doctype, df.fieldname, df.default)


def private_dir(*parts):
	path = os.path.abspath(frappe.get_site_path("private", "gis_plugins", *parts))
	os.makedirs(path, exist_ok=True)
	return path


def processing_dir():
	return private_dir("processing")


def server_staging_dir():
	return private_dir("server")


def require_admin():
	frappe.only_for("System Manager")


def plugin_worker(request, timeout=300):
	python = frappe.db.get_single_value("GIS Settings", "qgis_python") or "/usr/bin/python3"
	report = run_worker(python, WORKER, request, timeout=timeout)
	if "error" in report:
		frappe.throw(report["error"])
	if not report.get("ok"):
		frappe.throw(report.get("message") or _("The plugin worker failed."))
	return report


def max_bytes():
	return (cint(frappe.db.get_single_value(SETTINGS, "max_package_mb")) or 50) * 1024 * 1024


def package_path(plugin):
	if not plugin.package:
		frappe.throw(_("Attach the plugin package (.zip) first."))
	return frappe.get_doc("File", {"file_url": plugin.package}).get_full_path()


def sha256(path):
	digest = hashlib.sha256()
	with open(path, "rb") as f:
		for block in iter(lambda: f.read(1 << 20), b""):
			digest.update(block)
	return digest.hexdigest()


def log(plugin, message):
	stamp = now_datetime().strftime("%Y-%m-%d %H:%M")
	plugin.history = (f"{stamp} {frappe.session.user}: {message}\n" + (plugin.history or ""))[:20000]


def inside(path, *roots):
	real = os.path.realpath(path)
	return any(real.startswith(os.path.realpath(root) + os.sep) for root in roots if root)


# ---------------------------------------------------------------------------
# analyse
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def analyse_plugin(name):
	require_admin()
	plugin = frappe.get_doc(PLUGIN, name)
	if plugin.status in ("Installed", "Enabled"):
		frappe.throw(_("Uninstall the plugin before analysing a new package."))
	report = plugin_worker({"command": "analyse", "zip": package_path(plugin), "max_bytes": max_bytes()})
	other = frappe.db.get_value(PLUGIN, {"plugin_id": report["plugin_id"], "name": ["!=", plugin.name]})
	if other:
		frappe.throw(_("Plugin {0} is already registered ({1}).").format(report["plugin_id"], other))

	meta = report["metadata"]
	plugin.update(
		{
			"plugin_id": report["plugin_id"],
			"title": meta.get("name") or report["plugin_id"],
			"version": meta.get("version"),
			"description": "\n\n".join(filter(None, [meta.get("description"), meta.get("about")]))[:5000],
			"author": meta.get("author"),
			"license": meta.get("license"),
			"homepage": meta.get("homepage"),
			"repository": meta.get("repository"),
			"qgis_minimum_version": meta.get("qgisminimumversion"),
			"qgis_maximum_version": meta.get("qgismaximumversion"),
			"experimental": 1 if meta.get("experimental", "").lower() in ("true", "yes", "1") else 0,
			"checksum": report["checksum"],
			"package_size": report["size"],
			"compatibility_status": report["compatibility"],
			"status": "Analysed",
			"last_error": None,
		}
	)
	if plugin.category != "Frappe GIS Plugin":
		plugin.category = CATEGORY_FOR.get(report["compatibility"], plugin.category or "QGIS Desktop Plugin")
	scan = report["scan"]
	plugin.compatibility_report = "\n".join(
		[
			f"{report['compatibility']}: " + "; ".join(report["reasons"]),
			"",
			f"Files: {report['files']}, QGIS installed: {report['qgis_version']}",
			f"Processing provider: {scan['processing_provider']}, initProcessing: {scan['init_processing']}, server entry point: {scan['server_class_factory']}",
			f"Desktop interface: {', '.join(scan['gui_imports']) or 'none'}{'; uses iface' if scan['uses_iface'] else ''}",
			f"External imports: {', '.join(scan['external_imports']) or 'none'}",
			f"Compiled modules: {', '.join(report['compiled']) or 'none'}",
		]
	)
	plugin.set("dependencies", report["dependencies"])
	log(plugin, _("Analysed {0} {1}: {2}.").format(report["plugin_id"], meta.get("version") or "", report["compatibility"]))
	plugin.save()
	return plugin.compatibility_status


# ---------------------------------------------------------------------------
# install / enable / disable / uninstall
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def install_plugin(name):
	require_admin()
	settings = frappe.get_single(SETTINGS)
	plugin = frappe.get_doc(PLUGIN, name)
	if not settings.enable_third_party_plugins:
		frappe.throw(_("Third-party plugins are switched off (GIS Plugin Settings)."))
	if plugin.status != "Analysed":
		frappe.throw(_("Analyse the plugin before installing it."))
	if plugin.category not in REQUIRED_COMPATIBILITY:
		frappe.throw(_("{0} plugins are registered only: they cannot run here.").format(plugin.category))
	if plugin.category == "QGIS Processing Plugin" and not settings.allow_processing_plugins:
		frappe.throw(_("Processing plugins are not allowed (GIS Plugin Settings)."))
	if plugin.category == "QGIS Server Plugin" and not settings.allow_server_plugins:
		frappe.throw(_("QGIS Server plugins are not allowed (GIS Plugin Settings)."))
	if plugin.category == "QGIS Server Plugin" and not settings.manage_server_plugins:
		frappe.throw(SERVER_PLUGINS_ELSEWHERE)
	if plugin.experimental and not settings.allow_experimental:
		frappe.throw(_("Experimental plugins are not allowed (GIS Plugin Settings)."))
	if settings.enforce_compatibility:
		if plugin.compatibility_status != REQUIRED_COMPATIBILITY[plugin.category]:
			frappe.throw(_("A {0} must be {1}; the analysis says {2}.").format(plugin.category, REQUIRED_COMPATIBILITY[plugin.category], plugin.compatibility_status))
		unmet = [d.dependency for d in plugin.dependencies if not d.satisfied]
		if unmet:
			frappe.throw(_("Unmet dependencies: {0}.").format(", ".join(unmet)))
	path = package_path(plugin)
	if sha256(path) != plugin.checksum:
		frappe.throw(_("The package changed since it was analysed. Analyse it again."))

	destination = processing_dir() if plugin.category == "QGIS Processing Plugin" else server_staging_dir()
	report = plugin_worker({"command": "extract", "zip": path, "destination": destination, "plugin_id": plugin.plugin_id, "max_bytes": max_bytes()})
	plugin.update({"status": "Installed", "enabled": 0, "installed_path": report["path"], "installed_version": plugin.version, "installed_on": now_datetime(), "last_error": None})
	log(plugin, _("Installed {0} into {1}.").format(plugin.version or "", report["path"]))
	plugin.save()
	return plugin.status


@frappe.whitelist(methods=["POST"])
def set_plugin_enabled(name, enabled):
	require_admin()
	enabled = cint(enabled)
	settings = frappe.get_single(SETTINGS)
	plugin = frappe.get_doc(PLUGIN, name)
	if plugin.status not in ("Installed", "Enabled", "Error"):
		frappe.throw(_("Install the plugin first."))
	if enabled and not settings.enable_third_party_plugins:
		frappe.throw(_("Third-party plugins are switched off (GIS Plugin Settings)."))

	if plugin.category == "QGIS Server Plugin":
		if not settings.manage_server_plugins:
			frappe.throw(SERVER_PLUGINS_ELSEWHERE)
		target = os.path.join(settings.server_plugin_directory, plugin.plugin_id)
		if enabled:
			plugin_worker({"command": "extract", "zip": package_path(plugin), "destination": settings.server_plugin_directory, "plugin_id": plugin.plugin_id, "max_bytes": max_bytes()})
		elif os.path.isdir(target) and inside(target, settings.server_plugin_directory):
			shutil.rmtree(target)
		frappe.db.set_single_value(SETTINGS, "server_restart_required", 1)
		plugin.update({"enabled": enabled, "status": "Enabled" if enabled else "Installed", "last_error": None})
		log(plugin, _("Enabled in QGIS Server (restart QGIS Server).") if enabled else _("Disabled in QGIS Server (restart QGIS Server)."))
		plugin.save()
		return plugin.status

	plugin.update({"enabled": enabled, "status": "Enabled" if enabled else "Installed", "last_error": None})
	log(plugin, _("Enabled.") if enabled else _("Disabled."))
	plugin.save()
	frappe.db.commit()
	# rebuild the algorithm list with the plugin loaded (or gone); load errors come back on the plugin
	from is_production.geo_planning.services.gis_processing_service import refresh_registry

	refresh_registry()
	return frappe.db.get_value(PLUGIN, name, "status")


@frappe.whitelist(methods=["POST"])
def uninstall_plugin(name):
	require_admin()
	plugin = frappe.get_doc(PLUGIN, name)
	if plugin.enabled:
		set_plugin_enabled(name, 0)
		plugin.reload()
	path = plugin.installed_path
	if path and os.path.isdir(path) and inside(path, processing_dir(), server_staging_dir()):
		shutil.rmtree(path)
	plugin.update({"status": "Analysed", "enabled": 0, "installed_path": None, "installed_version": None, "installed_on": None})
	log(plugin, _("Uninstalled."))
	plugin.save()
	return plugin.status


def enabled_processing_plugins():
	"""Processing plugins for the processing worker to load: [{"id", "parent"}]."""
	settings = frappe.get_cached_doc(SETTINGS)
	if not settings.enable_third_party_plugins or not settings.allow_processing_plugins:
		return []
	rows = frappe.get_all(
		PLUGIN,
		filters={"enabled": 1, "category": "QGIS Processing Plugin", "installed_path": ["is", "set"]},
		fields=["plugin_id", "installed_path"],
	)
	return [{"id": r.plugin_id, "parent": os.path.dirname(r.installed_path)} for r in rows if os.path.isdir(r.installed_path)]


def record_load_errors(errors, providers=None):
	"""After the processing worker loaded the enabled plugins: mark the ones that failed, and note which
	Processing providers each one added (the toolbox hides them for users who switched the plugin off)."""
	for row in frappe.get_all(PLUGIN, filters={"enabled": 1, "category": "QGIS Processing Plugin"}, fields=["name", "plugin_id", "status"]):
		error = (errors or {}).get(row.plugin_id)
		values = {"status": "Error", "last_error": error[:1000]} if error else {"status": "Enabled", "last_error": None}
		values["processing_providers"] = ",".join((providers or {}).get(row.plugin_id) or [])
		frappe.db.set_value(PLUGIN, row.name, values, update_modified=False)


# ---------------------------------------------------------------------------
# the catalogue: what users see, their own choices, requests
# ---------------------------------------------------------------------------

USER_DEFAULT = "gis_plugins_off"  # per-user list of GIS Plugin names switched off for that user
CATALOGUE_FIELDS = [
	"name", "plugin_id", "title", "version", "category", "status", "compatibility_status", "description", "author",
	"homepage", "experimental", "last_error", "requested_by", "request_reason", "rejection_reason", "enabled",
	"processing_providers", "installed_version", "source_url", "modified",
]


def require_gis_user():
	frappe.has_permission("GIS Dataset", "read", throw=True)


def plugins_off_for_user(user=None):
	value = frappe.defaults.get_user_default(USER_DEFAULT, user=user or frappe.session.user)
	return set(frappe.parse_json(value) or []) if value else set()


def hidden_providers(user=None):
	"""Processing providers of the plugins this user switched off for their toolbox."""
	off = plugins_off_for_user(user)
	if not off:
		return set()
	rows = frappe.get_all(PLUGIN, filters={"name": ["in", list(off)]}, pluck="processing_providers")
	return {p for value in rows if value for p in value.split(",") if p}


@frappe.whitelist()
def plugin_catalogue():
	"""The plugins a user may see: everything for System Managers; for others the enabled (approved)
	plugins and their own requests. Each with whether the user has it on for their toolbox."""
	require_gis_user()
	admin = "System Manager" in frappe.get_roles()
	filters = None if admin else [["status", "in", ["Enabled", "Error"]]]
	rows = frappe.get_all(PLUGIN, filters=filters, fields=CATALOGUE_FIELDS, order_by="title asc")
	if not admin:
		rows += frappe.get_all(PLUGIN, filters={"requested_by": frappe.session.user, "status": ["not in", ["Enabled", "Error"]]}, fields=CATALOGUE_FIELDS)
	off = plugins_off_for_user()
	for row in rows:
		row["in_my_toolbox"] = row.status == "Enabled" and row.category == "QGIS Processing Plugin" and row.name not in off
	settings = frappe.get_cached_doc(SETTINGS)
	return {
		"plugins": rows,
		"is_admin": admin,
		"third_party_enabled": bool(settings.enable_third_party_plugins),
	}


@frappe.whitelist(methods=["POST"])
def set_my_plugin(name, use):
	"""Switch an approved processing plugin on or off in the current user's own toolbox."""
	require_gis_user()
	if frappe.db.get_value(PLUGIN, name, "status") not in ("Enabled", "Error"):
		frappe.throw(_("Only approved (enabled) plugins can be switched on or off."))
	off = plugins_off_for_user()
	if cint(use):
		off.discard(name)
	else:
		off.add(name)
	frappe.defaults.set_user_default(USER_DEFAULT, json.dumps(sorted(off)))
	return bool(cint(use))


@frappe.whitelist(methods=["POST"])
def request_plugin(title, download_url, reason=""):
	"""Any GIS user asks for a plugin from the repository; System Managers are notified."""
	require_gis_user()
	if not any(download_url.startswith(prefix) for prefix in allowed_sources()):
		frappe.throw(_("Plugins can only be requested from the allowed sources."))
	existing = frappe.db.get_value(PLUGIN, {"source_url": download_url}, ["name", "status"], as_dict=True)
	if existing:
		return existing.name
	plugin = frappe.get_doc(
		{
			"doctype": PLUGIN,
			"title": title,
			"source_url": download_url,
			"status": "Requested",
			"requested_by": frappe.session.user,
			"request_reason": (reason or "")[:1000],
		}
	)
	plugin.flags.ignore_permissions = True
	plugin.insert()
	for manager in frappe.get_all("Has Role", filters={"role": "System Manager", "parenttype": "User"}, pluck="parent"):
		if manager in ("Administrator", "Guest") or not frappe.db.get_value("User", manager, "enabled"):
			continue
		notify(manager, _("{0} requested the QGIS plugin {1}").format(frappe.session.user, title), plugin.name)
	return plugin.name


@frappe.whitelist(methods=["POST"])
def reject_plugin(name, reason):
	require_admin()
	plugin = frappe.get_doc(PLUGIN, name)
	if plugin.status in ("Installed", "Enabled", "Error"):
		frappe.throw(_("Uninstall the plugin before rejecting it."))
	plugin.status = "Rejected"
	plugin.rejection_reason = reason
	log(plugin, _("Rejected: {0}").format(reason))
	plugin.save()
	if plugin.requested_by:
		notify(plugin.requested_by, _("Your request for the QGIS plugin {0} was declined: {1}").format(plugin.title, reason), plugin.name)


@frappe.whitelist(methods=["POST"])
def approve_request(name):
	"""Download and analyse a requested plugin (the System Manager then installs and enables it)."""
	require_admin()
	plugin = frappe.get_doc(PLUGIN, name)
	if plugin.status not in ("Requested", "Rejected", "Registered"):
		frappe.throw(_("This plugin is past the request stage."))
	plugin.db_set("status", "Registered")
	return download_package(name)


@frappe.whitelist(methods=["POST"])
def upgrade_plugin(name, download_url):
	"""Replace an installed plugin with a newer package: uninstall, download, analyse, install, re-enable."""
	require_admin()
	plugin = frappe.get_doc(PLUGIN, name)
	was_enabled = bool(plugin.enabled)
	if plugin.status in ("Installed", "Enabled", "Error"):
		uninstall_plugin(name)
	frappe.db.set_value(PLUGIN, name, "source_url", download_url)
	download_package(name)
	install_plugin(name)
	if was_enabled:
		set_plugin_enabled(name, 1)
	return frappe.db.get_value(PLUGIN, name, "status")


def notify(user, subject, plugin_name):
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"for_user": user,
			"type": "Alert",
			"subject": subject,
			"document_type": PLUGIN,
			"document_name": plugin_name,
		}
	).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# packages from allowed sources and the QGIS plugin repository
# ---------------------------------------------------------------------------


def allowed_sources():
	text = frappe.db.get_single_value(SETTINGS, "allowed_sources") or ""
	return [line.strip() for line in text.splitlines() if line.strip()]


@frappe.whitelist(methods=["POST"])
def download_package(name):
	require_admin()
	plugin = frappe.get_doc(PLUGIN, name)
	url = (plugin.source_url or "").strip()
	if not url.startswith("https://") or not any(url.startswith(prefix) for prefix in allowed_sources()):
		frappe.throw(_("{0} is not an allowed source (GIS Plugin Settings → Allowed Download Sources).").format(url or "?"))
	if plugin.status in ("Installed", "Enabled"):
		frappe.throw(_("Uninstall the plugin before replacing its package."))
	if plugin.status in ("Requested", "Rejected"):
		plugin.status = "Registered"
	limit = max_bytes()
	fd, temporary = tempfile.mkstemp(suffix=".zip")
	try:
		with os.fdopen(fd, "wb") as out, requests.get(url, stream=True, timeout=60, headers={"User-Agent": "Isambane-Production-GIS/1.0"}) as response:
			response.raise_for_status()
			size = 0
			for chunk in response.iter_content(1 << 16):
				size += len(chunk)
				if size > limit:
					frappe.throw(_("The package is larger than allowed (GIS Plugin Settings)."))
				out.write(chunk)
		from is_production.geo_planning.services.gis_io_service import register_file

		if plugin.package:
			for old in frappe.get_all("File", filters={"file_url": plugin.package, "attached_to_name": plugin.name}, pluck="name"):
				frappe.delete_doc("File", old, ignore_permissions=True)
		file_name = f"gis-plugin-{plugin.name}-{os.path.basename(url.split('?')[0]) or 'package.zip'}"
		plugin.package = register_file(temporary, PLUGIN, plugin.name, "package", file_name)
	finally:
		if os.path.exists(temporary):
			os.remove(temporary)
	plugin.status = "Registered"
	log(plugin, _("Downloaded {0}.").format(url))
	plugin.save()
	return analyse_plugin(plugin.name)


@frappe.whitelist()
def search_repository(query=""):
	"""Search the QGIS plugin repository for plugins for the installed QGIS version (cached for a day).

	Any GIS user may browse it (to request a plugin); only System Managers register or install.
	"""
	require_gis_user()
	settings = frappe.get_cached_doc(SETTINGS)
	version = (frappe.db.get_single_value("GIS Settings", "qgis_version") or "3.44").split("-")[0]
	minor = ".".join(version.split(".")[:2])
	cache_key = f"gis_plugin_repository::{settings.repository_url}::{minor}"
	plugins = frappe.cache.get_value(cache_key)
	if plugins is None:
		response = requests.get(settings.repository_url, params={"qgis": minor}, timeout=60)
		response.raise_for_status()
		if len(response.content) > 50 * 1024 * 1024:
			frappe.throw(_("The repository listing is unexpectedly large."))
		root = ET.fromstring(response.content)
		plugins = []
		for node in root.findall("pyqgis_plugin"):
			text = lambda tag: (node.findtext(tag) or "").strip()  # noqa: E731
			plugins.append(
				{
					"name": node.get("name"),
					"version": node.get("version"),
					"description": text("description"),
					"author": text("author_name"),
					"download_url": text("download_url"),
					"homepage": text("homepage"),
					"experimental": text("experimental").lower() == "true",
					"server": text("server").lower() == "true",
					"tags": text("tags"),
				}
			)
		frappe.cache.set_value(cache_key, plugins, expires_in_sec=24 * 3600)
	q = (query or "").strip().lower()
	matches = [p for p in plugins if not q or q in f"{p['name']} {p['description']} {p['tags']}".lower()]
	known = {r.source_url: r for r in frappe.get_all(PLUGIN, fields=["name", "source_url", "status", "title", "installed_version"]) if r.source_url}
	by_title = {r.title: r for r in known.values()}
	for p in matches[:100]:
		record = known.get(p["download_url"]) or by_title.get(p["name"])
		p["record"] = record.name if record else None
		p["record_status"] = record.status if record else None
		p["upgradeable"] = bool(record and record.installed_version and version_key(p["version"]) > version_key(record.installed_version))
	return matches[:100]


def version_key(text):
	import re

	return tuple(int(n) for n in re.findall(r"\d+", text or "")[:4])


@frappe.whitelist(methods=["POST"])
def register_from_repository(name, download_url):
	require_admin()
	plugin = frappe.get_doc({"doctype": PLUGIN, "title": name, "source_url": download_url, "status": "Registered"}).insert()
	download_package(plugin.name)
	return plugin.name


# ---------------------------------------------------------------------------
# diagnostics (GIS Plugin Settings, health check)
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def scan_plugins():
	frappe.only_for(("System Manager", "Production Manager"))
	settings = frappe.get_single(SETTINGS)
	records = frappe.get_all(PLUGIN, fields=["name", "plugin_id", "category", "status", "enabled", "installed_path", "last_error"])
	known_paths = {r.installed_path for r in records if r.installed_path}
	enabled_server = {r.plugin_id for r in records if r.enabled and r.category == "QGIS Server Plugin"}
	found, unreadable = {}, []
	folders = [("processing", processing_dir()), ("server staging", server_staging_dir())]
	if settings.manage_server_plugins:  # QGIS Server's own folder is shared by all sites: only its manager looks
		folders.append(("QGIS Server", settings.server_plugin_directory))
	for label, directory in folders:
		entries = []
		try:
			if directory and os.path.isdir(directory):
				entries = sorted(d for d in os.listdir(directory) if not d.startswith(".") and os.path.isdir(os.path.join(directory, d)))
		except OSError as e:
			unreadable.append(_("Plugin folder {0} cannot be read by this bench's user ({1}).").format(directory, e.strerror))
		found[label] = entries
	found.setdefault("QGIS Server", [])
	orphans = [f"processing/{d}" for d in found["processing"] if os.path.join(processing_dir(), d) not in known_paths]
	orphans += [f"QGIS Server/{d}" for d in found["QGIS Server"] if d not in enabled_server]
	errors = {r.plugin_id or r.name: r.last_error for r in records if r.status == "Error"}
	counts = {}
	for r in records:
		counts[r.status] = counts.get(r.status, 0) + 1
	summary = _("{0} plugin(s): {1}.").format(len(records), ", ".join(f"{v} {k.lower()}" for k, v in sorted(counts.items())) or _("none"))
	if errors:
		summary += " " + _("Errors: {0}.").format(", ".join(errors))
	if orphans:
		summary += " " + _("Folders without a plugin record: {0}.").format(", ".join(orphans))
	frappe.db.set_single_value(
		SETTINGS,
		{
			"plugin_summary": summary,
			"last_scan_on": now_datetime(),
			"processing_plugin_directory": processing_dir(),
			"diagnostics": json.dumps({"directories": found, "orphans": orphans, "errors": errors, "unreadable": unreadable}, indent=1),
		},
	)
	return {"summary": summary, "orphans": orphans, "errors": errors, "unreadable": unreadable}
