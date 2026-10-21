# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document

from is_production.cad_engineering.services import cad_geometry

BLOCK_ENTITY_TYPES = cad_geometry.SUPPORTED_TYPES - cad_geometry.BLOB_TYPES
MAX_BLOCK_ENTITIES = 100000
MAX_NESTING = 16


class CADBlock(Document):
	def validate(self):
		self.block_name = (self.block_name or "").strip()
		if not self.block_name or len(self.block_name) > 140:
			frappe.throw(_("Block Name is required (at most 140 characters)."))
		if frappe.db.exists("CAD Block", {"cad_model": self.cad_model, "block_name": self.block_name, "name": ["!=", self.name]}):
			frappe.throw(_("Block {0} already exists in {1}.").format(self.block_name, self.cad_model))
		if not self.is_new() and self.has_value_changed("block_name"):
			frappe.throw(_("A block cannot be renamed: references find it by name."))
		self.base_point = json.dumps(cad_geometry.coordinate(frappe.parse_json(self.base_point or "[0, 0, 0]"), _("Base point")))
		entities = frappe.parse_json(self.entities or "[]")
		if not isinstance(entities, list) or len(entities) > MAX_BLOCK_ENTITIES:
			frappe.throw(_("A block holds a list of at most {0} entities.").format(MAX_BLOCK_ENTITIES))
		others = self.other_blocks()
		clean = []
		for n, row in enumerate(entities):
			entity_type = row.get("entity_type")
			if entity_type not in BLOCK_ENTITY_TYPES:
				frappe.throw(_("Block {0}: {1} entities cannot be part of a block.").format(self.block_name, entity_type))
			geometry = cad_geometry.normalise(entity_type, row.get("geometry"), lambda name: name in others or name == self.block_name)
			clean.append(
				{
					"entity_type": entity_type,
					"geometry": geometry,
					"layer": row.get("layer") or "0",
					"color": row.get("color") or None,
					"line_type": row.get("line_type") or None,
					**({"metadata": row["metadata"]} if row.get("metadata") else {}),
				}
			)
		self.check_nesting(clean, others)
		self.entities = json.dumps(clean)
		self.entity_count = len(clean)
		box = cad_geometry.block_local_extent(clean, self.extent_of(others)) or [0.0] * 6
		for field, value in zip(("min_x", "min_y", "min_z", "max_x", "max_y", "max_z"), box):
			self.set(field, value)
		if not (frappe.flags.cad_batch or frappe.flags.cad_model_delete):
			from is_production.cad_engineering.services.cad_revision_service import ensure_working_revision

			ensure_working_revision(self.cad_model)

	def other_blocks(self):
		return {
			row.block_name: row
			for row in frappe.get_all(
				"CAD Block",
				filters={"cad_model": self.cad_model, "name": ["!=", self.name or ""]},
				fields=["block_name", "entities", "base_point", "min_x", "min_y", "min_z", "max_x", "max_y", "max_z", "entity_count"],
			)
		}

	def check_nesting(self, entities, others):
		"""No block may contain itself, however deeply nested."""

		def children(content):
			return {e["geometry"]["block"] for e in content if e["entity_type"] == "Block Reference"}

		seen, frontier, depth = set(), children(entities), 0
		while frontier:
			if self.block_name in frontier:
				frappe.throw(_("Block {0} would contain itself.").format(self.block_name))
			depth += 1
			if depth > MAX_NESTING:
				frappe.throw(_("Blocks are nested more than {0} levels deep.").format(MAX_NESTING))
			seen |= frontier
			frontier = {c for name in frontier if name in others for c in children(frappe.parse_json(others[name].entities or "[]"))} - seen

	@staticmethod
	def extent_of(others):
		def extent(name):
			row = others.get(name)
			if not row or not row.entity_count:
				return None
			return [row.min_x, row.min_y, row.min_z, row.max_x, row.max_y, row.max_z], frappe.parse_json(row.base_point or "[0, 0, 0]")

		return extent

	def on_trash(self):
		if frappe.flags.cad_model_delete:
			return
		if frappe.db.exists("CAD Entity", {"cad_model": self.cad_model, "block_name": self.block_name}):
			frappe.throw(_("Block {0} is still used by block references.").format(self.block_name))
		for row in frappe.get_all("CAD Block", filters={"cad_model": self.cad_model, "name": ["!=", self.name]}, fields=["block_name", "entities"]):
			if any(e.get("entity_type") == "Block Reference" and e["geometry"].get("block") == self.block_name for e in frappe.parse_json(row.entities or "[]")):
				frappe.throw(_("Block {0} is still used inside block {1}.").format(self.block_name, row.block_name))
