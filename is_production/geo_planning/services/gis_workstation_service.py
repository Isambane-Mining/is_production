"""Server API for the GIS Workstation portal page (/portal_gis).

The browser map works in Web Mercator (EPSG:3857). Layers are fetched per view: the view box is
transformed into the dataset's CRS, features are found with the native spatial index, and their
geometry is transformed to EPSG:3857 and simplified to the view's pixel size before it is sent.
The browser never holds a whole large dataset. Every call checks Frappe permissions.
"""

from functools import lru_cache

import frappe
from frappe import _
from frappe.utils import cint, flt

from is_production.geo_planning.services.gis_storage_service import NON_SPATIAL_TYPES, features_in_bbox

WEB_MERCATOR = "EPSG:3857"
MERCATOR_LIMIT = 20037508.342789244
DATASET_FIELDS = [
	"name",
	"title",
	"category",
	"status",
	"geometry_type",
	"crs",
	"has_z",
	"feature_count",
	"default_color",
	"qgis_publish_status",
	"qgis_layer_name",
	"extent_min_x",
	"extent_min_y",
	"extent_max_x",
	"extent_max_y",
]


@lru_cache(maxsize=64)
def _transformer(source, target):
	from pyproj import Transformer

	return Transformer.from_crs(source, target, always_xy=True)


@frappe.whitelist()
def get_workstation_config():
	frappe.has_permission("GIS Dataset", "read", throw=True)
	settings = frappe.get_cached_doc("GIS Settings")
	return {
		"default_crs": settings.default_crs or "EPSG:4326",
		"basemap": settings.basemap or None,  # a GIS Map Service name
		"center": [flt(settings.default_center_longitude), flt(settings.default_center_latitude)],
		"zoom": cint(settings.default_zoom) or 11,
		"max_features": cint(settings.max_features_per_request) or 5000,
		"health": {
			"status": settings.health_status,
			"checked_on": settings.last_check_on,
			"summary": settings.health_summary,
			"qgis_version": settings.qgis_version,
		},
		"can_edit_features": bool(frappe.has_permission("GIS Feature", "write")),
		"can_manage_settings": bool(frappe.has_permission("GIS Settings", "write")),
		"can_manage_plugins": "System Manager" in frappe.get_roles(),
		"can_manage_map_services": bool(frappe.has_permission("GIS Map Service", "write")),
		"plugins": frappe.get_all(
			"GIS Plugin", filters={"enabled": 1}, fields=["title", "category", "version", "status"], order_by="title asc"
		) if frappe.db.table_exists("GIS Plugin") else [],
	}


@frappe.whitelist()
def list_datasets():
	"""Datasets the user may read, with their extent in EPSG:3857 for zoom-to-layer."""
	datasets = frappe.get_list(
		"GIS Dataset",
		fields=DATASET_FIELDS,
		filters={"status": ["!=", "Archived"]},
		order_by="title asc",
		limit_page_length=0,
	)
	for dataset in datasets:
		dataset["spatial"] = dataset.geometry_type not in NON_SPATIAL_TYPES
		dataset["published"] = dataset.qgis_publish_status == "Published"
		dataset["extent_3857"] = _extent_3857(dataset) if dataset["spatial"] and dataset.feature_count else None
		for key in ("extent_min_x", "extent_min_y", "extent_max_x", "extent_max_y"):
			dataset.pop(key)
	return datasets


def _extent_3857(dataset):
	try:
		box = _transformer(dataset.crs, WEB_MERCATOR).transform_bounds(
			dataset.extent_min_x, dataset.extent_min_y, dataset.extent_max_x, dataset.extent_max_y, densify_pts=21
		)
	except Exception:
		return None
	return [_clamp(v) for v in box]


@frappe.whitelist()
def get_crs_definition(crs):
	"""PROJ definition of a CRS, so the browser can show coordinates in it.

	proj4js ignores ``+axis``, so the axis order is sent separately: for south-orientated CRSs
	such as the South African Lo zones (``+axis=wsu``) the browser flips the signs to give
	westing/southing, the same x/y that pyproj (always_xy) and QGIS use.
	"""
	import re
	import warnings

	from pyproj import CRS
	from pyproj.exceptions import CRSError

	try:
		definition = CRS.from_user_input(crs)
	except CRSError:
		frappe.throw(_("Unknown CRS {0}.").format(crs))
	code = definition.to_epsg()
	with warnings.catch_warnings():
		warnings.simplefilter("ignore")  # "you will likely lose information": fine for display
		proj4 = definition.to_proj4()
	axis = re.search(r"\+axis=(\w+)", proj4)
	return {
		"code": f"EPSG:{code}" if code else crs,
		"name": definition.name,
		"proj4": re.sub(r"\s*\+axis=\w+", "", proj4),
		"axis": axis.group(1) if axis else "enu",
		"geographic": definition.is_geographic,
		"units": definition.axis_info[0].unit_name if definition.axis_info else "",
	}


@frappe.whitelist()
def get_layer_features(dataset, bbox, resolution=0):
	"""GeoJSON FeatureCollection (EPSG:3857) of a dataset's features in a view box (EPSG:3857)."""
	import shapely
	from shapely.geometry import mapping, shape

	frappe.has_permission("GIS Dataset", "read", dataset, throw=True)
	info = frappe.db.get_value("GIS Dataset", dataset, ["crs", "geometry_type"], as_dict=True)
	if not info or info.geometry_type in NON_SPATIAL_TYPES:
		return {"type": "FeatureCollection", "features": [], "truncated": False}

	bbox = frappe.parse_json(bbox) if isinstance(bbox, str) else bbox
	min_x, min_y, max_x, max_y = (_clamp(flt(v)) for v in bbox)
	try:
		box = _transformer(WEB_MERCATOR, info.crs).transform_bounds(min_x, min_y, max_x, max_y, densify_pts=21)
	except Exception:
		return {"type": "FeatureCollection", "features": [], "truncated": False}

	limit = cint(frappe.get_cached_doc("GIS Settings").max_features_per_request) or 5000
	rows = features_in_bbox(dataset, *box, limit=limit)

	to_web = _transformer(info.crs, WEB_MERCATOR)
	tolerance = max(flt(resolution), 0)
	features = []
	for row in rows:
		if not row.geometry:
			continue
		geom = shapely.force_2d(shape(row.geometry))
		geom = shapely.transform(geom, lambda xy: _transform_xy(to_web, xy))
		if tolerance and geom.geom_type not in ("Point", "MultiPoint"):
			geom = geom.simplify(tolerance, preserve_topology=True)
		features.append(
			{
				"type": "Feature",
				"id": row.name,
				"geometry": mapping(geom),
				"properties": {
					"label": row.feature_label,
					"geometry_type": row.geometry_type,
					"revision": row.revision,
					"validation_status": row.validation_status,
					"attributes": row.attributes,
				},
			}
		)
	return {"type": "FeatureCollection", "features": features, "truncated": len(rows) >= limit}


def _transform_xy(transformer, xy):
	import numpy as np

	x, y = transformer.transform(xy[:, 0], xy[:, 1])
	return np.column_stack([x, y])


def _clamp(value):
	return max(-MERCATOR_LIMIT, min(MERCATOR_LIMIT, value))
