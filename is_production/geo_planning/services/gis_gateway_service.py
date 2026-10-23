"""The QGIS gateway: the only way browsers reach QGIS Server.

``GET /api/method/is_production.geo_planning.services.gis_gateway_service.ows?dataset=<name>&SERVICE=...``

- the Frappe session must have read permission on the dataset (User Permissions included);
- only read-only requests pass: WMS GetMap / GetFeatureInfo / GetLegendGraphic / GetCapabilities and
  WFS GetCapabilities / DescribeFeatureType / GetFeature; never WFS-T or GetPrint;
- the project (MAP) is set here from the dataset, never taken from the request, and each dataset's
  project contains only that dataset, so a request cannot reach another dataset's data;
- image sizes and feature counts are capped.

QGIS Server itself listens on 127.0.0.1 only (GIS Settings → QGIS Server URL).
"""

import os

import frappe
import requests
from frappe import _
from frappe.utils import cint
from werkzeug.wrappers import Response

ALLOWED_REQUESTS = {
	"WMS": {"GETMAP", "GETFEATUREINFO", "GETLEGENDGRAPHIC", "GETCAPABILITIES"},
	"WFS": {"GETCAPABILITIES", "DESCRIBEFEATURETYPE", "GETFEATURE"},
}
# request parameters that are Frappe's or the gateway's own, or that could pick another project
DROPPED_PARAMS = {"MAP", "CMD", "DATASET", "_", "_T"}  # _t: the client's cache-buster
MAX_IMAGE_SIZE = 4096
TIMEOUT = 60
PASSED_HEADERS = ("Content-Type", "Content-Disposition")


@frappe.whitelist(methods=["GET"])
def ows(dataset=None, **kwargs):
	params = {k.upper(): v for k, v in frappe.local.form_dict.items() if isinstance(v, str)}
	dataset = dataset or params.get("DATASET")
	status, content, headers = proxy(dataset, params)
	response = Response(content, status=status)
	for name, value in headers.items():
		response.headers[name] = value
	# images and feature responses change when features are edited: short private caching only
	response.headers["Cache-Control"] = "private, max-age=15"
	return response


def proxy(dataset, params):
	"""Check a request and pass it to QGIS Server. Returns (status, body, headers)."""
	if not dataset:
		frappe.throw(_("Name a dataset."), frappe.ValidationError)
	frappe.has_permission("GIS Dataset", "read", dataset, throw=True)

	info = frappe.db.get_value(
		"GIS Dataset", dataset, ["qgis_publish_status", "qgis_project_file", "status"], as_dict=True
	)
	if not info or info.status == "Archived" or info.qgis_publish_status not in ("Published", "Queued"):
		frappe.throw(_("Dataset {0} is not published to QGIS.").format(dataset), frappe.DoesNotExistError)
	if not info.qgis_project_file or not os.path.isfile(info.qgis_project_file):
		frappe.throw(_("The QGIS project of {0} is missing; publish it again.").format(dataset), frappe.DoesNotExistError)

	service = (params.get("SERVICE") or "WMS").upper()
	request = (params.get("REQUEST") or "").upper()
	if request not in ALLOWED_REQUESTS.get(service, ()):
		frappe.throw(_("{0} {1} is not allowed through the GIS gateway.").format(service, request or "?"), frappe.PermissionError)

	query = {k: v for k, v in params.items() if k not in DROPPED_PARAMS}
	query["SERVICE"] = service
	query["REQUEST"] = request
	for key in ("WIDTH", "HEIGHT"):
		if key in query:
			query[key] = str(min(max(cint(query[key]), 1), MAX_IMAGE_SIZE))
	if request == "GETFEATURE":
		limit = cint(frappe.get_cached_doc("GIS Settings").max_features_per_request) or 5000
		for key in ("MAXFEATURES", "COUNT"):
			query[key] = str(min(cint(query.get(key)) or limit, limit))
	if request == "GETFEATUREINFO":
		query["FEATURE_COUNT"] = str(min(cint(query.get("FEATURE_COUNT")) or 1, 50))
	query["MAP"] = info.qgis_project_file

	url = frappe.get_cached_doc("GIS Settings").qgis_server_url
	try:
		upstream = requests.get(url, params=query, timeout=TIMEOUT)
	except requests.RequestException as e:
		frappe.log_error(title="GIS gateway: QGIS Server unreachable", message=str(e))
		return 502, b"QGIS Server is not reachable.", {"Content-Type": "text/plain; charset=utf-8"}

	headers = {name: upstream.headers[name] for name in PASSED_HEADERS if name in upstream.headers}
	return upstream.status_code, upstream.content, headers
