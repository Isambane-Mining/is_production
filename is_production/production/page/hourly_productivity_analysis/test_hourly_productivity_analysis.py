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
                         prod_month_end_date="2026-10-31", modified="2026-10-01 00:00:00",
                         shift_system="2x12Hour", month_prod_days=[dict(shift_start_date=f"2026-10-{d:02}",
                         shift_day_hours=12, shift_night_hours=12) for d in range(1, 32)])

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
        self.assertEqual([c["category"] for c in cells[:5]], ["green", "amber", "red", "bright_red", "unclassified"])
        self.assertTrue(cells[3]["has_record"])
        self.assertFalse(cells[4]["has_record"])
        self.assertEqual(data["pie"]["total_hours"], 4)
        self.assertEqual(data["pie"]["categories"]["green"]["hours"], 1)
        self.assertAlmostEqual(data["pie"]["categories"]["green"]["percent"], 25)
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

    def test_zero_count_does_not_prove_non_working_schedule(self):
        self.plan["num_excavators"] = 0
        self.plan["excavator_truck_assignments"] = [{"excavator": "SPARE", "truck": None}]
        data = self.build()
        self.assertEqual(data["pie"]["categories"]["grey"]["hours"], 0)
        self.assertEqual(data["excluded"]["no_target"], 24)
        with self.assertRaises(ValueError):
            self.build(plans=[])

    def test_month_boundary_uses_each_operational_dates_plan(self):
        september = dict(self.plan, name="SEP", num_excavators=1, prod_month_start_date="2026-09-01",
                         prod_month_end_date="2026-09-30", month_prod_days=[dict(shift_start_date="2026-09-30", shift_day_hours=12, shift_night_hours=12)],
                         excavator_truck_assignments=[{"excavator": "EX1", "truck": "T1"}])
        data = self.build(plans=[september, self.plan], start="2026-09-30", end="2026-10-01")
        self.assertEqual([r["plan_name"] for r in data["rows"]], ["SEP", "MPP"])
        self.assertEqual([r["cells"][0]["target_bcm"] for r in data["rows"]], [55, 110])
        self.assertEqual(data["rows"][0]["cells"][18]["target_bcm"], 220)
        self.assertEqual(data["pie"]["total_hours"], 0)

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
        self.assertEqual(data["rows"][0]["cells"][2]["category"], "unclassified")
        self.assertEqual(data["rows"][0]["cells"][2]["record_status"], "pending")
        self.assertEqual(data["pie"]["total_hours"], 0)
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

    def test_saved_site_schedule_zero_and_reduced_windows(self):
        self.plan['month_prod_days'][11].update(shift_day_hours=0, shift_night_hours=6)
        data = self.build([self.f.doc()])
        cells = data['rows'][0]['cells']
        self.assertTrue(all(c['category'] == 'grey' and c['target_bcm'] == 0 for c in cells[:12]))
        self.assertTrue(all(c['category'] == 'unclassified' and c['target_bcm'] is None for c in cells[12:]))
        self.assertEqual(data['pie']['total_hours'], 12)
        self.assertEqual(data['pie']['categories']['grey']['percent'], 100)
        self.assertEqual(data['excluded']['schedule_unknown'], 12)
        other = dict(self.plan, name='OTHER', location='Gwab', month_prod_days=[dict(shift_start_date='2026-10-12', shift_day_hours=12, shift_night_hours=12)])
        data = report.build_analysis('2026-10-12', '2026-10-12', 'Gwab', [self.plan, other], [], [], [], now=datetime(2026, 11, 2))
        self.assertTrue(all(c['schedule'] == 'working' for c in data['rows'][0]['cells']))

    def test_sunday_working_schedule_is_not_inferred_off(self):
        data = self.build(start='2026-10-11', end='2026-10-11')
        self.assertEqual(data['rows'][0]['cells'][0]['target_bcm'], 110)
        self.assertEqual(data['pie']['categories']['grey']['hours'], 0)

    def test_missing_schedule_and_incomplete_records_stay_unclassified(self):
        self.plan['month_prod_days'] = []
        data = self.build([self.f.doc()])
        self.assertEqual(data['excluded']['schedule_unknown'], 24)
        self.assertTrue(all(c['category'] == 'unclassified' for c in data['rows'][0]['cells']))
        self.assertIsNone(data['rows'][0]['cells'][0]['target_bcm'])
        self.setUp()
        data = self.build([self.f.doc(), self.f.doc('07:00-08:00')], now=datetime(2026, 10, 12, 7, 30))
        self.assertEqual(data['pie']['total_hours'], 1)
        self.assertEqual(data['excluded']['pending'], 23)
        self.assertEqual(data['rows'][0]['cells'][1]['category'], 'unclassified')

    def test_future_non_working_is_visible_but_excluded_from_pie(self):
        self.plan['month_prod_days'][11].update(shift_night_hours=0)
        data = self.build(now=datetime(2026, 10, 12, 12))
        self.assertTrue(all(c['category'] == 'grey' for c in data['rows'][0]['cells'][12:]))
        self.assertEqual(data['pie']['categories']['grey']['hours'], 0)
        self.assertEqual(data['excluded']['pending'], 18)

    def test_pie_totals_match_completed_classified_cells(self):
        self.plan['month_prod_days'][11].update(shift_night_hours=0)
        docs = [self.f.doc('06:00-07:00', rows=[self.f.row(bcm=110)]),
                self.f.doc('07:00-08:00', rows=[self.f.row(bcm=374)]),
                self.f.doc('08:00-09:00', rows=[self.f.row(bcm=100)]), self.f.doc('09:00-10:00')]
        data = self.build(docs)
        self.assertEqual(data['pie']['total_hours'], 16)
        self.assertEqual(data['excluded']['missing'], 8)
        for key, entry in data['pie']['categories'].items():
            self.assertEqual(entry['hours'], sum(c['category'] == key and c['completed'] for c in data['rows'][0]['cells']))
            self.assertAlmostEqual(entry['percent'], entry['hours'] / 16 * 100)
        self.assertAlmostEqual(sum(e['percent'] for e in data['pie']['categories'].values()), 100)
