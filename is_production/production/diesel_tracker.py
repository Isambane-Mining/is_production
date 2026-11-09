# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

"""The Diesel L/BCM Tracker (portal page /portal_diesel_tracker): the diesel each machine burns against the
coal and waste it moves, by site.

It keeps its own sites, equipment, entries and settings (the Diesel Tracker doctypes), separate from the
Mining Simulation and from the Daily Diesel Sheets. Every change answers with the page's data afresh, so the
page draws what the database holds.

	L/BCM = diesel litres ÷ (waste bcm + coal tons ÷ coal density)
"""

import random
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

SITE = "Diesel Tracker Site"
EQUIP = "Diesel Tracker Equipment"
ENTRY = "Diesel Tracker Entry"
SETTINGS = "Diesel Tracker Settings"

TYPES = ("Excavator", "Haul truck", "Front-end loader", "Dozer", "Drill rig", "Grader", "Water cart", "Other")
# the settings: the page's names and their fields, with the values used until they are saved
SETTING_FIELDS = {"density": "coal_density", "green": "green_limit", "amber": "amber_limit", "max": "gauge_max"}
DEFAULTS = {"density": 1.4, "green": 0.8, "amber": 1.2, "max": 2.0}
KINDS = {"sites": SITE, "equipment": EQUIP, "entries": ENTRY}


def permissions():
	"""What the user may do on the page."""
	has = frappe.has_permission
	return {
		"entry": bool(has(ENTRY, "create")),
		"deleteEntry": bool(has(ENTRY, "delete")),
		# sites and machines; removing one removes its entries too
		"setup": bool(has(SITE, "create") and has(EQUIP, "create")),
		"deleteSetup": bool(has(SITE, "delete") and has(EQUIP, "delete") and has(ENTRY, "delete")),
		"settings": bool(has(SETTINGS, "write")),
	}


def settings():
	stored = frappe.db.get_singles_dict(SETTINGS)
	out = dict(DEFAULTS)
	for key, field in SETTING_FIELDS.items():
		if stored.get(field) not in (None, ""):
			out[key] = flt(stored.get(field))
	return out


def page_data():
	"""Sites, equipment, entries (the user may read) and the settings, in the page's own terms."""
	sites = frappe.get_list(SITE, fields=["name", "site_name", "is_example"], order_by="site_name asc", limit_page_length=0)
	equipment = frappe.get_list(
		EQUIP, fields=["name", "site", "equipment_name", "equipment_type", "is_example"], order_by="equipment_name asc", limit_page_length=0
	)
	entries = frappe.get_list(
		ENTRY,
		fields=["name", "entry_date", "site", "equipment", "diesel_litres", "coal_tons", "waste_bcm", "is_example"],
		order_by="entry_date desc, creation desc",
		limit_page_length=0,
	)
	return {
		"sites": [{"id": s.name, "name": s.site_name, "example": bool(s.is_example)} for s in sites],
		"equipment": [
			{"id": q.name, "siteId": q.site, "name": q.equipment_name, "type": q.equipment_type, "example": bool(q.is_example)}
			for q in equipment
		],
		"entries": [
			{
				"id": e.name,
				"date": str(e.entry_date),
				"siteId": e.site,
				"equipmentId": e.equipment,
				"diesel": flt(e.diesel_litres),
				"coal": flt(e.coal_tons),
				"waste": flt(e.waste_bcm),
				"example": bool(e.is_example),
			}
			for e in entries
		],
		"settings": settings(),
		"can": permissions(),
	}


def _answer(doc_id=None):
	return {"id": doc_id, "data": page_data()}


@frappe.whitelist()
def get_data():
	frappe.has_permission(ENTRY, "read", throw=True)
	return page_data()


@frappe.whitelist(methods=["POST"])
def add_site(name=None):
	doc = frappe.get_doc({"doctype": SITE, "site_name": (name or "").strip()}).insert()
	return _answer(doc.name)


@frappe.whitelist(methods=["POST"])
def add_equipment(siteId=None, name=None, type=None):
	if type not in TYPES:
		frappe.throw(_("Choose the type of machine."))
	doc = frappe.get_doc({"doctype": EQUIP, "site": siteId, "equipment_name": (name or "").strip(), "equipment_type": type}).insert()
	return _answer(doc.name)


def _amount(value, label, required=False):
	if value in (None, ""):
		if required:
			frappe.throw(_("Type the {0}.").format(label))
		return 0.0
	try:
		v = float(value)
	except (TypeError, ValueError):
		frappe.throw(_("The {0} must be a number.").format(label))
	if v < 0:
		frappe.throw(_("The {0} must be zero or more.").format(label))
	return v


@frappe.whitelist(methods=["POST"])
def add_entry(date=None, siteId=None, equipmentId=None, diesel=None, coal=None, waste=None):
	if not date:
		frappe.throw(_("Enter the date for this entry."))
	doc = frappe.get_doc(
		{
			"doctype": ENTRY,
			"entry_date": getdate(date),
			"site": siteId,
			"equipment": equipmentId,
			"diesel_litres": _amount(diesel, _("diesel litres"), required=True),
			"coal_tons": _amount(coal, _("coal tons")),
			"waste_bcm": _amount(waste, _("waste bcm")),
		}
	).insert()
	return _answer(doc.name)


@frappe.whitelist(methods=["POST"])
def delete(kind=None, id=None):
	"""Delete an entry, a machine (with its entries) or a site (with its machines and their entries). Each
	goes to Deleted Documents, where it can be restored from the desk."""
	doctype = KINDS.get(kind)
	if not doctype or not id or not frappe.db.exists(doctype, id):
		frappe.throw(_("That is no longer there: the page has been refreshed."))
	if kind != "entries":
		for dt in (ENTRY, EQUIP, SITE):
			frappe.has_permission(dt, "delete", throw=True)
		field = "site" if kind == "sites" else "equipment"
		for name in frappe.get_all(ENTRY, filters={field: id}, pluck="name"):
			frappe.delete_doc(ENTRY, name)
		if kind == "sites":
			for name in frappe.get_all(EQUIP, filters={"site": id}, pluck="name"):
				frappe.delete_doc(EQUIP, name)
	frappe.delete_doc(doctype, id)
	return _answer()


@frappe.whitelist(methods=["POST"])
def save_settings(density=None, green=None, amber=None, max=None):
	doc = frappe.get_doc(SETTINGS)
	given = {"density": density, "green": green, "amber": amber, "max": max}
	for key, field in SETTING_FIELDS.items():
		doc.set(field, flt(given[key]) if given[key] not in (None, "") else DEFAULTS[key])
	doc.save()
	return _answer()


# ---- example data: two sites, six machines and weekly entries over the last four months

EXAMPLE_SITES = ("North Pit (example)", "South Pit (example)")
# site, fleet number, type, waste bcm a week, coal t a week, L/bcm it burns at
EXAMPLE_MACHINES = (
	(0, "EX01", "Excavator", 9500, 0, 0.46),
	(0, "HT01", "Haul truck", 3300, 800, 0.92),
	(0, "HT02", "Haul truck", 3100, 700, 1.34),
	(1, "EX02", "Excavator", 2200, 4600, 0.62),
	(1, "HT03", "Haul truck", 1800, 1500, 0.98),
	(1, "DZ01", "Dozer", 2900, 0, 1.12),
)


@frappe.whitelist(methods=["POST"])
def load_example():
	if frappe.db.exists(SITE, {"is_example": 1}):
		frappe.throw(_("The example data is already loaded."))
	for dt in (SITE, EQUIP, ENTRY):
		frappe.has_permission(dt, "create", throw=True)
	density = settings()["density"]
	sites = [frappe.get_doc({"doctype": SITE, "site_name": n, "is_example": 1}).insert().name for n in EXAMPLE_SITES]
	machines = [
		frappe.get_doc({"doctype": EQUIP, "site": sites[s], "equipment_name": n, "equipment_type": t, "is_example": 1}).insert().name
		for s, n, t, *_rest in EXAMPLE_MACHINES
	]
	rand = random.Random(20261109)
	end = getdate(today())
	day = end - timedelta(weeks=17)
	while day <= end:
		for (s, _n, _t, waste_w, coal_w, lbcm), machine in zip(EXAMPLE_MACHINES, machines):
			waste = round(waste_w * (0.85 + rand.random() * 0.3))
			coal = round(coal_w * (0.85 + rand.random() * 0.3))
			diesel = round((waste + coal / density) * lbcm * (0.88 + rand.random() * 0.24))
			frappe.get_doc(
				{
					"doctype": ENTRY,
					"entry_date": day,
					"site": sites[s],
					"equipment": machine,
					"diesel_litres": diesel,
					"coal_tons": coal,
					"waste_bcm": waste,
					"is_example": 1,
				}
			).insert()
		day += timedelta(weeks=1)
	return _answer()


@frappe.whitelist(methods=["POST"])
def clear_example():
	"""Delete the example data. An example site or machine that real entries were logged against is kept,
	with those entries."""
	for dt in (SITE, EQUIP, ENTRY):
		frappe.has_permission(dt, "delete", throw=True)
	for name in frappe.get_all(ENTRY, filters={"is_example": 1}, pluck="name"):
		frappe.delete_doc(ENTRY, name)
	kept = []
	for name, label in frappe.get_all(EQUIP, filters={"is_example": 1}, fields=["name", "equipment_name"], as_list=True):
		if frappe.db.exists(ENTRY, {"equipment": name}):
			kept.append(label)
		else:
			frappe.delete_doc(EQUIP, name)
	for name, label in frappe.get_all(SITE, filters={"is_example": 1}, fields=["name", "site_name"], as_list=True):
		if frappe.db.exists(EQUIP, {"site": name}) or frappe.db.exists(ENTRY, {"site": name}):
			kept.append(label)
		else:
			frappe.delete_doc(SITE, name)
	out = _answer()
	out["kept"] = kept
	return out
