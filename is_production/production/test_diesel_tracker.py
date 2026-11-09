# Copyright (c) 2026, Isambane Mining (Pty) Ltd and Contributors
# See license.txt

"""The Diesel L/BCM Tracker's service (is_production.production.diesel_tracker) and its doctypes."""

import frappe
from frappe.tests import IntegrationTestCase

from is_production.production import diesel_tracker as svc

USER = "diesel.tracker.user@example.com"


def clear():
	for dt in (svc.ENTRY, svc.EQUIP, svc.SITE):
		frappe.db.delete(dt)
	frappe.db.delete("Singles", {"doctype": svc.SETTINGS})


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
		out = svc.add_entry(date="2026-11-03", siteId=site, equipmentId=ht, diesel="2100", coal="1400", waste="")
		return site, ex, ht, out

	def test_add_and_read(self):
		site, ex, ht, out = self.add()
		data = out["data"]
		self.assertEqual([s["name"] for s in data["sites"]], ["Pit 3 East"])
		self.assertEqual({q["name"]: q["type"] for q in data["equipment"]}, {"EX01": "Excavator", "HT14": "Haul truck"})
		e = next(e for e in data["entries"] if e["equipmentId"] == ht)
		self.assertEqual((e["date"], e["siteId"], e["diesel"], e["coal"], e["waste"]), ("2026-11-03", site, 2100.0, 1400.0, 0.0))
		self.assertEqual(data["settings"], svc.DEFAULTS)
		self.assertTrue(all(data["can"].values()))
		self.assertEqual(svc.get_data()["entries"][0]["date"], "2026-11-03")  # the latest first

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
		data = svc.delete(kind="equipment", id=ht)["data"]
		self.assertEqual([q["id"] for q in data["equipment"]], [ex])
		self.assertEqual([e["equipmentId"] for e in data["entries"]], [ex])
		data = svc.delete(kind="sites", id=site)["data"]
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
		data = svc.load_example()["data"]
		self.assertEqual(len(data["sites"]), 2)
		self.assertEqual(len(data["equipment"]), 6)
		self.assertEqual(len(data["entries"]), 18 * 6)  # weekly over the last 17 weeks and this one
		self.assertTrue(all(e["example"] for e in data["entries"]))
		self.assertRaises(frappe.ValidationError, svc.load_example)
		# a real entry logged against an example machine keeps that machine and its site
		q = next(q for q in data["equipment"] if q["name"] == "EX01")
		svc.add_entry(date="2026-11-05", siteId=q["siteId"], equipmentId=q["id"], diesel=100, waste=200)
		out = svc.clear_example()
		self.assertEqual(sorted(out["kept"]), ["EX01", "North Pit (example)"])
		self.assertEqual([e["diesel"] for e in out["data"]["entries"]], [100.0])
		self.assertEqual([s["name"] for s in out["data"]["sites"]], ["North Pit (example)"])

	def test_production_user(self):
		site, ex, _ht, _out = self.add()
		frappe.set_user(USER)
		can = svc.get_data()["can"]
		self.assertEqual(can, {"entry": True, "deleteEntry": True, "setup": False, "deleteSetup": False, "settings": False})
		entry = svc.add_entry(date="2026-11-06", siteId=site, equipmentId=ex, diesel=50, waste=100)["id"]
		svc.delete(kind="entries", id=entry)
		self.assertRaises(frappe.PermissionError, svc.add_site, name="Pit 9")
		self.assertRaises(frappe.PermissionError, svc.add_equipment, siteId=site, name="HT99", type="Haul truck")
		self.assertRaises(frappe.PermissionError, svc.delete, kind="equipment", id=ex)
		self.assertRaises(frappe.PermissionError, svc.save_settings, density=1.4, green=0.8, amber=1.2, max=2)
		self.assertRaises(frappe.PermissionError, svc.load_example)
