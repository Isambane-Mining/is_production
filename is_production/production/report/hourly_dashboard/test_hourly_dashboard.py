"""Focused tests: run with the bench Python via unittest, without a site database."""
import sqlite3
import unittest
from datetime import date, datetime
from unittest.mock import patch

import frappe
from is_production.production.report.hourly_dashboard import hourly_dashboard as dashboard


class TestHourlyDashboard(unittest.TestCase):
    def test_operational_day_boundary(self):
        for timestamp, expected in [
            ("2026-09-30 10:00", date(2026, 9, 30)),
            ("2026-10-01 03:00", date(2026, 9, 30)),
            ("2026-10-01 05:59", date(2026, 9, 30)),
            ("2026-10-01 06:00", date(2026, 10, 1)),
        ]:
            with self.subTest(timestamp=timestamp), patch.object(
                dashboard, "now_datetime", return_value=datetime.fromisoformat(timestamp), create=True
            ):
                self.assertEqual(dashboard.get_operational_day(), expected)

    def test_selected_date_preserves_all_sites(self):
        plans = [frappe._dict(location=site, name=site) for site in
                 [*dashboard.SITE_ORDER, "New Site"]]
        def get_all(doctype, **kwargs):
            if doctype == "Monthly Production Planning":
                self.assertEqual(kwargs["filters"]["prod_month_start_date"], ["<=", date(2026, 9, 29)])
                self.assertNotIn("location", kwargs["filters"])
                return plans
            return [frappe._dict(name="EX-1", location="Klipfontein")]

        with patch.object(frappe, "get_all", side_effect=get_all), patch.object(
            dashboard, "get_all_hourly_data", return_value={"Klipfontein": {"EX-1": {"1": 230}}}
        ) as hourly:
            _, rows = dashboard.execute({"production_date": "2026-09-29"})
        hourly.assert_called_once_with(date(2026, 9, 29))
        self.assertEqual([r["site"] for r in rows], [*dashboard.SITE_ORDER, "New Site"])
        self.assertEqual(rows[0]["slot_01"], 230)
        self.assertEqual(rows[1]["is_empty_site"], 1)
        self.assertTrue(all(r["production_day"] == date(2026, 9, 29) for r in rows))

    def test_default_date_and_zero_production_excavator(self):
        with patch.object(dashboard, "now_datetime", return_value=datetime(2026, 10, 1, 3)), patch.object(
            frappe, "get_all", side_effect=[
                [frappe._dict(location="Gwab", name="PLAN")],
                [frappe._dict(location="Gwab", name="EX-2")],
            ]
        ), patch.object(dashboard, "get_all_hourly_data", return_value={}):
            _, rows = dashboard.execute()
        self.assertEqual(rows[0]["production_day"], date(2026, 9, 30))
        self.assertEqual(rows[0]["excavator"], "EX-2")
        self.assertEqual(rows[0]["is_empty_site"], 0)
        self.assertTrue(all(rows[0][f"slot_{slot:02d}"] == 0 for slot in range(1, 25)))

    def test_latest_plan_per_site_with_stable_tie_break(self):
        plans = [frappe._dict(name=name, location=site) for name, site in
                 [("PLAN-Z", "Koppie"), ("PLAN-A", "Koppie"), ("PLAN-B", "Gwab")]]
        with patch.object(frappe, "get_all", return_value=plans) as get_all:
            resolved = dashboard.get_active_planning_sites(date(2026, 9, 30))
        self.assertIsInstance(resolved, dict)
        self.assertEqual(resolved["Koppie"].name, "PLAN-Z")
        self.assertEqual(resolved["Gwab"].name, "PLAN-B")
        self.assertEqual(get_all.call_args.kwargs["order_by"], "modified desc, name desc")
        self.assertEqual(get_all.call_args.kwargs["filters"]["docstatus"], ["<", 2])

    def test_actual_sql_sums_loads_by_date_site_excavator_and_hour(self):
        # Execute the report's real aggregate query over small relational fixtures.
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute('CREATE TABLE "tabHourly Production" (name, location, prod_date, hour_slot)')
        db.execute('CREATE TABLE "tabTruck Loads" (parent, asset_name_shoval, bcms)')
        db.executemany('INSERT INTO "tabHourly Production" VALUES (?, ?, ?, ?)', [
            ("a", "Koppie", "2026-09-29", "6:00-7:00"),
            ("b", "Gwab", "2026-09-29", "6:00-7:00"),
            ("c", "Koppie", "2026-09-30", "6:00-7:00"),
            ("d", "Koppie", "2026-09-29", "5:00-6:00"),
            ("e", "Koppie", "2026-09-29", "unknown"),
        ])
        db.executemany('INSERT INTO "tabTruck Loads" VALUES (?, ?, ?)', [
            ("a", "EX-1", 100), ("a", "EX-1", 130), ("a", "EX-2", 50),
            ("b", "EX-1", 70), ("c", "EX-1", 900), ("d", "EX-1", 40),
            ("e", "EX-1", 999),
        ])
        def sql(query, prod_date, **kwargs):
            return [frappe._dict(dict(row)) for row in db.execute(query.replace("%s", "?"), (str(prod_date),))]
        with patch.object(frappe.local, "db", create=True) as frappe_db:
            frappe_db.sql.side_effect = sql
            result = dashboard.get_all_hourly_data(date(2026, 9, 29))
        self.assertEqual(result, {"Koppie": {"EX-1": {"1": 230, "24": 40}, "EX-2": {"1": 50}},
                                  "Gwab": {"EX-1": {"1": 70}}})


if __name__ == "__main__":
    unittest.main()
