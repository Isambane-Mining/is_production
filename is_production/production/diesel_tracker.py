# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

"""The Diesel L/BCM Tracker (portal page /portal_diesel_tracker): the diesel each machine burns against the
coal and waste it moves, by site.

	L/BCM = diesel litres ÷ (waste bcm + coal tons ÷ coal density)

It keeps its own sites, equipment, entries and settings (the Diesel Tracker doctypes), separate from the
Mining Simulation. Entries are typed on the page, or brought in from the production records (sync):

	diesel  the litres issued to each Asset on the submitted Daily Diesel Sheets of a Location, by sheet date
	waste   the bcm of the Hourly Production truck loads each excavator filled (Softs, Hards) and each truck
	coal    hauled (Coal), and the bcm of production dozing, by production date

one entry per site (Location), machine (Asset) and day. A truck's loads are its excavator's bcm, so its entries
do not count toward the site's volume (volume_counted = 0): the site counts each bcm once, while the truck's
own L/BCM still uses them. Coal comes as bcm; its tons are the bcm × the coal density of the settings.
The page asks for one period at a time, and the server sums it by machine.
"""

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, get_first_day, get_last_day, getdate, now_datetime, today

SITE = "Diesel Tracker Site"
EQUIP = "Diesel Tracker Equipment"
ENTRY = "Diesel Tracker Entry"
SETTINGS = "Diesel Tracker Settings"
MANUAL = "Manual"
AUTO = "Production records"

TYPES = (
	"Excavator", "Haul truck", "Front-end loader", "Dozer", "Drill rig", "Grader", "Water cart",
	"Light vehicle", "Lighting plant", "Generator", "Water pump", "Diesel bowser", "Other",
)
# the type of a machine made from an Asset, by its Asset Category
CATEGORY_TYPES = {
	"Excavator": "Excavator", "ADT": "Haul truck", "RDT": "Haul truck", "Loader": "Front-end loader",
	"Dozer": "Dozer", "Drills": "Drill rig", "Grader": "Grader", "Water Bowser": "Water cart",
	"LDV": "Light vehicle", "Vehicles": "Light vehicle", "Lightning Plant": "Lighting plant",
	"Generator": "Generator", "Water pump": "Water pump", "Diesel Bowsers": "Diesel bowser", "Diesel Bulk": "Diesel bowser",
}
# the settings: the page's names and their fields, with the values used until they are saved
SETTING_FIELDS = {"density": "coal_density", "green": "green_limit", "amber": "amber_limit", "max": "gauge_max"}
DEFAULTS = {"density": 1.4, "green": 0.8, "amber": 1.2, "max": 2.0}
KINDS = {"sites": SITE, "equipment": EQUIP, "entries": ENTRY}
LIST_LIMIT, ALL_LIMIT = 40, 2000  # the entries listed: the latest, or all of the period up to this
RECENT_DAYS, RESYNC_DAYS = 7, 90  # brought in again: every hour, and every night


def permissions():
	"""What the user may do on the page."""
	has = frappe.has_permission
	setup = bool(has(SITE, "create") and has(EQUIP, "create"))
	return {
		"entry": bool(has(ENTRY, "create")),
		"deleteEntry": bool(has(ENTRY, "delete")),
		# sites and machines; removing one removes its entries too
		"setup": setup,
		"deleteSetup": bool(has(SITE, "delete") and has(EQUIP, "delete") and has(ENTRY, "delete")),
		"settings": bool(has(SETTINGS, "write")),
		# bring the entries in from the production records now
		"sync": setup and bool(has(ENTRY, "create") and has(ENTRY, "delete")),
	}


def settings():
	stored = frappe.db.get_singles_dict(SETTINGS)
	out = dict(DEFAULTS)
	for key, field in SETTING_FIELDS.items():
		if stored.get(field) not in (None, ""):
			out[key] = flt(stored.get(field))
	return out


def last_sync():
	stored = frappe.db.get_singles_dict(SETTINGS)
	at = stored.get("last_synced")
	return {"at": str(at) if at else None, "note": stored.get("last_sync_note") or ""}


# ---- the page's view: one period (and site) at a time


def _view(view=None):
	"""{from, to, site, limit} of the page; latest: the latest month with entries (this month when none)."""
	v = json.loads(view) if isinstance(view, str) else dict(view or {})
	frm = getdate(v["from"]) if v.get("from") else None
	to = getdate(v["to"]) if v.get("to") else None
	if v.get("latest"):
		last = frappe.db.sql(f"select max(entry_date) from `tab{ENTRY}`")[0][0]
		day = getdate(last) if last else getdate(today())
		frm, to = get_first_day(day), get_last_day(day)
	site = v.get("site") if v.get("site") not in (None, "", "all") else None
	limit = ALL_LIMIT if cint(v.get("limit")) > LIST_LIMIT else LIST_LIMIT
	return {"from": frm, "to": to, "site": site, "limit": limit}


def page_data(view=None):
	"""Sites and equipment (with their entry counts), and for the period: each machine's sums, the latest entries
	and their number; the months that have entries, the settings and what the user may do."""
	v = _view(view)
	where, values = ["1=1"], {"auto": AUTO, "limit": v["limit"]}
	if v["from"]:
		where.append("entry_date >= %(from)s")
		values["from"] = v["from"]
	if v["to"]:
		where.append("entry_date <= %(to)s")
		values["to"] = v["to"]
	if v["site"]:
		where.append("site = %(site)s")
		values["site"] = v["site"]
	cond = " and ".join(where)
	summary = frappe.db.sql(
		f"""select equipment, site, sum(diesel_litres) diesel, sum(coal_tons) coal, sum(waste_bcm) waste,
			sum(if(volume_counted, coal_tons, 0)) coal_counted, sum(if(volume_counted, waste_bcm, 0)) waste_counted,
			count(*) n, sum(source = %(auto)s) n_auto
		from `tab{ENTRY}` where {cond} group by equipment, site""",
		values,
		as_dict=True,
	)
	entries = frappe.db.sql(
		f"""select name, entry_date, site, equipment, diesel_litres, coal_tons, waste_bcm, volume_counted, source, is_example
		from `tab{ENTRY}` where {cond} order by entry_date desc, creation desc limit %(limit)s""",
		values,
		as_dict=True,
	)
	count = frappe.db.sql(f"select count(*) from `tab{ENTRY}` where {cond}", values)[0][0]
	months = [r[0] for r in frappe.db.sql(f"select distinct date_format(entry_date, '%Y-%m') m from `tab{ENTRY}` order by m")]
	# the entries of each site and machine, all of them (what deleting it would take; production records cannot be)
	by_site = {r[0]: r[1:] for r in frappe.db.sql(f"select site, count(*), sum(source = %s) from `tab{ENTRY}` group by site", (AUTO,))}
	by_equip = {r[0]: r[1:] for r in frappe.db.sql(f"select equipment, count(*), sum(source = %s) from `tab{ENTRY}` group by equipment", (AUTO,))}
	sites = frappe.get_list(SITE, fields=["name", "site_name", "location", "is_example"], order_by="site_name asc", limit_page_length=0)
	equipment = frappe.get_list(
		EQUIP, fields=["name", "site", "equipment_name", "equipment_type", "asset", "is_example"], order_by="equipment_name asc", limit_page_length=0
	)
	return {
		"sites": [
			{"id": s.name, "name": s.site_name, "location": s.location, "example": bool(s.is_example),
			 "n": cint(by_site.get(s.name, (0, 0))[0]), "nAuto": cint(by_site.get(s.name, (0, 0))[1])}
			for s in sites
		],
		"equipment": [
			{"id": q.name, "siteId": q.site, "name": q.equipment_name, "type": q.equipment_type, "asset": q.asset, "example": bool(q.is_example),
			 "n": cint(by_equip.get(q.name, (0, 0))[0]), "nAuto": cint(by_equip.get(q.name, (0, 0))[1])}
			for q in equipment
		],
		"summary": [
			{"equipmentId": r.equipment, "siteId": r.site, "diesel": flt(r.diesel), "coal": flt(r.coal), "waste": flt(r.waste),
			 "coalCounted": flt(r.coal_counted), "wasteCounted": flt(r.waste_counted), "n": cint(r.n), "nAuto": cint(r.n_auto)}
			for r in summary
		],
		"entries": [
			{"id": e.name, "date": str(e.entry_date), "siteId": e.site, "equipmentId": e.equipment, "diesel": flt(e.diesel_litres),
			 "coal": flt(e.coal_tons), "waste": flt(e.waste_bcm), "counted": bool(e.volume_counted), "auto": e.source == AUTO, "example": bool(e.is_example)}
			for e in entries
		],
		"entryCount": cint(count),
		"months": months,
		"view": {"from": str(v["from"]) if v["from"] else "", "to": str(v["to"]) if v["to"] else "", "site": v["site"] or "all", "limit": v["limit"]},
		"settings": settings(),
		"sync": last_sync(),
		"can": permissions(),
	}


def _answer(view=None, doc_id=None):
	return {"id": doc_id, "data": page_data(view)}


@frappe.whitelist()
def get_data(view=None):
	frappe.has_permission(ENTRY, "read", throw=True)
	return page_data(view)


# ---- typed on the page


@frappe.whitelist(methods=["POST"])
def add_site(name=None, view=None):
	doc = frappe.get_doc({"doctype": SITE, "site_name": (name or "").strip()}).insert()
	return _answer(view, doc.name)


@frappe.whitelist(methods=["POST"])
def add_equipment(siteId=None, name=None, type=None, view=None):
	if type not in TYPES:
		frappe.throw(_("Choose the type of machine."))
	doc = frappe.get_doc({"doctype": EQUIP, "site": siteId, "equipment_name": (name or "").strip(), "equipment_type": type}).insert()
	return _answer(view, doc.name)


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
def add_entry(date=None, siteId=None, equipmentId=None, diesel=None, coal=None, waste=None, view=None):
	if not date:
		frappe.throw(_("Enter the date for this entry."))
	doc = frappe.get_doc(
		{
			"doctype": ENTRY,
			"entry_date": getdate(date),
			"site": siteId,
			"equipment": equipmentId,
			"source": MANUAL,
			"diesel_litres": _amount(diesel, _("diesel litres"), required=True),
			"coal_tons": _amount(coal, _("coal tons")),
			"waste_bcm": _amount(waste, _("waste bcm")),
			"volume_counted": 1,
		}
	).insert()
	return _answer(view, doc.name)


@frappe.whitelist(methods=["POST"])
def delete(kind=None, id=None, view=None):
	"""Delete a typed entry, a machine (with its entries) or a site (with its machines and their entries). Each
	goes to Deleted Documents, where it can be restored from the desk. What the production records bring in is
	not deleted here: it would come back with the next hour."""
	doctype = KINDS.get(kind)
	if not doctype or not id or not frappe.db.exists(doctype, id):
		frappe.throw(_("That is no longer there: the page has been refreshed."))
	if kind == "entries":
		if frappe.db.get_value(ENTRY, id, "source") == AUTO:
			frappe.throw(_("This entry comes from the production records. Correct the Daily Diesel Sheet or the Hourly Production instead: the entry follows within the hour."))
	else:
		for dt in (ENTRY, EQUIP, SITE):
			frappe.has_permission(dt, "delete", throw=True)
		field = "site" if kind == "sites" else "equipment"
		if frappe.db.exists(ENTRY, {field: id, "source": AUTO}):
			frappe.throw(_("Its entries come from the production records, so it would come back within the hour. Only sites and machines typed on the page can be deleted."))
		for name in frappe.get_all(ENTRY, filters={field: id}, pluck="name"):
			frappe.delete_doc(ENTRY, name)
		if kind == "sites":
			for name in frappe.get_all(EQUIP, filters={"site": id}, pluck="name"):
				frappe.delete_doc(EQUIP, name)
	frappe.delete_doc(doctype, id)
	return _answer(view)


@frappe.whitelist(methods=["POST"])
def save_settings(density=None, green=None, amber=None, max=None, view=None):
	doc = frappe.get_doc(SETTINGS)
	before = settings()["density"]
	given = {"density": density, "green": green, "amber": amber, "max": max}
	for key, field in SETTING_FIELDS.items():
		doc.set(field, flt(given[key]) if given[key] not in (None, "") else DEFAULTS[key])
	doc.save()
	if flt(doc.coal_density) != before:
		# (the coal of the production records comes as bcm: its tons follow the density)
		frappe.db.sql(f"update `tab{ENTRY}` set coal_tons = coal_bcm * %s where source = %s", (flt(doc.coal_density), AUTO))
	return _answer(view)


# ---- brought in from the production records

# the litres issued to each asset on the submitted Daily Diesel Sheets, by location and sheet date
DIESEL_SQL = """select s.location, s.daily_sheet_date, e.asset_name, sum(e.litres_issued)
	from `tabDaily Diesel Entries` e join `tabDaily Diesel Sheet` s on s.name = e.parent
	where e.parenttype = 'Daily Diesel Sheet' and s.docstatus = 1 and s.daily_sheet_date between %s and %s
		and ifnull(s.location, '') != '' and ifnull(e.asset_name, '') != '' and ifnull(e.litres_issued, 0) != 0
	group by s.location, s.daily_sheet_date, e.asset_name"""
# the bcm of the Hourly Production truck loads, coal and waste, by location, production date and the excavator
# (asset_name_shoval) or the truck (asset_name_truck)
LOADS_SQL = """select h.location, h.prod_date, t.{col},
		sum(if(t.mat_type = 'Coal', t.bcms, 0)), sum(if(t.mat_type = 'Coal', 0, t.bcms))
	from `tabTruck Loads` t join `tabHourly Production` h on h.name = t.parent
	where t.parenttype = 'Hourly Production' and h.prod_date between %s and %s
		and ifnull(h.location, '') != '' and ifnull(t.{col}, '') != '' and ifnull(t.bcms, 0) != 0
	group by h.location, h.prod_date, t.{col}"""
# production dozing: the bcm of each hour, by location, production date and dozer
DOZING_SQL = """select h.location, h.prod_date, d.asset_name, sum(d.bcm_hour)
	from `tabDozer Production` d join `tabHourly Production` h on h.name = d.parent
	where d.parenttype = 'Hourly Production' and h.prod_date between %s and %s
		and ifnull(h.location, '') != '' and ifnull(d.asset_name, '') != '' and ifnull(d.bcm_hour, 0) > 0
	group by h.location, h.prod_date, d.asset_name"""


def _first_source_day():
	days = [
		frappe.db.sql("select min(daily_sheet_date) from `tabDaily Diesel Sheet` where docstatus = 1")[0][0],
		frappe.db.sql("select min(prod_date) from `tabHourly Production`")[0][0],
	]
	days = [getdate(d) for d in days if d]
	return min(days) if days else None


def _ensure_sites(locations):
	"""The tracker's site of each Location: linked, or the one of the same name (linked now), or a new one."""
	out = {}
	for loc in sorted(locations):
		name = frappe.db.get_value(SITE, {"location": loc})
		if not name:
			name = frappe.db.get_value(SITE, {"site_name": loc})
			if name:
				frappe.db.set_value(SITE, name, "location", loc)
			else:
				name = frappe.get_doc({"doctype": SITE, "site_name": loc, "location": loc}).insert(ignore_permissions=True).name
		out[loc] = name
	return out


def _ensure_equipment(pairs):
	"""The tracker's machine of each (site, Asset): linked, or the one typed with the asset's fleet number on that
	site (linked now), or a new one named for the asset, its type from the Asset Category."""
	have = {(q.site, q.asset): q.name for q in frappe.get_all(EQUIP, filters={"asset": ["is", "set"]}, fields=["name", "site", "asset"], limit_page_length=0)}
	missing = sorted(p for p in pairs if p not in have)
	if missing:
		# (an asset since deleted from Asset, still on old sheets: its machine is kept by its fleet number, unlinked)
		category = dict(frappe.get_all("Asset", filters={"name": ["in", sorted({a for _s, a in missing})]}, fields=["name", "asset_category"], as_list=True, limit_page_length=0))
		for site, asset in missing:
			name = frappe.db.get_value(EQUIP, {"site": site, "equipment_name": asset, "asset": ["is", "not set"]})
			if name:
				if asset in category:
					frappe.db.set_value(EQUIP, name, "asset", asset)
			else:
				label = asset if not frappe.db.exists(EQUIP, {"site": site, "equipment_name": asset}) else f"{asset} (asset)"
				name = frappe.get_doc(
					{
						"doctype": EQUIP,
						"site": site,
						"equipment_name": label,
						"asset": asset if asset in category else None,
						"equipment_type": CATEGORY_TYPES.get(category.get(asset), "Other"),
					}
				).insert(ignore_permissions=True).name
			have[(site, asset)] = name
	return have


def sync(from_date=None, to_date=None):
	"""Make the production-record entries of the days from_date..to_date (every day the records hold when not
	given) match the Daily Diesel Sheets and Hourly Production: new ones added, changed ones updated, those whose
	records are gone removed. Typed entries are left as they are. → what it did."""
	frm = getdate(from_date) if from_date else _first_source_day()
	to = getdate(to_date) if to_date else getdate(today())
	result = {"from": str(frm) if frm else "", "to": str(to), "added": 0, "updated": 0, "removed": 0, "sites": 0, "machines": 0}
	if frm and frm <= to:
		rows = {}

		def row(loc, day, asset):
			key = (loc, getdate(day), asset)
			if key not in rows:
				rows[key] = {"diesel": 0.0, "coal": 0.0, "waste": 0.0, "dig": False, "haul": False}
			return rows[key]

		for loc, day, asset, litres in frappe.db.sql(DIESEL_SQL, (frm, to)):
			row(loc, day, asset)["diesel"] += flt(litres)
		# the volumes: the excavators' loads and production dozing count toward the site; a truck's loads are
		# its excavator's, so they are its own but do not count again
		for loc, day, asset, coal, waste in frappe.db.sql(LOADS_SQL.format(col="asset_name_shoval"), (frm, to)):
			r = row(loc, day, asset)
			r["coal"] += flt(coal)
			r["waste"] += flt(waste)
			r["dig"] = True
		for loc, day, asset, bcm in frappe.db.sql(DOZING_SQL, (frm, to)):
			r = row(loc, day, asset)
			r["waste"] += flt(bcm)
			r["dig"] = True
		for loc, day, asset, coal, waste in frappe.db.sql(LOADS_SQL.format(col="asset_name_truck"), (frm, to)):
			r = row(loc, day, asset)
			if not r["dig"]:
				r["coal"] += flt(coal)
				r["waste"] += flt(waste)
				r["haul"] = True

		sites_before, machines_before = frappe.db.count(SITE), frappe.db.count(EQUIP)
		site_of = _ensure_sites({k[0] for k in rows})
		equip_of = _ensure_equipment({(site_of[loc], asset) for loc, _d, asset in rows})
		result["sites"] = frappe.db.count(SITE) - sites_before
		result["machines"] = frappe.db.count(EQUIP) - machines_before

		density = settings()["density"]
		want = {}
		for (loc, day, asset), r in rows.items():
			site = site_of[loc]
			coal = round(max(r["coal"], 0.0), 3)
			want[(site, equip_of[(site, asset)], day)] = {
				"diesel_litres": round(max(r["diesel"], 0.0), 3),
				"coal_bcm": coal,
				"coal_tons": round(coal * density, 3),
				"waste_bcm": round(max(r["waste"], 0.0), 3),
				"volume_counted": 0 if (r["haul"] and not r["dig"]) else 1,
			}
		have = {
			(e.site, e.equipment, getdate(e.entry_date)): e
			for e in frappe.get_all(
				ENTRY,
				filters={"source": AUTO, "entry_date": ["between", [frm, to]]},
				fields=["name", "site", "equipment", "entry_date", *want_fields()],
				limit_page_length=0,
			)
		}
		new = []
		for key, vals in want.items():
			old = have.get(key)
			if old is None:
				new.append((key, vals))
			elif any(abs(flt(old.get(f)) - flt(v)) > 1e-6 for f, v in vals.items()):
				frappe.db.set_value(ENTRY, old.name, vals)
				result["updated"] += 1
		gone = [e.name for key, e in have.items() if key not in want]
		for i in range(0, len(gone), 1000):
			frappe.db.delete(ENTRY, {"name": ["in", gone[i : i + 1000]]})
		result["removed"] = len(gone)
		if new:
			stamp, user = now_datetime(), frappe.session.user
			fields = ["name", "creation", "modified", "owner", "modified_by", "docstatus", "idx", "entry_date", "site", "equipment", "source", "is_example", *want_fields()]
			frappe.db.bulk_insert(
				ENTRY,
				fields,
				[
					(frappe.generate_hash(length=10), stamp, stamp, user, user, 0, 0, day, site, eq, AUTO, 0, *(vals[f] for f in want_fields()))
					for (site, eq, day), vals in new
				],
				chunk_size=2000,
			)
		result["added"] = len(new)
	note = (
		_("{0} to {1}: {2} added, {3} updated, {4} removed").format(result["from"], result["to"], result["added"], result["updated"], result["removed"])
		if result["from"]
		else _("No production records yet")
	)
	if result["sites"] or result["machines"]:
		note += _("; {0} sites and {1} machines made").format(result["sites"], result["machines"])
	frappe.db.set_single_value(SETTINGS, {"last_synced": now_datetime(), "last_sync_note": note})
	result["note"] = note
	return result


def want_fields():
	return ("diesel_litres", "coal_bcm", "coal_tons", "waste_bcm", "volume_counted")


@frappe.whitelist(methods=["POST"])
def sync_now(from_date=None, to_date=None, view=None):
	"""Bring the entries in from the production records now, for the days given (all of them when none)."""
	if not permissions()["sync"]:
		raise frappe.PermissionError(_("A Production Manager brings the entries in from the production records."))
	result = sync(from_date or None, to_date or None)
	out = _answer(view)
	out["sync"] = result
	return out


def scheduled_sync():
	"""Every hour (hooks): the last week; the first time, every day the production records hold."""
	if frappe.db.exists(ENTRY, {"source": AUTO}):
		sync(add_days(today(), -RECENT_DAYS), today())
	else:
		sync()


def scheduled_resync():
	"""Every night (hooks): the last three months, for sheets submitted, cancelled or corrected late."""
	sync(add_days(today(), -RESYNC_DAYS), today())


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
def load_example(view=None):
	import random
	from datetime import timedelta

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
					"source": MANUAL,
					"diesel_litres": diesel,
					"coal_tons": coal,
					"waste_bcm": waste,
					"is_example": 1,
				}
			).insert()
		day += timedelta(weeks=1)
	return _answer(view)


@frappe.whitelist(methods=["POST"])
def clear_example(view=None):
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
	out = _answer(view)
	out["kept"] = kept
	return out
