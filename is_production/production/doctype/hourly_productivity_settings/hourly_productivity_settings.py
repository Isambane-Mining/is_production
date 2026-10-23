import frappe
from frappe.model.document import Document


class HourlyProductivitySettings(Document):
    def validate(self):
        if not 0 <= float(self.orange_threshold) < float(self.green_threshold):
            frappe.throw("Thresholds must satisfy 0 <= orange < green.")
