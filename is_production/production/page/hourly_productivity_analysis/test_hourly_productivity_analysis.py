from datetime import datetime
from unittest import TestCase
from unittest.mock import patch

from is_production.production.page.hourly_productivity_analysis import hourly_productivity_analysis as report
from is_production.production.page.hourly_productivity_summary import test_hourly_productivity_summary as fixtures


class TestHourlyProductivityAnalysis(TestCase):
    def setUp(self):
        self.f = fixtures.TestHourlyProductivitySummary()
        self.f.setUp()
        self.plan = dict(self.f.plan, location="Koppie", prod_month_start_date="2026-10-01",
                         prod_month_end_date="2026-10-31", modified="2026-10-01 00:00:00")

    def build(self, docs=(), plans=None, pbm=(), start="2026-10-12", end="2026-10-12", now=None):
        return report.build_analysis(start, end, "Koppie", [self.plan] if plans is None else plans,
                                     self.f.assets, pbm, docs, now=now or datetime(2026, 11, 2))

    def test_operational_day_has_24_hours_and_next_date_midnight(self):
        docs = [self.f.doc("00:00-01:00", "2026-10-13", [self.f.row(bcm=50)]),
                self.f.doc("00:00-01:00", "2026-10-12", [self.f.row(bcm=999)]),
                self.f.doc("06:00-07:00", "2026-10-13", [self.f.row(bcm=999)])]
        data = self.build(docs)
        cells = data["rows"][0]["cells"]
        self.assertEqual(len(cells), 24)
        self.assertEqual(cells[18]["actual_bcm"], 50)
        self.assertEqual(sum(c["actual_bcm"] for c in cells), 50)
        self.assertEqual([cells[i]["target_bcm"] for i in (0, 11, 12, 23)], [110, 330, 110, 330])

    def test_colour_thresholds_bright_red_precedence_and_missing_status(self):
        docs = [self.f.doc("06:00-07:00", rows=[self.f.row(bcm=110)]),
                self.f.doc("07:00-08:00", rows=[self.f.row(bcm=374)]),
                self.f.doc("08:00-09:00", rows=[self.f.row(bcm=373)]), self.f.doc("09:00-10:00")]
        data = self.build(docs)
        cells = data["rows"][0]["cells"]
        self.assertEqual([c["category"] for c in cells[:5]], ["green", "amber", "red", "bright_red", "bright_red"])
        self.assertTrue(cells[3]["has_record"])
        self.assertFalse(cells[4]["has_record"])
        self.assertEqual(data["pie"]["total_hours"], 24)
        self.assertEqual(data["pie"]["categories"]["green"]["hours"], 1)
        self.assertAlmostEqual(data["pie"]["categories"]["green"]["percent"], 100 / 24)
        self.assertAlmostEqual(sum(v["percent"] for v in data["pie"]["categories"].values()), 100)

    def test_zero_during_full_excavator_pbm_is_ordinary_red_and_target_fixed(self):
        pbm = [{"asset_name": exc, "breakdown_start_datetime": "2026-10-12 06:00:00",
                "resolved_datetime": "2026-10-12 07:00:00"} for exc in ("EX1", "EX2")]
        cell = self.build([self.f.doc()], pbm=pbm)["rows"][0]["cells"][0]
        self.assertEqual(cell["category"], "red")
        self.assertEqual(cell["target_bcm"], 110)

    def test_unknown_production_identities_cannot_support_bright_red(self):
        self.plan["num_excavators"] = 3
        data = self.build([self.f.doc()])
        cell = data["rows"][0]["cells"][0]
        self.assertEqual(cell["target_bcm"], 165)
        self.assertIsNone(cell["excavator_available"])
        self.assertEqual(cell["category"], "red")
        self.assertTrue(any("browser" in warning for warning in data["warnings"]))

    def test_zero_mpp_count_is_grey_not_missing_mpp(self):
        self.plan["num_excavators"] = 0
        self.plan["excavator_truck_assignments"] = [{"excavator": "SPARE", "truck": None}]
        data = self.build()
        self.assertEqual(data["pie"]["categories"]["grey"]["hours"], 24)
        self.assertEqual(data["pie"]["categories"]["grey"]["percent"], 100)
        with self.assertRaises(ValueError):
            self.build(plans=[])

    def test_month_boundary_uses_each_operational_dates_plan(self):
        september = dict(self.plan, name="SEP", num_excavators=1, prod_month_start_date="2026-09-01",
                         prod_month_end_date="2026-09-30",
                         excavator_truck_assignments=[{"excavator": "EX1", "truck": "T1"}])
        data = self.build(plans=[september, self.plan], start="2026-09-30", end="2026-10-01")
        self.assertEqual([r["plan_name"] for r in data["rows"]], ["SEP", "MPP"])
        self.assertEqual([r["cells"][0]["target_bcm"] for r in data["rows"]], [55, 110])
        self.assertEqual(data["rows"][0]["cells"][18]["target_bcm"], 220)
        self.assertEqual(data["pie"]["total_hours"], 48)

    def test_latest_covering_plan_draft_allowed_cancelled_and_other_site_excluded(self):
        newer = dict(self.plan, name="NEW", modified="2026-10-02", num_excavators=1,
                     excavator_truck_assignments=[{"excavator": "EX1", "truck": "T1"}])
        plans = [self.plan, newer, dict(newer, name="CANCELLED", modified="2026-10-03", docstatus=2),
                 dict(newer, name="OTHER", modified="2026-10-04", location="Gwab")]
        self.assertEqual(self.build(plans=plans)["rows"][0]["plan_name"], "NEW")

    def test_record_dedup_spare_bcm_included_dozer_excluded(self):
        doc = self.f.doc(rows=[self.f.row("SPARE", "T1", bcm=110, name="LOAD1")])
        doc["dozer_production"] = [{"bcm_hour": 999}]
        data = self.build([doc, dict(doc)])
        self.assertEqual(data["rows"][0]["cells"][0]["actual_bcm"], 110)
        self.assertEqual(data["rows"][0]["cells"][0]["category"], "green")

    def test_pending_hours_are_not_claimed_zero_with_verified_availability(self):
        data = self.build(now=datetime(2026, 10, 12, 7))
        self.assertEqual(data["rows"][0]["cells"][2]["category"], "red")
        self.assertEqual(data["rows"][0]["cells"][2]["record_status"], "pending")
        self.assertEqual(data["pie"]["total_hours"], 24)
        self.assertTrue(data["warnings"])

    def test_invalid_or_excessive_range_rejected(self):
        for start, end in [("2026-10-13", "2026-10-12"), ("2025-01-01", "2026-10-12")]:
            with self.assertRaises(ValueError):
                self.build(start=start, end=end)

    def test_guest_rejected_before_loading(self):
        with patch.object(report.frappe, "session", report.frappe._dict(user="Guest")):
            with self.assertRaises(report.frappe.PermissionError):
                report.get_analysis("Koppie", "2026-10-12", "2026-10-12")

    def test_analysis_endpoint_loads_through_next_morning_and_respects_denial(self):
        def load(site, start, end):
            self.assertEqual(site, "Koppie")
            self.assertEqual(start, datetime(2026, 9, 30, 6))
            self.assertEqual(end, datetime(2026, 10, 2, 6))
            raise report.frappe.PermissionError("restricted source")
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")), \
                patch.object(report.summary, "load_sources", side_effect=load):
            with self.assertRaises(report.frappe.PermissionError):
                report.get_analysis("Koppie", "2026-09-30", "2026-10-01")

    def test_defaults_stop_at_last_completed_operational_day(self):
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")):
            for now, expected in [(datetime(2026, 10, 13, 2), "2026-10-11"),
                                  (datetime(2026, 10, 13, 6), "2026-10-12")]:
                with patch.object(report, "now_datetime", return_value=now):
                    self.assertEqual(report.get_defaults()["to_date"], expected)

    def test_shift_load_times_keep_day_and_night_restarts_separate(self):
        docs = [self.f.doc(rows=[self.f.row(exc_start_load_time="06:15", exc_end_load_time="10:20")]),
                self.f.doc("16:00-17:00", rows=[self.f.row(exc_start_load_time="11:10", exc_end_load_time="17:45")]),
                self.f.doc("18:00-19:00", rows=[self.f.row(exc_start_load_time="18:20", exc_end_load_time="23:45")]),
                self.f.doc("00:00-01:00", "2026-10-13", [self.f.row(exc_start_load_time="00:30", exc_end_load_time="05:50")])]
        loads = self.build(docs)["rows"][0]["load_times"]
        day = next(r for r in loads["Day"] if r["excavator"] == "EX1")
        night = next(r for r in loads["Night"] if r["excavator"] == "EX1")
        self.assertEqual((day["first_load_time"], day["last_load_time"]), ("06:15", "17:45"))
        self.assertEqual((night["first_load_time"], night["last_load_time"]), ("18:20", "05:50"))
