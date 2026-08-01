import unittest

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
