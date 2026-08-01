import itertools
import json
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.cli import main
from fluent_pipeline.generation_workflow import _recipe_step_to_ir
from fluent_pipeline.request_spec import (
    RECIPE_STEP_SHORTHAND_KEYS,
    recipe_step_produces_ir,
    recipe_step_type,
)
from fluent_pipeline.spec_lint import (
    ERROR,
    WARNING,
    lint_request_spec,
    lint_request_spec_file,
    render_lint_report,
)


def _valid_recipe_spec():
    return {
        "schema_version": "tecan.request_spec.v1",
        "request": {"intent": "RGA verification script"},
        "source": {"context": "demo-context"},
        "verification_recipe": {
            "groups": [
                {
                    "name": "Setup",
                    "description": "Confirms operator setup before verification moves.",
                    "steps": [
                        {"comment": "Begin verification"},
                        {"prompt": "Confirm A200 is connected.", "instrument_init_check": True},
                        {"subroutine": "Demo\\SUB_Get_Fingers_v1.0"},
                        {"move": {"labware": "Plate"}},
                        {"type": "prompt", "text": "Explicit prompt"},
                    ],
                }
            ],
            "labware": [
                {"label": "Plate", "catalog": "MyPlate", "location": "Carrier", "site": 1}
            ],
        },
    }


def _locations(findings):
    return [f.location for f in findings]


def _messages(findings):
    return " ".join(f.message for f in findings)


class CleanSpecTests(unittest.TestCase):
    def test_clean_recipe_spec_has_no_findings(self):
        result = lint_request_spec(_valid_recipe_spec())
        self.assertEqual(result.errors, [], _messages(result.errors))
        self.assertEqual(result.warnings, [], _messages(result.warnings))
        self.assertTrue(result.ok)
        self.assertEqual(result.estimated_ir_body_steps, 5)

    def test_spec_without_recipe_is_ok(self):
        spec = {
            "schema_version": "tecan.request_spec.v1",
            "request": {"intent": "transfer 20uL"},
            "source": {"project_archives": ["base.zeia"]},
        }
        result = lint_request_spec(spec)
        self.assertTrue(result.ok)
        self.assertEqual(result.estimated_ir_body_steps, 0)


class RecipeErrorTests(unittest.TestCase):
    def test_zeia_preferred_label_catalog_mismatch_is_error(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["labware"].append(
            {
                "label": "AdapterA200",
                "catalog": "Adapter A200",
                "location": "Demo_Nest_Pos",
                "site": 1,
            }
        )

        result = lint_request_spec(
            spec,
            preferred_label_catalogs={"adaptera200": "Adapter A200_ElutionRack"},
        )

        self.assertIn(
            "verification_recipe.labware[1].catalog",
            _locations(result.errors),
        )
        self.assertIn("Adapter A200_ElutionRack", _messages(result.errors))

    def test_without_zeia_preferred_map_adapter_catalog_is_not_invented(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["labware"].append(
            {
                "label": "AdapterA200",
                "catalog": "Adapter A200",
                "location": "Demo_Nest_Pos",
                "site": 1,
            }
        )
        # No preferred map and no loadable context → no invented ElutionRack rule.
        result = lint_request_spec(spec, preferred_label_catalogs={})
        self.assertFalse(any("ElutionRack" in f.message for f in result.errors))

    def test_empty_groups_is_error(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["groups"] = []
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertIn("verification_recipe.groups", _locations(result.errors))

    def test_missing_groups_key_is_error(self):
        spec = _valid_recipe_spec()
        del spec["verification_recipe"]["groups"]
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertIn("verification_recipe.groups", _locations(result.errors))

    def test_all_empty_steps_groups_is_empty_ir_error(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["groups"] = [
            {"name": "Empty A", "steps": []},
            {"name": "Empty B", "steps": []},
        ]
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertEqual(result.estimated_ir_body_steps, 0)
        self.assertTrue(any("0 IR body steps" in f.message for f in result.errors))

    def test_malformed_step_not_mapping_is_error(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["groups"][0]["steps"] = ["just a string"]
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertTrue(any("must be a mapping" in f.message for f in result.errors))

    def test_step_without_recognized_key_is_error(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["groups"][0]["steps"] = [{"unknown_key": "value"}]
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertTrue(any("recognized shorthand" in f.message for f in result.errors))

    def test_unrecognized_type_is_error(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["groups"][0]["steps"] = [{"type": "teleport"}]
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertTrue(any("unrecognized step type" in f.message for f in result.errors))

    def test_empty_prompt_text_is_error(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["groups"][0]["steps"] = [
            {"prompt": "   "},
            {"prompt": "real prompt"},
        ]
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertTrue(any("empty text" in f.message for f in result.errors))
        # The empty prompt still counts as a body step (matches the IR builder).
        self.assertEqual(result.estimated_ir_body_steps, 2)

    def test_subroutine_without_name_is_error(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["groups"][0]["steps"] = [{"subroutine": ""}]
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertTrue(any("no subroutine name" in f.message for f in result.errors))
        # A nameless subroutine emits nothing, so this is also the empty-IR trap.
        self.assertEqual(result.estimated_ir_body_steps, 0)


class LabwareTests(unittest.TestCase):
    def test_labware_missing_label_is_error(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["labware"] = [{"catalog": "Plate"}]
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertTrue(any("missing 'label'" in f.message for f in result.errors))

    def test_labware_missing_catalog_is_warning(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["labware"] = [
            {"label": "Plate", "location": "Carrier", "site": 1}
        ]
        result = lint_request_spec(spec)
        self.assertTrue(result.ok)
        self.assertTrue(any(f.severity == WARNING and "catalog" in f.message for f in result.warnings))

    def test_labware_missing_location_is_warning(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["labware"] = [{"label": "Plate", "catalog": "MyPlate"}]
        result = lint_request_spec(spec)
        self.assertTrue(result.ok)
        self.assertTrue(any("location" in f.message for f in result.warnings))


class IntentAndTypeTests(unittest.TestCase):
    def test_missing_intent_is_error(self):
        result = lint_request_spec({"schema_version": "tecan.request_spec.v1", "request": {}})
        self.assertFalse(result.ok)
        self.assertIn("request.intent", _locations(result.errors))

    def test_non_mapping_spec_is_error(self):
        result = lint_request_spec(["not", "a", "mapping"])
        self.assertFalse(result.ok)
        self.assertIn("<root>", _locations(result.errors))

    def test_unsupported_version_is_error(self):
        spec = _valid_recipe_spec()
        spec["schema_version"] = "tecan.request_spec.v999"
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertIn("schema_version", _locations(result.errors))

    def test_bad_prompt_only_type_is_warning(self):
        spec = _valid_recipe_spec()
        spec["generation"] = {"prompt_only": "yes"}
        result = lint_request_spec(spec)
        self.assertTrue(result.ok)
        self.assertIn("generation.prompt_only", _locations(result.warnings))

    def test_preserve_regeneration_baseline_requires_boolean(self):
        spec = _valid_recipe_spec()
        spec["generation"] = {"preserve_regeneration_baseline": "yes"}
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertIn(
            "generation.preserve_regeneration_baseline",
            _locations(result.errors),
        )

    def test_bad_required_checks_type_is_warning(self):
        spec = _valid_recipe_spec()
        spec["acceptance"] = {"required_checks": "should-be-list", "enforce_prompt_coverage": 1}
        result = lint_request_spec(spec)
        self.assertTrue(result.ok)
        self.assertIn("acceptance.required_checks", _locations(result.warnings))
        self.assertIn("acceptance.enforce_prompt_coverage", _locations(result.warnings))

    def test_missing_source_is_warning(self):
        spec = {
            "schema_version": "tecan.request_spec.v1",
            "request": {"intent": "do a thing"},
        }
        result = lint_request_spec(spec)
        self.assertTrue(result.ok)
        self.assertIn("source", _locations(result.warnings))


class ClassifierAlignmentTests(unittest.TestCase):
    """recipe_step_produces_ir must match the real IR builder's accept/reject."""

    STEPS = [
        {"comment": "x"},
        {"comment": ""},
        {"prompt": "x"},
        {"prompt": ""},
        {"query": {"variable": "TubeCount", "prompt": "How many tubes?"}},
        {"query_variable": {"variable": "TubeCount", "prompt": "How many tubes?"}},
        {"runtime_variable_prompt": {"variables": [{"name": "TubeCount"}]}},
        {"execute_vb_script": {"script": "Run"}},
        {"subroutine": "Sub"},
        {"subroutine": ""},
        {"subroutine": {"name": "Sub"}},
        {"subroutine": {"name": ""}},
        {"move": {"labware": "P"}},
        {"manual_move": {"labware": "P"}},
        {"verified_move": {"labware": "P"}},
        {"type": "prompt", "text": "x"},
        {"type": "comment", "comment": "x"},
        {"type": "subroutine", "name": "Sub"},
        {"type": "move", "labware": "P"},
        {"type": "teleport"},
        {"type": ""},
        {"unknown": "key"},
        "not-a-dict",
        {},
    ]

    def test_all_shorthand_keys_resolve_to_a_supported_type(self):
        expected = {
            "query": "query_variable",
            "manual_move": "move",
            "verified_move": "move",
        }
        for key in RECIPE_STEP_SHORTHAND_KEYS:
            self.assertEqual(recipe_step_type({key: {}}), expected.get(key, key))

    def test_query_shorthand_builds_query_variable_ir(self):
        counter = itertools.count(1)

        def _next_step():
            idx = next(counter)
            return idx, f"step_{idx:03d}"

        built = _recipe_step_to_ir(
            {"query": {"variable": "TubeCount", "prompt": "How many tubes?"}},
            "Group",
            _next_step,
            [],
        )

        self.assertIsNotNone(built)
        assert built is not None
        self.assertEqual(built["operation"], "query_variable")

    def test_empty_subroutine_does_not_produce_ir(self):
        self.assertFalse(recipe_step_produces_ir({"subroutine": ""}))

    def test_alignment(self):
        for step in self.STEPS:
            counter = itertools.count(1)

            def _next_step():
                idx = next(counter)
                return idx, f"step_{idx:03d}"

            built = _recipe_step_to_ir(step, "Group", _next_step, [])
            self.assertEqual(
                recipe_step_produces_ir(step),
                built is not None,
                f"mismatch for step: {step!r}",
            )


class FileAndCliTests(unittest.TestCase):
    def _write(self, tmp, payload, name="request.spec.json"):
        path = Path(tmp) / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_lint_file_reports_findings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, _valid_recipe_spec())
            result = lint_request_spec_file(path)
            self.assertTrue(result.ok)

    def test_lint_missing_file_is_error(self):
        result = lint_request_spec_file(Path("does-not-exist.spec.yaml"))
        self.assertFalse(result.ok)

    def test_cli_exit_zero_for_clean_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, _valid_recipe_spec())
            self.assertEqual(main(["validate-spec", str(path)]), 0)

    def test_cli_exit_nonzero_for_empty_recipe(self):
        with tempfile.TemporaryDirectory() as tmp:
            spec = _valid_recipe_spec()
            spec["verification_recipe"]["groups"] = []
            path = self._write(tmp, spec)
            self.assertEqual(main(["validate-spec", str(path)]), 1)

    def test_render_report_strings(self):
        result = lint_request_spec(_valid_recipe_spec())
        report = render_lint_report(result, source="x")
        self.assertIn("Result: OK", report)

    def test_catalog_defaults_valid_mapping_passes(self):
        spec = _valid_recipe_spec()
        spec["generation"] = {"catalog_defaults": {"Plate96": "96 Well Flat"}}
        result = lint_request_spec(spec)
        self.assertTrue(result.ok)

    def test_catalog_defaults_rejects_unknown_class(self):
        spec = _valid_recipe_spec()
        spec["generation"] = {"catalog_defaults": {"NotAClass": "96 Well Flat"}}
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertIn("generation.catalog_defaults.'NotAClass'", _locations(result.errors))

    def test_catalog_defaults_rejects_non_mapping(self):
        spec = _valid_recipe_spec()
        spec["generation"] = {"catalog_defaults": ["Plate96"]}
        result = lint_request_spec(spec)
        self.assertFalse(result.ok)
        self.assertIn("generation.catalog_defaults", _locations(result.errors))

    def test_prompt_media_boilerplate_is_warning(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["groups"][0]["steps"][1] = {
            "prompt": (
                "Confirm run. Reference images and videos for this prompt will be attached later."
            ),
        }
        result = lint_request_spec(spec)
        self.assertTrue(result.ok)
        warnings = [f for f in result.warnings if f.severity == WARNING]
        self.assertTrue(any("media-attachment boilerplate" in f.message for f in warnings))

    def test_recipe_prompt_step_normalizes_media_boilerplate(self):
        counter = itertools.count(1)

        def _next_step():
            idx = next(counter)
            return idx, f"step_{idx:03d}"

        step = _recipe_step_to_ir(
            {
                "prompt": (
                    "Make sure A200 is ready. Watch the operator reference media when it is "
                    "attached, then confirm the screen finished."
                )
            },
            "Setup",
            _next_step,
            [],
        )
        self.assertIsNotNone(step)
        assert step is not None
        self.assertNotIn("reference media", step["parameters"]["prompt"].casefold())
        self.assertNotIn("attached later", step["parameters"]["prompt"].casefold())

    def test_external_initialization_prompt_requires_instrument_init_check(self):
        spec = _valid_recipe_spec()
        spec["verification_recipe"]["groups"][0]["steps"][1] = {
            "prompt": "Make sure A200 is actually connected and initialized.",
            "plain_prompt": True,
        }
        result = lint_request_spec(spec)
        self.assertTrue(result.ok)
        self.assertTrue(
            any("instrument_init_check: true" in finding.message for finding in result.warnings)
        )


if __name__ == "__main__":
    unittest.main()
