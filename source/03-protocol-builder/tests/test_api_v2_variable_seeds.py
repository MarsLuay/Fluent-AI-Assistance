import unittest

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
