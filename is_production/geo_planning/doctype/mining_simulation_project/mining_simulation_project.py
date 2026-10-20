# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from is_production.geo_planning.services.mining_simulation_service import (
	HOUR_FIELDS,
	PRODUCTION_EQUIPMENT_KINDS,
	TABLE_MAP,
	new_project_id,
)

# id prefixes the simulator uses for rows it creates itself
ID_PREFIX = {
	"surfaces": "srf",
	"layers": "lay",
	"activities": "act",
	"fleet": "eq",
	"roads": "rd",
	"dumps": "dmp",
	"overlays": "ovl",
	"limits": "lim",
	"pits": "pit",
	"batters": "bat",
	"oldDumps": "old",
	"rehandles": "rh",
	"fills": "fil",
	"raises": "rse",
	"drains": "drn",
	"qrasters": "qr",
	"teamList": "tm",
}


class MiningSimulationProject(Document):
	def before_naming(self):
		if not self.simulation_id:
			self.simulation_id = new_project_id()

	def validate(self):
		self.validate_json_fields()
		self.set_row_ids()
		self.set_block_geometry()
		self.set_hours_per_day()
		self.validate_teams()
		self.validate_equipment()
		self.block_count = len(self.blocks)
		self.activity_count = len(self.activities)

	def set_hours_per_day(self):
		"""The simulator takes one figure: the average over the working days (same hours a week)."""
		working = [flt(self.get(f)) for f in HOUR_FIELDS if flt(self.get(f)) > 0]
		if working:
			self.hours_per_day = sum(working) / len(working)

	def validate_teams(self):
		seen = set()
		for row in self.teams:
			row.team_name = (row.team_name or "").strip()
			if row.team_name in seen:
				frappe.throw(_("Team {0} is listed twice").format(frappe.bold(row.team_name)))
			seen.add(row.team_name)
		self.team_rows = len(self.teams)

	def validate_equipment(self):
		"""Team equipment: production Asset Categories, their Items, and the teams of this project."""
		teams = {row.team_name for row in self.teams}
		for row in self.fleet:
			if row.asset_category:
				if row.asset_category not in PRODUCTION_EQUIPMENT_KINDS:
					frappe.throw(
						_("Team Equipment row {0}: {1} is not production equipment ({2})").format(
							row.idx, row.asset_category, ", ".join(PRODUCTION_EQUIPMENT_KINDS)
						)
					)
				row.equipment_kind = PRODUCTION_EQUIPMENT_KINDS[row.asset_category]
			if row.item:
				item = frappe.db.get_value("Item", row.item, ["asset_category", "item_name"], as_dict=True)
				if row.asset_category and item.asset_category != row.asset_category:
					frappe.throw(
						_("Team Equipment row {0}: Item {1} is in Asset Category {2}, not {3}").format(
							row.idx, row.item, item.asset_category or "-", row.asset_category
						)
					)
				row.equipment_name = row.equipment_name or item.item_name
			if row.team and row.team not in teams:
				frappe.throw(_("Team Equipment row {0}: there is no team {1} in the Teams table").format(row.idx, frappe.bold(row.team)))
		self.equipment_count = sum(flt(row.qty) for row in self.fleet if row.item)

	def validate_json_fields(self):
		for doc in [self, *self.get_all_children()]:
			for df in doc.meta.fields:
				if df.fieldtype == "Code" and df.options == "JSON" and doc.get(df.fieldname):
					try:
						json.loads(doc.get(df.fieldname))
					except ValueError:
						frappe.throw(
							_("{0}: {1} is not valid JSON").format(
								_(doc.meta.name) if doc != self else _(self.doctype), _(df.label)
							)
						)

	def set_row_ids(self):
		"""Rows added in the desk need the id the simulator links them by."""
		for key, (table, _id_key, mapping) in TABLE_MAP.items():
			id_field = mapping[0][1]
			for row in self.get(table):
				if not row.get(id_field):
					if key == "blocks":
						frappe.throw(_("Row {0} of Blocks has no Block ID").format(row.idx))
					row.set(id_field, f"{ID_PREFIX[key]}_{frappe.generate_hash(length=12).lower()}")

	def set_block_geometry(self):
		for row in self.blocks:
			pts = json.loads(row.points) if row.points else []
			area, cx, cy = polygon_area_centroid(pts)
			row.block_area, row.centroid_x, row.centroid_y = area, cx, cy


def polygon_area_centroid(pts):
	"""Area and centroid of a polygon given as [[x, y], ...] (open or closed ring)."""
	pts = [p for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
	if len(pts) < 3:
		return 0, 0, 0
	a = cx = cy = 0.0
	x0, y0 = pts[0][0], pts[0][1]  # relative to the first point: projected coordinates are large
	for i in range(len(pts)):
		x1, y1 = pts[i][0] - x0, pts[i][1] - y0
		x2, y2 = pts[(i + 1) % len(pts)][0] - x0, pts[(i + 1) % len(pts)][1] - y0
		cross = x1 * y2 - x2 * y1
		a += cross
		cx += (x1 + x2) * cross
		cy += (y1 + y2) * cross
	if not a:
		return 0, x0, y0
	return abs(a) / 2, cx / (3 * a) + x0, cy / (3 * a) + y0
