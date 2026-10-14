from copy import deepcopy
from datetime import datetime, time, timedelta
from unittest import TestCase
from unittest.mock import patch

from is_production.production.page.hourly_productivity_summary import hourly_productivity_summary as report

dashboard = report


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

    def test_fixed_mpp_target_deduplicates_teams_without_classifying_unpaired(self):
        data = self.build()
        self.assertEqual(data["summary"]["target_bcm"], 4840)
        self.assertEqual([data["hours"][i]["target_bcm"] for i in (0, 1, 11)], [110, 440, 330])
        self.assertEqual(data["unpaired_excavators"][0]["asset"], "SPARE")
        self.assertEqual(data["unpaired_excavators"][0]["classification"], "Production/spare unknown")
        self.assertEqual(data["spare_excavators"], [])

    def test_spare_output_contributes_without_an_additional_target(self):
        data = self.build([self.doc(rows=[self.row(bcm=50), self.row("SPARE", "T2", bcm=60)])])
        self.assertEqual(data["hours"][0]["actual_bcm"], 110)
        self.assertEqual(data["hours"][0]["achievement"], 100)
        self.assertEqual(data["unpaired_excavators"][0]["actual_bcm"], 60)

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

    def test_saved_count_cannot_identify_unpaired_production_excavator(self):
        self.plan["num_excavators"] = 3
        data = self.build([self.doc()])
        self.assertFalse(data["roster_complete"])
        self.assertEqual(len(data["production_excavators"]), 2)
        self.assertEqual(data["unpaired_excavators"][0]["classification"], "Production/spare unknown")
        self.assertEqual(data["spare_excavators"], [])
        self.assertEqual(data["hours"][0]["target_bcm"], 165)
        self.assertIsNone(data["hours"][0]["excavator_available"])
        self.assertEqual(data["pie"]["availability_estimate"], 0)

    def test_excavator_availability_merges_pbm_and_deduplicates_utilisation(self):
        pbm = [{"asset_name": "EX1", "breakdown_start_datetime": "2026-10-12 06:00:00",
                "resolved_datetime": "2026-10-12 06:30:00"},
               {"asset_name": "EX1", "breakdown_start_datetime": "2026-10-12 06:15:00",
                "resolved_datetime": "2026-10-12 06:45:00"}]
        data = self.build([self.doc(rows=[self.row(), self.row(truck="T2"), self.row("SPARE", "T3")])], pbm)
        hour = data["hours"][0]
        self.assertEqual(hour["excavator_available"], 1.25)
        self.assertEqual(hour["excavator_unavailable"], .75)
        self.assertEqual(hour["excavator_pbm_count"], 1)
        self.assertEqual(hour["excavator_utilised"], 1)
        self.assertEqual(hour["unpaired_available"], 1)
        self.assertEqual(hour["unpaired_utilised"], 1)
        self.assertIsNone(hour["spare_available"])
        self.assertEqual(hour["target_bcm"], 110)

    def test_repeated_document_and_named_child_count_once(self):
        row = self.row(name="LOAD1", bcm=50)
        doc = self.doc(rows=[row, dict(row)])
        data = self.build([doc, dict(doc)])
        self.assertEqual(data["hours"][0]["actual_bcm"], 50)
        self.assertEqual(data["hours"][0]["utilised"], 1)

    def test_unresolved_roster_cannot_claim_full_excavator_availability(self):
        self.plan["num_excavators"] = 3
        data = self.build()
        self.assertIsNone(data["hours"][0]["excavator_available"])

    def test_breakdown_restart_keeps_earliest_first_and_latest_last(self):
        docs = [self.doc(rows=[self.row(exc_start_load_time="06:18", exc_end_load_time="09:45")]),
                self.doc("11:00-12:00", rows=[self.row(exc_start_load_time="11:20", exc_end_load_time="17:35")]),
                self.doc("17:00-18:00", rows=[self.row(exc_start_load_time="11:20", exc_end_load_time="16:50")])]
        pbm = [{"asset_name": "EX1", "breakdown_start_datetime": "2026-10-12 09:45:00",
                "resolved_datetime": "2026-10-12 11:20:00"}]
        data = self.build(docs, pbm)
        exc = next(row for row in data["excavators"] if row["excavator"] == "EX1")
        self.assertEqual(exc["first_load_time"], "06:18")
        self.assertEqual(exc["last_load_time"], "17:35")
        self.assertEqual(data["hours"][0]["first_load_events"], [{"excavator": "EX1", "time": "06:18"}])
        self.assertEqual(data["hours"][11]["last_load_events"], [{"excavator": "EX1", "time": "17:35"}])
        self.assertEqual(sum(len(h["first_load_events"]) for h in data["hours"]), 1)

    def test_night_restarts_compare_times_in_shift_order_not_clock_order(self):
        docs = [self.doc("18:00-19:00", rows=[self.row(exc_start_load_time=time(18, 25), exc_end_load_time="23:40")]),
                self.doc("00:00-01:00", "2026-10-13", [self.row(exc_start_load_time=timedelta(minutes=10), exc_end_load_time="05:40")]),
                self.doc("05:00-06:00", "2026-10-13", [self.row(exc_start_load_time="01:15", exc_end_load_time="02:10")])]
        exc = next(row for row in self.build(list(reversed(docs)), shift="Night")["excavators"] if row["excavator"] == "EX1")
        self.assertEqual(exc["first_load_time"], "18:25")
        self.assertEqual(exc["last_load_time"], "05:40")

    def test_invalid_and_outside_shift_times_are_not_inferred_from_bcm(self):
        docs = [self.doc(rows=[self.row(bcm=400, exc_start_load_time="invalid", exc_end_load_time="05:59")]),
                self.doc("17:00-18:00", rows=[self.row(exc_start_load_time="18:30", exc_end_load_time="bad")])]
        exc = next(row for row in self.build(docs)["excavators"] if row["excavator"] == "EX1")
        self.assertIsNone(exc["first_load_time"])
        self.assertIsNone(exc["last_load_time"])

    def test_captured_midnight_is_valid_and_last_at_shift_end_is_retained(self):
        docs = [self.doc("00:00-01:00", "2026-10-13", [self.row(exc_start_load_time=timedelta(0), exc_end_load_time="05:50")]),
                self.doc("05:00-06:00", "2026-10-13", [self.row(exc_end_load_time="06:00")])]
        data = self.build(docs, shift="Night")
        exc = next(row for row in data["excavators"] if row["excavator"] == "EX1")
        self.assertEqual(exc["first_load_time"], "00:00")
        self.assertEqual(exc["last_load_time"], "06:00")
        self.assertEqual(exc["last_load_hour"], "05:00–06:00")

    def test_multiple_excavators_and_row_order_do_not_mix_times(self):
        docs = [self.doc(rows=[self.row(exc_start_load_time="06:30", exc_end_load_time="17:10"),
                              self.row("EX2", "T3", exc_start_load_time="07:20", exc_end_load_time="16:40")]),
                self.doc("09:00-10:00", rows=[self.row(exc_start_load_time="06:10", exc_end_load_time="17:50"),
                                              self.row("EX2", "T3", exc_start_load_time="08:10", exc_end_load_time="15:20")])]
        rows = {r["excavator"]: r for r in self.build(docs)["excavators"]}
        self.assertEqual((rows["EX1"]["first_load_time"], rows["EX1"]["last_load_time"]), ("06:10", "17:50"))
        self.assertEqual((rows["EX2"]["first_load_time"], rows["EX2"]["last_load_time"]), ("07:20", "16:40"))


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
        fixture.plan.update(location="Koppie", prod_month_start_date="2026-10-01", prod_month_end_date="2026-10-31")
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


class TestSummaryDashboard(TestCase):
    def setUp(self):
        self.plan = dict(name='MPP', location='Koppie', prod_month_start_date='2026-10-01',
                         prod_month_end_date='2026-10-31', num_excavators=1, shift_system='2x12Hour',
                         excavator_truck_assignments=[dict(excavator='EX1', truck='T1')],
                         month_prod_days=[dict(shift_start_date='2026-10-12', shift_day_hours=12, shift_night_hours=12)])
        self.assets = [dict(name=x, asset_name=x, location='Koppie', asset_category=c)
                       for x, c in [('EX1', 'Excavator'), ('T1', 'ADT')]]

    def doc(self, hour=6, bcm=0, loads=0, date='2026-10-12', **times):
        return dict(name=f'{date}-{hour}', location='Koppie', prod_date=date,
                    hour_slot=f'{hour:02}:00-{(hour+1)%24:02}:00',
                    truck_loads=[dict(asset_name_shoval='EX1', asset_name_truck='T1',
                                      bcms=bcm, loads=loads, **times)], dozer_production=[dict(bcms=9999)])

    def build(self, docs=(), pbm=(), **kw):
        return dashboard.build_dashboard('Koppie', kw.pop('from_date', '2026-10-12'),
                                          kw.pop('to_date', '2026-10-12'), kw.pop('shift', 'Full Day'),
                                          kw.pop('plans', [self.plan]), self.assets, pbm, docs,
                                          now=kw.pop('now', datetime(2026, 12, 1)), **kw)

    def test_full_day_midnight_and_no_duplicates(self):
        doc = self.doc(0, 10, 1, '2026-10-13')
        result = self.build([doc, doc, self.doc(6, 999, date='2026-10-13')])
        self.assertEqual(len(result['days'][0]['hours']), 24)
        self.assertEqual(result['summary']['actual_bcm'], 10)
        self.assertEqual(result['summary']['target_bcm'], 4840)
        self.assertEqual(result['days'][0]['hours'][18]['actual_bcm'], 10)

    def test_month_specific_count_and_site(self):
        nov = deepcopy(self.plan)
        nov.update(name='NOV', num_excavators=2, prod_month_start_date='2026-11-01', prod_month_end_date='2026-11-30',
                   month_prod_days=[dict(shift_start_date='2026-11-01', shift_day_hours=12, shift_night_hours=12)])
        self.plan['month_prod_days'][0]['shift_start_date'] = '2026-10-31'
        other = dict(nov, name='OTHER', location='Other', num_excavators=99)
        result = self.build(from_date='2026-10-31', to_date='2026-11-01', plans=[self.plan, nov, other])
        self.assertEqual(result['summary']['target_bcm'], 14520)
        self.assertEqual([d['plan_name'] for d in result['days']], ['MPP', 'NOV'])

    def test_night_at_month_end_uses_start_date_plan(self):
        self.plan['month_prod_days'][0]['shift_start_date'] = '2026-10-31'
        november = deepcopy(self.plan)
        november.update(name='NOV', num_excavators=9, prod_month_start_date='2026-11-01', prod_month_end_date='2026-11-30')
        result = self.build([self.doc(0, 220, 1, '2026-11-01')], shift='Night',
                            from_date='2026-10-31', to_date='2026-10-31', plans=[self.plan, november])
        self.assertEqual(result['summary']['target_bcm'], 2420)
        self.assertEqual(result['days'][0]['hours'][6]['category'], 'green')
        self.assertEqual(result['days'][0]['hours'][-1]['end'], '2026-11-01 06:00:00')

    def test_future_non_working_visible_but_not_in_completed_pie(self):
        self.plan['month_prod_days'][0]['shift_night_hours'] = 0
        result = self.build(now=datetime(2026, 10, 12, 12))
        self.assertTrue(all(h['category'] == 'grey' for h in result['days'][0]['hours'][12:]))
        self.assertEqual(result['pie']['counts']['grey'], 0)
        self.assertEqual(result['excluded']['unfinished'], 18)

    def test_saved_zero_count_is_not_inferred_as_zero_required_hours(self):
        self.plan['num_excavators'] = 0
        self.plan['excavator_truck_assignments'] = []
        result = self.build(shift='Day')
        self.assertEqual(result['excluded']['no_target'], 12)
        self.assertEqual(result['pie']['counts']['grey'], 0)

    def test_all_captured_output_including_unverified_machines_is_retained(self):
        doc = self.doc(6, 40, 1)
        doc['truck_loads'].append(dict(asset_name_shoval='UNPAIRED', asset_name_truck='T1', bcms=15, loads=1))
        result = self.build([doc], shift='Day')
        self.assertEqual(result['summary']['actual_bcm'], 55)
        self.assertEqual(result['days'][0]['hours'][0]['category'], 'green')
        unknown = next(r for r in result['days'][0]['excavator_production'] if r['asset'] == 'UNPAIRED')
        self.assertEqual(unknown['availability_status'], 'Unverified')
        self.assertEqual(result['summary']['target_bcm'], 2420)

    def test_zero_hours_not_weekend_assumptions(self):
        self.plan['month_prod_days'] = [dict(shift_start_date='2026-10-11', shift_day_hours=12, shift_night_hours=0)]
        result = self.build(from_date='2026-10-11', to_date='2026-10-11')
        self.assertEqual(result['summary']['target_bcm'], 2420)
        self.assertEqual(result['pie']['counts']['grey'], 12)
        self.assertEqual(result['pie']['total_hours'], 12)
        self.assertTrue(all(h['category'] == 'neutral' for h in result['days'][0]['hours'][:12]))

    def test_reduced_or_absent_schedule_is_unknown(self):
        self.plan['month_prod_days'][0]['shift_day_hours'] = 6
        result = self.build(shift='Day')
        self.assertIsNone(result['summary']['target_bcm'])
        self.assertEqual(result['pie']['total_hours'], 0)
        self.assertEqual(result['excluded']['schedule_unknown'], 12)
        self.plan['month_prod_days'] = []
        self.assertEqual(self.build(shift='Day')['excluded']['schedule_unknown'], 12)
        self.plan['shift_system'] = '3x8Hour'
        self.assertEqual(self.build()['excluded']['schedule_unknown'], 24)

    def test_five_categories_percentages_and_missing(self):
        self.plan['month_prod_days'][0]['shift_night_hours'] = 0
        docs = [self.doc(6, 55, 1), self.doc(7, 187, 1), self.doc(8, 100, 1), self.doc(9)]
        result = self.build(docs)
        self.assertEqual(result['pie']['counts'], dict(green=1, amber=1, red=1, bright_red=1, grey=12))
        self.assertEqual(result['pie']['total_hours'], 16)
        self.assertAlmostEqual(sum(result['pie']['percentages'].values()), 100)
        self.assertEqual(result['excluded']['missing'], 8)
        self.assertEqual(result['pie']['percentages']['green'], 6.25)

    def test_pbm_does_not_reduce_target_or_claim_zero_with_available_machine(self):
        pbm = [dict(asset_name='EX1', breakdown_start_datetime='2026-10-12 09:00', resolved_datetime='2026-10-12 10:00')]
        result = self.build([self.doc(9)], pbm, shift='Day')
        h = result['days'][0]['hours'][3]
        self.assertEqual(h['category'], 'red')
        self.assertEqual(h['target_bcm'], 220)
        self.assertEqual(h['excavator_unavailable'], 1)
        self.plan['num_excavators'] = 2
        h = self.build([self.doc(9)], shift='Day')['days'][0]['hours'][3]
        self.assertEqual(h['category'], 'red')  # Unknown complete production roster cannot prove availability.
        self.assertIsNone(h['excavator_available'])

    def test_future_and_incomplete_excluded_and_completed_kpi_unchanged(self):
        result = self.build([self.doc(6, 55, 1), self.doc(7, 220, 1)], shift='Day', now=datetime(2026, 10, 12, 7, 30))
        self.assertEqual(result['pie']['total_hours'], 1)
        self.assertEqual(result['summary']['completed_target_bcm'], 55)
        self.assertEqual(result['summary']['completed_actual_bcm'], 55)
        self.assertEqual(result['excluded']['unfinished'], 11)

    def test_average_hourly_utilisation_and_timeline_missing(self):
        result = self.build([self.doc(6, 55, 1), self.doc(7, 0, 0)], shift='Day')
        self.assertEqual(result['summary']['average_adts_utilised'], 0.5)
        self.assertEqual(result['summary']['average_excavators_utilised'], 0.5)
        timeline = result['days'][0]['timeline'][0]['cells']
        self.assertEqual(timeline[0]['state'], 'used')
        self.assertEqual(timeline[1]['state'], 'idle')
        self.assertEqual(timeline[2]['state'], 'missing')
        self.assertIsNone(timeline[2]['loads'])

    def test_timeline_partial_pbm_union(self):
        pbm = [dict(asset_name='T1', breakdown_start_datetime='2026-10-12 06:00', resolved_datetime=end)
               for end in ['2026-10-12 06:15', '2026-10-12 06:30']]
        cells = self.build([self.doc(6, 20, 3)], pbm, shift='Day')['days'][0]['timeline'][0]['cells']
        self.assertEqual(cells[0]['available_minutes'], 30)
        self.assertEqual(cells[0]['loads'], 3)
        self.assertEqual(cells[0]['state'], 'partial')

    def test_captured_timings_separate_shifts_and_restart(self):
        docs = [self.doc(6, 10, 1, exc_start_load_time='06:18', exc_end_load_time='07:00'),
                self.doc(10, 10, 1, exc_start_load_time='10:20', exc_end_load_time='17:34'),
                self.doc(18, 10, 1, exc_start_load_time='18:26', exc_end_load_time='22:00'),
                self.doc(0, 10, 1, '2026-10-13', exc_start_load_time='00:30', exc_end_load_time='05:42')]
        rows = self.build(docs)['days'][0]['excavator_production']
        self.assertEqual([(r['shift'], r['first_load_time'], r['last_load_time']) for r in rows],
                         [('Day', '06:18', '17:34'), ('Night', '18:26', '05:42')])

    def test_startup_specs_dynamic_night_and_no_target_exclusion(self):
        specs = [dict(site='Koppie', shift='Night', day_type='Weekday', startup_start='23:30', startup_end='00:15',
                      fatigue_start='03:20', fatigue_end='03:40'),
                 dict(site='Other', shift='Night', day_type='Weekday', startup_start='18:00', startup_end='19:00')]
        result = self.build(specifications=specs)
        indicators = result['days'][0]['indicators']
        self.assertEqual(indicators[0]['label'], 'Startup')
        self.assertEqual(indicators[0]['start'], '2026-10-12 23:30:00')
        self.assertEqual(indicators[0]['end'], '2026-10-13 00:15:00')
        self.assertEqual(result['summary']['target_bcm'], 4840)

    def test_validation_and_inputs_unchanged(self):
        original = deepcopy(self.plan)
        self.build()
        self.assertEqual(self.plan, original)
        for kw in [dict(from_date='2026-10-13'), dict(shift='Other'), dict(to_date='2028-01-01')]:
            with self.assertRaises(ValueError):
                self.build(**kw)


class TestDashboardPermissions(TestCase):
    def test_guest_denied_before_any_query(self):
        with patch.object(dashboard.frappe, 'session', dashboard.frappe._dict(user='Guest')), \
                patch.object(dashboard, 'load_sources') as load:
            with self.assertRaises(dashboard.frappe.PermissionError):
                dashboard.get_dashboard('Koppie', '2026-10-12', '2026-10-12')
            load.assert_not_called()

    def test_optional_specification_access_does_not_bypass_permissions(self):
        fixture = TestSummaryDashboard()
        fixture.setUp()
        with patch.object(dashboard.frappe, 'session', dashboard.frappe._dict(user='user@example.com')), \
                patch.object(dashboard, 'load_sources', return_value=([fixture.plan], [], fixture.assets, [])) as load, \
                patch.object(dashboard.frappe, 'has_permission', return_value=False), \
                patch.object(dashboard, 'now_datetime', return_value=datetime(2026, 10, 14)), \
                patch.object(dashboard, '_permitted_rows') as rows:
            result = dashboard.get_dashboard('Koppie', '2026-10-12', '2026-10-12', 'Full Day')
            rows.assert_not_called()
            self.assertEqual(result['days'][0]['indicators'], [])
            self.assertEqual(load.call_args.args, ('Koppie', datetime(2026, 10, 12, 6), datetime(2026, 10, 13, 6)))

    def test_masked_mpp_schedule_and_specification_fields_fail_closed(self):
        class Document:
            restricted = False
            def check_permission(self, permission):
                pass
            def apply_fieldlevel_read_permissions(self):
                self.restricted = True
            def as_dict(self):
                return {} if self.restricted else dict(month_prod_days=[dict(shift_day_hours=12)], startup_start='06:00')
        for doctype in ['Monthly Production Planning', dashboard.SPECIFICATION]:
            with patch.object(dashboard.frappe, 'get_doc', return_value=Document()):
                with self.assertRaises(dashboard.frappe.PermissionError):
                    dashboard._read_document(doctype, 'restricted')


def browser_fixture():
    plan = dict(name='2026-10-Koppie', location='Koppie', prod_month_start_date='2026-10-01',
                prod_month_end_date='2026-10-31', shift_system='2x12Hour', num_excavators=2,
                excavator_truck_assignments=[dict(excavator='EX1', truck='T1'), dict(excavator='EX2', truck='T2')],
                month_prod_days=[dict(shift_start_date='2026-10-12', shift_day_hours=12, shift_night_hours=12),
                                 dict(shift_start_date='2026-10-13', shift_day_hours=12, shift_night_hours=0)])
    assets = [dict(name=asset, asset_name=label, location='Koppie', asset_category=category)
              for asset, label, category in [('EX1', 'IS0309', 'Excavator'), ('EX2', 'IS0310', 'Excavator'),
                                            ('T1', 'IS0501', 'ADT'), ('T2', 'IS0502', 'ADT')]]
    documents = []
    for day in [12, 13]:
        for index in range(24):
            if index in [8, 9]:
                continue
            hour = (6 + index) % 24
            calendar_date = f'2026-10-{day + (index >= 18):02}'
            target = 55 if index % 12 == 0 else 165 if index % 12 == 11 else 220
            bcm = target * [1, .9, .65, 0, 1.05][index % 5]
            rows = [dict(asset_name_shoval=excavator, asset_name_truck=truck, bcms=bcm, loads=3 if bcm else 0,
                         exc_start_load_time='06:18' if index < 12 else '18:26',
                         exc_end_load_time='17:34' if index < 12 else '05:42')
                    for excavator, truck in [('EX1', 'T1'), ('EX2', 'T2')]]
            documents.append(dict(name=f'HP-{day}-{index}', location='Koppie', prod_date=calendar_date,
                                  hour_slot=f'{hour:02}:00-{(hour + 1) % 24:02}:00', truck_loads=rows))
    breakdowns = [dict(asset_name='T1', breakdown_start_datetime='2026-10-12 06:00', resolved_datetime='2026-10-12 06:15'),
                  dict(asset_name='T2', breakdown_start_datetime='2026-10-12 09:00', resolved_datetime='2026-10-12 10:00')]
    specs = [dict(site='Koppie', shift='Day', day_type='Weekday', startup_start='06:00', startup_end='06:15',
                  fatigue_start='17:15', fatigue_end='18:00'),
             dict(site='Koppie', shift='Night', day_type='Weekday', startup_start='18:00', startup_end='18:15',
                  fatigue_start='05:15', fatigue_end='06:00')]
    return report.build_dashboard('Koppie', '2026-10-12', '2026-10-13', 'Full Day', [plan], assets, breakdowns, documents,
                           now=datetime(2026, 10, 14, 7), specifications=specs)



class TestLegacyDashboardCompatibility(TestCase):
    def test_original_imports_and_api_resolve_to_standard_page_functions(self):
        from . import summary_dashboard as legacy
        self.assertIs(legacy.build_dashboard, report.build_dashboard)
        self.assertIs(legacy.planned_schedule, report.planned_schedule)
        self.assertIs(legacy.get_dashboard, report.get_dashboard)
        self.assertIn(legacy.get_dashboard, report.frappe.whitelisted)
