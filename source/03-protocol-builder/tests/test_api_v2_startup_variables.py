import os
import unittest
from unittest.mock import patch

import fluent_pipeline.api_v2_startup_variables as startup_variables
from tecan_common import native_acceleration
from fluent_pipeline.api_v2_startup_variables import (
    compare_startup_variable_snapshots,
    expectations_as_tuple,
    expectations_from_tuple,
    query_at_startup_expectations,
    run_startup_variable_value_check,
    variable_values_match,
)


class _DictVariableReader:
    def __init__(self, values: dict[str, str]):
        self._values = dict(values)

    def get_variable_value(self, name: str) -> str | None:
        return self._values.get(name)


class ApiV2StartupVariableTests(unittest.TestCase):
    def test_variable_value_normalization_uses_native_ascii_and_python_fallback(self):
        values = [
            None,
            True,
            False,
            " TRUE ",
            "Yes",
            "ON",
            " no ",
            "OFF",
            "Yeſ",
            "Straße",
            "enabled",
            0,
        ]

        def legacy_normalize(value):
            if value is None:
                return ""
            if isinstance(value, bool):
                return "True" if value else "False"
            text = str(value).strip()
            normalized = text.casefold()
            if normalized in {"true", "yes", "on"}:
                return "True"
            if normalized in {"false", "no", "off"}:
                return "False"
            return text

        expected = [legacy_normalize(value) for value in values]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = [startup_variables.normalize_variable_value(value) for value in values]
        self.assertEqual(disabled, expected)

        native_eligible_count = sum(
            value is not None and not isinstance(value, bool) for value in values
        )
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                startup_variables,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = [startup_variables.normalize_variable_value(value) for value in values]
                self.assertEqual(native_call.call_count, native_eligible_count)
        self.assertEqual(actual, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                startup_variables,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                forced_fallback = [
                    startup_variables.normalize_variable_value(value) for value in values
                ]
        self.assertEqual(forced_fallback, expected)

    def test_query_at_startup_expectations_from_ir_defaults(self):
        ir = {
            "variables": [
                {"name": "StartupVolume", "query_at_startup": True, "default_value": 50},
                {"name": "Ignored", "query_at_startup": False, "default_value": 1},
            ],
            "simulation_values": [{"name": "StartupVolume", "value": 75}],
        }
        expectations = query_at_startup_expectations(ir)
        self.assertEqual(expectations["StartupVolume"], "75")
        self.assertNotIn("Ignored", expectations)

    def test_variable_values_match_numeric_and_bool_aliases(self):
        self.assertTrue(variable_values_match("1", "1.0"))
        self.assertTrue(variable_values_match("True", "true"))
        self.assertFalse(variable_values_match("50", "51"))

    def test_compare_snapshots_reports_mismatch_and_missing(self):
        report = compare_startup_variable_snapshots(
            {"StartupVolume": "50", "TubeCount": "24"},
            {"StartupVolume": "50", "TubeCount": None},
        )
        self.assertEqual(report.status, "failed")
        self.assertIn("TubeCount", report.missing)

    def test_run_startup_variable_value_check_on_scripted_session(self):
        reader = _DictVariableReader({"StartupVolume": "50"})
        report = run_startup_variable_value_check(reader, {"StartupVolume": "50"})
        self.assertEqual(report.status, "passed")
        self.assertTrue(report.snapshots[0].matched)

    def test_run_startup_variable_value_check_detects_mismatch(self):
        reader = _DictVariableReader({"StartupVolume": "99"})
        report = run_startup_variable_value_check(reader, {"StartupVolume": "50"})
        self.assertEqual(report.status, "failed")
        self.assertEqual(len(report.mismatches), 1)

    def test_expectations_tuple_roundtrip(self):
        items = expectations_as_tuple({"TubeCount": 24, "StartupVolume": "50"})
        restored = expectations_from_tuple(items)
        self.assertEqual(restored, {"StartupVolume": "50", "TubeCount": "24"})


if __name__ == "__main__":
    unittest.main()
