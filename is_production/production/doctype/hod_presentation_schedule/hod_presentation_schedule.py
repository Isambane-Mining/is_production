import frappe
from frappe.model.document import Document
from is_production.production.controllers.hod_presentation_scheduler import validate_schedule, check_environment


class HODPresentationSchedule(Document):
    def before_validate(self):
        if not self.delivery_site:
            self.delivery_site = frappe.local.site
        for row in self.get('sites') or []:
            row.user = self.run_as

    def validate(self):
        validate_schedule(self)
        if self.enabled:
            check_environment(self)
