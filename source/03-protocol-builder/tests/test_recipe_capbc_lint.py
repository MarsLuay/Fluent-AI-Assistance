"""Tests for CapBC / ScanTubes verification-recipe lint warnings."""

import unittest

from fluent_pipeline.recipe_capbc_lint import explicit_gripper_values_from_recipe
from fluent_pipeline.spec_lint import lint_request_spec


def _capbc_recipe(**overrides):
    recipe = {
        "worktable": "Demo_WT",
        "groups": [
            {
                "name": "Cap/scan",
                "description": "Verifies tube cap removal, barcode scan, and recap.",
                "steps": [
                    {
                        "subroutine": {
                            "name": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2",
                            "variable_mappings_start": [
                                {"target": "InputSubLocation", "source": "TubeLocationName"},
                            ],
                        }
                    }
                ],
            }
        ],
    }
    recipe.update(overrides)
    return {
        "schema_version": "tecan.request_spec.v1",
        "request": {"intent": "CapBC verification"},
        "source": {},
        "verification_recipe": recipe,
    }


class RecipeCapbcLintTests(unittest.TestCase):
    def test_warns_when_capbc_without_source_scripts(self):
        result = lint_request_spec(_capbc_recipe())
        self.assertTrue(any(f.location == "source.source_scripts" for f in result.warnings))
        self.assertTrue(any("CapBC" in f.message for f in result.warnings))

    def test_warns_when_gripper_would_stay_zero(self):
        result = lint_request_spec(_capbc_recipe())
        self.assertTrue(
            any(f.location == "verification_recipe" and "GripperClose" in f.message for f in result.warnings)
        )

    def test_warns_when_input_sub_location_unresolvable(self):
        result = lint_request_spec(_capbc_recipe())
        self.assertTrue(
            any("InputSubLocation" in f.message and "variable_mappings_start" in f.location for f in result.warnings)
        )

    def test_no_gripper_warning_when_recipe_declares_values(self):
        spec = _capbc_recipe(
            capbc_prep={"GripperClose": 11, "GripperOpen": 22},
            tube_deck_location="Demo_Tube_Pos_1",
        )
        result = lint_request_spec(spec)
        self.assertFalse(
            any(
                f.location == "verification_recipe" and "GripperClose" in f.message
                for f in result.warnings
            )
        )

    def test_no_gripper_warning_when_source_scripts_set(self):
        spec = _capbc_recipe(tube_deck_location="Demo_Tube_Pos_1")
        spec["source"] = {"source_scripts": ["Demo_Script_2_50mL_v3.2"]}
        result = lint_request_spec(spec)
        self.assertFalse(any("GripperClose" in f.message for f in result.warnings))

    def test_no_input_sub_location_warning_when_context_set(self):
        spec = _capbc_recipe()
        spec["source"] = {"context": "full-export-demo"}
        result = lint_request_spec(spec)
        self.assertFalse(any("InputSubLocation" in f.message for f in result.warnings))

    def test_no_input_sub_location_warning_when_deck_location_set(self):
        spec = _capbc_recipe(
            tube_deck_location="Demo_Tube_Pos_1",
            capbc_prep={"GripperClose": 11, "GripperOpen": 22},
        )
        result = lint_request_spec(spec)
        self.assertFalse(any("InputSubLocation" in f.message for f in result.warnings))

    def test_scantubes_also_triggers_source_script_warning(self):
        spec = _capbc_recipe()
        spec["verification_recipe"]["groups"][0]["steps"] = [
            {"subroutine": "Demo\\SUB_ScanTubes_50mL_v2"},
        ]
        result = lint_request_spec(spec)
        self.assertTrue(any("ScanTubes" in f.message for f in result.warnings))

    def test_explicit_gripper_from_prep_steps(self):
        values = explicit_gripper_values_from_recipe(
            {
                "prep_steps": [
                    {"set_variable": {"variable": "GripperClose", "value": 11}},
                    {"set_variable": {"variable": "GripperOpen", "value": 22}},
                ]
            }
        )
        self.assertEqual(values["GripperClose"], 11)
        self.assertEqual(values["GripperOpen"], 22)

    def test_clean_capbc_recipe_with_source_and_overrides(self):
        spec = _capbc_recipe(
            tube_deck_location="Demo_Tube_Pos_1",
            capbc_prep={"GripperClose": 11, "GripperOpen": 22},
        )
        spec["source"] = {
            "context": "demo",
            "source_scripts": ["Demo_Script_2_50mL_v3.2"],
        }
        result = lint_request_spec(spec)
        capbc_warnings = [
            f
            for f in result.warnings
            if f.location.startswith(("source.source_scripts", "verification_recipe"))
            or "InputSubLocation" in f.message
        ]
        self.assertEqual(capbc_warnings, [])


if __name__ == "__main__":
    unittest.main()
