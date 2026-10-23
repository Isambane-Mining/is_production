"""CAD dependency health check (CAD Settings → Check Dependencies, and daily).

Critical: the CAD worker Python, the OCP binding / OCCT and its boolean self-test, model storage and the
job workspace. Optional (warnings only): import/export libraries and disabled features. The browser
renderer assets are checked in the bench's built assets.
"""

import json
import os
import shutil

import frappe
from frappe import _
from frappe.utils import now_datetime

from is_production.cad_engineering.services import cad_storage_service, cad_worker_service

SETTINGS = "CAD Settings"
OPTIONAL_LIBRARIES = {
	"ezdxf": _("DXF import/export (Phase 2)"),
	"numpy": _("surface and volume calculations"),
	"scipy": _("surface tools (Phase 3)"),
	"pythoncdt": _("surface triangulation with breaklines and exact volumes (Phase 3)"),
	"matplotlib": _("point location on surfaces (Phase 3)"),
	"laspy": _("LAS point cloud import (Phase 3)"),
	"lazrs": _("LAZ (compressed LAS) import (Phase 3)"),
}


def run_health_check():
	settings = frappe.get_single(SETTINGS)
	problems, warnings = [], []

	storage = cad_storage_service.model_storage_status()
	if not storage["ok"]:
		problems.append(_("Model storage: {0}").format(storage.get("message")))

	workspace = None
	try:
		workspace = cad_worker_service.job_workspace(f"health-{frappe.generate_hash(length=8)}")
	except OSError as e:
		problems.append(_("The private job workspace cannot be created: {0}").format(e))

	try:
		worker = cad_worker_service.run({"command": "health", "workspace": workspace})
	finally:
		if workspace:
			shutil.rmtree(workspace, ignore_errors=True)

	occt = worker.get("occt") or {"ok": False, "message": worker.get("error")}
	self_test = worker.get("self_test") or {}
	if not occt.get("ok"):
		problems.append(_("Open CASCADE is not available: {0}").format(occt.get("message")))
	elif not self_test.get("ok"):
		problems.append(_("The OCCT self-test failed: {0}").format(self_test.get("message") or json.dumps(self_test)))
	if workspace and worker.get("workspace") and not worker["workspace"].get("ok"):
		problems.append(_("The CAD worker cannot write to its job workspace: {0}").format(worker["workspace"].get("message")))

	libraries = worker.get("libraries") or {}
	for name, purpose in OPTIONAL_LIBRARIES.items():
		if occt.get("ok") and not libraries.get(name):
			warnings.append(_("Optional library {0} is missing; needed for {1}.").format(name, purpose))

	renderer = renderer_status()
	if not renderer["ok"]:
		problems.append(renderer["message"])
	if not settings.cad_worker_enabled:
		warnings.append(_("The CAD worker is switched off: solids, validation and measurements are unavailable."))
	if not settings.enable_cad_workstation:
		warnings.append(_("The CAD Workstation is switched off."))

	status = "Error" if problems else "Warning" if warnings else "OK"
	report = {"worker": worker, "storage": storage, "renderer": renderer, "problems": problems, "warnings": warnings}
	frappe.db.set_single_value(
		SETTINGS,
		{
			"health_status": status,
			"last_check_on": now_datetime(),
			"health_summary": "\n".join(problems + warnings) or _("No problems found."),
			"worker_health_status": _("Available") if occt.get("ok") and self_test.get("ok") else _("Unavailable"),
			"occt_version": occt.get("version"),
			"binding_version": occt.get("binding"),
			"worker_python_version": worker.get("python"),
			"storage_status": _("Writable") if storage["ok"] else _("Not writable"),
			"optional_libraries": "\n".join(f"{name}: {version or _('missing')}" for name, version in libraries.items()),
			"renderer_status": _("Built") if renderer["ok"] else _("Missing"),
			"health_report": json.dumps(report, indent=1, default=str),
		},
	)
	return {"status": status, "problems": problems, "warnings": warnings}


def renderer_status():
	"""The workstation bundle (Vue + three.js) must be built into the bench's assets."""
	from frappe.utils.jinja_globals import bundled_asset

	path = bundled_asset("cad_workstation.bundle.js")
	local = os.path.join(frappe.local.sites_path, path.lstrip("/")) if path else ""
	if path and os.path.isfile(local):
		return {"ok": True, "asset": path}
	return {"ok": False, "message": _("The CAD Workstation assets are not built; run: bench build --app is_production")}


def scheduled_health_check():
	if frappe.db.exists("DocType", SETTINGS) and frappe.db.get_single_value(SETTINGS, "enable_cad_workstation"):
		run_health_check()


def ensure_settings_defaults():
	"""CAD Settings' JSON defaults only exist once it is saved: store any value never set (after migrate).
	Checked against tabSingles directly, because get_single_value turns a missing Check into 0."""
	if not frappe.db.exists("DocType", SETTINGS):
		return
	stored = set(frappe.db.sql_list("select field from `tabSingles` where doctype=%s", SETTINGS))
	for df in frappe.get_meta(SETTINGS).fields:
		if df.default and df.fieldname not in stored:
			frappe.db.set_single_value(SETTINGS, df.fieldname, df.default)
