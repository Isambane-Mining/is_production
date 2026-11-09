# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

"""The Diesel L/BCM Tracker's service (is_production.production.diesel_tracker) and its doctypes."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import now_datetime

from is_production.production import diesel_tracker as svc

USER = "diesel.tracker.user@example.com"
ALL = {"from": "", "to": "", "site": "all", "limit": 2000}
LOC = "DT Test Pit"  # (the source records of the sync tests, in 2001, away from real data)


def clear():
	for dt in (svc.ENTRY, svc.EQUIP, svc.SITE):
		frappe.db.delete(dt)
	frappe.db.delete("Singles", {"doctype": svc.SETTINGS})


def raw(doctype, rows):
	"""Source records written straight to their tables (they need no forms or other masters here)."""
	stamp = now_datetime()
	fields = sorted({k for r in rows for k in r} | {"creation", "modified", "owner", "modified_by"})
	values = [tuple({"creation": stamp, "modified": stamp, "owner": "Administrator", "modified_by": "Administrator", **r}.get(f) for f in fields) for r in rows]
	frappe.db.bulk_insert(doctype, fields, values)


def sources():
	"""Two days at LOC: EX-T1 fills HT-T1 (waste and coal), DZ-T1 dozes; diesel to all three and a light vehicle.
	(Written afresh each time: a test may have changed them.)"""
	sheets, hours = ["DDS-T1", "DDS-T2", "DDS-T3"], ["HP-T1", "HP-T2"]
	frappe.db.delete("Daily Diesel Entries", {"parent": ["in", sheets]})
	frappe.db.delete("Daily Diesel Sheet", {"name": ["in", sheets]})
	for child in ("Truck Loads", "Dozer Production"):
		frappe.db.delete(child, {"parent": ["in", hours]})
	frappe.db.delete("Hourly Production", {"name": ["in", hours]})
	if not frappe.db.exists("Location", LOC):
		raw("Location", [{"name": LOC, "location_name": LOC}])
	raw("Asset", [
		{"name": n, "asset_name": n, "asset_category": c, "docstatus": 1}
		for n, c in (("EX-T1", "Excavator"), ("HT-T1", "ADT"), ("DZ-T1", "Dozer"), ("LV-T1", "LDV"))
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
	raw("Hourly Production", [{"name": "HP-T1", "location": LOC, "prod_date": "2001-03-01"}, {"name": "HP-T2", "location": LOC, "prod_date": "2001-03-02"}])
	load = lambda parent, i, bcm, mat: {"name": f"{parent}-L{i}", "parent": parent, "parenttype": "Hourly Production", "parentfield": "truck_loads", "idx": i, "asset_name_shoval": "EX-T1", "asset_name_truck": "HT-T1", "bcms": bcm, "mat_type": mat}
	raw("Truck Loads", [load("HP-T1", 1, 1000, "Hards"), load("HP-T1", 2, 200, "Softs"), load("HP-T1", 3, 300, "Coal"), load("HP-T2", 1, 900, "Hards")])
	raw("Dozer Production", [
		{"name": "HP-T1-D1", "parent": "HP-T1", "parenttype": "Hourly Production", "parentfield": "dozer_production", "idx": 1, "asset_name": "DZ-T1", "bcm_hour": 150},
		{"name": "HP-T1-D2", "parent": "HP-T1", "parenttype": "Hourly Production", "parentfield": "dozer_production", "idx": 2, "asset_name": "DZ-T1", "bcm_hour": 0},
	])


class TestDieselTracker(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("User", USER):
			frappe.get_doc(
				{"doctype": "User", "email": USER, "first_name": "Diesel", "send_welcome_email": 0, "roles": [{"role": "Production User"}]}
			).insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")
		clear()

	def tearDown(self):
		frappe.set_user("Administrator")

	def add(self):
		site = svc.add_site(name="Pit 3 East")["id"]
		ex = svc.add_equipment(siteId=site, name="EX01", type="Excavator")["id"]
		ht = svc.add_equipment(siteId=site, name="HT14", type="Haul truck")["id"]
		svc.add_entry(date="2026-11-02", siteId=site, equipmentId=ex, diesel=4800, coal=0, waste=9600)
		out = svc.add_entry(date="2026-11-03", siteId=site, equipmentId=ht, diesel="2100", coal="1400", waste="", view=ALL)
		return site, ex, ht, out

	def test_add_and_read(self):
		site, ex, ht, out = self.add()
		data = out["data"]
		self.assertEqual([s["name"] for s in data["sites"]], ["Pit 3 East"])
		self.assertEqual({q["name"]: q["type"] for q in data["equipment"]}, {"EX01": "Excavator", "HT14": "Haul truck"})
		e = next(e for e in data["entries"] if e["equipmentId"] == ht)
		self.assertEqual((e["date"], e["siteId"], e["diesel"], e["coal"], e["waste"], e["counted"], e["auto"]), ("2026-11-03", site, 2100.0, 1400.0, 0.0, True, False))
		self.assertEqual(data["settings"], svc.DEFAULTS)
		self.assertTrue(all(data["can"].values()))
		self.assertEqual(data["entryCount"], 2)
		# each machine's sums over the period
		s = {r["equipmentId"]: r for r in data["summary"]}
		self.assertEqual((s[ex]["diesel"], s[ex]["waste"], s[ex]["wasteCounted"], s[ht]["coalCounted"]), (4800.0, 9600.0, 9600.0, 1400.0))
		# a period and a site
		nov3 = svc.get_data(view={"from": "2026-11-03", "to": "2026-11-30", "site": site})
		self.assertEqual([r["equipmentId"] for r in nov3["summary"]], [ht])
		self.assertEqual(svc.get_data(view={"latest": 1})["view"]["from"], "2026-11-01")
		self.assertEqual(svc.get_data(view={"site": "other"})["entryCount"], 0)

	def test_rules(self):
		site, ex, ht, _out = self.add()
		other = svc.add_site(name="North Pit")["id"]
		self.assertRaises(frappe.ValidationError, svc.add_site, name="pit 3 east")  # the same name, any case
		self.assertRaises(frappe.ValidationError, svc.add_site, name="  ")
		self.assertRaises(frappe.ValidationError, svc.add_equipment, siteId=site, name="ex01", type="Excavator")
		svc.add_equipment(siteId=other, name="EX01", type="Excavator")  # the same fleet number on another site
		self.assertRaises(frappe.ValidationError, svc.add_equipment, siteId=site, name="DZ1", type="Bulldozer")
		self.assertRaises(frappe.ValidationError, svc.add_entry, date="2026-11-04", siteId=other, equipmentId=ex, diesel=10)
		self.assertRaises(frappe.ValidationError, svc.add_entry, date="2026-11-04", siteId=site, equipmentId=ex, diesel=-1)
		self.assertRaises(frappe.ValidationError, svc.add_entry, date="2026-11-04", siteId=site, equipmentId=ex, diesel="")
		self.assertRaises(frappe.ValidationError, svc.add_entry, date="", siteId=site, equipmentId=ex, diesel=5)

	def test_delete_cascades(self):
		site, ex, ht, _out = self.add()
		data = svc.delete(kind="equipment", id=ht, view=ALL)["data"]
		self.assertEqual([q["id"] for q in data["equipment"]], [ex])
		self.assertEqual([e["equipmentId"] for e in data["entries"]], [ex])
		data = svc.delete(kind="sites", id=site, view=ALL)["data"]
		self.assertEqual((data["sites"], data["equipment"], data["entries"]), ([], [], []))
		self.assertTrue(frappe.db.exists("Deleted Document", {"deleted_doctype": svc.SITE, "deleted_name": site}))
		self.assertRaises(frappe.ValidationError, svc.delete, kind="sites", id=site)  # gone already
		self.assertRaises(frappe.ValidationError, svc.delete, kind="users", id="Administrator")

	def test_settings(self):
		data = svc.save_settings(density="1.45", green=0.7, amber=1.1, max=2.5)["data"]
		self.assertEqual(data["settings"], {"density": 1.45, "green": 0.7, "amber": 1.1, "max": 2.5})
		self.assertRaises(frappe.ValidationError, svc.save_settings, density=1.4, green=1.2, amber=1.2, max=2)
		self.assertRaises(frappe.ValidationError, svc.save_settings, density=1.4, green=0.8, amber=1.2, max=1.2)
		self.assertRaises(frappe.ValidationError, svc.save_settings, density=0, green=0.8, amber=1.2, max=2)
		self.assertEqual(svc.settings()["density"], 1.45)

	def test_example_data(self):
		data = svc.load_example(view=ALL)["data"]
		self.assertEqual(len(data["sites"]), 2)
		self.assertEqual(len(data["equipment"]), 6)
		self.assertEqual(data["entryCount"], 18 * 6)  # weekly over the last 17 weeks and this one
		self.assertTrue(all(e["example"] for e in data["entries"]))
		self.assertRaises(frappe.ValidationError, svc.load_example)
		# a real entry logged against an example machine keeps that machine and its site
		q = next(q for q in data["equipment"] if q["name"] == "EX01")
		svc.add_entry(date="2026-11-05", siteId=q["siteId"], equipmentId=q["id"], diesel=100, waste=200)
		out = svc.clear_example(view=ALL)
		self.assertEqual(sorted(out["kept"]), ["EX01", "North Pit (example)"])
		self.assertEqual([e["diesel"] for e in out["data"]["entries"]], [100.0])
		self.assertEqual([s["name"] for s in out["data"]["sites"]], ["North Pit (example)"])

	def test_production_user(self):
		site, ex, _ht, _out = self.add()
		frappe.set_user(USER)
		can = svc.get_data()["can"]
		self.assertEqual(can, {"entry": True, "deleteEntry": True, "setup": False, "deleteSetup": False, "settings": False, "sync": False})
		entry = svc.add_entry(date="2026-11-06", siteId=site, equipmentId=ex, diesel=50, waste=100)["id"]
		svc.delete(kind="entries", id=entry)
		self.assertRaises(frappe.PermissionError, svc.add_site, name="Pit 9")
		self.assertRaises(frappe.PermissionError, svc.add_equipment, siteId=site, name="HT99", type="Haul truck")
		self.assertRaises(frappe.PermissionError, svc.delete, kind="equipment", id=ex)
		self.assertRaises(frappe.PermissionError, svc.save_settings, density=1.4, green=0.8, amber=1.2, max=2)
		self.assertRaises(frappe.PermissionError, svc.load_example)
		self.assertRaises(frappe.PermissionError, svc.sync_now, from_date="2001-03-01", to_date="2001-03-02")

	# ---- the production records

	def entries(self):
		rows = frappe.get_all(
			svc.ENTRY, filters={"source": svc.AUTO}, fields=["name", "entry_date", "equipment", "diesel_litres", "coal_bcm", "coal_tons", "waste_bcm", "volume_counted"]
		)
		names = dict(frappe.get_all(svc.EQUIP, fields=["name", "equipment_name"], as_list=True))
		return {(str(r.entry_date), names[r.equipment]): r for r in rows}

	def test_sync(self):
		sources()
		manual_site = svc.add_site(name=LOC)["id"]  # a site typed with the Location's name takes the link
		r = svc.sync("2001-03-01", "2001-03-02")
		self.assertEqual((r["added"], r["updated"], r["removed"], r["sites"], r["machines"]), (6, 0, 0, 0, 4))
		self.assertEqual(frappe.db.get_value(svc.SITE, manual_site, "location"), LOC)
		machines = {q.equipment_name: q for q in frappe.get_all(svc.EQUIP, fields=["equipment_name", "equipment_type", "asset", "site"])}
		self.assertEqual({n: (q.equipment_type, q.asset) for n, q in machines.items()},
			{"EX-T1": ("Excavator", "EX-T1"), "HT-T1": ("Haul truck", "HT-T1"), "DZ-T1": ("Dozer", "DZ-T1"), "LV-T1": ("Light vehicle", "LV-T1")})
		e = self.entries()
		# the excavator: its diesel (not the cancelled sheet's) and the loads it filled, counted
		ex1 = e[("2001-03-01", "EX-T1")]
		self.assertEqual((ex1.diesel_litres, ex1.waste_bcm, ex1.coal_bcm, ex1.coal_tons, ex1.volume_counted), (800, 1200, 300, 420, 1))
		self.assertEqual(e[("2001-03-02", "EX-T1")].diesel_litres, 700)
		# the truck: the same loads, its own, not counted again
		ht1 = e[("2001-03-01", "HT-T1")]
		self.assertEqual((ht1.diesel_litres, ht1.waste_bcm, ht1.coal_bcm, ht1.volume_counted), (300, 1200, 300, 0))
		self.assertEqual((e[("2001-03-02", "HT-T1")].diesel_litres, e[("2001-03-02", "HT-T1")].waste_bcm), (0, 900))
		# the dozer's production dozing; the light vehicle's diesel only
		self.assertEqual((e[("2001-03-01", "DZ-T1")].waste_bcm, e[("2001-03-01", "DZ-T1")].volume_counted), (150, 1))
		self.assertEqual((e[("2001-03-01", "LV-T1")].diesel_litres, e[("2001-03-01", "LV-T1")].waste_bcm), (60, 0))
		# the page: each bcm once in the site's totals
		data = svc.get_data(view={"from": "2001-03-01", "to": "2001-03-02"})
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
		self.assertEqual((r["updated"], r["removed"]), (4, 1))  # EX, HT, DZ lose their diesel; EX on the 2nd; LV goes
		# only the days given are touched
		frappe.db.set_value("Daily Diesel Entries", "DDS-T2-1", "litres_issued", 10)
		svc.sync("2001-03-01", "2001-03-01")
		self.assertEqual(self.entries()[("2001-03-02", "EX-T1")].diesel_litres, 720)
		# the coal's tons follow the density
		svc.save_settings(density=1.5, green=0.8, amber=1.2, max=2)
		self.assertEqual(self.entries()[("2001-03-01", "EX-T1")].coal_tons, 450)

	def test_records_are_not_deleted_here(self):
		sources()
		svc.sync("2001-03-01", "2001-03-02")
		entry = frappe.get_all(svc.ENTRY, filters={"source": svc.AUTO}, pluck="name")[0]
		site = frappe.db.get_value(svc.SITE, {"location": LOC})
		machine = frappe.db.get_value(svc.EQUIP, {"asset": "EX-T1"})
		self.assertRaises(frappe.ValidationError, svc.delete, kind="entries", id=entry)
		self.assertRaises(frappe.ValidationError, svc.delete, kind="equipment", id=machine)
		self.assertRaises(frappe.ValidationError, svc.delete, kind="sites", id=site)
		s = next(x for x in svc.get_data(view=ALL)["sites"] if x["id"] == site)
		self.assertEqual((s["n"], s["nAuto"], s["location"]), (6, 6, LOC))
		# the hourly job: everything the first time, the last week after
		clear()
		svc.scheduled_sync()
		self.assertTrue(frappe.db.exists(svc.ENTRY, {"source": svc.AUTO, "entry_date": "2001-03-01"}))
		self.assertTrue(svc.last_sync()["at"])
