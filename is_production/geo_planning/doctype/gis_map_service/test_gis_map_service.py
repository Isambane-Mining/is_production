# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from is_production.geo_planning.services import gis_map_service as svc

IGNORE_TEST_RECORD_DEPENDENCIES = ["User"]
PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 0))]
LOOPBACK = [(2, 1, 6, "", ("127.0.0.1", 0))]


def make(**values):
	doc = {"doctype": svc.DOCTYPE, "title": "test_svc " + frappe.generate_hash(length=6), "url": "https://tiles.example.com/{z}/{x}/{y}.png"}
	doc.update(values)
	return frappe.get_doc(doc).insert()


def upstream_response(content=b"PNG", status=200, content_type="image/png"):
	response = MagicMock(ok=status < 400, status_code=status, content=content, headers={"Content-Type": content_type})
	return response


class IntegrationTestGISMapService(IntegrationTestCase):
	def test_default_services(self):
		svc.ensure_default_map_services()
		self.assertEqual(frappe.db.get_value(svc.DOCTYPE, "Esri World Imagery", "category"), "Satellite")
		self.assertEqual(frappe.db.get_value(svc.DOCTYPE, "Google Satellite", ["enabled", "service_type"]), (0, "Google Map Tiles"))
		self.assertEqual(frappe.db.get_value(svc.DOCTYPE, "Place Names and Boundaries (Esri)", "use_as"), "Overlay")

	def test_validation(self):
		with self.assertRaises(frappe.ValidationError):
			make(url="https://tiles.example.com/{z}/{x}.png")  # no {y}
		with self.assertRaises(frappe.ValidationError):
			make(authentication="API Key", api_key_parameter="key")  # enabled without a key
		header = make(authentication="HTTP Header", header_name="X-Api-Key", header_value="s3cret", delivery="Direct")
		self.assertEqual(header.delivery, "Through Frappe")

	def test_direct_key_is_in_the_url_but_proxied_key_is_not(self):
		direct = make(authentication="API Key", api_key="abc123", url="https://t.example.com/{z}/{x}/{y}.png?token={api_key}")
		self.assertIn("token=abc123", svc.client_config(direct)["url"])
		proxied = make(authentication="API Key", api_key="abc123", api_key_parameter="key", delivery="Through Frappe")
		config = svc.client_config(proxied)
		self.assertNotIn("abc123", str(config))
		self.assertTrue(config["url"].startswith(svc.PROXY + "tile?"))

	@patch("is_production.geo_planning.services.gis_map_service.socket.getaddrinfo", return_value=PUBLIC)
	@patch("is_production.geo_planning.services.gis_map_service.requests.get", return_value=upstream_response())
	def test_proxy_adds_credentials(self, get, _dns):
		doc = make(authentication="HTTP Header", header_name="X-Api-Key", header_value="s3cret", url="https://t.example.com/{z}/{x}/{-y}.png")
		response = svc.tile(doc.name, 3, 2, 1)
		self.assertEqual((response.status_code, response.data), (200, b"PNG"))
		url = get.call_args.args[0]
		self.assertEqual(url, "https://t.example.com/3/2/6.png")  # {-y}: TMS row 2^3 - 1 - 1
		self.assertEqual(get.call_args.kwargs["headers"]["X-Api-Key"], "s3cret")
		self.assertEqual(svc.tile(doc.name, 3, 9, 1).status_code, 400)  # x outside the zoom level

	@patch("is_production.geo_planning.services.gis_map_service.socket.getaddrinfo", return_value=LOOPBACK)
	def test_proxy_refuses_private_addresses(self, _dns):
		doc = make(url="http://qgis.internal/{z}/{x}/{y}.png", delivery="Through Frappe")
		with self.assertRaises(frappe.PermissionError):
			svc.tile(doc.name, 1, 0, 0)

	@patch("is_production.geo_planning.services.gis_map_service.socket.getaddrinfo", return_value=PUBLIC)
	@patch("is_production.geo_planning.services.gis_map_service.requests.get", return_value=upstream_response())
	def test_wms_proxy_only_draws_the_configured_layers(self, get, _dns):
		doc = make(service_type="WMS", url="https://wms.example.com/ows", wms_layers="imagery", delivery="Through Frappe")
		frappe.local.form_dict = frappe._dict(service_name=doc.name, REQUEST="GetMap", LAYERS="secret_layer", WIDTH="99999")
		self.assertEqual(svc.wms(doc.name).status_code, 200)
		params = get.call_args.kwargs["params"]
		self.assertEqual((params["LAYERS"], params["WIDTH"]), ("imagery", "4096"))
		frappe.local.form_dict = frappe._dict(service_name=doc.name, REQUEST="GetFeatureInfo")
		self.assertEqual(svc.wms(doc.name).status_code, 400)

	def test_guest_gets_no_services(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				svc.list_map_services()
		finally:
			frappe.set_user("Administrator")
