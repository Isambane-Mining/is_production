"""Editing GIS Features from the GIS Workstation.

The browser keeps an edit session (undo/redo) and sends it here in one request:

    save_edits(dataset, changes={"adds": [...], "updates": [...], "deletes": [...]}, default_z)

- Geometry arrives as GeoJSON in EPSG:3857 (the map's CRS) and is transformed to the dataset's CRS here
  with pyproj, the same transformation the map's features came from.
- All changes go through the GIS Feature document (permissions, validation, revisions, version
  history): the server is authoritative. They are saved together or not at all: if any change fails,
  everything is rolled back and every failure is returned, keyed by the browser's reference.
- Conflict detection: updates and deletes carry the revision the browser loaded; if someone else
  changed (or deleted) the feature since, the change is refused as a conflict.
- Elevation: MariaDB geometry and the browser are 2D. For datasets with Z, vertices that did not move
  keep their stored Z; new vertices are interpolated along their line or ring from the nearest kept
  ones; a moved single point keeps its Z; anything else takes ``default_z`` (the editor's
  "Z for new vertices").

``geometry_operation`` (split, merge) and ``validate_geometries`` compute results without saving, so the
browser can put them in its edit session (and undo them).
"""

import json
import math

import frappe
from frappe import _
from frappe.utils import cint, flt

from is_production.geo_planning.services.gis_storage_service import (
	MULTI_OF,
	NON_SPATIAL_TYPES,
	normalize_geometry,
	refresh_dataset_statistics,
)
from is_production.geo_planning.services.gis_workstation_service import WEB_MERCATOR, _transform_xy, _transformer

MAX_CHANGES = 5000
SAVEPOINT = "gis_save_edits"
Z_MATCH_DECIMALS = 3  # a vertex "did not move" when it is back within 1 mm of a stored one


class ZRequired(frappe.ValidationError):
	pass


# ---------------------------------------------------------------------------
# Schema for the editor
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_edit_schema(dataset):
	frappe.has_permission("GIS Dataset", "read", dataset, throw=True)
	doc = frappe.get_cached_doc("GIS Dataset", dataset)
	editable = doc.status != "Archived" and doc.geometry_type not in NON_SPATIAL_TYPES
	return {
		"name": doc.name,
		"geometry_type": doc.geometry_type,
		"has_z": bool(doc.has_z),
		"crs": doc.crs,
		"reject_invalid_geometry": bool(doc.reject_invalid_geometry),
		"attributes": [
			{
				"field_name": row.field_name,
				"label": row.label or row.field_name,
				"data_type": row.data_type,
				"required": bool(row.required),
				"default_value": row.default_value,
				"max_length": cint(row.max_length),
				"allowed_values": [v.strip() for v in (row.allowed_values or "").splitlines() if v.strip()],
			}
			for row in doc.attributes
		],
		"can_create": editable and bool(frappe.has_permission("GIS Feature", "create")),
		"can_write": editable and bool(frappe.has_permission("GIS Feature", "write")),
		"can_delete": editable and bool(frappe.has_permission("GIS Feature", "delete")),
	}


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def save_edits(dataset, changes, default_z=None):
	frappe.has_permission("GIS Dataset", "read", dataset, throw=True)
	changes = frappe.parse_json(changes) if isinstance(changes, str) else changes or {}
	adds, updates, deletes = (changes.get(k) or [] for k in ("adds", "updates", "deletes"))
	if len(adds) + len(updates) + len(deletes) > MAX_CHANGES:
		frappe.throw(_("Save at most {0} changes at a time.").format(MAX_CHANGES))
	default_z = None if default_z in (None, "") else flt(default_z)

	ds = frappe.get_doc("GIS Dataset", dataset)
	if ds.status == "Archived":
		frappe.throw(_("Dataset {0} is archived.").format(dataset))

	errors, created, updated, deleted = [], {}, [], []

	def attempt(ref, fn):
		try:
			fn()
		except (frappe.ValidationError, frappe.PermissionError) as e:
			errors.append({"ref": ref, "message": _clean(e), "conflict": isinstance(e, EditConflict)})

	frappe.db.savepoint(SAVEPOINT)
	frappe.flags.in_gis_bulk_write = True
	try:
		for change in deletes:
			attempt(change.get("id"), lambda c=change: deleted.append(_delete(dataset, c)))
		for change in updates:
			attempt(change.get("id"), lambda c=change: updated.append(_update(ds, c, default_z)))
		for change in adds:
			attempt(change.get("ref"), lambda c=change: created.__setitem__(str(c.get("ref")), _add(ds, c, default_z)))

		if errors:
			frappe.db.rollback(save_point=SAVEPOINT)  # only this save's changes
			frappe.local.message_log = []
			return {"ok": False, "errors": errors}

		refresh_dataset_statistics(dataset)
	finally:
		frappe.flags.in_gis_bulk_write = False
	frappe.local.message_log = []
	return {"ok": True, "created": created, "updated": updated, "deleted": deleted}


class EditConflict(frappe.ValidationError):
	pass


def _add(ds, change, default_z):
	doc = frappe.get_doc(
		{
			"doctype": "GIS Feature",
			"dataset": ds.name,
			"feature_label": change.get("label"),
			"geometry": to_native(ds, change.get("geometry"), None, default_z),
			"attributes": change.get("attributes") or {},
		}
	)
	doc.insert()
	return doc.name


def _update(ds, change, default_z):
	doc = _load_current(ds.name, change)
	if "geometry" in change:
		doc.geometry = to_native(ds, change["geometry"], doc, default_z)
	if "attributes" in change:
		doc.attributes = change["attributes"] or {}
	if "label" in change:
		doc.feature_label = change["label"]
	doc.save()
	return {"id": doc.name, "revision": doc.revision}


def _delete(dataset, change):
	doc = _load_current(dataset, change)
	frappe.delete_doc("GIS Feature", doc.name)
	return doc.name


def _load_current(dataset, change):
	name = cint(change.get("id"))
	if not frappe.db.exists("GIS Feature", {"name": name, "dataset": dataset}):
		raise EditConflict(_("Feature {0} was deleted by someone else.").format(name))
	doc = frappe.get_doc("GIS Feature", name)
	if cint(change.get("revision")) != cint(doc.revision):
		raise EditConflict(
			_("Feature {0} was changed by {1} since you loaded it (revision {2}, yours {3}).").format(
				name, doc.modified_by, doc.revision, cint(change.get("revision"))
			)
		)
	return doc


def _clean(e):
	from frappe.utils import strip_html

	return strip_html(str(e)).strip() or e.__class__.__name__


# ---------------------------------------------------------------------------
# Geometry: map CRS <-> dataset CRS, elevation
# ---------------------------------------------------------------------------


def to_native(ds, geometry, existing_doc, default_z):
	"""GeoJSON geometry in EPSG:3857 -> GeoJSON in the dataset's CRS (with Z restored if the dataset has Z)."""
	import shapely
	from shapely.geometry import mapping, shape

	if not geometry:
		frappe.throw(_("Geometry is required."))
	data = geometry if isinstance(geometry, dict) else json.loads(geometry)
	geom = shapely.force_2d(shape(data))
	to_ds = _transformer(WEB_MERCATOR, ds.crs)
	geom = shapely.transform(geom, lambda xy: _transform_xy(to_ds, xy))
	result = mapping(geom)
	if ds.has_z:
		old = None
		if existing_doc and existing_doc.geometry:
			stored = existing_doc.geometry
			old = shape(stored if isinstance(stored, dict) else json.loads(stored))
		result = restore_z(result, old, default_z)
	return json.loads(json.dumps(result))  # tuples -> lists


def restore_z(geojson, old_geom, default_z):
	"""Give every vertex of a 2D GeoJSON geometry a Z (see the module docstring)."""
	import shapely

	lookup, single_z = {}, None
	if old_geom is not None and old_geom.has_z:
		coords = shapely.get_coordinates(old_geom, include_z=True)
		for x, y, z in coords:
			lookup[(round(x, Z_MATCH_DECIMALS), round(y, Z_MATCH_DECIMALS))] = float(z)
		if len(coords) == 1:
			single_z = float(coords[0][2])

	def fill(sequence):
		zs = [lookup.get((round(p[0], Z_MATCH_DECIMALS), round(p[1], Z_MATCH_DECIMALS))) for p in sequence]
		known = [i for i, z in enumerate(zs) if z is not None]
		if not known:
			z = single_z if (single_z is not None and len(sequence) == 1) else default_z
			if z is None:
				raise ZRequired(_("Enter a Z (elevation) for new vertices: this dataset stores elevations."))
			return [[p[0], p[1], z] for p in sequence]
		distance = [0.0]
		for a, b in zip(sequence, sequence[1:]):
			distance.append(distance[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
		for i, z in enumerate(zs):
			if z is not None:
				continue
			before = max((k for k in known if k < i), default=None)
			after = min((k for k in known if k > i), default=None)
			if before is None or after is None:
				zs[i] = zs[after if before is None else before]
			else:
				span = distance[after] - distance[before]
				t = (distance[i] - distance[before]) / span if span else 0.0
				zs[i] = zs[before] + t * (zs[after] - zs[before])
		return [[p[0], p[1], z] for p, z in zip(sequence, zs)]

	def walk(coords):
		if coords and isinstance(coords[0], (int, float)):  # a single position (Point)
			return fill([coords])[0]
		if coords and isinstance(coords[0][0], (int, float)):  # a sequence (line, ring, multipoint)
			if geojson["type"] == "MultiPoint":
				return [fill([p])[0] for p in coords]
			return fill(coords)
		return [walk(c) for c in coords]

	return {"type": geojson["type"], "coordinates": walk(list(_as_lists(geojson["coordinates"])))}


def _as_lists(value):
	return json.loads(json.dumps(value))


# ---------------------------------------------------------------------------
# Geometry operations (no saving)
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def geometry_operation(dataset, operation, geometries, blade=None):
	"""Split or merge geometries (EPSG:3857 GeoJSON); results are coerced to the dataset's type."""
	from shapely.geometry import mapping, shape
	from shapely.ops import linemerge, split, unary_union

	frappe.has_permission("GIS Dataset", "read", dataset, throw=True)
	frappe.has_permission("GIS Feature", "write", throw=True)
	target = frappe.db.get_value("GIS Dataset", dataset, "geometry_type")
	geometries = frappe.parse_json(geometries) if isinstance(geometries, str) else geometries
	shapes = [shape(g) for g in geometries or []]

	if operation == "merge":
		if len(shapes) < 2:
			frappe.throw(_("Select at least two features to merge."))
		merged = unary_union(shapes)
		if merged.geom_type in ("MultiLineString", "GeometryCollection") and all("LineString" in s.geom_type for s in shapes):
			merged = linemerge(merged)
		return {"geometry": mapping(_coerce(merged, target))}

	if operation == "split":
		if not blade:
			frappe.throw(_("Draw a line to split with."))
		cutter = shape(frappe.parse_json(blade) if isinstance(blade, str) else blade)
		results = []
		for geom in shapes:
			parts = []
			for single in getattr(geom, "geoms", [geom]):
				if "Point" in single.geom_type:
					parts.append(single)
					continue
				parts.extend(split(single, cutter).geoms)
			results.append([mapping(_coerce(p, target)) for p in parts] if len(parts) > len(getattr(geom, "geoms", [geom])) else None)
		if not any(results):
			frappe.throw(_("The line does not cut any of the selected features."))
		return {"results": results}

	frappe.throw(_("Unknown operation {0}.").format(operation))


def _coerce(geom, target):
	"""Make a result fit the dataset's geometry type (single -> multi), or explain why it cannot."""
	from shapely.geometry import MultiLineString, MultiPoint, MultiPolygon

	kind = geom.geom_type
	if target == "Geometry" or kind == target:
		return geom
	if MULTI_OF.get(kind) == target:
		return {"Point": MultiPoint, "LineString": MultiLineString, "Polygon": MultiPolygon}[kind]([geom])
	frappe.throw(
		_("The result is a {0}, which a {1} dataset cannot hold (e.g. the merged features do not touch).").format(
			kind, target
		)
	)


@frappe.whitelist(methods=["POST"])
def validate_geometries(dataset, items):
	"""Check edited geometries without saving: [{ref, geometry (EPSG:3857)}] -> [{ref, valid, message}]."""
	import shapely
	from shapely.geometry import mapping, shape

	frappe.has_permission("GIS Dataset", "read", dataset, throw=True)
	ds = frappe.get_cached_doc("GIS Dataset", dataset)
	to_ds = _transformer(WEB_MERCATOR, ds.crs)
	results = []
	for item in (frappe.parse_json(items) if isinstance(items, str) else items or [])[:MAX_CHANGES]:
		try:
			geom = shapely.transform(shapely.force_2d(shape(item["geometry"])), lambda xy: _transform_xy(to_ds, xy))
			check = normalize_geometry(mapping(geom), ds.geometry_type, False)
			results.append({"ref": item.get("ref"), "valid": check["is_valid"], "message": check["validation_message"]})
		except frappe.ValidationError as e:
			results.append({"ref": item.get("ref"), "valid": False, "message": _clean(e)})
	frappe.local.message_log = []
	return results
