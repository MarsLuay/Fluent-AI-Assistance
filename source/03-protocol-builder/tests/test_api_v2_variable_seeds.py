import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
import fluent_pipeline.variable_seeds as variable_seeds_module
from fluent_pipeline.api_v2.runtime import MockRuntimeController, seed_simulation_values
from fluent_pipeline.api_v2.types import VariableSeed
from fluent_pipeline.variable_seeds import (
    apply_variable_seeds_offline,
    collect_variable_seeds,
    variable_seeds_as_json,
)


class VariableSeedCollectionTests(unittest.TestCase):
    def test_collects_simulation_values_and_query_at_startup_defaults(self) -> None:
        ir = {
            "simulation_values": [
                {"name": 'MountedFESfinger()<>"Eccentric[001]"', "value": 0},
            ],
            "variables": [
                {"name": "StartupVolume", "query_at_startup": True, "default_value": 25},
            ],
            "steps": [],
        }
        spec = {
            "verification_recipe": {
                "simulation_values": [{"name": "PlateCount", "value": 2}],
            }
        }

        seeds = collect_variable_seeds(protocol_ir=ir, request_spec=spec)

        self.assertIn(('MountedFESfinger()<>"Eccentric[001]"', "0"), seeds)
        self.assertIn(("StartupVolume", "25"), seeds)
        self.assertIn(("PlateCount", "2"), seeds)

    def test_ir_overrides_request_spec_for_same_name(self) -> None:
        ir = {"simulation_values": [{"name": "PlateCount", "value": 4}], "variables": [], "steps": []}
        spec = {"verification_recipe": {"simulation_values": [{"name": "PlateCount", "value": 2}]}}

        seeds = dict(collect_variable_seeds(protocol_ir=ir, request_spec=spec))

        self.assertEqual(seeds["PlateCount"], "4")

    def test_coerce_bool_uses_native_ascii_parser_and_fallbacks(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "parse_ascii_bool_text"
        ):
            self.skipTest("optional Rust boolean parser is not built")
        values = [
            "1",
            " TRUE ",
            "false",
            "yes",
            "ON",
            "no",
            "Straße",
            None,
            True,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [variable_seeds_module._coerce_bool(value) for value in values]
        self.assertEqual(
            expected,
            [True, True, False, True, True, False, False, False, True],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration,
                "parse_ascii_bool_text",
                wraps=native_acceleration.parse_ascii_bool_text,
            ) as native_parse, patch.object(
                native_acceleration,
                "ascii_lower",
                wraps=native_acceleration.ascii_lower,
            ) as native_lower:
                actual = [variable_seeds_module._coerce_bool(value) for value in values]
                self.assertEqual(native_parse.call_count, 7)
                self.assertEqual(native_lower.call_count, 5)
            native_parse.assert_any_call("TRUE")
            native_parse.assert_any_call("Straße")
            native_lower.assert_any_call("Straße")
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration, "parse_ascii_bool_text", return_value=None
            ):
                with patch.object(
                    native_acceleration, "ascii_lower", return_value=None
                ):
                    forced_fallback = [
                        variable_seeds_module._coerce_bool(value) for value in values
                    ]
        self.assertEqual(forced_fallback, expected)


class VariableSeedOfflineTests(unittest.TestCase):
    def test_offline_apply_records_seeded_variables(self) -> None:
        details = apply_variable_seeds_offline(
            [("StartupVolume", "25"), ('MountedFESfinger()<>"Eccentric[001]"', "0")],
        )
        self.assertTrue(details["variable_seed_ok"])
        self.assertEqual(details["seeded_variables"]["StartupVolume"], "25")

    def test_mock_runtime_set_variable_value_before_prepare(self) -> None:
        runtime = MockRuntimeController(runnable_methods=["Demo"])
        ok, errors = seed_simulation_values(
            runtime,
            [VariableSeed(name="StartupVolume", value="25")],
        )
        self.assertTrue(ok)
        self.assertEqual(errors, [])
        self.assertEqual(runtime.variables["StartupVolume"], "25")

    def test_seeds_json_placeholder_is_available(self) -> None:
        seeds = (("StartupVolume", "25"),)
        self.assertEqual(
            variable_seeds_as_json(seeds),
            '[{"name":"StartupVolume","value":"25"}]',
        )


if __name__ == "__main__":
    unittest.main()
