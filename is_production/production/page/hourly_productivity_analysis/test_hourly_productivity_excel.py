"""Inspect real XLSX output from the analysis and its download endpoint."""
from io import BytesIO
from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from is_production.production.page.hourly_productivity_analysis import hourly_productivity_analysis as report
from is_production.production.page.hourly_productivity_analysis import test_hourly_productivity_analysis as fixtures
from is_production.production.page.hourly_productivity_analysis.hourly_productivity_analysis import make_workbook


class TestHourlyProductivityExcel(TestCase):
    def setUp(self):
        fixture = fixtures.TestHourlyProductivityAnalysis()
        fixture.setUp()
        docs = [fixture.f.doc("07:00-08:00", rows=[fixture.f.row(bcm=880)]),
                fixture.f.doc("08:00-09:00", rows=[fixture.f.row(bcm=400)])]
        fatigue = [{"site": "Koppie", "shift": "Day", "day_type": "Weekday",
                    "fatigue_start": "13:00", "fatigue_end": "13:30"}]
        self.data = fixture.build(docs, fatigue=fatigue)

    def test_workbook_four_categories_fractional_totals_colours_and_chart(self):
        content = make_workbook(self.data)
        book = load_workbook(BytesIO(content))
        summary, month = book["Summary"], book["October 2026"]
        self.assertEqual([cell.value for cell in summary[9]], [
            "Month", "Days", "Actual BCM", "Target BCM", "Achievement",
            "Green hrs", "Orange hrs", "Red hrs", "Fatigue / Non-working hrs"])
        self.assertEqual([summary.cell(10, col).value for col in range(6, 10)], [1, 1, 21.5, .5])
        self.assertEqual(summary["C10"].value, 1280)
        self.assertEqual(summary["D10"].value, 7920)
        self.assertEqual(
            sum(cell["target_bcm"] for cell in self.data["rows"][0]["cells"]),
            9680,
        )
        self.assertEqual(self.data["rows"][0]["mpp_daily_target_bcm"], 7920)
        self.assertEqual(summary.auto_filter.ref, "A9:I10")
        expected = [
            ("Green · 220 and above per production excavator", 1, "FF00B050"),
            ("Orange · 200-219 per production excavator", 1, "FFFF9F1C"),
            ("Red · Below 200 per production excavator", 21.5, "FFD63C4B"),
            ("Fatigue / Non-working", .5, "FFF5F5F5"),
        ]
        for row, (label, hours, colour) in enumerate(expected, 13):
            self.assertEqual(month.cell(row, 1).value, label)
            self.assertEqual(month.cell(row, 2).value, hours)
            self.assertEqual(month.cell(row, 1).fill.fgColor.rgb, colour)
        self.assertEqual(month["E7"].fill.fgColor.rgb, "FF00B050")
        self.assertEqual(month["F7"].fill.fgColor.rgb, "FFFF9F1C")
        self.assertEqual(month["D7"].fill.fgColor.rgb, "FFD63C4B")
        self.assertIn("grey: 50%", month["K7"].comment.text)
        self.assertIn("red: 50%", month["K7"].comment.text)
        text = " ".join(str(cell.value) for sheet in book for row in sheet for cell in row if cell.value)
        self.assertNotIn("Zero-production", text)
        self.assertNotIn("machines available", text)
        self.assertIn("partial hours", text)
        self.assertEqual(len(summary._charts), 1)
        self.assertEqual(summary._charts[0].series[0].val.numRef.f, "Summary!$C$10")
        with ZipFile(BytesIO(content)) as archive:
            self.assertIsNone(archive.testzip())

    def test_multiple_months_keep_summary_and_chart_ranges_aligned(self):
        fixture = fixtures.TestHourlyProductivityAnalysis()
        fixture.setUp()
        september = dict(fixture.plan, name="SEP", prod_month_start_date="2026-09-01",
                         prod_month_end_date="2026-09-30")
        september["month_prod_days"] = [{
            "shift_start_date": "2026-09-30",
            "production_excavators": "2",
            "shift_day_hours": 9,
            "shift_night_hours": 9,
            "bcm_per_day": 7920,
        }]
        fixture.plan["month_prod_days"] = [{
            "shift_start_date": "2026-10-01",
            "production_excavators": "2",
            "shift_day_hours": 9,
            "shift_night_hours": 9,
            "bcm_per_day": 7920,
        }]
        data = fixture.build(plans=[september, fixture.plan], start="2026-09-30", end="2026-10-01")
        book = load_workbook(BytesIO(make_workbook(data)))
        self.assertEqual(book.sheetnames, ["Summary", "September 2026", "October 2026"])
        summary = book["Summary"]
        self.assertEqual(summary.auto_filter.ref, "A9:I11")
        for row in (10, 11):
            self.assertEqual([summary.cell(row, col).value for col in range(6, 10)], [0, 0, 24, 0])
        self.assertEqual(summary._charts[0].series[0].val.numRef.f, "Summary!$C$10:$C$11")
        self.assertEqual(summary._charts[0].series[1].val.numRef.f, "Summary!$D$10:$D$11")

    def test_spare_fleet_does_not_raise_excel_bcm_thresholds(self):
        fixture = fixtures.TestHourlyProductivityAnalysis()
        fixture.setUp()
        plan = dict(fixture.plan, num_excavators=1)
        plan["month_prod_days"] = [
            dict(row, production_excavators="1", bcm_per_day=3960)
            for row in fixture.plan["month_prod_days"]
        ]
        plan["excavator_truck_assignments"] = plan["excavator_truck_assignments"] + [
            {"excavator": f"SPARE{i}", "truck": None} for i in range(8)]
        docs = [fixture.f.doc("07:00-08:00", rows=[fixture.f.row(bcm=440)]),
                fixture.f.doc("08:00-09:00", rows=[fixture.f.row(bcm=200)])]
        data = fixture.build(docs, plans=[plan])
        book = load_workbook(BytesIO(make_workbook(data)))
        self.assertEqual([book["Summary"].cell(10, col).value for col in range(6, 10)], [1, 1, 22, 0])
        self.assertEqual(book["October 2026"]["E7"].fill.fgColor.rgb, "FF00B050")
        self.assertEqual(book["October 2026"]["F7"].fill.fgColor.rgb, "FFFF9F1C")
        self.assertEqual(data["pie"]["categories"]["green"]["hours"], 1)
        self.assertEqual(data["pie"]["categories"]["amber"]["hours"], 1)



    def test_hourly_export_maps_all_24_hours_correctly(self):
        book = load_workbook(BytesIO(make_workbook(self.data)))
        sheet = book["October 2026"]

        first_row = self.data["rows"][0]

        self.assertEqual(sheet["P6"].value, "Day Avg")
        self.assertEqual(sheet["Q6"].value, first_row["cells"][12]["label"])
        self.assertEqual(sheet["AB6"].value, first_row["cells"][23]["label"])
        self.assertEqual(sheet["AC6"].value, "Night Avg")

        exported_actual = 0.0

        for excel_row, row in enumerate(self.data["rows"], start=7):
            for hour_index, cell in enumerate(row["cells"]):
                column = hour_index + 3 + (1 if hour_index >= 12 else 0)
                address = f"{get_column_letter(column + 1)}{excel_row}"

                actual = float(cell.get("actual_bcm") or 0)

                if not cell.get("has_record"):
                    expected = f"{actual:g}*"
                elif not cell.get("completed"):
                    expected = f"{actual:g}{chr(8224)}"
                else:
                    expected = actual

                self.assertEqual(
                    sheet[address].value,
                    expected,
                    f"{row['date']} {cell['label']}"
                )

                exported_actual += float(
                    str(sheet[address].value).rstrip("*" + chr(8224))
                )

        self.assertAlmostEqual(
            exported_actual,
            book["Summary"]["C10"].value
        )

    def test_monthly_export_includes_day_and_night_average_columns(self):
        book = load_workbook(BytesIO(make_workbook(self.data)))
        sheet = book["October 2026"]
        row = self.data["rows"][0]

        self.assertEqual(sheet["P6"].value, "Day Avg")
        self.assertEqual(sheet["AC6"].value, "Night Avg")

        for address, cells in (
            ("P7", row["cells"][:12]),
            ("AC7", row["cells"][12:24]),
        ):
            values = [
                float(cell["actual_bcm"])
                for cell in cells
                if cell.get("completed") and cell.get("has_record")
                and cell.get("actual_bcm") is not None
                and __import__("math").isfinite(float(cell["actual_bcm"]))
            ]
            expected = sum(values) / len(values) if values else None

            if expected is None:
                self.assertEqual(sheet[address].value, "—")
            else:
                self.assertAlmostEqual(sheet[address].value, expected, places=1)

            self.assertEqual(sheet[address].fill.fgColor.rgb, "FFDDEBF7")

    def test_availability_export_includes_day_and_night_average_columns(self):
        response = {}

        with patch.object(report, "get_machine_analysis", return_value=self.data) as get_machine_analysis, \
                patch.object(report.frappe, "response", response):
            report.export_machine_excel(
                "Koppie", "2026-10-12", "2026-10-12",
                machine_scope="all", view_mode="availability"
            )

        get_machine_analysis.assert_called_once_with(
            "Koppie", "2026-10-12", "2026-10-12", machine_scope="all"
        )
        self.assertEqual(response["type"], "download")

        book = load_workbook(BytesIO(response["filecontent"]))
        sheet = book["October 2026"]
        row = self.data["rows"][0]

        self.assertEqual(sheet["P6"].value, "Day Avg")
        self.assertEqual(sheet["AC6"].value, "Night Avg")
        self.assertEqual(sheet["Q6"].value, row["cells"][12]["label"])
        self.assertEqual(sheet["AB6"].value, row["cells"][23]["label"])
        self.assertTrue(str(sheet["AB7"].value).strip())

        for address, cells in (
            ("P7", row["cells"][:12]),
            ("AC7", row["cells"][12:24]),
        ):
            values = []
            for cell in cells:
                if not cell.get("completed") or not cell.get("has_record"):
                    continue
                try:
                    value = float(cell.get("available"))
                except (TypeError, ValueError):
                    continue
                if __import__("math").isfinite(value) and value >= 0:
                    values.append(value)

            average = sum(values) / len(values) if values else None
            actual = str(sheet[address].value)

            if average is None:
                self.assertTrue(actual.startswith("—"))
            else:
                self.assertIn(f"{average:.2f}", actual)

            self.assertEqual(sheet[address].fill.fgColor.rgb, "FFDDEBF7")



    def test_utilisation_export_matches_dashboard_production_thresholds(self):
        data = deepcopy(self.data)
        row = data["rows"][0]
        row["production_fleet_total"] = 5

        for index, utilised in ((1, 5), (2, 4), (3, 3)):
            cell = row["cells"][index]
            cell.update({
                "completed": True,
                "has_record": True,
                "available": 5,
                "utilised": utilised,
                "production_available": 5,
                "production_utilised": utilised,
            })

        response = {}

        with patch.object(report, "get_machine_analysis", return_value=data) as get_machine_analysis, \
                patch.object(report.frappe, "response", response):
            report.export_machine_excel(
                "Koppie", "2026-10-12", "2026-10-12",
                machine_scope="production", view_mode="utilisation"
            )

        get_machine_analysis.assert_called_once_with(
            "Koppie", "2026-10-12", "2026-10-12",
            machine_scope="production"
        )

        sheet = load_workbook(BytesIO(response["filecontent"]))["October 2026"]
        row = data["rows"][0]
        self.assertEqual(sheet["P6"].value, row["cells"][12]["label"])
        self.assertEqual(sheet["AA6"].value, row["cells"][23]["label"])
        self.assertTrue(str(sheet["AA7"].value).strip())

        self.assertEqual(sheet["E7"].value, "5 / 5")
        self.assertEqual(sheet["F7"].value, "4 / 5")
        self.assertEqual(sheet["G7"].value, "3 / 5")

        self.assertEqual(sheet["E7"].fill.fgColor.rgb, "FFB9EDC5")
        self.assertEqual(sheet["F7"].fill.fgColor.rgb, "FFFFC18A")
        self.assertEqual(sheet["G7"].fill.fgColor.rgb, "FFF3A6AE")



    def test_availability_export_preserves_averages_and_filter_across_months(self):
        data = deepcopy(self.data)
        second = deepcopy(data["rows"][0])
        second.update({
            "date": "2026-11-01",
            "day": "Sun",
            "month": "November 2026",
            "month_key": "2026-11",
        })
        data["rows"].append(second)

        response = {}

        with patch.object(report, "get_machine_analysis", return_value=data) as get_machine_analysis, \
                patch.object(report.frappe, "response", response):
            report.export_machine_excel(
                "Koppie", "2026-10-12", "2026-11-01",
                machine_scope="all",
                unavailable_number="1",
                view_mode="availability"
            )

        get_machine_analysis.assert_called_once_with(
            "Koppie", "2026-10-12", "2026-11-01", machine_scope="all"
        )

        book = load_workbook(BytesIO(response["filecontent"]))
        self.assertEqual(book.sheetnames, ["Summary", "October 2026", "November 2026"])
        self.assertEqual(book["Summary"]["B9"].value, "0-1 unavailable")

        for name in ("October 2026", "November 2026"):
            sheet = book[name]
            self.assertEqual(sheet["P6"].value, "Day Avg")
            self.assertEqual(sheet["AC6"].value, "Night Avg")
            self.assertIn("/", str(sheet["P7"].value))
            self.assertIn("/", str(sheet["AC7"].value))

    def test_download_endpoint_returns_readable_workbook_and_preserves_scope(self):
        response = {}
        with patch.object(report, "get_analysis", return_value=self.data) as get_analysis, \
                patch.object(report.frappe, "response", response):
            report.export_excel("Koppie", "2026-10-12", "2026-10-12", machine_scope="production")
        get_analysis.assert_called_once_with("Koppie", "2026-10-12", "2026-10-12", machine_scope="production")
        self.assertEqual(response["type"], "download")
        self.assertEqual(response["filename"], "Koppie_Hourly_Productivity_2026-10-12_to_2026-10-12.xlsx")
        book = load_workbook(BytesIO(response["filecontent"]))
        self.assertEqual([book["Summary"].cell(10, col).value for col in range(6, 10)], [1, 1, 21.5, .5])

    def test_download_rejects_no_exportable_analysis(self):
        with patch.object(report, "get_analysis", return_value={"rows": []}), \
                patch.object(report.frappe, "throw", side_effect=ValueError("No exportable analysis")):
            with self.assertRaisesRegex(ValueError, "No exportable analysis"):
                report.export_excel("Koppie", "2026-10-12", "2026-10-12")


class TestExcavatorAvailabilityExcel(TestCase):

    def test_workbook_exports_summary_hourly_segments_and_downtime(self):
        cells = []

        for index in range(24):
            label = f"{(index + 6) % 24:02d}:00-{(index + 7) % 24:02d}:00"

            if index == 0:
                segments = [
                    {"start_minute": 0, "end_minute": 15,
                     "category": "green", "available": 3,
                     "unavailable": 0, "unknown": 0},
                    {"start_minute": 15, "end_minute": 45,
                     "category": "orange", "available": 2,
                     "unavailable": 1, "unknown": 0,
                     "unavailable_excavators": ["IS0309"]},
                    {"start_minute": 45, "end_minute": 60,
                     "category": "green", "available": 3,
                     "unavailable": 0, "unknown": 0},
                ]
                details = [
                    {
                        "asset": "IS0309", "label": "IS0309",
                        "status": "Unavailable",
                        "events": [{
                            "downtime_type": "Breakdown",
                            "status": "Closed",
                            "reason": "Aircon fault",
                            "start": "2026-11-05 06:15:00",
                            "resolved": "2026-11-05 06:45:00",
                            "effective_end": "2026-11-05 06:45:00",
                            "exclude_from_au": True,
                            "resolution": "Fixed",
                        }],
                    },
                    {"asset": "IS0310", "label": "IS0310",
                     "status": "Available", "events": []},
                    {"asset": "IS0311", "label": "IS0311",
                     "status": "Available", "events": []},
                ]
            else:
                segments = [{
                    "start_minute": 0, "end_minute": 60,
                    "category": "green", "available": 3,
                    "unavailable": 0, "unknown": 0,
                }]
                details = []

            cells.append({
                "label": label,
                "completed": True,
                "has_record": True,
                "available": 2 if index == 0 else 3,
                "unavailable": 1 if index == 0 else 0,
                "category": "orange" if index == 0 else "green",
                "segments": segments,
                "details": details,
            })

        data = {
            "site": "Koppie",
            "from_date": "2026-11-05",
            "to_date": "2026-11-05",
            "hours": [cell["label"] for cell in cells],
            "rows": [{
                "date": "2026-11-05",
                "day": "Thu",
                "month": "November 2026",
                "month_key": "2026-11",
                "fleet_total": 3,
                "cells": cells,
            }],
        }

        content = report.make_excavator_availability_workbook(data)
        book = load_workbook(BytesIO(content))

        self.assertEqual(book.sheetnames, [
            "Summary", "Hourly Detail", "Downtime Details",
        ])

        summary = book["Summary"]
        self.assertEqual(summary["A8"].value, "Date")
        self.assertEqual(summary["C9"].value, 3)
        self.assertAlmostEqual(summary["D9"].value, 71.5)
        self.assertAlmostEqual(summary["E9"].value, 0.5)
        self.assertAlmostEqual(summary["H9"].value, 71.5 / 72)

        hourly = book["Hourly Detail"]
        self.assertEqual(hourly["E5"].value, "2 / 3")

        self.assertIn(
            "06:15:00-06:45:00: orange",
            hourly["E5"].comment.text,
        )

        downtime = book["Downtime Details"]
        self.assertEqual(downtime["F2"].value, "Aircon fault")
        self.assertEqual(downtime["J2"].value, "Yes")
        self.assertEqual(downtime["L2"].value, 30)

        with ZipFile(BytesIO(content)) as archive:
            self.assertIsNone(archive.testzip())
