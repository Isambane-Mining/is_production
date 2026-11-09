# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

"""The Diesel L/BCM Tracker's service (is_production.production.diesel_tracker) and its doctypes."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from is_production.production import diesel_tracker as svc

USER = "diesel.tracker.user@example.com"
MANAGER = "diesel.tracker.manager@example.com"
ALL = {"from": "", "to": "", "site": "all", "limit": 2000}
LOC = "DT Test Pit"  # (the source records, in 2001, away from real data)
MARCH = {"from": "2001-03-01", "to": "2001-03-31"}


def clear():
	for dt in (svc.ENTRY, svc.EQUIP, svc.SITE):
		frappe.db.delete(dt)
	frappe.db.delete("Singles", {"doctype": svc.SETTINGS})


def raw(doctype, rows):
	"""Source records written straight to their tables (they need no forms or other masters here)."""
	if not rows:
		return
	stamp = now_datetime()
	fields = sorted({k for r in rows for k in r} | {"creation", "modified", "owner", "modified_by"})
	values = [tuple({"creation": stamp, "modified": stamp, "owner": "Administrator", "modified_by": "Administrator", **r}.get(f) for f in fields) for r in rows]
	frappe.db.bulk_insert(doctype, fields, values)


def sources():
	"""Two days at LOC in the production month MPP-T1: EX-T1 fills HT-T1 (waste and coal), DZ-T1 dozes; diesel to
	all three and a light vehicle; their engine hours and the excavator's OEM rate. (Written afresh each time: a
	test may have changed them.)"""
	sheets, hours, pre = ["DDS-T1", "DDS-T2", "DDS-T3"], ["HP-T1", "HP-T2"], ["PU-T1", "PU-T2", "PU-T3", "PU-T4"]
	frappe.db.delete("Daily Diesel Entries", {"parent": ["in", sheets]})
	frappe.db.delete("Daily Diesel Sheet", {"name": ["in", sheets]})
	for child in ("Truck Loads", "Dozer Production"):
		frappe.db.delete(child, {"parent": ["in", hours]})
	frappe.db.delete("Hourly Production", {"name": ["in", hours]})
	frappe.db.delete("Pre-use Assets", {"parent": ["in", pre]})
	frappe.db.delete("Pre-Use Hours", {"name": ["in", pre]})
	frappe.db.delete("OEM Diesel Burn Rate", {"name": ["in", ["OEM-T1", "OEM-T2"]]})
	frappe.db.delete("Monthly Production Days", {"parent": "MPP-T1"})
	frappe.db.delete(svc.MPP, {"name": "MPP-T1"})
	if not frappe.db.exists("Location", LOC):
		raw("Location", [{"name": LOC, "location_name": LOC}])
	raw("Asset", [
		{"name": n, "asset_name": n, "asset_category": c, "item_code": m, "docstatus": 1}
		for n, c, m in (("EX-T1", "Excavator", "EX-MODEL-T"), ("HT-T1", "ADT", "ADT-MODEL-T"), ("DZ-T1", "Dozer", "DZ-MODEL-T"), ("LV-T1", "LDV", None))
		if not frappe.db.exists("Asset", n)
	])
	raw("Daily Diesel Sheet", [
		{"name": "DDS-T1", "location": LOC, "daily_sheet_date": "2001-03-01", "docstatus": 1},
		{"name": "DDS-T2", "location": LOC, "daily_sheet_date": "2001-03-02", "docstatus": 1},
		{"name": "DDS-T3", "location": LOC, "daily_sheet_date": "2001-03-02", "docstatus": 2},  # cancelled
	])
	sheet = lambda parent, i, asset, litres: {"name": f"{parent}-{i}", "parent": parent, "parenttype": "Daily Diesel Sheet", "parentfield": "daily_diesel_entries", "idx": i, "asset_name": asset, "litres_issued": litres}
	raw("Daily Diesel Entries", [
		sheet("DDS-T1", 1, "EX-T1", 800), sheet("DDS-T1", 2, "HT-T1", 300), sheet("DDS-T1", 3, "DZ-T1", 400), sheet("DDS-T1", 4, "LV-T1", 60),
		sheet("DDS-T2", 1, "EX-T1", 700), sheet("DDS-T3", 1, "EX-T1", 999),
	])
	raw("Hourly Production", [
		{"name": "HP-T1", "location": LOC, "prod_date": "2001-03-01", "month_prod_planning": "MPP-T1"},
		{"name": "HP-T2", "location": LOC, "prod_date": "2001-03-02", "month_prod_planning": "MPP-T1"},
	])
	load = lambda parent, i, bcm, mat: {"name": f"{parent}-L{i}", "parent": parent, "parenttype": "Hourly Production", "parentfield": "truck_loads", "idx": i, "asset_name_shoval": "EX-T1", "asset_name_truck": "HT-T1", "bcms": bcm, "mat_type": mat}
	raw("Truck Loads", [load("HP-T1", 1, 1000, "Hards"), load("HP-T1", 2, 200, "Softs"), load("HP-T1", 3, 300, "Coal"), load("HP-T2", 1, 900, "Hards")])
	raw("Dozer Production", [
		{"name": "HP-T1-D1", "parent": "HP-T1", "parenttype": "Hourly Production", "parentfield": "dozer_production", "idx": 1, "asset_name": "DZ-T1", "bcm_hour": 150},
		{"name": "HP-T1-D2", "parent": "HP-T1", "parenttype": "Hourly Production", "parentfield": "dozer_production", "idx": 2, "asset_name": "DZ-T1", "bcm_hour": 0},
	])
	# the production month: a survey on the 1st found 300 bcm more than the tallies; 600 t of coal actual
	raw(svc.MPP, [{"name": "MPP-T1", "location": LOC, "prod_month_start_date": "2001-03-01", "prod_month_end_date": "2001-03-31",
		"monthly_act_tally_survey_variance": 300, "month_actual_coal": 600}])
	raw("Monthly Production Days", [
		{"name": "MPP-T1-1", "parent": "MPP-T1", "parenttype": svc.MPP, "parentfield": "month_prod_days", "idx": 1, "shift_start_date": "2001-03-01", "tot_cum_ts_survey": 1800, "tot_cum_dozing_survey": 150},
		{"name": "MPP-T1-2", "parent": "MPP-T1", "parenttype": svc.MPP, "parentfield": "month_prod_days", "idx": 2, "shift_start_date": "2001-03-02", "tot_cum_ts_survey": 0, "tot_cum_dozing_survey": 0},
	])
	# engine hours (drafts count): EX-T1 20 h on the 1st, its 2nd has a closing reading of 0; HT-T1 has no night
	# sheet on the 1st and 12 h on the 2nd; DZ-T1 a day of 30 h (left out)
	raw("Pre-Use Hours", [
		{"name": "PU-T1", "location": LOC, "shift_date": "2001-03-01", "shift": "Day", "docstatus": 0},
		{"name": "PU-T2", "location": LOC, "shift_date": "2001-03-01", "shift": "Night", "docstatus": 0},
		{"name": "PU-T3", "location": LOC, "shift_date": "2001-03-02", "shift": "Day", "docstatus": 0},
		{"name": "PU-T4", "location": LOC, "shift_date": "2001-03-02", "shift": "Night", "docstatus": 0},
	])
	pu = lambda parent, i, asset, start, end: {"name": f"{parent}-{i}", "parent": parent, "parenttype": "Pre-Use Hours", "parentfield": "pre_use_assets", "idx": i, "asset_name": asset, "eng_hrs_start": start, "eng_hrs_end": end}
	raw("Pre-use Assets", [
		pu("PU-T1", 1, "EX-T1", 1000, 1010), pu("PU-T2", 1, "EX-T1", 1010, 1020),
		pu("PU-T3", 1, "EX-T1", 1020, 1031), pu("PU-T4", 1, "EX-T1", 1031, 0),
		pu("PU-T1", 2, "HT-T1", 480, 490),
		pu("PU-T3", 2, "HT-T1", 500, 506), pu("PU-T4", 2, "HT-T1", 506, 512),
		pu("PU-T1", 3, "DZ-T1", 100, 115), pu("PU-T2", 3, "DZ-T1", 115, 130),
	])
	# the excavator's OEM rate: 70 L/h from January; 80 from April, after the period
	raw("OEM Diesel Burn Rate", [
		{"name": "OEM-T1", "model": "EX-MODEL-T", "burn_rate_lph": 70, "effective_from": "2001-01-01"},
		{"name": "OEM-T2", "model": "EX-MODEL-T", "burn_rate_lph": 80, "effective_from": "2001-04-01"},
	])


class TestDieselTracker(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		for email, role in ((USER, "Production User"), (MANAGER, "Production Manager")):
			if not frappe.db.exists("User", email):
				frappe.get_doc({"doctype": "User", "email": email, "first_name": "Diesel", "send_welcome_email": 0, "roles": [{"role": role}]}).insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")
		clear()
		sources()

	def tearDown(self):
		frappe.set_user("Administrator")

	def entries(self):
		rows = frappe.get_all(
			svc.ENTRY, filters={"source": svc.AUTO}, fields=["name", "entry_date", "equipment", "diesel_litres", "coal_bcm", "coal_tons", "waste_bcm", "volume_counted"]
		)
		names = dict(frappe.get_all(svc.EQUIP, fields=["name", "equipment_name"], as_list=True))
		return {(str(r.entry_date), names[r.equipment]): r for r in rows}

	def test_sync(self):
		typed = frappe.get_doc({"doctype": svc.SITE, "site_name": LOC}).insert().name  # a site of the Location's name takes the link
		r = svc.sync("2001-03-01", "2001-03-02")
		self.assertEqual((r["added"], r["updated"], r["removed"], r["sites"], r["machines"]), (6, 0, 0, 0, 4))
		self.assertEqual(frappe.db.get_value(svc.SITE, typed, "location"), LOC)
		machines = {q.equipment_name: q for q in frappe.get_all(svc.EQUIP, fields=["equipment_name", "equipment_type", "asset", "site"])}
		self.assertEqual({n: (q.equipment_type, q.asset) for n, q in machines.items()},
			{"EX-T1": ("Excavator", "EX-T1"), "HT-T1": ("Haul truck", "HT-T1"), "DZ-T1": ("Dozer", "DZ-T1"), "LV-T1": ("Light vehicle", "LV-T1")})
		e = self.entries()
		# the excavator: its diesel (not the cancelled sheet's) and the loads it filled, counted; coal t = bcm × 1.5
		ex1 = e[("2001-03-01", "EX-T1")]
		self.assertEqual((ex1.diesel_litres, ex1.waste_bcm, ex1.coal_bcm, ex1.coal_tons, ex1.volume_counted), (800, 1200, 300, 450, 1))
		self.assertEqual(e[("2001-03-02", "EX-T1")].diesel_litres, 700)
		# the truck: the same loads, its own, not counted again
		ht1 = e[("2001-03-01", "HT-T1")]
		self.assertEqual((ht1.diesel_litres, ht1.waste_bcm, ht1.coal_bcm, ht1.volume_counted), (300, 1200, 300, 0))
		self.assertEqual((e[("2001-03-02", "HT-T1")].diesel_litres, e[("2001-03-02", "HT-T1")].waste_bcm), (0, 900))
		# the dozer's production dozing; the light vehicle's diesel only
		self.assertEqual((e[("2001-03-01", "DZ-T1")].waste_bcm, e[("2001-03-01", "DZ-T1")].volume_counted), (150, 1))
		self.assertEqual((e[("2001-03-01", "LV-T1")].diesel_litres, e[("2001-03-01", "LV-T1")].waste_bcm), (60, 0))
		data = svc.get_data(view=MARCH)
		self.assertEqual(sum(s["wasteCounted"] for s in data["summary"]), 1200 + 900 + 150)
		self.assertEqual(sum(s["diesel"] for s in data["summary"]), 800 + 700 + 300 + 400 + 60)
		self.assertEqual(data["entryCount"], 6)
		self.assertTrue(all(x["auto"] for x in data["entries"]))
		# again: nothing changes
		r = svc.sync("2001-03-01", "2001-03-02")
		self.assertEqual((r["added"], r["updated"], r["removed"]), (0, 0, 0))
		# a sheet corrected, one cancelled: updated and removed (the light vehicle had diesel only)
		frappe.db.set_value("Daily Diesel Entries", "DDS-T1-1", "litres_issued", 850)
		frappe.db.set_value("Daily Diesel Sheet", "DDS-T1", "docstatus", 2)
		frappe.db.set_value("Daily Diesel Entries", "DDS-T2-1", "litres_issued", 720)
		r = svc.sync("2001-03-01", "2001-03-02")
		e = self.entries()
		self.assertEqual((e[("2001-03-01", "EX-T1")].diesel_litres, e[("2001-03-02", "EX-T1")].diesel_litres), (0, 720))
		self.assertNotIn(("2001-03-01", "LV-T1"), e)
		self.assertEqual((r["updated"], r["removed"]), (4, 1))
		# only the days given are touched
		frappe.db.set_value("Daily Diesel Entries", "DDS-T2-1", "litres_issued", 10)
		svc.sync("2001-03-01", "2001-03-01")
		self.assertEqual(self.entries()[("2001-03-02", "EX-T1")].diesel_litres, 720)
		# the hourly job: everything the first time
		clear()
		svc.scheduled_sync()
		self.assertTrue(frappe.db.exists(svc.ENTRY, {"source": svc.AUTO, "entry_date": "2001-03-01"}))
		self.assertTrue(svc.last_sync()["at"])

	def test_actual_volume(self):
		svc.sync("2001-03-01", "2001-03-02")
		# the tallies: 1 650 bcm on the 1st (1 200 waste + 300 coal loads, 150 dozed), 900 on the 2nd; the survey
		# on the 1st adds the plan's 300 there; coal: 300 bcm tallied (450 t), 600 t actual
		a = svc.get_data(view=MARCH)["actual"]
		self.assertEqual((a["tallied"], a["bcm"], a["coalTallied"], a["coalT"], a["wasteBcm"], a["months"], a["surveyed"]), (2550, 2850, 450, 600, 2450, 1, 1))
		first = svc.get_data(view={"from": "2001-03-01", "to": "2001-03-01"})["actual"]
		second = svc.get_data(view={"from": "2001-03-02", "to": "2001-03-02"})["actual"]
		self.assertEqual((first["bcm"], first["coalT"]), (1950, 600))
		self.assertEqual((second["bcm"], second["coalT"]), (900, 0))  # (after the survey: as tallied)
		# a day in no production month: as tallied
		frappe.db.set_value(svc.MPP, "MPP-T1", "prod_month_end_date", "2001-03-01")
		frappe.db.set_value("Hourly Production", "HP-T2", "month_prod_planning", None)
		a = svc.get_data(view=MARCH)["actual"]
		self.assertEqual((a["bcm"], a["months"]), (2850, 1))
		self.assertEqual(svc.get_data(view={"from": "2001-03-02", "to": "2001-03-02"})["actual"]["months"], 0)
		# another site's view: none of it
		other = frappe.get_doc({"doctype": svc.SITE, "site_name": "Elsewhere"}).insert().name
		self.assertEqual(svc.get_data(view=dict(MARCH, site=other))["actual"]["bcm"], 0)

	def test_burn_rates(self):
		svc.sync("2001-03-01", "2001-03-02")
		data = svc.get_data(view=MARCH)
		self.assertEqual([f["key"] for f in data["burn"]], ["adt", "excavator", "dozer"])
		burn = {f["key"]: f for f in data["burn"]}
		ex = burn["excavator"]["machines"][0]
		self.assertEqual((ex["litres"], ex["hours"], ex["days"], ex["lph"], ex["oem"], ex["model"]), (1500, 20, 1, 75, 70, "EX-MODEL-T"))
		self.assertEqual((burn["excavator"]["lph"], burn["excavator"]["oem"], burn["excavator"]["oemShare"]), (75, 70, 1))
		ht = burn["adt"]["machines"][0]
		self.assertEqual((ht["litres"], ht["hours"], ht["lph"], ht["oem"]), (300, 12, 25, None))
		dz = burn["dozer"]
		self.assertEqual((dz["machines"][0]["litres"], dz["machines"][0]["hours"], dz["machines"][0]["lph"], dz["lph"], dz["noHours"]), (400, 0, None, None, 1))
		# the OEM rate in force at the end of the period: April's for a period to April
		april = {f["key"]: f for f in svc.get_data(view={"from": "2001-03-01", "to": "2001-04-30"})["burn"]}
		self.assertEqual(april["excavator"]["machines"][0]["oem"], 80)

	def test_settings(self):
		data = svc.save_settings(green=0.7, amber=1.1, max=2.5)["data"]
		self.assertEqual(data["settings"], {"green": 0.7, "amber": 1.1, "max": 2.5, "density": 1.5})
		self.assertRaises(frappe.ValidationError, svc.save_settings, green=1.2, amber=1.2, max=2)
		self.assertRaises(frappe.ValidationError, svc.save_settings, green=0.8, amber=1.2, max=1.2)

	def test_permissions(self):
		svc.sync("2001-03-01", "2001-03-02")
		# a Production User reads; nothing more
		frappe.set_user(USER)
		self.assertEqual(svc.get_data(view=MARCH)["can"], {"settings": False, "sync": False})
		self.assertRaises(frappe.PermissionError, svc.save_settings, green=0.8, amber=1.2, max=2)
		self.assertRaises(frappe.PermissionError, svc.sync_now, from_date="2001-03-01", to_date="2001-03-02")
		# a Production Manager sets the gauge and brings the records in, but types nothing: sites, machines and
		# entries come from the production records only
		frappe.set_user(MANAGER)
		self.assertEqual(svc.get_data(view=MARCH)["can"], {"settings": True, "sync": True})
		self.assertEqual(svc.sync_now(from_date="2001-03-01", to_date="2001-03-02")["sync"]["added"], 0)
		svc.save_settings(green=0.9, amber=1.3, max=2)
		for doc in ({"doctype": svc.SITE, "site_name": "Typed"}, {"doctype": svc.ENTRY, "entry_date": "2001-03-03"}):
			self.assertRaises(frappe.PermissionError, frappe.get_doc(doc).insert)
		for gone in ("add_site", "add_equipment", "add_entry", "delete", "load_example", "clear_example"):
			self.assertFalse(hasattr(svc, gone), gone)
