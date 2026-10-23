# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, now_datetime

# the design rules: frozen once a version is Active (designs record which version they were checked against)
RULE_FIELDS = (
	"standard_type", "applicable_location", "company", "bench_height", "bench_reference_elevation", "face_angle",
	"maximum_slope_angle", "berm_width", "minimum_bench_width", "geotechnical_berm_width", "geotechnical_berm_every",
	"minimum_floor_width", "road_width", "minimum_road_width", "maximum_gradient", "minimum_turning_radius",
	"normal_crossfall", "maximum_superelevation", "cut_batter_angle", "fill_batter_angle", "block_length",
	"block_width", "mining_direction", "design_tolerance", "angle_tolerance", "drill_burden", "drill_spacing",
	"drill_pattern", "drill_crest_standoff", "drill_hole_diameter", "drill_subdrill", "drill_max_inclination", "drill_spacing_tolerance",
)


class CADDesignStandard(Document):
	def autoname(self):
		self.version = cint(self.version) or 1
		self.name = f"{self.standard_code.strip()}-V{self.version}"

	def validate(self):
		self.standard_code = (self.standard_code or "").strip()
		if not 0 < (self.face_angle or 0) < 90:
			frappe.throw(_("The bench face angle must be between 0° and 90°."))
		if not 0 < (self.maximum_slope_angle or 0) < 90:
			frappe.throw(_("The maximum overall slope angle must be between 0° and 90°."))
		if (self.bench_height or 0) <= 0:
			frappe.throw(_("The bench height must be greater than zero."))
		if (self.minimum_bench_width or 0) > (self.berm_width or 0):
			frappe.throw(_("The design berm width is less than the minimum bench width."))
		if (self.minimum_road_width or 0) > (self.road_width or 0):
			frappe.throw(_("The road width is less than the minimum road width."))
		for field in ("cut_batter_angle", "fill_batter_angle"):
			if not 0 < (self.get(field) or 0) < 90:
				frappe.throw(_("{0} must be between 0° and 90°.").format(_(self.meta.get_label(field))))
		if self.is_new():
			return
		before = self.get_doc_before_save()
		if before and before.status != "Draft":
			changed = [f for f in RULE_FIELDS if str(before.get(f) or "") != str(self.get(f) or "")]
			if changed:
				frappe.throw(
					_("{0} is {1}: its rules cannot change ({2}). Make a new version.").format(self.name, _(before.status), ", ".join(_(self.meta.get_label(f)) for f in changed))
				)

	def on_trash(self):
		if self.status != "Draft" or frappe.db.exists("CAD Design Validation", {"design_standard": self.name}):
			frappe.throw(_("{0} has been used or activated; it is kept as a record.").format(self.name))


@frappe.whitelist(methods=["POST"])
def activate(name):
	"""Make this version the Active one of its code (the previous Active version becomes Superseded)."""
	doc = frappe.get_doc("CAD Design Standard", name)
	doc.check_permission("write")
	if doc.status != "Draft":
		frappe.throw(_("Only a Draft version can be activated."))
	for other in frappe.get_all("CAD Design Standard", filters={"standard_code": doc.standard_code, "status": "Active", "name": ["!=", name]}, pluck="name"):
		frappe.db.set_value("CAD Design Standard", other, "status", "Superseded")
	doc.db_set({"status": "Active", "activated_on": now_datetime(), "activated_by": frappe.session.user})
	return doc.name


@frappe.whitelist(methods=["POST"])
def new_version(name):
	"""A Draft copy with the next version number."""
	source = frappe.get_doc("CAD Design Standard", name)
	source.check_permission("read")
	frappe.has_permission("CAD Design Standard", "create", throw=True)
	latest = max(frappe.get_all("CAD Design Standard", filters={"standard_code": source.standard_code}, pluck="version") or [source.version])
	doc = frappe.copy_doc(source)
	doc.update({"version": cint(latest) + 1, "status": "Draft", "previous_version": source.name, "activated_on": None, "activated_by": None})
	doc.insert()
	return doc.name
