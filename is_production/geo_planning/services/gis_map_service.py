"""Basemaps and overlay map services (GIS Map Service) for the GIS Workstation.

A service is XYZ tiles, a WMS, or Google Map Tiles (the same imagery choices as the Mining Simulation:
Google satellite with a Map Tiles API key, Esri World Imagery without a key, or any XYZ URL).

Credentials (API key, HTTP header, basic auth) are Password fields. Delivery decides who fetches tiles:

- **Direct**: the browser fetches from the provider. An API key is then part of the tile URL the
  browser sees, so the key must be restricted to this site in the provider's console.
- **Through Frappe**: the browser asks ``tile`` / ``wms`` here; the server adds the credentials and
  fetches the tile. Credentials never reach the browser. HTTP header and basic auth always use this.

The proxy only fetches from public internet addresses: a service URL that resolves to a private,
loopback or link-local address is refused, so it cannot be used to reach QGIS Server or other internal
services. Tiles are not stored on the server; browsers may cache them for a day.
"""

import ipaddress
import socket
from urllib.parse import urlencode, urlparse

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt
from werkzeug.wrappers import Response

DOCTYPE = "GIS Map Service"
PROXY = "/api/method/is_production.geo_planning.services.gis_map_service."
GOOGLE_SESSION_URL = "https://tile.googleapis.com/v1/createSession"
GOOGLE_TILE_URL = "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}"
TIMEOUT = 15
MAX_WMS_SIZE = 4096
USER_AGENT = "Isambane-Production-GIS/1.0 (Frappe)"

DEFAULT_SERVICES = [
	{
		"title": "OpenStreetMap",
		"use_as": "Basemap",
		"category": "Street",
		"url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
		"attribution": "© OpenStreetMap contributors",
		"max_zoom": 19,
		"sort_order": 10,
		"notes": "OpenStreetMap's tile usage policy applies (no heavy or bulk use).",
	},
	{
		"title": "Esri World Imagery",
		"use_as": "Basemap",
		"category": "Satellite",
		"url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
		"attribution": "Imagery © Esri, Maxar, Earthstar Geographics, and the GIS User Community",
		"max_zoom": 19,
		"sort_order": 20,
		"notes": "Satellite imagery without a key, as in the Mining Simulation. Check Esri's terms of use for your use.",
	},
	{
		"title": "Esri World Topographic",
		"use_as": "Basemap",
		"category": "Topographic",
		"url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
		"attribution": "© Esri and its data providers",
		"max_zoom": 19,
		"sort_order": 30,
	},
	{
		"title": "OpenTopoMap",
		"use_as": "Basemap",
		"category": "Topographic",
		"url": "https://tile.opentopomap.org/{z}/{x}/{y}.png",
		"attribution": "Map data © OpenStreetMap contributors, SRTM · style © OpenTopoMap (CC-BY-SA)",
		"max_zoom": 17,
		"sort_order": 40,
	},
	{
		"title": "Google Satellite",
		"enabled": 0,
		"use_as": "Basemap",
		"category": "Satellite",
		"service_type": "Google Map Tiles",
		"authentication": "API Key",
		"google_map_type": "satellite",
		"attribution": "Imagery © Google",
		"max_zoom": 21,
		"sort_order": 50,
		"notes": "Add a Google Maps Platform API key with the Map Tiles API enabled, then enable this service.",
	},
	{
		"title": "Place Names and Boundaries (Esri)",
		"use_as": "Overlay",
		"category": "Other",
		"url": "https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
		"attribution": "© Esri and its data providers",
		"max_zoom": 19,
		"sort_order": 60,
		"notes": "Labels to put over satellite imagery.",
	},
]


def ensure_default_map_services():
	"""Create the default services once (an admin's later changes are kept). Runs after migrate."""
	if not frappe.db.table_exists(DOCTYPE):
		return
	for service in DEFAULT_SERVICES:
		if not frappe.db.exists(DOCTYPE, service["title"]):
			frappe.get_doc({"doctype": DOCTYPE, **service}).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def validate_map_service(doc):
	if doc.service_type in ("XYZ Tiles", "WMS"):
		url = (doc.url or "").strip()
		if urlparse(url.replace("{", "").replace("}", "")).scheme not in ("http", "https"):
			frappe.throw(_("The URL must start with https:// (or http://)."))
		has_y = "{y}" in url or "{-y}" in url
		if doc.service_type == "XYZ Tiles" and not ("{z}" in url and "{x}" in url and has_y):
			frappe.throw(_("An XYZ URL needs {z}, {x} and {y} (or {-y})."))
		if doc.service_type == "WMS" and not doc.wms_layers:
			frappe.throw(_("Name the WMS layer(s) to show."))
		doc.url = url
	if doc.service_type == "Google Map Tiles":
		doc.authentication = "API Key"
	if doc.authentication in ("HTTP Header", "Basic Auth"):
		doc.delivery = "Through Frappe"  # browsers cannot add these to tile requests without seeing them
	if doc.authentication == "API Key" and doc.service_type != "Google Map Tiles" and "{api_key}" not in (doc.url or "") and not doc.api_key_parameter:
		frappe.throw(_("Put {api_key} in the URL, or name the query parameter the key goes in."))
	if doc.enabled and doc.authentication == "API Key" and not doc.get_password("api_key", raise_exception=False):
		frappe.throw(_("Enter the API key before enabling the service."))
	doc.min_zoom = min(max(cint(doc.min_zoom), 0), 24)
	doc.max_zoom = min(max(cint(doc.max_zoom) or 19, doc.min_zoom), 24)
	doc.opacity = min(max(flt(doc.opacity) if doc.opacity is not None else 1, 0), 1)


# ---------------------------------------------------------------------------
# what the browser gets
# ---------------------------------------------------------------------------


@frappe.whitelist()
def list_map_services():
	"""Enabled services, as the browser needs them (no secrets unless a Direct service's key)."""
	frappe.has_permission("GIS Dataset", "read", throw=True)
	services = []
	for name in frappe.get_all(DOCTYPE, filters={"enabled": 1}, order_by="sort_order asc, title asc", pluck="name"):
		doc = frappe.get_doc(DOCTYPE, name)
		try:
			services.append(client_config(doc))
		except Exception as e:
			frappe.log_error(title=f"GIS map service {name}", message=str(e))
			services.append({"name": doc.name, "title": doc.title, "use_as": doc.use_as, "error": str(e)})
	return services


def client_config(doc):
	config = {
		"name": doc.name,
		"title": doc.title,
		"use_as": doc.use_as,
		"category": doc.category,
		"kind": "wms" if doc.service_type == "WMS" else "xyz",
		"attribution": doc.attribution,
		"min_zoom": cint(doc.min_zoom),
		"max_zoom": cint(doc.max_zoom) or 19,
		"opacity": flt(doc.opacity) if doc.opacity is not None else 1,
		"proxied": doc.delivery == "Through Frappe",
	}
	if doc.delivery == "Through Frappe":
		if doc.service_type == "WMS":
			config["url"] = PROXY + "wms?" + urlencode({"service_name": doc.name})
			config["params"] = wms_params(doc)
		else:
			config["url"] = PROXY + "tile?" + urlencode({"service_name": doc.name}) + "&z={z}&x={x}&y={y}"
	elif doc.service_type == "Google Map Tiles":
		config["url"] = google_template(doc)
	elif doc.service_type == "WMS":
		config["url"] = with_key(doc, doc.url)
		config["params"] = wms_params(doc)
	else:
		config["url"] = with_key(doc, doc.url)
	return config


def wms_params(doc):
	return {
		"LAYERS": doc.wms_layers,
		"STYLES": doc.wms_styles or "",
		"FORMAT": doc.wms_format or "image/png",
		"VERSION": doc.wms_version or "1.3.0",
		"TRANSPARENT": "TRUE",
	}


def with_key(doc, url):
	if doc.authentication != "API Key":
		return url
	key = doc.get_password("api_key", raise_exception=False) or ""
	if "{api_key}" in url:
		return url.replace("{api_key}", requests.utils.quote(key, safe=""))
	return url + ("&" if "?" in url else "?") + urlencode({doc.api_key_parameter: key})


def google_template(doc):
	"""XYZ template for Google Map Tiles: a session (created server-side, cached until near expiry) + key."""
	key = doc.get_password("api_key", raise_exception=False)
	if not key:
		frappe.throw(_("{0}: no API key.").format(doc.title))
	cache_key = f"gis_google_session::{doc.name}::{doc.modified}"
	session = frappe.cache.get_value(cache_key)
	if not session:
		body = {"mapType": doc.google_map_type or "satellite", "language": doc.google_language or "en-ZA", "region": doc.google_region or "ZA"}
		if doc.google_layer_types:
			body["layerTypes"] = [t.strip() for t in doc.google_layer_types.split(",") if t.strip()]
		response = requests.post(GOOGLE_SESSION_URL, params={"key": key}, json=body, timeout=TIMEOUT)
		if not response.ok:
			frappe.throw(
				_("Google refused the Map Tiles session (HTTP {0}). Check that the Map Tiles API is enabled for the key.").format(
					response.status_code
				)
			)
		session = response.json()["session"]
		frappe.cache.set_value(cache_key, session, expires_in_sec=6 * 24 * 3600)  # sessions last about two weeks
	return GOOGLE_TILE_URL + "?" + urlencode({"session": session, "key": key})


# ---------------------------------------------------------------------------
# proxy (Delivery: Through Frappe)
# ---------------------------------------------------------------------------


def proxied_service(service_name):
	frappe.has_permission("GIS Dataset", "read", throw=True)
	doc = frappe.get_doc(DOCTYPE, service_name)
	if not doc.enabled or doc.delivery != "Through Frappe":
		frappe.throw(_("{0} is not served through Frappe.").format(service_name), frappe.PermissionError)
	return doc


def check_public(url):
	"""Refuse URLs that resolve to private, loopback, link-local or reserved addresses (SSRF guard)."""
	host = urlparse(url).hostname
	if not host:
		frappe.throw(_("The service URL has no host."))
	try:
		addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
	except socket.gaierror:
		frappe.throw(_("{0} could not be resolved.").format(host))
	for address in addresses:
		ip = ipaddress.ip_address(address.split("%")[0])
		if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
			frappe.throw(_("Map services on private or local addresses are not proxied."), frappe.PermissionError)


def upstream(doc, url, params=None):
	check_public(url)
	headers = {"User-Agent": USER_AGENT, "Referer": frappe.utils.get_url()}
	auth = None
	if doc.authentication == "HTTP Header" and doc.header_name:
		headers[doc.header_name] = doc.get_password("header_value", raise_exception=False) or ""
	elif doc.authentication == "Basic Auth":
		auth = (doc.username or "", doc.get_password("password", raise_exception=False) or "")
	try:
		response = requests.get(url, params=params, headers=headers, auth=auth, timeout=TIMEOUT, allow_redirects=False)
	except requests.RequestException:
		return Response(b"", status=504)
	result = Response(response.content, status=response.status_code if response.status_code < 400 else 502)
	result.headers["Content-Type"] = response.headers.get("Content-Type", "application/octet-stream")
	result.headers["Cache-Control"] = "private, max-age=86400" if response.ok else "no-store"
	return result


@frappe.whitelist(methods=["GET"])
def tile(service_name, z, x, y, **kwargs):
	doc = proxied_service(service_name)
	z, x, y = cint(z), cint(x), cint(y)
	if not (0 <= z <= 24 and 0 <= x < 2**z and 0 <= y < 2**z):
		return Response(b"", status=400)
	if doc.service_type == "Google Map Tiles":
		url = google_template(doc).replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
	else:
		url = with_key(doc, doc.url).replace("{z}", str(z)).replace("{x}", str(x)).replace("{-y}", str(2**z - 1 - y)).replace("{y}", str(y))
	return upstream(doc, url)


@frappe.whitelist(methods=["GET"])
def wms(service_name, **kwargs):
	doc = proxied_service(service_name)
	params = {k.upper(): v for k, v in frappe.local.form_dict.items() if isinstance(v, str) and k not in ("cmd", "service_name")}
	if params.get("REQUEST", "").upper() not in ("GETMAP", "GETLEGENDGRAPHIC"):
		return Response(b"", status=400)
	for key in ("WIDTH", "HEIGHT"):
		if key in params:
			params[key] = str(min(max(cint(params[key]), 1), MAX_WMS_SIZE))
	params.update({k: v for k, v in wms_params(doc).items() if k in ("LAYERS", "STYLES")})  # only the configured layers
	return upstream(doc, with_key(doc, doc.url), params)
