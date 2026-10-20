"""Mining Simulation Project <-> the simulator's project model.

The simulator (portal page /portal_mining_simulation) works on one JSON project object. Each part of it
is kept in its own fields here: the header settings on the parent, and surfaces, layers,
blocks, activities, fleet, roads, dumps, overlays, depth limits and pits in child tables.
The terrain grid (and an optional satellite image) are binary and stay private File
attachments.

The mapping is lossless: per document and per row, `json_meta` records which mapped keys the
source object had, which of them were null (numeric fields cannot hold null), and any keys
the mapping does not know yet, so newer simulator versions keep their data. Exporting a
project returns the object it was imported from.
"""

import base64
import copy
import json
import os
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

DOCTYPE = "Mining Simulation Project"
BUNDLE_FORMAT = "rollover-mine-sim"
BLOB_FIELDS = {"grid": "grid_file", "sat": "imagery_file"}

# (path in the project object, fieldname, kind). A kind ending in "?" is an optional value: blank in a
# row made in the desk, it is left out of the object, as the simulator reads 0 as a value there.
HEADER_MAP = [
	("name", "project_name", "str"),
	("description", "description", "str"),
	("startDate", "start_date", "date"),
	("schema", "schema_version", "int"),
	("gridVersion", "grid_version", "int"),
	("isDemo", "is_demo", "bool"),
	("created", "source_created", "str"),
	("modified", "source_modified", "str"),
	("crs.preset", "crs_preset", "str"),
	("crs.proj4", "crs_proj4", "str"),
	("crs.axis", "crs_axis", "str"),
	("calendar", "hours", "weekhours"),
	("calendar.saHolidays", "sa_holidays", "bool"),
	("calendar.maxYears", "max_years", "int"),
	("grid.x0", "grid_x0", "float"),
	("grid.y0", "grid_y0", "float"),
	("grid.nx", "grid_nx", "int"),
	("grid.ny", "grid_ny", "int"),
	("grid.cell", "grid_cell", "float"),
	("cutoff.by", "cutoff_by", "str"),
	("cutoff.stop", "cutoff_stop", "bool"),
	("targets.coal", "target_coal", "float?"),
	("targets.waste", "target_waste", "float?"),
	("targets.wasteUnit", "target_waste_unit", "str"),
	("targets.basis", "target_basis", "str"),
	("targets.mode", "target_mode", "str"),
	("targets.coalBasis", "target_coal_basis", "str"),
	("targets.rampMonths", "ramp_up_months", "int"),
	("targets.exposedMonths", "exposed_coal_months", "float"),
	("teams.count", "team_count", "float"),
	("teams.perTeam", "volume_per_team", "float"),
	("losses.geo", "geological_loss", "float"),
	("losses.mining", "mining_loss", "float"),
	("losses.contam", "contamination", "float"),
	("topsoilAsWaste", "topsoil_as_waste", "bool"),
	("startBy.topsoil", "start_topsoil_by", "date"),
	("startBy.waste", "start_waste_by", "date"),
	("startBy.coal", "start_coal_by", "date"),
	("bench.on", "bench_on", "bool"),
	("bench.lead", "bench_lead", "int"),
	("reserve.on", "reserve_on", "bool"),
	("reserve.name", "reserve_name", "str"),
	("reserve.pts", "reserve_points", "json"),
	("rehab.on", "rehab_on", "bool"),
	("rehab.backfillTo", "backfill_to", "str"),
	("rehab.above", "backfill_above", "float"),
	("rehab.topsoil", "topsoil_thickness", "float"),
	("rehab.radius", "surrounding_radius", "float"),
	("rehab.soilDirect", "topsoil_direct", "bool"),
	("rehab.lift", "lift_landform", "bool"),
	("rehab.liftMax", "lift_by", "float"),
	("rehab.liftTie", "lift_tie_slope", "float"),
	("rehab.liftBank", "lift_bank_slope", "float"),
	("rehab.finalVoid", "fill_final_void", "bool"),
	("rehab.finalRate", "final_void_rate", "float?"),
	("rehab.pathFall", "flow_path_fall", "float"),
	("rehab.bank", "drain_bank_slope", "float"),
	("rehab.cutDepth", "drain_depth", "float"),
	("voidTip.on", "void_tip_on", "bool"),
	("voidTip.ref", "void_tip_ref", "str"),
	("voidTip.afterStrips", "void_tip_after_strips", "int"),
	("voidTip.roads", "void_tip_roads", "bool"),
	("voidTip.dozeAfterStrips", "void_tip_doze_after_strips", "int"),
	("imagery.provider", "imagery_provider", "str"),
	("imagery.googleKey", "imagery_google_key", "str"),
	("imagery.customUrl", "imagery_custom_url", "str"),
	("imagery.fileName", "imagery_file_name", "str"),
	("imagery.opacity", "imagery_opacity", "float"),
	("view.vex", "vertical_exaggeration", "float"),
	("idColumn", "id_column", "str"),
	("attrColumns", "attribute_columns", "json"),
	("attrReport", "attribute_report", "json"),
	("source", "source_json", "json"),
	("stripField", "strip_field", "str"),
	# the analysis modules: nested settings with per-seam values, kept as JSON
	("margin", "margin_settings", "json"),
	("wash", "washability", "json"),
	("hydro", "hydrogeology", "json"),
	("blast", "drill_and_blast", "json"),
	("fin", "finance", "json"),
	("accTable", "accumulation_table", "json"),
]
# Working hours for each weekday (Monday first, as the simulator's calendar.workDays). The simulator's engine
# takes one figure, calendar.hoursPerDay: it gets the average over the working days, so the hours of a week are
# the same. The list itself goes along as calendar.hoursByDay, for a simulator that reads it.
HOUR_FIELDS = ["hours_monday", "hours_tuesday", "hours_wednesday", "hours_thursday", "hours_friday", "hours_saturday", "hours_sunday"]
PRODUCTION_EQUIPMENT_KINDS = {"ADT": "truck", "Rigid": "truck", "Excavator": "excavator", "Dozer": "dozer"}

# project key -> (table fieldname, id key, [(path, fieldname, kind)])
TABLE_MAP = {
	"surfaces": ("surfaces", "id", [
		("id", "surface_id", "str"),
		("name", "surface_name", "str"),
		("file", "source_file", "str"),
	]),
	"layers": ("layers", "id", [
		("id", "layer_id", "str"),
		("name", "layer_name", "str"),
		("type", "layer_type", "str"),
		("top", "top_surface", "str"),
		("bottom", "bottom_surface", "str"),
		("density", "density", "float"),
		("swell", "swell", "float"),
		("minThick", "min_thickness", "float"),
		("losses.geo", "geological_loss", "float?"),
		("losses.mining", "mining_loss", "float?"),
		("losses.contam", "contamination", "float?"),
		("color", "color", "str"),
		("densityField", "density_field", "str"),
		("qualityFields", "quality_fields", "json"),
	]),
	"blocks": ("blocks", "id", [
		("id", "block_id", "str"),
		("pts", "points", "json"),
		("attrs", "attributes", "json"),
	]),
	"activities": ("activities", "id", [
		("id", "activity_id", "str"),
		("name", "activity_name", "str"),
		("layerId", "layer_id", "str"),
		("pitId", "pit_id", "str"),
		("method", "mining_method", "str"),
		("destMode", "destination_mode", "str"),
		("dests", "destinations", "json"),
		("pushBearing", "push_bearing", "float"),
		("lookahead", "lookahead", "int"),
		("startDate", "start_date", "date"),
		("maxRate", "max_rate", "float?"),
		("dozeShare", "doze_share", "float"),
		("mixMode", "mix_mode", "str"),
		("fleetOf", "shared_fleet_of", "str"),
		("dep.on", "depends_on", "str"),
		("dep.lead", "dependency_lead", "int"),
		("dep.unit", "dependency_unit", "str"),
		("color", "color", "str"),
		("path", "block_path", "json"),
	]),
	"fleet": ("fleet", "id", [
		("id", "equipment_id", "str"),
		("kind", "equipment_kind", "str"),
		("name", "equipment_name", "str"),
		("activityId", "activity_id", "str"),
		("team", "team", "str"),
		("assetCategory", "asset_category", "str"),
		("item", "item", "str"),
		("qty", "qty", "float"),
		("avail", "availability", "float"),
		("util", "utilisation", "float"),
		("prodMode", "production_mode", "str"),
		("prod", "production_rate", "float"),
		("bucket", "bucket_size", "float"),
		("fill", "bucket_fill", "float"),
		("cycle", "cycle_time", "float"),
		("payload", "payload", "float"),
		("body", "body_volume", "float"),
		("spot", "spot_time", "float"),
		("dump", "dump_time", "float"),
		("vLoadFlat", "speed_loaded_flat", "float"),
		("vLoadRamp", "speed_loaded_ramp", "float"),
		("vEmptyFlat", "speed_empty_flat", "float"),
		("vEmptyRamp", "speed_empty_ramp", "float"),
	]),
	"roads": ("roads", "id", [
		("id", "road_id", "str"),
		("name", "road_name", "str"),
		("type", "road_type", "str"),
		("width", "road_width", "float"),
		("gradient", "gradient", "float"),
		("startElev", "start_elevation", "float"),
		("endElev", "end_elevation", "float"),
		("leaveInBlocks", "leave_in_blocks", "bool"),
		("follow", "follows_pit", "bool"),
		("spoilDumpId", "spoil_dump_id", "str"),
		("cutBlocks", "cut_blocks", "bool"),
		("toeSurf", "toe_surface", "str"),
		("useFrom", "use_from", "date"),
		("useTo", "use_to", "date"),
		("pts", "points", "json"),
	]),
	"dumps": ("dumps", "id", [
		("id", "dump_id", "str"),
		("name", "dump_name", "str"),
		("kind", "dump_kind", "str"),
		("maxHeight", "max_height", "float"),
		("crestElev", "crest_elevation", "float?"),
		("fillTo", "fill_to", "str"),
		("liftHeight", "lift_height", "float"),
		("faceAngle", "face_angle", "float"),
		("color", "color", "str"),
		("entry", "entry_point", "json"),
		("pts", "points", "json"),
	]),
	"overlays": ("overlays", "id", [
		("id", "overlay_id", "str"),
		("name", "overlay_name", "str"),
		("file", "source_file", "str"),
		("color", "color", "str"),
		("show", "show_overlay", "bool"),
		("surface", "surface_id", "str"),
		("angle", "wall_angle", "float"),
		("dumps", "applies_to_dumps", "bool"),
		("folders", "folders", "json"),
		("features", "features", "json"),
	]),
	"limits": ("limits", "id", [
		("id", "limit_id", "str"),
		("name", "limit_name", "str"),
		("layerId", "layer_id", "str"),
		("pts", "points", "json"),
	]),
	"pits": ("pits", "id", [
		("id", "pit_id", "str"),
		("name", "pit_name", "str"),
		("color", "color", "str"),
		("startDate", "start_date", "date"),
		("priority", "priority", "bool"),
		("boxcut.months", "boxcut_months", "float?"),
		("boxcut.pts", "boxcut_points", "json?"),
		("pts", "points", "json"),
	]),
	# the teams the equipment is allocated to (ERP planning; the simulator works with teams.count)
	"teamList": ("teams", "id", [
		("id", "team_id", "str"),
		("name", "team_name", "str"),
		("remarks", "remarks", "str"),
	]),
	"batters": ("batters", "id", [
		("id", "batter_id", "str"),
		("name", "batter_name", "str"),
		("angle", "wall_angle", "float"),
		("side", "side", "str"),
		("surfId", "surface_id", "str"),
		("pts", "points", "json"),
	]),
	"oldDumps": ("old_dumps", "id", [
		("id", "old_dump_id", "str"),
		("name", "old_dump_name", "str"),
		("elev", "elevation", "float?"),
		("start", "start_date", "date"),
		("rate", "rate", "float"),
		("dest", "destination_dump_id", "str"),
		("pts", "points", "json"),
	]),
	"rehandles": ("rehandles", "id", [
		("id", "rehandle_id", "str"),
		("name", "rehandle_name", "str"),
		("from", "from_dump_id", "str"),
		("to", "destination", "str"),
		("when", "timing", "str"),
		("start", "start_date", "date"),
		("rate", "rate", "float"),
		("qty", "quantity", "float?"),
		("color", "color", "str"),
	]),
	"fills": ("fills", "id", [
		("id", "fill_id", "str"),
		("name", "fill_name", "str"),
		("start", "start_date", "date"),
		("pts", "points", "json"),
	]),
	"raises": ("raises", "id", [
		("id", "raise_id", "str"),
		("name", "raise_name", "str"),
		("height", "height", "float"),
		("slope", "slope", "float"),
		("pts", "points", "json"),
	]),
	"drains": ("drains", "id", [
		("id", "drain_id", "str"),
		("name", "drain_name", "str"),
		("pts", "points", "json"),
	]),
	"qrasters": ("quality_rasters", "id", [
		("id", "raster_id", "str"),
		("label", "label", "str"),
		("kind", "quality_kind", "str"),
		("layerId", "layer_id", "str"),
		("file", "source_file", "str"),
		("col", "attribute_column", "str"),
		("res", "resolution", "float"),
		("read", "read_on", "date"),
		("guessed", "guessed", "bool"),
		("seamGuessed", "seam_guessed", "bool"),
		("sig", "signature", "str"),
		("stats", "statistics", "json"),
		("prev", "previous", "json"),
		("cmp", "comparison", "json"),
	]),
}

_MISSING = object()


# ---------------------------------------------------------------- object paths
def _get(obj, path):
	for part in path.split("."):
		if not isinstance(obj, dict) or part not in obj:
			return _MISSING
		obj = obj[part]
	return obj


def _set(obj, path, value):
	parts = path.split(".")
	for part in parts[:-1]:
		if not isinstance(obj.get(part), dict):
			obj[part] = {}
		obj = obj[part]
	obj[parts[-1]] = value


def _pop(obj, path):
	"""Remove a mapped leaf; drop parent objects it leaves empty."""
	parts = path.split(".")
	chain = [obj]
	for part in parts[:-1]:
		obj = obj[part]
		chain.append(obj)
	del obj[parts[-1]]
	for i in range(len(parts) - 1, 0, -1):
		if chain[i] == {}:
			del chain[i - 1][parts[i - 1]]


def _merge(base, extra):
	for k, v in extra.items():
		if isinstance(v, dict) and isinstance(base.get(k), dict):
			_merge(base[k], v)
		else:
			base[k] = v
	return base


def _empty(v):
	return v is None or v == "" or v == 0 or v is False or v == [] or v == {}


# ---------------------------------------------------------------- values
def _to_field(value, kind):
	if value is None:
		return None
	if kind == "json":
		return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
	if kind == "bool":
		return 1 if value else 0
	if kind == "int":
		return cint(value)
	if kind == "float":
		return flt(value)
	if kind == "date":
		return getdate(value).isoformat() if value else None
	return value if isinstance(value, str) else str(value)


def _from_field(value, kind):
	if kind == "json":
		return json.loads(value) if value else None
	if kind == "bool":
		return bool(cint(value))
	if kind == "int":
		return cint(value) if value is not None else None
	if kind == "float":
		if value is None:
			return None
		f = flt(value)
		return int(f) if f.is_integer() else f
	if kind == "date":
		return str(value) if value else ""
	return value if value is not None else ""


def _fits(value, kind):
	"""Whether a value has the shape its field holds; anything else is kept as-is in json_meta."""
	if kind == "json":
		return True
	if kind == "str":
		return isinstance(value, str)
	if kind == "bool":
		return isinstance(value, bool)
	if kind == "int":
		return isinstance(value, int) and not isinstance(value, bool)
	if kind == "float":
		return isinstance(value, (int, float)) and not isinstance(value, bool)
	if kind == "date":
		return isinstance(value, str) and (value == "" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) is not None)
	return False


def _blank(kind):
	return None if kind in ("json", "date", "str") else 0


def _select_options(doctype):
	return {
		df.fieldname: set((df.options or "").split("\n"))
		for df in frappe.get_meta(doctype).fields
		if df.fieldtype == "Select"
	}


def _read(target, source, mapping, options=None):
	"""Copy an object's mapped keys into `target` (doc or dict); return its json_meta.

	`options` (fieldname -> allowed values) keeps Select values the field does not offer yet
	in json_meta instead of failing validation."""
	options = options or {}
	extra = copy.deepcopy(source)
	keys, nulls = [], []
	for path, field, kind in mapping:
		kind = kind.rstrip("?")
		value = _get(source, path)
		if kind == "weekhours":
			_read_week(target, value if isinstance(value, dict) else {}, keys, nulls, extra)
			continue
		target[field] = _blank(kind)
		if value is _MISSING:
			continue
		keys.append(path)
		if value is None:
			nulls.append(path)
			_pop(extra, path)
		elif _fits(value, kind) and (field not in options or value in options[field]):
			target[field] = _to_field(value, kind)
			_pop(extra, path)
	return {"keys": keys, "nulls": nulls, "extra": extra}


def _number(v):
	return isinstance(v, (int, float)) and not isinstance(v, bool)


def _read_week(target, cal, keys, nulls, extra):
	"""calendar.workDays / hoursPerDay / hoursByDay -> hours_<weekday> and hours_per_day."""
	days, hpd, by_day = cal.get("workDays"), cal.get("hoursPerDay"), cal.get("hoursByDay")
	days_ok = isinstance(days, list) and len(days) == 7 and all(d in (0, 1) for d in days)
	by_day_ok = isinstance(by_day, list) and len(by_day) == 7 and all(_number(h) and h >= 0 for h in by_day)
	worked = [bool(d) for d in days] if days_ok else [True] * 7
	if by_day_ok and [h > 0 for h in by_day] == worked and _number(hpd):
		working = [h for h in by_day if h > 0]
		if working and abs(sum(working) / len(working) - hpd) > 1e-6:
			by_day_ok = False  # hours per day changed in the simulator since: it wins
	else:
		by_day_ok = False
	hours = by_day if by_day_ok else [(hpd if _number(hpd) else 0) if w else 0 for w in worked]
	for f, h in zip(HOUR_FIELDS, hours):
		target[f] = flt(h)
	target["hours_per_day"] = flt(hpd) if _number(hpd) else 0
	for key, ok in (("workDays", days_ok), ("hoursPerDay", _number(hpd)), ("hoursByDay", by_day_ok)):
		if key in cal and ok:
			keys.append("calendar." + key)
			_pop(extra, "calendar." + key)
	if days_ok and any(isinstance(d, bool) for d in days):
		nulls.append("calendar.workDays:bool")


def _write_week(out, source, keys, nulls):
	hours = [flt(source.get(f)) for f in HOUR_FIELDS]
	working = [h for h in hours if h > 0]
	hpd = round(sum(working) / len(working), 6) if working else flt(source.get("hours_per_day"))
	hpd = int(hpd) if float(hpd).is_integer() else hpd
	days = [1 if h > 0 else 0 for h in hours]
	if keys is None or "calendar.workDays" in keys:
		_set(out, "calendar.workDays", [bool(d) for d in days] if "calendar.workDays:bool" in nulls else days)
	if keys is None or "calendar.hoursPerDay" in keys:
		_set(out, "calendar.hoursPerDay", hpd)
	if (keys is not None and "calendar.hoursByDay" in keys) or len(set(working)) > 1:
		_set(out, "calendar.hoursByDay", [int(h) if h.is_integer() else h for h in hours])


def _dump(meta):
	return json.dumps(meta, separators=(",", ":"), ensure_ascii=False)


def _write(source, mapping, meta_json):
	"""Rebuild an object from mapped fields of `source` (doc or row) and its json_meta."""
	meta = json.loads(meta_json) if meta_json else None
	keys = set(meta["keys"]) if meta else None
	nulls = set(meta["nulls"]) if meta else set()
	out = {}
	for path, field, kind in mapping:
		optional, kind = kind.endswith("?"), kind.rstrip("?")
		if kind == "weekhours":
			_write_week(out, source, keys, nulls)
			continue
		value = _from_field(source.get(field), kind)
		if path in nulls and _empty(value):
			value = None
		elif (keys is not None or optional) and (keys is None or path not in keys) and _empty(value):
			continue  # not in the source object (or, made in the desk, an optional value left blank)
		_set(out, path, value)
	if meta:
		_merge(out, meta["extra"])
	return out


# ---------------------------------------------------------------- project <-> document
def apply_project(doc, project):
	"""Fill a Mining Simulation Project document from a simulator project object."""
	header = {k: v for k, v in project.items() if k not in TABLE_MAP and k != "id"}
	values = {}
	meta = _read(values, header, HEADER_MAP, _select_options(DOCTYPE))
	meta["order"] = list(project.keys())
	meta["absent"] = []
	doc.update(values)

	for key, (table, id_key, mapping) in TABLE_MAP.items():
		items = project.get(key)
		if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
			# a missing or odd-shaped table (e.g. null) is kept exactly as it was
			if key in project:
				meta["extra"][key] = items
			else:
				meta["absent"].append(key)
			doc.set(table, [])
			continue
		options = _select_options(doc.meta.get_field(table).options)
		existing = {r.get(mapping[0][1]): r for r in doc.get(table) or []}
		rows = []
		for item in items:
			row_values = {}
			row_meta = _read(row_values, item, mapping, options)
			row = existing.pop(item.get(id_key), None) if isinstance(item.get(id_key), str) else None
			if row is None:
				row = doc.append(table, {})
			row.update(row_values)
			row.json_meta = _dump(row_meta)
			rows.append(row)
		doc.set(table, rows)
	doc.json_meta = _dump(meta)
	return doc


def build_project(doc):
	"""The simulator project object stored in a Mining Simulation Project document."""
	meta = json.loads(doc.json_meta) if doc.json_meta else {}
	project = {"id": doc.name}
	project.update(_write(doc, HEADER_MAP, doc.json_meta))
	if not meta and not (cint(doc.grid_nx) and cint(doc.grid_ny)):
		project["grid"] = None  # created in the desk: no terrain grid yet
	order = meta.get("order") or []
	for key, (table, _id_key, mapping) in TABLE_MAP.items():
		if key in meta.get("extra", {}) and not doc.get(table):
			continue  # kept as it was in json_meta (e.g. null)
		if (key in meta.get("absent", []) or (order and key not in order)) and not doc.get(table):
			continue  # not in the source object (or added to this model since), and still empty
		project[key] = [_write(row, mapping, row.json_meta) for row in doc.get(table) or []]
	return {k: project[k] for k in order if k in project} | project


def new_project_id():
	return "prj_" + frappe.generate_hash(length=12).lower()


def _clean_id(value):
	value = str(value or "").strip()
	return value if re.fullmatch(r"[A-Za-z0-9_\-]{3,100}", value) else None


# ---------------------------------------------------------------- blobs (grid, satellite image)
def _blob_field(key):
	if key not in BLOB_FIELDS:
		frappe.throw(_("Unknown project data: {0}").format(key))
	return BLOB_FIELDS[key]


def _read_blob(doc, key):
	"""The stored bytes (gzip, as the simulator writes them)."""
	url = doc.get(_blob_field(key))
	if not url:
		return None
	name = frappe.db.get_value("File", {"file_url": url, "attached_to_doctype": DOCTYPE, "attached_to_name": doc.name})
	if not name:
		return None
	with open(frappe.get_doc("File", name).get_full_path(), "rb") as f:
		return f.read()


def _write_blob(doc, key, data):
	"""Replace a blob attachment with `data` (gzip bytes) or remove it (None). Saves no document."""
	field = _blob_field(key)
	for name in frappe.get_all(
		"File", filters={"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name, "attached_to_field": field}, pluck="name"
	):
		frappe.delete_doc("File", name, ignore_permissions=True)
	if data is None:
		doc.set(field, None)
		return
	f = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{doc.name}-{key}.gz",
			"is_private": 1,
			"content": data,
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": doc.name,
			"attached_to_field": field,
		}
	)
	f.flags.ignore_permissions = True
	f.save()
	doc.set(field, f.file_url)


# ---------------------------------------------------------------- bundles (.isamiq.json / older .rollover.json files)
def read_bundle(text):
	try:
		bundle = json.loads(text)
	except ValueError:
		frappe.throw(_("The file is not valid JSON."))
	if not isinstance(bundle, dict) or bundle.get("format") != BUNDLE_FORMAT or not isinstance(bundle.get("project"), dict):
		frappe.throw(_("This file is not a mining simulation project file (format '{0}').").format(BUNDLE_FORMAT))
	blobs = {}
	for key, value in (bundle.get("blobs") or {}).items():
		_blob_field(key)
		blobs[key] = base64.b64decode(value)
	return bundle["project"], blobs


def save_from_project(project, blobs=None, name=None, insert=False):
	"""Create or update a document from a project object (and optional blobs, gzip bytes)."""
	if insert:
		doc = frappe.new_doc(DOCTYPE)
		doc.simulation_id = name
	else:
		doc = frappe.get_doc(DOCTYPE, name)
	apply_project(doc, project)
	if insert:
		doc.insert()
	if blobs is not None:
		for key in BLOB_FIELDS:
			_write_blob(doc, key, blobs.get(key))
	if not insert or blobs is not None:
		doc.save()
	return doc


def _file_text(file_url):
	name = frappe.db.get_value("File", {"file_url": file_url})
	if not name:
		frappe.throw(_("File not found: {0}").format(file_url))
	f = frappe.get_doc("File", name)
	f.check_permission("read")
	with open(f.get_full_path(), "rb") as fh:
		return f, fh.read().decode("utf-8-sig")


# ---------------------------------------------------------------- API: desk (import / export)
@frappe.whitelist()
def check_import_file(file_url):
	"""What importing this file would do: its project name and whether that project exists."""
	_f, text = _file_text(file_url)
	project, blobs = read_bundle(text)
	pid = _clean_id(project.get("id"))
	existing = pid if pid and frappe.db.exists(DOCTYPE, pid) else None
	return {
		"project_name": project.get("name"),
		"project_id": pid,
		"existing": existing,
		"blocks": len(project.get("blocks") or []),
		"has_grid": "grid" in blobs,
	}


@frappe.whitelist()
def import_simulation_file(file_url, mode="new", target=None):
	"""Import a project file (.isamiq.json, or an older .rollover.json).

	mode 'new': a new project (with the file's project id, or a fresh one when that is taken);
	mode 'replace': overwrite `target` (or the existing project with the file's id) with the file.
	The uploaded file is removed once its content is in the project.
	"""
	f, text = _file_text(file_url)
	project, blobs = read_bundle(text)
	pid = _clean_id(project.get("id"))
	if mode == "replace":
		name = target or pid
		if not name or not frappe.db.exists(DOCTYPE, name):
			frappe.throw(_("There is no project to replace."))
		frappe.has_permission(DOCTYPE, "write", doc=name, throw=True)
		doc = save_from_project(project, blobs, name=name)
	else:
		frappe.has_permission(DOCTYPE, "create", throw=True)
		if not pid or frappe.db.exists(DOCTYPE, pid):
			if pid:  # a copy of a project that is already here: name it apart, as the simulator does
				name = project.get("name") or ""
				project["name"] = name if name.endswith("(imported)") else f"{name} (imported)".strip()
			pid = new_project_id()
		doc = save_from_project(project, blobs, name=pid, insert=True)
	if not f.attached_to_doctype:
		frappe.delete_doc("File", f.name, ignore_permissions=True)
	return doc.name


def bundle_for(doc):
	project = build_project(doc)
	blobs = {}
	for key in BLOB_FIELDS:
		data = _read_blob(doc, key)
		if data:
			blobs[key] = base64.b64encode(data).decode()
	return {
		"format": BUNDLE_FORMAT,
		"version": 1,
		"app": "ISAMIQ",
		"exported": frappe.utils.now_datetime().isoformat(),
		"project": project,
		"blobs": blobs,
	}


@frappe.whitelist()
def export_simulation_file(name):
	"""Download the project as a .isamiq.json file the standalone simulator can open."""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	bundle = bundle_for(doc)
	slug = re.sub(r"[^a-z0-9]+", "_", (doc.project_name or doc.name).lower()).strip("_") or "project"
	frappe.response.filename = f"{slug}.isamiq.json"
	frappe.response.filecontent = json.dumps(bundle, separators=(",", ":"), ensure_ascii=False)
	frappe.response.type = "download"


# ---------------------------------------------------------------- API: the simulator page (its Store)
def _meta(row):
	return {
		"id": row.name,
		"name": row.project_name,
		"modified": row.source_modified or str(row.modified),
		"startDate": str(row.start_date or ""),
		"description": row.description or "",
		"blocks": cint(row.block_count),
		"isDemo": bool(row.is_demo),
	}


@frappe.whitelist()
def list_projects():
	rows = frappe.get_list(
		DOCTYPE,
		fields=["name", "project_name", "source_modified", "modified", "start_date", "description", "block_count", "is_demo"],
		order_by="modified desc",
		limit_page_length=0,
	)
	return [_meta(r) for r in rows]


@frappe.whitelist()
def load_project(name):
	if not frappe.db.exists(DOCTYPE, name):
		return None
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	return build_project(doc)


@frappe.whitelist(methods=["POST"])
def save_project(project):
	project = json.loads(project) if isinstance(project, str) else project
	name = _clean_id(project.get("id"))
	if not name:
		frappe.throw(_("The project has no valid id."))
	if frappe.db.exists(DOCTYPE, name):
		frappe.has_permission(DOCTYPE, "write", doc=name, throw=True)
		doc = save_from_project(project, name=name)
	else:
		frappe.has_permission(DOCTYPE, "create", throw=True)
		doc = save_from_project(project, name=name, insert=True)
	return {"name": doc.name, "modified": str(doc.modified)}


@frappe.whitelist(methods=["POST"])
def delete_project(name):
	if frappe.db.exists(DOCTYPE, name):
		frappe.delete_doc(DOCTYPE, name)


@frappe.whitelist(methods=["POST"])
def get_blob(name, key):
	if not frappe.db.exists(DOCTYPE, name):
		return None
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")
	data = _read_blob(doc, key)
	return base64.b64encode(data).decode() if data else None


@frappe.whitelist(methods=["POST"])
def put_blob(name, key, data):
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	_write_blob(doc, key, base64.b64decode(data))
	doc.save()


@frappe.whitelist(methods=["POST"])
def delete_blob(name, key):
	if not frappe.db.exists(DOCTYPE, name):
		return
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")
	_write_blob(doc, key, None)
	doc.save()


def asset_version():
	"""Cache-buster for the simulator page's scripts."""
	base = os.path.join(frappe.get_app_path("is_production"), "public", "mining_simulation")
	stamp = 0
	for root, _dirs, files in os.walk(base):
		for fn in files:
			stamp = max(stamp, int(os.path.getmtime(os.path.join(root, fn))))
	return str(stamp)
