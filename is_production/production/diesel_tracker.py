# Copyright (c) 2026, Isambane Mining (Pty) Ltd and contributors
# For license information, please see license.txt

"""The Diesel L/BCM Tracker (portal page /portal_diesel_tracker): the diesel each machine burns against the
coal and waste it moves, by site.

It keeps its own sites, equipment, entries and settings (the Diesel Tracker doctypes), separate from the
Mining Simulation. Everything is brought in from the production records (sync); nothing is typed:

	diesel  the litres issued to each Asset on the submitted Daily Diesel Sheets of a Location, by sheet date
	waste   the bcm of the Hourly Production truck loads each excavator filled (Softs, Hards) and each truck
	coal    hauled (Coal), and the bcm of production dozing, by production date

one entry per site (Location), machine (Asset) and day. These are the tallies. A truck's loads are its
excavator's bcm, so its entries do not count toward the site's volume (volume_counted = 0): the site counts
each bcm once, while the truck's own L/BCM still uses them. Coal comes as bcm; its tons are bcm × 1.5, as in
Hourly Production and the Monthly Production Planning.

The site's volume is the actual volume, not the tallies (actual_volumes): the surveys applied as the Monthly
Production Planning applies them to its Monthly Actual BCMs.

	L/BCM (the site's) = diesel litres ÷ the actual bcm
	L/BCM (a machine's) = its diesel litres ÷ its tallied bcm (surveys are by site, not by machine)
	burn rate (L/h)     = diesel litres ÷ engine hours (Pre-Use Hours)

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
AUTO = "Production records"
COAL_T_PER_BCM = 1.5  # (as Hourly Production and the Monthly Production Planning)

# the type of a machine made from an Asset, by its Asset Category
CATEGORY_TYPES = {
	"Excavator": "Excavator", "ADT": "Haul truck", "RDT": "Haul truck", "Loader": "Front-end loader",
	"Dozer": "Dozer", "Drills": "Drill rig", "Grader": "Grader", "Water Bowser": "Water cart",
	"LDV": "Light vehicle", "Vehicles": "Light vehicle", "Lightning Plant": "Lighting plant",
	"Generator": "Generator", "Water pump": "Water pump", "Diesel Bowsers": "Diesel bowser", "Diesel Bulk": "Diesel bowser",
}
# the gauge's settings: the page's names and their fields, with the values used until they are saved
SETTING_FIELDS = {"green": "green_limit", "amber": "amber_limit", "max": "gauge_max"}
DEFAULTS = {"green": 0.8, "amber": 1.2, "max": 2.0}
LIST_LIMIT, ALL_LIMIT = 40, 2000  # the entries listed: the latest, or all of the period up to this
RECENT_DAYS, RESYNC_DAYS = 7, 90  # brought in again: every hour, and every night


def permissions():
	"""What the user may do on the page: change the gauge's bands, and bring the production records in now."""
	settings_ = bool(frappe.has_permission(SETTINGS, "write"))
	return {"settings": settings_, "sync": settings_}


def settings():
	stored = frappe.db.get_singles_dict(SETTINGS)
	out = dict(DEFAULTS)
	for key, field in SETTING_FIELDS.items():
		if stored.get(field) not in (None, ""):
			out[key] = flt(stored.get(field))
	out["density"] = COAL_T_PER_BCM
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


def _where(v, extra=()):
	"""The entries of the view: an SQL condition and its values."""
	where, values = ["1=1", *extra], {"auto": AUTO}
	if v["from"]:
		where.append("entry_date >= %(from)s")
		values["from"] = v["from"]
	if v["to"]:
		where.append("entry_date <= %(to)s")
		values["to"] = v["to"]
	if v["site"]:
		where.append("site = %(site)s")
		values["site"] = v["site"]
	return " and ".join(where), values


def page_data(view=None):
	"""Sites and equipment, and for the period: each machine's sums, the actual volume, the burn rates, the latest
	entries and their number; the months that have entries, the settings and what the user may do."""
	v = _view(view)
	cond, values = _where(v)
	values["limit"] = v["limit"]
	summary = frappe.db.sql(
		f"""select equipment, site, sum(diesel_litres) diesel, sum(coal_tons) coal, sum(waste_bcm) waste,
			sum(if(volume_counted, coal_tons, 0)) coal_counted, sum(if(volume_counted, waste_bcm, 0)) waste_counted,
			count(*) n, sum(source = %(auto)s) n_auto
		from `tab{ENTRY}` where {cond} group by equipment, site""",
		values,
		as_dict=True,
	)
	entries = frappe.db.sql(
		f"""select name, entry_date, site, equipment, diesel_litres, coal_tons, waste_bcm, volume_counted, source
		from `tab{ENTRY}` where {cond} order by entry_date desc, creation desc limit %(limit)s""",
		values,
		as_dict=True,
	)
	count = frappe.db.sql(f"select count(*) from `tab{ENTRY}` where {cond}", values)[0][0]
	months = [r[0] for r in frappe.db.sql(f"select distinct date_format(entry_date, '%Y-%m') m from `tab{ENTRY}` order by m")]
	sites = frappe.get_list(SITE, fields=["name", "site_name", "location"], order_by="site_name asc", limit_page_length=0)
	equipment = frappe.get_list(
		EQUIP, fields=["name", "site", "equipment_name", "equipment_type", "asset"], order_by="equipment_name asc", limit_page_length=0
	)
	return {
		"sites": [{"id": s.name, "name": s.site_name, "location": s.location} for s in sites],
		"equipment": [{"id": q.name, "siteId": q.site, "name": q.equipment_name, "type": q.equipment_type, "asset": q.asset} for q in equipment],
		"summary": [
			{"equipmentId": r.equipment, "siteId": r.site, "diesel": flt(r.diesel), "coal": flt(r.coal), "waste": flt(r.waste),
			 "coalCounted": flt(r.coal_counted), "wasteCounted": flt(r.waste_counted), "n": cint(r.n), "nAuto": cint(r.n_auto)}
			for r in summary
		],
		"entries": [
			{"id": e.name, "date": str(e.entry_date), "siteId": e.site, "equipmentId": e.equipment, "diesel": flt(e.diesel_litres),
			 "coal": flt(e.coal_tons), "waste": flt(e.waste_bcm), "counted": bool(e.volume_counted), "auto": e.source == AUTO}
			for e in entries
		],
		"entryCount": cint(count),
		"actual": actual_volumes(v, sites),
		"burn": burn_rates(v, summary, sites),
		"months": months,
		"view": {"from": str(v["from"]) if v["from"] else "", "to": str(v["to"]) if v["to"] else "", "site": v["site"] or "all", "limit": v["limit"]},
		"settings": settings(),
		"sync": last_sync(),
		"can": permissions(),
	}


@frappe.whitelist()
def get_data(view=None):
	frappe.has_permission(ENTRY, "read", throw=True)
	return page_data(view)


@frappe.whitelist(methods=["POST"])
def save_settings(green=None, amber=None, max=None, view=None):
	"""The gauge's bands (L/bcm): green up to, amber up to (red above), and the gauge's top."""
	doc = frappe.get_doc(SETTINGS)
	given = {"green": green, "amber": amber, "max": max}
	for key, field in SETTING_FIELDS.items():
		doc.set(field, flt(given[key]) if given[key] not in (None, "") else DEFAULTS[key])
	doc.save()
	return {"data": page_data(view)}


# ---- the actual volume: the tallies with the surveys applied

MPP = "Monthly Production Planning"


def actual_volumes(v, sites):
	"""The actual volume of the period (and site): the Monthly Production Planning's Monthly Actual BCMs and Monthly
	Actual Coal tons (the tallies with the surveys applied), shared over the days of each production month:

	- a day belongs to the production month (plan) most of its Hourly Production records belong to, else to the
	  one whose dates hold it;
	- bcm: the plan's survey variance (Monthly Actual BCM Tally Variance) falls on the days up to its last survey
	  (the day of the plan's survey row), pro rata to their tallies; the days after it count as tallied. A whole
	  production month so comes to its tallies + the variance, the plan's Monthly Actual BCMs;
	- coal: the plan's Monthly Actual Coal tons, shared over its days pro rata to their tallied coal;
	- days in no production month count as tallied.

	The tallies are the volumes the site counts (the excavators' loads and production dozing). The figures are
	as fresh as the plans: worked out again every hour while their month runs, or with the plan's Update MTD
	Production. → {bcm, coalT, wasteBcm (actual); tallied, coalTallied; months, surveyed (production months, with
	a survey variance)}"""
	cond, values = _where(v, ["source = %(auto)s", "volume_counted = 1"])
	days = frappe.db.sql(
		f"select site, entry_date, sum(waste_bcm + coal_bcm), sum(coal_bcm) from `tab{ENTRY}` where {cond} group by site, entry_date",
		values,
	)
	location = {s.name: s.location for s in sites if s.location}
	locs = sorted({location[s] for s, *_x in days if s in location})
	plans, day_plan, tallies = {}, {}, {}
	if locs:
		lo, hi = min(getdate(d) for _s, d, *_x in days), max(getdate(d) for _s, d, *_x in days)
		for name, loc, start, end, var, coal in frappe.db.sql(
			f"""select name, location, prod_month_start_date, prod_month_end_date, monthly_act_tally_survey_variance, month_actual_coal
			from `tab{MPP}` where location in %(locs)s and prod_month_start_date <= %(hi)s and prod_month_end_date >= %(lo)s""",
			{"locs": tuple(locs), "hi": hi, "lo": lo},
		):
			plans[name] = {"loc": loc, "start": getdate(start), "end": getdate(end), "var": flt(var), "coal": flt(coal), "until": None}
	if plans:
		names = tuple(plans)
		first, last = min(p["start"] for p in plans.values()), max(p["end"] for p in plans.values())
		# the plan of each day: the one most of its Hourly Production records belong to
		counts = {}
		for loc, day, plan, n in frappe.db.sql(
			"""select location, prod_date, month_prod_planning, count(*) from `tabHourly Production`
			where location in %(locs)s and prod_date between %(lo)s and %(hi)s and month_prod_planning in %(names)s
			group by location, prod_date, month_prod_planning""",
			{"locs": tuple(locs), "lo": first, "hi": last, "names": names},
		):
			key = (loc, getdate(day))
			if n > counts.get(key, 0):
				counts[key], day_plan[key] = n, plan
		# the day of each plan's last survey (its survey row)
		for parent, day in frappe.db.sql(
			"""select parent, shift_start_date from `tabMonthly Production Days` where parenttype = %(mpp)s and parent in %(names)s
				and (ifnull(tot_cum_ts_survey, 0) != 0 or ifnull(tot_cum_dozing_survey, 0) != 0) order by shift_start_date""",
			{"mpp": MPP, "names": names},
		):
			plans[parent]["until"] = getdate(day)
		# the tallies of the whole production months, beyond the period too (the variance and coal are shared over them)
		site_loc = {s: loc for s, loc in location.items() if loc in locs}
		for site, day, vol, coal in frappe.db.sql(
			f"""select site, entry_date, sum(waste_bcm + coal_bcm), sum(coal_bcm) from `tab{ENTRY}`
			where source = %(auto)s and volume_counted = 1 and site in %(sites)s and entry_date between %(lo)s and %(hi)s
			group by site, entry_date""",
			{"auto": AUTO, "sites": tuple(site_loc), "lo": first, "hi": last},
		):
			t = tallies.setdefault((site_loc[site], getdate(day)), [0.0, 0.0])
			t[0] += flt(vol)
			t[1] += flt(coal)

	def plan_of(loc, day):
		name = day_plan.get((loc, day))
		if name in plans:
			return name
		hit = [n for n, p in plans.items() if p["loc"] == loc and p["start"] <= day <= p["end"]]
		return max(hit, key=lambda n: plans[n]["start"]) if hit else None

	def in_view(loc, day):
		return (not v["from"] or day >= v["from"]) and (not v["to"] or day <= v["to"]) and (not v["site"] or location.get(v["site"]) == loc)

	# what each plan shares over its days: the variance over those up to its survey, the coal over all of them
	lumps = 0.0, 0.0
	for name, p in plans.items():
		mine = [(day, t) for (loc, day), t in tallies.items() if loc == p["loc"] and plan_of(loc, day) == name]
		upto = p["until"] if p["until"] else p["end"]  # (a variance without its survey row: over the whole month)
		p["base"] = sum(t[0] for day, t in mine if day <= upto)
		p["upto"] = upto
		coal = sum(t[1] for _d, t in mine) * COAL_T_PER_BCM
		p["kc"] = p["coal"] / coal if coal > 0 else 1.0
		# (a variance or coal with no tallies to share it over: on the survey day, or the month's last)
		if p["var"] and p["base"] <= 0 and in_view(p["loc"], upto):
			lumps = (lumps[0] + p["var"], lumps[1])
		if p["coal"] and coal <= 0 and in_view(p["loc"], p["end"]):
			lumps = (lumps[0], lumps[1] + p["coal"])
	bcm = tallied = coal_t = coal_tallied = 0.0
	used, surveyed = set(), set()
	for site, day, vol, coal in days:
		day, vol, coal = getdate(day), flt(vol), flt(coal)
		loc = location.get(site)
		name = plan_of(loc, day) if plans and loc else None
		p = plans.get(name) if name else None
		share = vol / p["base"] if p and p["var"] and p["base"] > 0 and day <= p["upto"] else 0.0
		if p:
			used.add(name)
			if p["var"]:
				surveyed.add(name)
		tallied += vol
		bcm += vol + share * (p["var"] if p else 0.0)
		coal_tallied += coal * COAL_T_PER_BCM
		coal_t += coal * COAL_T_PER_BCM * (p["kc"] if p else 1.0)
	bcm += lumps[0]
	coal_t += lumps[1]
	return {
		"bcm": round(bcm, 1), "coalT": round(coal_t, 1), "wasteBcm": round(bcm - coal_t / COAL_T_PER_BCM, 1),
		"tallied": round(tallied, 1), "coalTallied": round(coal_tallied, 1), "months": len(used), "surveyed": len(surveyed),
	}


# ---- burn rate: litres an engine hour of the ADTs, excavators and dozers

# the three graphs: key, title, Asset Categories
FLEETS = (("adt", "ADTs", ("ADT",)), ("excavator", "Excavators", ("Excavator",)), ("dozer", "Dozers", ("Dozer",)))
# the engine hours of each asset a day at a location, as the Monthly Diesel Reconciliation counts them: the Night
# (Afternoon) shift's closing hour-meter reading less the Day (Morning) shift's opening one, on the Pre-Use Hours
# sheets not cancelled; a day without both, with a closing reading of 0, or under 0 or over 24 hours is left out
HOURS_SQL = """select location, asset_name, sum(h_end - h_start), count(*) from (
		select p.location, e.asset_name, p.shift_date,
			max(case when p.shift in ('Day', 'Morning') then e.eng_hrs_start end) h_start,
			max(case when p.shift in ('Night', 'Afternoon') then e.eng_hrs_end end) h_end
		from `tabPre-use Assets` e join `tabPre-Use Hours` p on p.name = e.parent
		where e.parenttype = 'Pre-Use Hours' and e.parentfield = 'pre_use_assets' and p.docstatus < 2
			and p.shift_date between %(from)s and %(to)s and e.asset_name in %(assets)s {loc}
		group by p.location, e.asset_name, p.shift_date
	) d
	where h_start is not null and h_end is not null and h_end != 0 and h_end - h_start between 0 and 24
	group by location, asset_name"""


def _oem_rates(models, day):
	"""The OEM burn rate (L/h) of each model on the day: the latest effective then (OEM Diesel Burn Rate)."""
	if not models:
		return {}
	rows = frappe.db.sql(
		"""select model, burn_rate_lph from `tabOEM Diesel Burn Rate`
		where model in %(models)s and effective_from <= %(day)s
		order by model, effective_from, modified""",
		{"models": tuple(models), "day": day},
	)
	return {model: flt(rate) for model, rate in rows}  # (the latest of each model last)


def burn_rates(v, summary, sites):
	"""Each ADT, excavator and dozer of the period (and site): its litres (its entries'), engine hours (Pre-Use
	Hours, as the Monthly Diesel Reconciliation counts them) and burn rate = litres ÷ hours, against the OEM burn
	rate of its model at the end of the period; and each fleet's: the litres ÷ the hours of its machines that
	have hours, against their OEM rates weighted by their hours."""
	cats = {c: key for key, _t, cs in FLEETS for c in cs}
	assets = {
		a.name: a
		for a in frappe.get_all("Asset", filters={"asset_category": ["in", list(cats)]}, fields=["name", "asset_category", "item_code"], limit_page_length=0)
	}
	where = {"asset": ["in", list(assets) or [""]]}
	if v["site"]:
		where["site"] = v["site"]
	machines = frappe.get_all(EQUIP, filters=where, fields=["name", "site", "asset"], limit_page_length=0)
	litres = {r.equipment: flt(r.diesel) for r in summary}
	location = {s.name: s.location for s in sites if s.location}
	day_to = v["to"] or getdate(today())
	hours = {}
	if machines:
		locs = sorted({location[q.site] for q in machines if q.site in location})
		if locs:
			for loc, asset, h, days in frappe.db.sql(
				HOURS_SQL.format(loc="and p.location in %(locs)s"),
				{"from": v["from"] or "2000-01-01", "to": day_to, "assets": tuple(sorted({q.asset for q in machines})), "locs": tuple(locs)},
			):
				hours[(loc, asset)] = (flt(h), cint(days))
	oem = _oem_rates(sorted({assets[q.asset].item_code for q in machines if assets[q.asset].item_code}), day_to)
	out = []
	for key, title, _cs in FLEETS:
		rows, lit_h, hrs, oem_h, hrs_oem, no_hours = [], 0.0, 0.0, 0.0, 0.0, 0
		for q in machines:
			a = assets[q.asset]
			if cats.get(a.asset_category) != key:
				continue
			h, days = hours.get((location.get(q.site), q.asset), (0.0, 0))
			l = litres.get(q.name, 0.0)
			if not l and not h:
				continue
			rate = oem.get(a.item_code)
			rows.append({"equipmentId": q.name, "siteId": q.site, "model": a.item_code or "", "litres": l, "hours": round(h, 2), "days": days,
						 "lph": round(l / h, 2) if h > 0 else None, "oem": rate})
			if h > 0:
				lit_h += l
				hrs += h
				if rate:
					oem_h += rate * h
					hrs_oem += h
			elif l:
				no_hours += 1
		out.append({"key": key, "title": title, "machines": rows, "litres": round(lit_h, 2), "hours": round(hrs, 2),
					"lph": round(lit_h / hrs, 2) if hrs > 0 else None, "oem": round(oem_h / hrs_oem, 2) if hrs_oem > 0 else None,
					"oemShare": round(hrs_oem / hrs, 3) if hrs > 0 else 0, "noHours": no_hours})
	return out


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
WANT_FIELDS = ("diesel_litres", "coal_bcm", "coal_tons", "waste_bcm", "volume_counted")


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
	"""The tracker's machine of each (site, Asset): linked, or the one named for the asset's fleet number on that
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
	"""Make the entries of the days from_date..to_date (every day the records hold when not given) match the Daily
	Diesel Sheets and Hourly Production: new ones added, changed ones updated, those whose records are gone
	removed. → what it did."""
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

		want = {}
		for (loc, day, asset), r in rows.items():
			site = site_of[loc]
			coal = round(max(r["coal"], 0.0), 3)
			want[(site, equip_of[(site, asset)], day)] = {
				"diesel_litres": round(max(r["diesel"], 0.0), 3),
				"coal_bcm": coal,
				"coal_tons": round(coal * COAL_T_PER_BCM, 3),
				"waste_bcm": round(max(r["waste"], 0.0), 3),
				"volume_counted": 0 if (r["haul"] and not r["dig"]) else 1,
			}
		have = {
			(e.site, e.equipment, getdate(e.entry_date)): e
			for e in frappe.get_all(
				ENTRY,
				filters={"source": AUTO, "entry_date": ["between", [frm, to]]},
				fields=["name", "site", "equipment", "entry_date", *WANT_FIELDS],
				limit_page_length=0,
			)
		}
		new = []
		for key, vals in want.items():
			old = have.get(key)
			if old is None:
				new.append((key, vals))
			elif any(abs(flt(old.get(f)) - flt(val)) > 1e-6 for f, val in vals.items()):
				frappe.db.set_value(ENTRY, old.name, vals)
				result["updated"] += 1
		gone = [e.name for key, e in have.items() if key not in want]
		for i in range(0, len(gone), 1000):
			frappe.db.delete(ENTRY, {"name": ["in", gone[i : i + 1000]]})
		result["removed"] = len(gone)
		if new:
			stamp, user = now_datetime(), frappe.session.user
			fields = ["name", "creation", "modified", "owner", "modified_by", "docstatus", "idx", "entry_date", "site", "equipment", "source", *WANT_FIELDS]
			frappe.db.bulk_insert(
				ENTRY,
				fields,
				[
					(frappe.generate_hash(length=10), stamp, stamp, user, user, 0, 0, day, site, eq, AUTO, *(vals[f] for f in WANT_FIELDS))
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


@frappe.whitelist(methods=["POST"])
def sync_now(from_date=None, to_date=None, view=None):
	"""Bring the entries in from the production records now, for the days given (all of them when none)."""
	if not permissions()["sync"]:
		raise frappe.PermissionError(_("A Production Manager brings the entries in from the production records."))
	result = sync(from_date or None, to_date or None)
	return {"data": page_data(view), "sync": result}


def scheduled_sync():
	"""Every hour (hooks): the last week; the first time, every day the production records hold."""
	if frappe.db.exists(ENTRY, {"source": AUTO}):
		sync(add_days(today(), -RECENT_DAYS), today())
	else:
		sync()


def scheduled_resync():
	"""Every night (hooks): the last three months, for sheets submitted, cancelled or corrected late."""
	sync(add_days(today(), -RESYNC_DAYS), today())
