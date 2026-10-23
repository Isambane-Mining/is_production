# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

from is_production.geo_planning.services.gis_map_service import validate_map_service
from frappe.model.document import Document


class GISMapService(Document):
	def validate(self):
		validate_map_service(self)
