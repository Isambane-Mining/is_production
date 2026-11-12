# Copyright (c) 2026, Isambane Mining (Pty) Ltd
import frappe
from frappe.tests.utils import FrappeTestCase

from is_production.production.doctype.hourly_production.hourly_production import (
    validate_excavator_boundary_load_times,
)


class TestExcavatorBoundaryLoadTimes(FrappeTestCase):

    def validate(self, slot, rows, shift="Day", **overrides):
        values = {
            "shift": shift,
            "shift_num_hour": slot,
            "shift_system": "2x12Hour",
            "total_working_hours": 12,
            "total_working_hour": 12,
            "truck_loads": rows,
        }
        values.update(overrides)
        return validate_excavator_boundary_load_times(**values)

    def row(self, excavator="EX001", loads=0, **times):
        return {
            "asset_name_shoval": excavator,
            "excavator_plant_no": excavator,
            "loads": loads,
            **times,
        }

    def test_first_hour_requires_time_when_excavator_has_loads(self):
        with self.assertRaises(frappe.ValidationError):
            self.validate("Day-1", [self.row(loads=5)])

    def test_first_hour_allows_zero_loads(self):
        self.validate("Day-1", [self.row(loads=0)])

    def test_last_hour_requires_time_when_excavator_has_loads(self):
        with self.assertRaises(frappe.ValidationError):
            self.validate("Day-12", [self.row(loads=5)])

    def test_last_hour_allows_zero_loads(self):
        self.validate("Day-12", [self.row(loads=0)])

    def test_multiple_adts_need_only_one_excavator_time(self):
        self.validate("Day-1", [
            self.row(loads=2),
            self.row(loads=3, exc_start_load_time="06:15:00"),
        ])

    def test_nonworking_excavator_does_not_need_time(self):
        self.validate("Day-1", [
            self.row("EX001", 5, exc_start_load_time="06:15:00"),
            self.row("EX002", 0),
        ])

    def test_working_excavator_with_zero_boundary_loads_is_exempt(self):
        self.validate("Day-1", [self.row(loads=0)])

    def test_intermediate_hours_do_not_require_times(self):
        self.validate("Day-6", [self.row(loads=5)])

    def test_no_excavator_loads_require_no_times(self):
        self.validate("Day-1", [
            self.row("EX001", 0),
            self.row("EX002", 0),
        ])

    def test_first_and_last_requirements_are_independent(self):
        self.validate("Day-1", [
            self.row(loads=5, exc_start_load_time="06:15:00"),
        ])
        self.validate("Day-12", [
            self.row(loads=5, exc_end_load_time="17:45:00"),
        ])

    def test_night_eight_hour_final_slot(self):
        with self.assertRaises(frappe.ValidationError):
            self.validate(
                "Night-8",
                [self.row(loads=5)],
                shift="Night",
                shift_system="3x8Hour",
                total_working_hour=8,
            )
