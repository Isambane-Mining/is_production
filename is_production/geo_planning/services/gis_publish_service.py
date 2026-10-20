"""Publish GIS Datasets to QGIS Server.

Each spatial dataset gets its own QGIS project, ``<QGIS Project Directory>/<dataset>.qgs``, written by
the PyQGIS worker ``qgis_worker/publish.py`` (system Python). The project reads features live from
``tabGIS Feature`` through the read-only QGIS database account, so editing features needs no
republish. A dataset is republished (in the background) when anything the project depends on
changes: its title, type, CRS, Z setting, colour or attribute schema, or the QGIS account in
GIS Settings.

One project per dataset keeps the gateway's permission check simple: a request names one dataset, the
gateway checks read permission on it, and the project it opens contains nothing else.
"""

import hashlib
import json
import os

import frappe
from frappe import _
from frappe.utils import now_datetime

from is_production.geo_planning.services.gis_health_service import database_account, run_worker
from is_production.geo_planning.services.gis_storage_service import NON_SPATIAL_TYPES

PUBLISH_FIELDS = ("title", "geometry_type", "crs", "has_z", "default_color")


def project_directory():
	directory = frappe.db.get_single_value("GIS Settings", "qgis_project_directory")
	if not directory:
		frappe.throw(_("Set the QGIS Project Directory in GIS Settings first."))
	return directory


def project_path(dataset_name):
	return os.path.join(project_directory(), f"{dataset_name}.qgs")


def publish_signature(dataset):
	"""Hash of everything the QGIS project is built from (except the database account)."""
	data = {field: dataset.get(field) for field in PUBLISH_FIELDS}
	data["attributes"] = [(row.field_name, row.data_type, row.label) for row in dataset.attributes]
	return hashlib.sha1(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def is_publishable(dataset):
	return dataset.geometry_type not in NON_SPATIAL_TYPES and dataset.status != "Archived"


def enqueue_publish(dataset_name):
	if frappe.flags.in_test:  # tests publish directly; a queued job could outlive the test's dataset
		return
	frappe.db.set_value(
		"GIS Dataset", dataset_name, "qgis_publish_status", "Queued", update_modified=False
	)
	frappe.enqueue(
		"is_production.geo_planning.services.gis_publish_service.publish_dataset",
		queue="long",
		job_id=f"gis-publish-{frappe.local.site}-{dataset_name}",
		deduplicate=True,
		enqueue_after_commit=True,
		dataset_name=dataset_name,
	)


def publish_dataset(dataset_name):
	"""Write the dataset's QGIS project now. Returns the worker's report."""
	if not frappe.db.exists("GIS Dataset", dataset_name):  # deleted while the job waited
		return {"ok": False, "message": _("Dataset {0} no longer exists.").format(dataset_name)}
	dataset = frappe.get_doc("GIS Dataset", dataset_name)
	if not is_publishable(dataset):
		unpublish(dataset_name)
		return {"ok": False, "message": _("Only active spatial datasets are published.")}

	settings = frappe.get_single("GIS Settings")
	if not settings.qgis_db_user:
		return record(dataset, {"ok": False, "message": _("No QGIS database account is set in GIS Settings.")})

	directory = settings.qgis_project_directory
	if not directory or not os.path.isdir(directory) or not os.access(directory, os.W_OK):
		return record(
			dataset, {"ok": False, "message": _("QGIS Project Directory {0} is missing or not writable.").format(directory)}
		)

	extent = None
	if dataset.feature_count:
		extent = [dataset.extent_min_x, dataset.extent_min_y, dataset.extent_max_x, dataset.extent_max_y]
	report = run_worker(
		settings.qgis_python,
		"publish.py",
		{
			"db": database_account(settings),
			"output": os.path.join(directory, f"{dataset.name}.qgs"),
			"dataset": {
				"name": dataset.name,
				"title": dataset.title,
				"geometry_type": dataset.geometry_type,
				"crs": dataset.crs,
				"has_z": bool(dataset.has_z),
				"color": dataset.default_color,
				"extent": extent,
				"attributes": [
					{"field_name": row.field_name, "data_type": row.data_type, "label": row.label}
					for row in dataset.attributes
				],
			},
		},
	)
	if "error" in report:
		report = {"ok": False, "message": report["error"]}
	return record(dataset, report)


def record(dataset, report):
	values = {
		"qgis_publish_status": "Published" if report.get("ok") else "Error",
		"qgis_publish_message": None if report.get("ok") else report.get("message"),
	}
	if report.get("ok"):
		values.update(
			{
				"qgis_project_file": report["path"],
				"qgis_published_on": now_datetime(),
				"qgis_publish_signature": publish_signature(dataset),
				"qgis_layer_name": report["wms_name"],
			}
		)
	frappe.db.set_value("GIS Dataset", dataset.name, values, update_modified=False)
	return report


def unpublish(dataset_name):
	path = frappe.db.get_value("GIS Dataset", dataset_name, "qgis_project_file")
	directory = frappe.db.get_single_value("GIS Settings", "qgis_project_directory")
	# only ever delete a .qgs this app wrote, inside the configured directory
	if path and directory and os.path.isfile(path) and os.path.dirname(path) == os.path.normpath(directory):
		os.remove(path)
	if frappe.db.exists("GIS Dataset", dataset_name):
		frappe.db.set_value(
			"GIS Dataset",
			dataset_name,
			{"qgis_publish_status": "Not Published", "qgis_project_file": None, "qgis_publish_signature": None},
			update_modified=False,
		)


def can_publish():
	"""True when GIS Settings has what publishing needs (projects are written in the background)."""
	settings = frappe.get_cached_doc("GIS Settings")
	return bool(settings.qgis_db_user and settings.qgis_project_directory)


def publish_all():
	"""Republish every active spatial dataset (background jobs)."""
	names = frappe.get_all(
		"GIS Dataset",
		filters={"status": ["!=", "Archived"], "geometry_type": ["not in", list(NON_SPATIAL_TYPES)]},
		pluck="name",
	)
	for name in names:
		enqueue_publish(name)
	return len(names)
