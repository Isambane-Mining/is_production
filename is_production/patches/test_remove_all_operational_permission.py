"""Focused regression tests; run with python -m unittest discover -s APP/patches -p test_remove_all_operational_permission.py."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DOCTYPE = 'Define Monthly Production'

class PermissionRemovalTests(unittest.TestCase):
    def test_source_has_no_all_permission(self):
        data = json.loads((ROOT / 'production/doctype/define_monthly_production/define_monthly_production.json').read_text())
        self.assertNotIn("All", [row["role"] for row in data["permissions"]])
        self.assertIn("System Manager", [row["role"] for row in data["permissions"]])

    def test_patch_is_scoped_and_idempotent(self):
        rows = {table: [
            {"parent": DOCTYPE, "role": "All", "permlevel": 0},
            {"parent": DOCTYPE, "role": "All", "permlevel": 1},
            {"parent": DOCTYPE, "role": "System Manager", "permlevel": 0},
            {"parent": "Equipment Damages", "role": "All", "permlevel": 0},
            {"parent": "Classify Incident", "role": "All", "permlevel": 0},
        ] for table in ("DocPerm", "Custom DocPerm")}
        expected = {table: values[2:] for table, values in rows.items()}
        def delete(table, filters):
            rows[table] = [row for row in rows[table] if not all(row.get(k) == v for k, v in filters.items())]
        fake = SimpleNamespace(db=SimpleNamespace(delete=delete), clear_cache=lambda **kwargs: None)
        spec = importlib.util.spec_from_file_location("permission_patch", ROOT / "patches/remove_all_operational_permission.py")
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"frappe": fake}):
            spec.loader.exec_module(module)
            module.execute()
            self.assertEqual(rows, expected)
            module.execute()
            self.assertEqual(rows, expected)

if __name__ == "__main__":
    unittest.main()
