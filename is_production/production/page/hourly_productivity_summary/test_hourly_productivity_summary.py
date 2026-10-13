from datetime import datetime
from unittest import TestCase
from unittest.mock import patch

from is_production.production.page.hourly_productivity_summary import hourly_productivity_summary as report


class TestHourlyProductivitySummary(TestCase):
    def setUp(self):
        self.plan = {"name": "MPP", "num_excavators": 2, "excavator_truck_assignments": [
            {"excavator": "EX1", "truck": "T1"}, {"excavator": "EX1", "truck": "T2"},
            {"excavator": "EX2", "truck": "T3"}, {"excavator": "SPARE", "truck": None}]}
        self.assets = [{"name": n, "asset_name": n, "location": "Koppie", "asset_category": c}
                       for n, c in [("EX1", "Excavator"), ("EX2", "Excavator"), ("SPARE", "Excavator"),
                                    ("T1", "ADT"), ("T2", "ADT"), ("T3", "ADT")]]

    def doc(self, hour="06:00-07:00", date="2026-10-12", rows=()):
        return {"name": "HP-" + date + hour, "prod_date": date, "hour_slot": hour,
                "location": "Koppie", "docstatus": 0, "truck_loads": list(rows)}

    def row(self, excavator="EX1", truck="T1", bcm=20, loads=1, **kw):
        return dict(asset_name_shoval=excavator, asset_name_truck=truck, bcms=bcm, loads=loads, **kw)

    def build(self, docs=(), breakdowns=(), shift="Day", plan=None, now=None, **kw):
        return report.build_summary("2026-10-12", shift, "Koppie", self.plan if plan is None else plan,
                                    self.assets, breakdowns, docs,
                                    now=now or datetime(2026, 10, 14), **kw)

    def test_fixed_mpp_target_deduplicates_teams_and_excludes_spares(self):
        data = self.build()
        self.assertEqual(data["summary"]["target_bcm"], 4840)
        self.assertEqual([data["hours"][i]["target_bcm"] for i in (0, 1, 11)], [110, 440, 330])
        self.assertEqual(data["spare_excavators"][0]["asset"], "SPARE")

    def test_spare_output_contributes_without_an_additional_target(self):
        data = self.build([self.doc(rows=[self.row(bcm=50), self.row("SPARE", "T2", bcm=60)])])
        self.assertEqual(data["hours"][0]["actual_bcm"], 110)
        self.assertEqual(data["hours"][0]["achievement"], 100)
        self.assertEqual(data["spare_excavators"][0]["actual_bcm"], 60)

    def test_missing_and_recorded_zero_have_distinct_statuses(self):
        data = self.build([self.doc(rows=[self.row(bcm=0, loads=0)])])
        self.assertEqual(data["hours"][0]["record_status"], "recorded")
        self.assertEqual(data["hours"][1]["record_status"], "missing")
        self.assertEqual(data["hours"][1]["actual_bcm"], 0)

    def test_night_uses_next_calendar_date_and_excludes_wrong_dates(self):
        docs = [self.doc("00:00-01:00", "2026-10-13", [self.row(bcm=100)]),
                self.doc("00:00-01:00", "2026-10-12", [self.row(bcm=999)]),
                self.doc("06:00-07:00", "2026-10-13", [self.row(bcm=999)])]
        data = self.build(docs, shift="Night")
        self.assertEqual(data["hours"][6]["actual_bcm"], 100)
        self.assertEqual(data["summary"]["actual_bcm"], 100)
        self.assertEqual(data["hours"][11]["end"], "2026-10-13 06:00:00")

    def test_breakdowns_never_reduce_targets_and_estimate_is_capped(self):
        pbm = [{"asset_name": "EX1", "breakdown_start_datetime": "2026-10-12 06:00:00",
                "resolved_datetime": "2026-10-12 07:00:00"},
               {"asset_name": "T1", "breakdown_start_datetime": "2026-10-12 06:00:00",
                "resolved_datetime": "2026-10-12 07:00:00"}]
        data = self.build([self.doc(rows=[self.row(bcm=45), self.row("EX2", "T3", bcm=55)])], pbm)
        self.assertEqual(data["hours"][0]["target_bcm"], 110)
        self.assertEqual(data["hours"][0]["availability_estimate_bcm"], 10)
        self.assertEqual(data["pie"]["achieved"], 100)
        self.assertEqual(data["pie"]["availability_estimate"], 10)
        self.assertEqual(data["pie"]["remaining"], 4730)

    def test_missing_hours_and_absent_pbm_cannot_support_attribution(self):
        pbm = [{"asset_name": "EX1", "breakdown_start_datetime": "2026-10-12 06:00:00"}]
        self.assertEqual(self.build(breakdowns=pbm)["pie"]["availability_estimate"], 0)
        self.assertEqual(self.build([self.doc()])["pie"]["availability_estimate"], 0)

    def test_adt_fraction_and_excavator_overlap_count_once(self):
        pbm = [{"asset_name": "T1", "breakdown_start_datetime": "2026-10-12 07:00:00",
                "resolved_datetime": "2026-10-12 08:00:00"},
               {"asset_name": "EX1", "breakdown_start_datetime": "2026-10-12 07:30:00",
                "resolved_datetime": "2026-10-12 08:00:00"}]
        data = self.build([self.doc("07:00-08:00")], pbm)
        self.assertEqual(data["hours"][1]["availability_estimate_bcm"], 165)
        self.assertEqual(data["hours"][1]["available"], 2)

    def test_count_mismatch_uses_saved_count_without_inventing_identities(self):
        self.plan["num_excavators"] = 3
        data = self.build([self.doc()])
        self.assertEqual(data["summary"]["target_bcm"], 7260)
        self.assertFalse(data["roster_complete"])
        self.assertTrue(data["warnings"])
        self.assertEqual(data["pie"]["availability_estimate"], 0)

    def test_missing_plan_has_no_assumed_target(self):
        data = self.build(plan={})
        self.assertIsNone(data["hours"][0]["target_bcm"])
        self.assertIsNone(data["hours"][0]["achievement"])

    def test_future_and_current_hours_not_attributed_or_in_completed_pie(self):
        data = self.build([self.doc()], now=datetime(2026, 10, 12, 7, 30))
        self.assertEqual(data["hours"][1]["record_status"], "in_progress_missing")
        self.assertEqual(data["hours"][2]["record_status"], "pending")
        self.assertEqual(sum(data["pie"][k] for k in ("achieved", "availability_estimate", "remaining")), 110)

    def test_configurable_thresholds_and_unrounded_achievement(self):
        for bcm, colour in [(110, "green"), (88, "orange"), (87, "red")]:
            self.assertEqual(self.build([self.doc(rows=[self.row(bcm=bcm)])])["hours"][0]["colour"], colour)
        self.assertEqual(self.build([self.doc(rows=[self.row(bcm=88)])], orange_threshold=90)["hours"][0]["colour"], "red")

    def test_spare_overproduction_offsets_losses_without_double_counting(self):
        docs = [self.doc(rows=[self.row("SPARE", "T1", bcm=5000)])]
        data = self.build(docs)
        self.assertEqual(data["pie"]["achieved"], 4840)
        self.assertEqual(data["pie"]["remaining"], 0)
        self.assertEqual(data["pie"]["availability_estimate"], 0)

    def test_shared_truck_disables_ambiguous_availability_attribution(self):
        self.plan["excavator_truck_assignments"].append({"excavator": "EX2", "truck": "T1"})
        data = self.build([self.doc()])
        self.assertFalse(data["roster_complete"])
        self.assertTrue(data["warnings"])

    def test_nonfinite_and_negative_bcm_cannot_create_invalid_pie(self):
        for value in [-1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                self.build([self.doc(rows=[self.row(bcm=value)])])

    def test_completed_actual_is_preserved_without_plan(self):
        data = self.build([self.doc(rows=[self.row(bcm=50)])], plan={})
        self.assertEqual(data["summary"]["completed_actual_bcm"], 50)

    def test_planned_excavator_without_records_has_unknown_load_times(self):
        data = self.build()
        self.assertEqual(len(data["excavators"]), 3)
        self.assertIsNone(data["excavators"][0]["first_load_time"])

    def test_asset_label_and_recorded_plant_number_do_not_duplicate_excavators(self):
        self.assets[0]["asset_name"] = "EX001"
        data = self.build([self.doc(rows=[self.row(excavator_plant_no="Old label", exc_start_load_time="06:15")])])
        self.assertEqual(len(data["excavators"]), 3)
        by_label = {r["excavator"]: r for r in data["excavators"]}
        self.assertEqual(by_label["EX001"]["first_load_time"], "06:15")


class TestLoaderPermissions(TestCase):
    def test_guest_denied_before_reading_sources(self):
        with patch.object(report.frappe, "session", report.frappe._dict(user="Guest")):
            with self.assertRaises(report.frappe.PermissionError):
                report.get_summary("2026-10-12", "Day", "Koppie")

    def test_invalid_shift_rejected(self):
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")):
            with self.assertRaises(ValueError):
                report.get_summary("2026-10-12", "Invalid", "Koppie")

    def test_document_read_permission_checked(self):
        class DeniedDocument:
            def check_permission(self, permission):
                raise report.frappe.PermissionError("denied")
        with patch.object(report.frappe, "get_doc", return_value=DeniedDocument()):
            with self.assertRaises(report.frappe.PermissionError):
                report._read_document("Location", "Koppie")

    def test_restricted_calculation_fields_fail_closed(self):
        class RestrictedDocument:
            restricted = False
            def check_permission(self, permission):
                pass
            def apply_fieldlevel_read_permissions(self):
                self.restricted = True
            def as_dict(self):
                return {"name": "MPP", "num_excavators": None if self.restricted else 2}
        with patch.object(report.frappe, "get_doc", return_value=RestrictedDocument()):
            with self.assertRaises(report.frappe.PermissionError):
                report._read_document("Monthly Production Planning", "MPP")

    def test_permission_filtered_pagination_keeps_later_rows(self):
        rows = [{"name": f"HP-{i}"} for i in range(501)]
        def database_list(doctype, **kwargs):
            start = kwargs["limit_start"]
            return rows[start:start + kwargs["limit_page_length"]]
        with patch.object(report.frappe, "get_list", side_effect=database_list):
            self.assertEqual(len(report._permitted_rows("Hourly Production", {})), 501)

    def test_loader_uses_calendar_window_and_permission_checked_parents(self):
        fixture = TestHourlyProductivitySummary()
        fixture.setUp()
        docs = {
            ("Location", "Koppie"): {"name": "Koppie"},
            ("Monthly Production Planning", "MPP"): fixture.plan,
            ("Hourly Production", "HP1"): fixture.doc("00:00-01:00", "2026-10-13", [fixture.row(bcm=100)]),
            ("Hourly Productivity Settings", "Hourly Productivity Settings"): {"green_threshold": 100, "orange_threshold": 80}
        }
        docs.update({("Asset", a["name"]): a for a in fixture.assets})
        reads = []
        class DatabaseDocument:
            def __init__(self, doctype, name):
                self.key = (doctype, name)
            def check_permission(self, permission):
                reads.append((self.key, permission))
            def apply_fieldlevel_read_permissions(self):
                pass
            def as_dict(self):
                return docs[self.key]
        def database_list(doctype, **kwargs):
            filters = kwargs["filters"]
            if doctype == "Hourly Production":
                self.assertEqual(filters["prod_date"], ["between", [report.getdate("2026-10-12"), report.getdate("2026-10-13")]])
                self.assertNotIn("shift", filters)
                return [report.frappe._dict(name="HP1")]
            if doctype == "Monthly Production Planning":
                self.assertEqual(kwargs["order_by"], "modified desc, name desc")
                return [report.frappe._dict(name="MPP")]
            if doctype == "Asset":
                return [report.frappe._dict(name=a["name"]) for a in fixture.assets]
            if doctype == "Plant Breakdown or Maintenance":
                self.assertEqual(filters["exclude_from_au"], 0)
                self.assertEqual(filters["breakdown_reason"], ["!=", ""])
                return []
            raise AssertionError(doctype)
        with patch.object(report.frappe, "session", report.frappe._dict(user="user@example.com")), \
                patch.object(report.frappe, "get_doc", side_effect=DatabaseDocument), \
                patch.object(report.frappe, "get_list", side_effect=database_list), \
                patch.object(report.frappe, "has_permission", return_value=True), \
                patch.object(report, "now_datetime", return_value=datetime(2026, 10, 14)):
            data = report.get_summary("2026-10-12", "Night", "Koppie")
        self.assertEqual(data["hours"][6]["actual_bcm"], 100)
        self.assertIn((("Hourly Production", "HP1"), "read"), reads)
