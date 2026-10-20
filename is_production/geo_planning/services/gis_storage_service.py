"""MariaDB spatial storage for GIS Dataset / GIS Feature.

GIS Feature keeps its geometry twice:

- ``geometry`` (JSON field): GeoJSON in the dataset's CRS, full precision,
  including Z. This is what the Frappe API and forms read and write.
- ``geom`` (native ``GEOMETRY`` column, ``SPATIAL`` index): 2D copy with the
  dataset's SRID, written by the controller in the same transaction. Spatial
  queries and QGIS Server (GDAL/OGR MySQL driver, read-only user) use it.

Frappe's schema sync does not know ``geom``, so ``ensure_geometry_schema`` adds
the column and index after install and after every migrate. It is idempotent.
The column has an expression default (an empty geometry collection) because a
SPATIAL index needs NOT NULL and Frappe's INSERT does not list ``geom``.

MariaDB geometries are 2D only; Z values live only in the GeoJSON copy.
"""

import json
import math
import re

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime

FEATURE_TABLE = "tabGIS Feature"
GEOMETRY_COLUMN = "geom"
SPATIAL_INDEX = "gis_feature_geom_sidx"
EMPTY_GEOMETRY_SQL = "ST_GeomFromText('GEOMETRYCOLLECTION EMPTY')"

NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

NON_SPATIAL_TYPES = ("No Geometry", "Raster")
MULTI_OF = {"Point": "MultiPoint", "LineString": "MultiLineString", "Polygon": "MultiPolygon"}

TRUE_VALUES = {"1", "true", "yes", "y", "t"}
FALSE_VALUES = {"0", "false", "no", "n", "f"}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def ensure_geometry_schema():
	"""Add the native geometry column and its spatial index to tabGIS Feature."""
	if not frappe.db.table_exists("GIS Feature"):
		return

	if not frappe.db.sql(f"SHOW COLUMNS FROM `{FEATURE_TABLE}` LIKE %s", GEOMETRY_COLUMN):
		frappe.db.sql_ddl(
			f"ALTER TABLE `{FEATURE_TABLE}` ADD COLUMN `{GEOMETRY_COLUMN}` GEOMETRY NOT NULL "
			f"DEFAULT ({EMPTY_GEOMETRY_SQL})"
		)

	if not _spatial_index_exists():
		frappe.db.sql_ddl(
			f"ALTER TABLE `{FEATURE_TABLE}` ADD SPATIAL INDEX `{SPATIAL_INDEX}` (`{GEOMETRY_COLUMN}`)"
		)


def get_geometry_schema_status():
	"""Report on the spatial schema, for health checks."""
	if not frappe.db.table_exists("GIS Feature"):
		return {"ok": False, "message": "Table tabGIS Feature does not exist."}

	column = frappe.db.sql(f"SHOW COLUMNS FROM `{FEATURE_TABLE}` LIKE %s", GEOMETRY_COLUMN, as_dict=True)
	has_column = bool(column) and column[0].Type.lower() == "geometry"
	has_index = _spatial_index_exists()
	return {
		"ok": has_column and has_index,
		"geometry_column": has_column,
		"spatial_index": has_index,
		"message": "Geometry schema is intact."
		if has_column and has_index
		else "Geometry column or spatial index is missing. Run bench migrate.",
	}


def _spatial_index_exists():
	return bool(
		frappe.db.sql(
			f"SHOW INDEX FROM `{FEATURE_TABLE}` WHERE Key_name = %s AND Index_type = 'SPATIAL'",
			SPATIAL_INDEX,
		)
	)


def write_feature_geometry(feature_name, wkb_hex, srid):
	"""Store a feature's 2D WKB (hex) in the native column (None = empty geometry).

	Hex + UNHEX because MySQLdb sends bytes as a string literal, which
	MariaDB refuses as a ST_GeomFromWKB argument.
	"""
	if wkb_hex is None:
		frappe.db.sql(
			f"UPDATE `{FEATURE_TABLE}` SET `{GEOMETRY_COLUMN}` = {EMPTY_GEOMETRY_SQL} WHERE name = %s",
			feature_name,
		)
	else:
		frappe.db.sql(
			f"UPDATE `{FEATURE_TABLE}` SET `{GEOMETRY_COLUMN}` = ST_GeomFromWKB(UNHEX(%s), %s) WHERE name = %s",
			(wkb_hex, cint(srid), feature_name),
		)


# ---------------------------------------------------------------------------
# CRS
# ---------------------------------------------------------------------------


def resolve_crs(value):
	"""Return ("EPSG:<code>", code) for any CRS pyproj understands that has an EPSG code."""
	from pyproj import CRS
	from pyproj.exceptions import CRSError

	try:
		crs = CRS.from_user_input((value or "").strip())
	except CRSError:
		frappe.throw(_("{0} is not a coordinate reference system PROJ recognises.").format(frappe.bold(value)))

	code = crs.to_epsg()
	if not code:
		frappe.throw(
			_("{0} has no EPSG code. Use an EPSG code such as EPSG:2053.").format(frappe.bold(value))
		)
	return f"EPSG:{code}", code


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


def parse_json_value(value, label):
	"""Accept a dict/list or a JSON string; return the parsed value (None if blank)."""
	if value in (None, ""):
		return None
	if isinstance(value, (dict, list)):
		return value
	try:
		return json.loads(value)
	except (TypeError, ValueError):
		frappe.throw(_("{0} is not valid JSON.").format(label))


def normalize_geometry(value, dataset_geometry_type, has_z):
	"""Validate a GeoJSON geometry against its dataset.

	Returns a dict with the normalised GeoJSON, geometry type, bbox, Z range, 2D WKB and
	validity (WKB as hex). Single parts are promoted to one-part multis for Multi* datasets.
	"""
	import shapely
	from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon, mapping, shape
	from shapely.validation import explain_validity

	data = parse_json_value(value, _("Geometry"))
	if not data:
		frappe.throw(_("Geometry is required."))
	if not isinstance(data, dict):
		frappe.throw(_("Geometry must be a GeoJSON geometry object."))
	if data.get("type") == "Feature":
		data = data.get("geometry") or {}
	if data.get("type") == "FeatureCollection":
		frappe.throw(_("Geometry must be a single geometry, not a FeatureCollection."))

	try:
		geom = shape(data)
	except Exception as e:
		frappe.throw(_("Geometry is not valid GeoJSON: {0}").format(e))

	if geom.is_empty:
		frappe.throw(_("Geometry is empty."))

	coords = shapely.get_coordinates(geom, include_z=geom.has_z)
	if not all(math.isfinite(c) for c in coords.flat):
		frappe.throw(_("Geometry has non-numeric or infinite coordinates."))

	geom_type = geom.geom_type
	if dataset_geometry_type != "Geometry" and geom_type != dataset_geometry_type:
		if MULTI_OF.get(geom_type) == dataset_geometry_type:
			promote = {"Point": MultiPoint, "LineString": MultiLineString, "Polygon": MultiPolygon}
			geom = promote[geom_type]([geom])
			geom_type = geom.geom_type
		else:
			frappe.throw(
				_("This dataset holds {0} geometries; got {1}.").format(dataset_geometry_type, geom_type)
			)

	if has_z and not geom.has_z:
		frappe.throw(_("This dataset needs Z (elevation) values on every coordinate."))
	if not has_z and geom.has_z:
		frappe.throw(_("This dataset is 2D. Remove the Z values, or turn on Has Z on the dataset."))

	is_valid = bool(geom.is_valid)
	min_x, min_y, max_x, max_y = geom.bounds
	z_range = (float(coords[:, 2].min()), float(coords[:, 2].max())) if has_z else (None, None)
	return {
		"geojson": json.dumps(mapping(geom), separators=(",", ":")),
		"geometry_type": geom_type,
		"bbox": (min_x, min_y, max_x, max_y),
		"z_range": z_range,
		"wkb_2d": shapely.to_wkb(shapely.force_2d(geom), hex=True),
		"is_valid": is_valid,
		"validation_message": None if is_valid else explain_validity(geom),
	}


# ---------------------------------------------------------------------------
# Attributes
# ---------------------------------------------------------------------------


def reserved_attribute_names():
	"""Names an attribute may not use: they clash with columns QGIS sees on tabGIS Feature."""
	from frappe.model import default_fields

	names = {GEOMETRY_COLUMN, "fid", "id"} | set(default_fields)
	if frappe.db.exists("DocType", "GIS Feature"):
		names |= {df.fieldname for df in frappe.get_meta("GIS Feature").fields}
	return names


def allowed_values_of(definition):
	return [v.strip() for v in (definition.allowed_values or "").splitlines() if v.strip()]


def coerce_value(definition, value):
	"""Convert one attribute value to its declared type (None stays None)."""
	if value is None or (isinstance(value, str) and value.strip() == "" and definition.data_type != "Text"):
		return None

	label = frappe.bold(definition.field_name)
	data_type = definition.data_type
	try:
		if data_type == "Text":
			value = value if isinstance(value, str) else str(value)
			if cint(definition.max_length) and len(value) > cint(definition.max_length):
				frappe.throw(
					_("{0} is longer than {1} characters.").format(label, cint(definition.max_length))
				)
		elif data_type == "Integer":
			if isinstance(value, bool):
				raise ValueError
			number = float(value)
			if not number.is_integer():
				raise ValueError
			value = int(number)
		elif data_type == "Float":
			if isinstance(value, bool):
				raise ValueError
			value = float(value)
			if not math.isfinite(value):
				raise ValueError
		elif data_type == "Boolean":
			if isinstance(value, bool):
				pass
			elif str(value).strip().lower() in TRUE_VALUES:
				value = True
			elif str(value).strip().lower() in FALSE_VALUES:
				value = False
			else:
				raise ValueError
		elif data_type == "Date":
			value = getdate(value).isoformat()
		elif data_type == "Datetime":
			value = get_datetime(value).strftime("%Y-%m-%d %H:%M:%S")
	except (TypeError, ValueError):
		frappe.throw(_("{0} must be a {1}; got {2}.").format(label, data_type, frappe.bold(repr(value))))

	allowed = allowed_values_of(definition)
	if allowed and value is not None and str(value) not in allowed:
		frappe.throw(_("{0} must be one of: {1}.").format(label, ", ".join(allowed)))
	return value


def coerce_attributes(value, definitions, is_new):
	"""Check a feature's attribute values against the dataset schema.

	Unknown keys are rejected; missing values take the default on new features;
	required fields must end up with a value. Returns the JSON string to store.
	"""
	data = parse_json_value(value, _("Attributes")) or {}
	if not isinstance(data, dict):
		frappe.throw(_("Attributes must be a JSON object."))

	by_name = {d.field_name: d for d in definitions}
	unknown = sorted(set(data) - set(by_name))
	if unknown:
		frappe.throw(
			_("Attributes not in the dataset's schema: {0}.").format(", ".join(frappe.bold(k) for k in unknown))
		)

	result = {}
	for name, definition in by_name.items():
		if name in data:
			result[name] = coerce_value(definition, data[name])
		elif is_new and definition.default_value not in (None, ""):
			result[name] = coerce_value(definition, definition.default_value)
		else:
			result[name] = None

		if cint(definition.required) and result[name] in (None, ""):
			frappe.throw(_("{0} is required.").format(frappe.bold(definition.label or name)))

	return json.dumps(result, separators=(",", ":"), ensure_ascii=False)


# ---------------------------------------------------------------------------
# Dataset statistics and spatial queries
# ---------------------------------------------------------------------------


def refresh_dataset_statistics(dataset):
	"""Recompute feature count and extent of a dataset from its features."""
	row = frappe.db.sql(
		f"""SELECT COUNT(*) AS feature_count,
			MIN(bbox_min_x) AS extent_min_x, MIN(bbox_min_y) AS extent_min_y,
			MAX(bbox_max_x) AS extent_max_x, MAX(bbox_max_y) AS extent_max_y,
			MIN(min_z) AS extent_min_z, MAX(max_z) AS extent_max_z
		FROM `{FEATURE_TABLE}` WHERE dataset = %s""",
		dataset,
		as_dict=True,
	)[0]
	row["last_feature_change"] = now_datetime()
	frappe.db.set_value("GIS Dataset", dataset, row, update_modified=False)


@frappe.whitelist()
def features_in_bbox(dataset, min_x, min_y, max_x, max_y, limit=1000, start=0):
	"""Features of a dataset whose bounding box intersects the given box (dataset CRS).

	Uses the native spatial index. Read permission on the dataset is checked,
	which includes User Permissions on GIS Dataset.
	"""
	frappe.has_permission("GIS Dataset", "read", dataset, throw=True)
	frappe.has_permission("GIS Feature", "read", throw=True)

	srid = frappe.db.get_value("GIS Dataset", dataset, "srid")
	min_x, min_y, max_x, max_y = (float(v) for v in (min_x, min_y, max_x, max_y))
	box = (
		f"POLYGON(({min_x!r} {min_y!r},{max_x!r} {min_y!r},{max_x!r} {max_y!r},"
		f"{min_x!r} {max_y!r},{min_x!r} {min_y!r}))"
	)
	limit = min(max(cint(limit), 1), 20000)
	start = max(cint(start), 0)

	rows = frappe.db.sql(
		f"""SELECT name, feature_label, geometry_type, revision, validation_status, geometry, attributes
		FROM `{FEATURE_TABLE}`
		WHERE dataset = %(dataset)s
			AND MBRIntersects(`{GEOMETRY_COLUMN}`, ST_GeomFromText(%(box)s, %(srid)s))
		ORDER BY name
		LIMIT %(limit)s OFFSET %(start)s""",
		{"dataset": dataset, "box": box, "srid": cint(srid), "limit": limit, "start": start},
		as_dict=True,
	)
	for row in rows:
		row["geometry"] = json.loads(row["geometry"]) if row["geometry"] else None
		row["attributes"] = json.loads(row["attributes"]) if row["attributes"] else {}
	return rows
