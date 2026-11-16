"""Isolated regression tests for HOD Month End scope selection."""

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


SOURCE = Path(__file__).with_name("hod_presentation.py")


def load_helper(report_rows):
    tree = ast.parse(SOURCE.read_text())
    function = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "get_hod_month_end_gauge_averages"
    )

    # Substitute the Month End import with a controlled report mock.
    function.body = [
        node for node in function.body
        if not (
            isinstance(node, ast.ImportFrom)
            and node.module
            and "availability_and_utilisation_month_end_report"
            in node.module
        )
    ]

    mock_execute = Mock(return_value=([], report_rows))

    def throw(message):
        raise ValueError(message)

    namespace = {
        "month_end": SimpleNamespace(execute=mock_execute),
        "frappe": SimpleNamespace(throw=throw),
    }

    module = ast.fix_missing_locations(
        ast.Module(body=[function], type_ignores=[])
    )

    exec(compile(module, str(SOURCE), "exec"), namespace)

    return (
        namespace["get_hod_month_end_gauge_averages"],
        mock_execute,
    )


def total(category, scope, avail, util):
    return {
        "asset_category": category,
        "summary_scope": scope,
        "is_scope_total": True,
        "asset_name": None,
        "avail_percent": avail,
        "util_percent": util,
    }


def run_helper(helper):
    return helper(
        "Koppie",
        "2026-07-01",
        "2026-07-19",
        "85% A & U",
        "All Assets",
    )


class MonthEndGaugeTests(unittest.TestCase):

    def test_production_and_overall_not_spare(self):
        rows = [
            total("Excavator", "production", 44.605, 63.829),
            total("Excavator", "overall", 75.453, 63.829),
            total("Excavator", "spare", 75.453, 57.034),
            total("ADT", "production", 72.826, 73.437),
            total("ADT", "overall", 72.826, 73.437),
            total("ADT", "spare", 64.651, 68.735),
        ]

        helper, execute = load_helper(rows)
        result = run_helper(helper)

        self.assertEqual(
            result["Excavator"]["excluding"],
            {"avail": 44.605, "util": 63.829},
        )
        self.assertEqual(
            result["Excavator"]["including"],
            {"avail": 75.453, "util": 63.829},
        )
        self.assertEqual(
            result["ADT"]["including"],
            {"avail": 72.826, "util": 73.437},
        )

        execute.assert_called_once()
        filters = execute.call_args.args[0]
        self.assertEqual(
            filters["machine_scope"],
            "Production + Swing/Spare Machines",
        )

    def test_machine_rows_are_ignored(self):
        rows = [
            total("Excavator", "production", 44.605, 63.829),
            total("Excavator", "overall", 75.453, 63.829),
            {
                **total("Excavator", "overall", 99, 99),
                "is_scope_total": False,
                "asset_name": "EX01",
            },
        ]

        helper, _ = load_helper(rows)
        result = run_helper(helper)

        self.assertEqual(
            result["Excavator"]["including"]["avail"],
            75.453,
        )

    def test_duplicate_scope_totals_rejected(self):
        rows = [
            total("Excavator", "overall", 75, 60),
            total("Excavator", "overall", 80, 65),
        ]

        helper, _ = load_helper(rows)

        with self.assertRaises(ValueError):
            run_helper(helper)

    def test_missing_categories_return_unavailable(self):
        helper, _ = load_helper([])
        result = run_helper(helper)

        self.assertIsNone(
            result["Dozer"]["including"]["avail"]
        )
        self.assertIsNone(
            result["ADT"]["excluding"]["util"]
        )


if __name__ == "__main__":
    unittest.main()
