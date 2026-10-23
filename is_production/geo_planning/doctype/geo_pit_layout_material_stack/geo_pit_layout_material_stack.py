import frappe
from frappe.model.document import Document


class GeoPitLayoutMaterialStack(Document):
    def validate(self):
        self.set_defaults()
        self.validate_required_fields()
        self.validate_items()

    def has_field(self, fieldname):
        return frappe.get_meta(self.doctype).has_field(fieldname)

    def set_defaults(self):
        if not self.stack_status:
            self.stack_status = "Draft"

        if self.has_field("attach_status") and not self.attach_status:
            self.attach_status = "Not Attached"

        if self.has_field("calculation_status") and not self.calculation_status:
            self.calculation_status = "Not Calculated"

    def validate_required_fields(self):
        if not self.stack_name:
            frappe.throw("Stack Name is required.")

        if not self.geo_project:
            frappe.throw("Geo Project is required.")

        if not self.geo_pit_layout:
            frappe.throw("Geo Pit Layout is required.")

    def validate_items(self):
        if not self.get("item"):
            return

        # a layout published from an approved CAD design takes its materials from the design, not a geology run
        from_cad = bool(frappe.db.get_value("Geo Pit Layout", self.geo_pit_layout, "source_cad_model")) if self.geo_pit_layout else False

        for row in self.get("item"):
            if not row.material_seam:
                frappe.throw(f"Material / Seam is required in row {row.idx}.")

            if not row.value_type:
                frappe.throw(f"Value Type is required in row {row.idx}.")

            if not row.geology_run and not from_cad:
                frappe.throw(f"Geology Run is required in row {row.idx}.")

            run_layout = frappe.db.get_value("Geo Pit Layout Geology Run", row.geology_run, "geo_pit_layout")

            if run_layout and run_layout != self.geo_pit_layout:
                frappe.throw(
                    f"Row {row.idx}: Geology Run {row.geology_run} belongs to layout {run_layout}, "
                    f"but this stack belongs to layout {self.geo_pit_layout}."
                )

            density_source = row.get("density_source") if hasattr(row, "density_source") else None
            manual_density = row.get("manual_density") if hasattr(row, "manual_density") else None

            if density_source == "Manual" and not manual_density:
                frappe.throw(f"Row {row.idx}: Manual Density is required when Density Source is Manual.")