import frappe
from frappe.model.document import Document


class GeoPitLayout(Document):
    def validate(self):
        self.set_defaults()
        self.validate_required_settings()

    def set_defaults(self):
        if not self.layout_version:
            self.layout_version = "V001"

        if not self.layout_type:
            self.layout_type = "Pit Layout"

        if not self.layout_status:
            self.layout_status = "Draft"

        if not self.numbering_style:
            self.numbering_style = "C1B1"

        if not self.block_size_x:
            self.block_size_x = 100

        if not self.block_size_y:
            self.block_size_y = 40

        if self.block_angle_degrees in (None, ""):
            self.block_angle_degrees = 0

        if self.minimum_inside_percent in (None, ""):
            self.minimum_inside_percent = 50

        if not self.default_cut_no:
            self.default_cut_no = 1

        if frappe.get_meta(self.doctype).has_field("generation_status") and not self.generation_status:
            self.generation_status = "Draft"

    def validate_required_settings(self):
        if not self.layout_name:
            frappe.throw("Layout Name is required.")

        if not self.geo_project:
            frappe.throw("Geo Project is required.")

        if not self.pit_outline_batch and not self.get("source_cad_model"):
            # a layout published from an approved CAD design has its blocks from the design, not a pit outline
            frappe.throw("Pit Outline Batch is required.")

        if float(self.block_size_x or 0) <= 0:
            frappe.throw("Block Size X must be greater than zero.")

        if float(self.block_size_y or 0) <= 0:
            frappe.throw("Block Size Y must be greater than zero.")