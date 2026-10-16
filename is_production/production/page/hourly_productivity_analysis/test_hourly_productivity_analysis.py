from datetime import datetime
from unittest import TestCase
from unittest.mock import Mock, patch

from is_production.production.page.hourly_productivity_analysis import hourly_productivity_analysis as report
from is_production.production.page.hourly_productivity_summary import test_hourly_productivity_summary as fixtures


class TestHourlyProductivityAnalysis(TestCase):
    def setUp(self):
        self.f = fixtures.TestHourlyProductivitySummary()
        self.f.setUp()
        self.plan = dict(self.f.plan, location="Koppie", prod_month_start_date="2026-10-01",
                         prod_month_end_date="2026-10-31", modified="2026-10-01 00:00:00")

    def build(self, docs=(), plans=None, pbm=(), start="2026-10-12", end="2026-10-12", now=None, fatigue=()):
        return report.build_analysis(start, end, "Koppie", [self.plan] if plans is None else plans,
                                     self.f.assets, pbm, docs, now=now or datetime(2026, 11, 2), fatigue=fatigue)

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

    def test_zero_machine_count_does_not_prove_non_working_hours(self):
        self.plan["num_excavators"] = 0
        self.plan["excavator_truck_assignments"] = [{"excavator": "SPARE", "truck": None}]
        data = self.build()
        self.assertEqual(data["pie"]["categories"]["grey"]["hours"], 0)
        self.assertEqual(data["rows"][0]["cells"][0]["category"], "red")

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
        def load(site, start, end, *, plans):
            self.assertEqual(site, "Koppie")
            self.assertEqual(start, datetime(2026, 9, 30, 6))
            self.assertEqual(end, datetime(2026, 10, 2, 6))
            raise report.frappe.PermissionError("restricted source")
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")), \
                patch.object(report.summary, "load_sources", side_effect=load), \
                patch.object(report, "load_plans", return_value=[dict(self.plan, prod_month_start_date="2026-09-01")]):
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

    def test_missing_plans_report_all_months_without_results_or_traceback(self):
        data = self.build(plans=[], start="2026-09-01", end="2026-10-02")
        self.assertEqual(data["rows"], [])
        self.assertNotIn("pie", data)
        error = data["planning_error"]
        self.assertEqual(error["title"], "Monthly Production Planning Not Found")
        self.assertEqual([r["month"] for r in error["issues"]], ["September 2026", "October 2026"])
        self.assertIn("permission", error["issues"][0]["message"])

    def test_explicit_mpp_zero_hours_controls_shift_independently_of_weekday(self):
        self.plan["month_prod_days"] = [
            {"shift_start_date": "2026-10-11", "shift_day_hours": 12, "shift_night_hours": 0},
            {"shift_start_date": "2026-10-12", "shift_day_hours": 0, "shift_night_hours": 12}]
        data = self.build(start="2026-10-11", end="2026-10-12")
        sunday, monday = data["rows"]
        self.assertEqual(sunday["cells"][0]["category"], "bright_red")
        self.assertEqual(sunday["cells"][12]["category"], "grey")
        self.assertEqual(monday["cells"][0]["category"], "grey")
        self.assertEqual(monday["cells"][12]["category"], "bright_red")
        self.assertEqual(data["pie"]["categories"]["grey"]["hours"], 24)
        self.assertEqual(monday["cells"][0]["target_bcm"], 110)

    def test_partial_fatigue_preserves_bcm_target_and_weights_pie(self):
        fatigue = [{"site": "Koppie", "shift": "Day", "day_type": "Weekday",
                    "fatigue_start": "13:00", "fatigue_end": "13:30"}]
        data = self.build([self.f.doc("13:00-14:00", rows=[self.f.row(bcm=440)])], fatigue=fatigue)
        cell = data["rows"][0]["cells"][7]
        self.assertEqual(cell["actual_bcm"], 440)
        self.assertEqual(cell["target_bcm"], 440)
        self.assertEqual(cell["non_working_minutes"], 30)
        self.assertEqual(cell["segments"], [
            {"start_percent": 0, "end_percent": 50, "category": "grey"},
            {"start_percent": 50, "end_percent": 100, "category": "green"}])
        self.assertEqual(data["pie"]["categories"]["grey"]["hours"], .5)
        self.assertEqual(data["pie"]["categories"]["green"]["hours"], .5)
        self.assertEqual(sum(v["hours"] for v in data["pie"]["categories"].values()), 24)

    def test_fatigue_crosses_midnight_and_uses_shift_start_day_type(self):
        fatigue = [{"site": "Koppie", "shift": "Night", "day_type": "Weekday",
                    "fatigue_start": "23:45", "fatigue_end": "00:15"},
                   {"site": "Other", "shift": "Day", "day_type": "Weekday",
                    "fatigue_start": "06:00", "fatigue_end": "18:00"}]
        data = self.build(fatigue=fatigue)
        cells = data["rows"][0]["cells"]
        self.assertEqual(cells[17]["non_working_minutes"], 15)
        self.assertEqual(cells[18]["non_working_minutes"], 15)
        self.assertEqual(cells[0]["non_working_minutes"], 0)

    def test_non_working_shift_and_fatigue_do_not_double_count(self):
        self.plan["month_prod_days"] = [{"shift_start_date": "2026-10-12", "shift_day_hours": 0,
                                        "shift_night_hours": 12}]
        data = self.build(fatigue=[{"site": "Koppie", "shift": "Day", "day_type": "Weekday",
                                   "fatigue_start": "13:00", "fatigue_end": "13:30"}])
        self.assertEqual(data["pie"]["categories"]["grey"]["hours"], 12)

    def test_missing_daily_hours_are_not_assumed_zero(self):
        self.plan["month_prod_days"] = [{"shift_start_date": "2026-10-12", "shift_day_hours": None}]
        data = self.build()
        self.assertEqual(data["pie"]["categories"]["grey"]["hours"], 0)
        self.assertTrue(any("working hours" in w for w in data["warnings"]))

    def test_endpoint_missing_plans_returns_message_before_other_sources(self):
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")), \
                patch.object(report, "load_plans", return_value=[]):
            data = report.get_analysis("Koppie", "2026-09-01", "2026-10-02")
        self.assertEqual(len(data["planning_error"]["issues"]), 2)
        self.assertNotIn("pie", data)

    def test_endpoint_known_mpp_permission_denial_has_no_record_details(self):
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")), \
                patch.object(report, "load_plans", return_value=None):
            data = report.get_analysis("Koppie", "2026-09-01", "2026-10-02")
        self.assertEqual(data["planning_error"]["title"], "Monthly Production Planning Access Denied")
        self.assertEqual([i["status"] for i in data["planning_error"]["issues"]],
                         ["permission_denied", "permission_denied"])
        self.assertNotIn("MPP-", str(data))

    def test_endpoint_fatigue_permission_denial_does_not_publish_misleading_chart(self):
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")), \
                patch.object(report, "load_plans", return_value=[self.plan]), \
                patch.object(report.summary, "load_sources", return_value=([], [], [], [])), \
                patch.object(report, "load_fatigue", side_effect=report.frappe.PermissionError):
            data = report.get_analysis("Koppie", "2026-10-12", "2026-10-12")
        self.assertIn("configuration_error", data)
        self.assertNotIn("pie", data)

    def test_month_boundary_uses_daily_hours_from_each_plan(self):
        september = dict(self.plan, name="SEP", prod_month_start_date="2026-09-01",
                         prod_month_end_date="2026-09-30", month_prod_days=[
                             {"shift_start_date": "2026-09-30", "shift_day_hours": 0, "shift_night_hours": 12}])
        self.plan["month_prod_days"] = [{"shift_start_date": "2026-10-01", "shift_day_hours": 12,
                                        "shift_night_hours": 0}]
        data = self.build(plans=[september, self.plan], start="2026-09-30", end="2026-10-01")
        self.assertEqual([r["cells"][0]["non_working_minutes"] for r in data["rows"]], [60, 0])
        self.assertEqual([r["cells"][12]["non_working_minutes"] for r in data["rows"]], [0, 60])

    def test_fatigue_configuration_is_specific_to_day_type_and_shift(self):
        configs = [{"site": "Koppie", "shift": "Day", "day_type": kind,
                    "fatigue_start": start, "fatigue_end": end} for kind, start, end in
                   [("Weekday", "13:00", "13:30"), ("Sunday", "12:15", "12:45")]]
        cells = self.build(start="2026-10-11", end="2026-10-11", fatigue=configs)["rows"][0]["cells"]
        self.assertEqual(cells[6]["non_working_minutes"], 30)
        self.assertEqual(cells[7]["non_working_minutes"], 0)
        self.assertEqual(cells[18]["non_working_minutes"], 0)

    def test_different_day_type_does_not_supply_fatigue(self):
        fatigue = [{"site": "Koppie", "shift": "Day", "day_type": "Weekday",
                    "fatigue_start": "13:00", "fatigue_end": "14:00"}]
        data = self.build(start="2026-10-11", end="2026-10-11", fatigue=fatigue)
        self.assertEqual(data["pie"]["categories"]["grey"]["hours"], 0)
        self.assertTrue(any("Sunday" in warning for warning in data["warnings"]))

    def test_hidden_daily_hours_cannot_be_used_as_zero_schedule(self):
        # Frappe document loading/field stripping is external; exercise our permission gate.
        doc = Mock()
        doc.as_dict.side_effect = [
            {"month_prod_days": [{"shift_day_hours": 0}]}, {"month_prod_days": None}]
        with patch.object(report.frappe, "get_doc", return_value=doc):
            with self.assertRaises(report.frappe.PermissionError):
                report._schedule_document("Monthly Production Planning", "restricted", ("month_prod_days",))

    def test_site_access_denial_remains_denied_before_diagnostics(self):
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")), \
                patch.object(report.summary, "_read_document", side_effect=report.frappe.PermissionError):
            with self.assertRaises(report.frappe.PermissionError):
                report.get_analysis("Koppie", "2026-09-01", "2026-10-02")

    def test_endpoint_passes_loaded_fatigue_to_analysis(self):
        docs = [self.f.doc("13:00-14:00", rows=[self.f.row(bcm=440)])]
        fatigue = [{"site": "Koppie", "shift": "Day", "day_type": "Weekday",
                    "fatigue_start": "13:00", "fatigue_end": "13:30"}]
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")), \
                patch.object(report, "load_plans", return_value=[self.plan]), \
                patch.object(report.summary, "load_sources", return_value=([], docs, self.f.assets, [])), \
                patch.object(report, "load_fatigue", return_value=fatigue), \
                patch.object(report, "now_datetime", return_value=datetime(2026, 11, 2)):
            data = report.get_analysis("Koppie", "2026-10-12", "2026-10-12")
        self.assertEqual(data["rows"][0]["cells"][7]["non_working_minutes"], 30)
        self.assertEqual(data["pie"]["categories"]["grey"]["hours"], .5)

    def test_inaccessible_plan_does_not_hide_other_readable_months(self):
        september = dict(self.plan, name="SEP", prod_month_start_date="2026-09-01",
                         prod_month_end_date="2026-09-30")
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")), \
                patch.object(report.summary, "_read_document", return_value={"name": "Koppie"}), \
                patch.object(report.frappe, "has_permission", return_value=True), \
                patch.object(report.summary, "_permitted_rows", return_value=[
                    report.frappe._dict(name="SEP"), report.frappe._dict(name="PRIVATE")]), \
                patch.object(report, "_schedule_document", side_effect=[september, report.frappe.PermissionError]):
            data = report.get_analysis("Koppie", "2026-09-01", "2026-10-02")
        self.assertEqual([i["month"] for i in data["planning_error"]["issues"]], ["October 2026"])
        self.assertNotIn("PRIVATE", str(data))
