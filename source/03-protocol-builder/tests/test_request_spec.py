import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.protocol_ir import RUP_VARIABLE_SELECTOR_INSTRUCTIONS
from fluent_pipeline.generation_options import GenerationOptions
from fluent_pipeline.generation_options import normalize_generation_options
from fluent_pipeline.readiness_gates import readiness_gate_request_spec_approved
from fluent_pipeline.request_spec import (
    _check_ready_validation,
    build_request_spec,
    build_request_validation_diff,
    extract_intent_checks,
    load_request_spec,
    normalize_request_spec,
    request_verbatim_prompt,
    request_spec_generation_defaults,
    render_request_validation_diff_markdown,
    write_request_spec,
)


class RequestSpecTests(unittest.TestCase):
    def test_verbatim_prompt_roundtrips_without_normalizing_text(self):
        prompt = "  Make a NEW scipt with Odd CAPS,\n\tkeep teh exact wells!!!\n  pipettt from A1 -> B2?  "
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.spec.yaml"
            spec = build_request_spec(
                intent=prompt,
                protocol_name="verbatim prompt test",
                source_scripts=["DemoScript"],
            )

            write_request_spec(spec, path)
            loaded = load_request_spec(path)
            defaults = request_spec_generation_defaults(loaded)

            self.assertEqual(loaded["request"]["verbatim_prompt"], prompt)
            self.assertEqual(loaded["request"]["original_user_prompt"], prompt)
            self.assertEqual(defaults["intent"], prompt)

    def test_request_spec_roundtrips_yaml_and_feeds_generation_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.spec.yaml"
            spec = build_request_spec(
                intent="Transfer 20 uL from SourcePlate A1 to DestinationPlate B1",
                protocol_name="source to destination transfer",
                contexts=[{"name": "source-assay"}, {"name": "cleanup-assay"}],
                source_scripts=["source-assay:DemoScript"],
                pattern_refs=["pick_up_tips from DemoScript"],
                index_db=Path("build/index.sqlite"),
                pattern_ids=[12],
                pattern_queries=["aspirate"],
                source_script_rank=2,
                simulate=False,
                compile_xscr=False,
                approve_partial_zeia=True,
                fluent_method="Demo Method",
            )

            write_request_spec(spec, path)
            loaded = load_request_spec(path)
            defaults = request_spec_generation_defaults(loaded)

            self.assertEqual(loaded["schema_version"], "tecan.request_spec.v1")
            self.assertEqual(defaults["intent"], "Transfer 20 uL from SourcePlate A1 to DestinationPlate B1")
            self.assertEqual(defaults["protocol_name"], "source to destination transfer")
            self.assertEqual(defaults["contexts"], ["source-assay", "cleanup-assay"])
            self.assertEqual(defaults["source_scripts"], ["source-assay:DemoScript"])
            self.assertEqual(defaults["pattern_refs"], ["pick_up_tips from DemoScript"])
            self.assertEqual(defaults["pattern_ids"], ["12"])
            self.assertEqual(defaults["pattern_queries"], ["aspirate"])
            self.assertEqual(defaults["source_script_rank"], 2)
            self.assertFalse(defaults["simulate"])
            self.assertFalse(defaults["compile_xscr"])
            self.assertEqual(defaults["max_repair_iterations"], 1)
            self.assertFalse(defaults["strict_readiness"])
            self.assertTrue(defaults["approve_partial_zeia"])
            self.assertFalse(defaults["approve_deck_layout"])
            self.assertFalse(defaults["approve_command_inventory"])
            self.assertFalse(defaults["approve_unsupported_raw_xml"])
            self.assertEqual(defaults["generation_options"].approved_unsupported_command_ids, ())
            self.assertFalse(defaults["waive_checksum_recompute"])
            self.assertFalse(defaults["fluent_context_check"])
            self.assertEqual(defaults["fluent_provider"], "auto")
            self.assertEqual(defaults["fluent_timeout"], 180.0)
            self.assertFalse(defaults["allow_script_replacement"])
            self.assertEqual(defaults["fluent_method"], "Demo Method")
            self.assertIsInstance(defaults["generation_options"], GenerationOptions)

    def test_catalog_defaults_roundtrip_in_generation_defaults(self):
        spec = build_request_spec(intent="Transfer with defaults")
        spec["generation"]["catalog_defaults"] = {
            "Plate96": "96 Well Flat",
            "MCA100Box": "MCA96, 100ul, Box",
        }
        normalized = normalize_request_spec(spec)
        defaults = request_spec_generation_defaults(normalized)
        self.assertEqual(
            defaults["catalog_defaults"],
            {
                "Plate96": "96 Well Flat",
                "MCA100Box": "MCA96, 100ul, Box",
            },
        )

    def test_preserve_regeneration_baseline_roundtrips_explicitly(self):
        spec = build_request_spec(
            intent="Preserve this exact existing script",
            generation_options=GenerationOptions(
                preserve_regeneration_baseline=True,
            ),
        )
        defaults = request_spec_generation_defaults(spec)

        self.assertTrue(spec["generation"]["preserve_regeneration_baseline"])
        self.assertTrue(defaults["preserve_regeneration_baseline"])
        self.assertTrue(
            defaults["generation_options"].preserve_regeneration_baseline
        )

    def test_generation_options_roundtrip_in_request_spec(self):
        spec = build_request_spec(
            intent="Transfer with explicit generation options",
            max_repair_iterations=3,
            strict_readiness=True,
            apply_modeling=True,
            verification_prompt_rup="worktable",
            target_fluentcontrol_version="3.2",
            approve_partial_zeia=True,
            approve_deck_layout=True,
            approve_command_inventory=True,
            approve_unsupported_raw_xml=True,
            approved_unsupported_command_ids=["LabwareSectionInfo"],
            waive_checksum_recompute=True,
            fluent_context_check=True,
            fluent_provider="external",
            fluent_timeout=42.5,
        )

        generation = spec["generation"]
        self.assertEqual(generation["max_repair_iterations"], 3)
        self.assertTrue(generation["strict_readiness"])
        self.assertTrue(generation["apply_modeling"])
        self.assertEqual(generation["verification_prompt_rup"], "worktable")
        self.assertEqual(generation["target_fluentcontrol_version"], "3.2")
        self.assertTrue(generation["approve_partial_zeia"])
        self.assertTrue(generation["approve_deck_layout"])
        self.assertTrue(generation["approve_command_inventory"])
        self.assertTrue(generation["approve_unsupported_raw_xml"])
        self.assertEqual(generation["approved_unsupported_command_ids"], ["LabwareSectionInfo"])
        self.assertTrue(generation["waive_checksum_recompute"])
        self.assertTrue(generation["fluent_context_check"])
        self.assertEqual(generation["fluent_provider"], "external")
        self.assertEqual(generation["fluent_timeout"], 42.5)

    def test_target_fluentcontrol_below_3_6_forces_worktable_prompt_images(self):
        legacy = normalize_generation_options(
            {
                "verification_prompt_rup": "standard",
                "target_fluentcontrol_version": "FluentControl 3.4 SP1",
            }
        )
        current = normalize_generation_options(
            {
                "verification_prompt_rup": "standard",
                "target_fluentcontrol_version": "3.6",
            }
        )

        self.assertEqual(legacy.verification_prompt_rup, "worktable")
        self.assertEqual(current.verification_prompt_rup, "standard")

    def test_normalize_request_spec_syncs_partial_zeia_approval_locations(self):
        normalized = normalize_request_spec(
            {
                "request": {"intent": "Sync partial ZEIA approval"},
                "source": {
                    "full_zeia_export": {
                        "approved_partial_zeia": False,
                    }
                },
                "generation": {
                    "approve_partial_zeia": True,
                },
            }
        )

        self.assertTrue(normalized["generation"]["approve_partial_zeia"])
        self.assertTrue(normalized["source"]["full_zeia_export"]["approved_partial_zeia"])

    def test_verification_recipe_selector_instructions_are_shortened(self):
        spec = normalize_request_spec(
            {
                "request": {"intent": "recipe test"},
                "verification_recipe": {
                    "category_selector_instructions": (
                        'For each test, leave it on "yes" to run it or set it to "no" to skip it. '
                        "Operator deck-load setup and the final sign-off always run."
                    ),
                    "groups": [
                        {
                            "name": "Operator setup",
                            "steps": [{"prompt": "Confirm deck load."}],
                        }
                    ],
                },
            }
        )

        self.assertEqual(
            spec["verification_recipe"]["category_selector_instructions"],
            RUP_VARIABLE_SELECTOR_INSTRUCTIONS,
        )

    def test_verification_recipe_pre_movement_prompts_use_concise_operator_wording(self):
        spec = normalize_request_spec(
            {
                "request": {"intent": "recipe test"},
                "verification_recipe": {
                    "groups": [
                        {
                            "name": "Arm verification",
                            "steps": [
                                {
                                    "prompt": (
                                        "Next: the RGA arm will present its fingers for inspection. "
                                        "Keep hands clear of the deck, then press OK."
                                    )
                                }
                            ],
                        }
                    ],
                },
            }
        )

        self.assertEqual(
            spec["verification_recipe"]["groups"][0]["steps"][0]["prompt"],
            "Next: the arm will move to present its fingers for inspection. Press Continue to proceed.",
        )

    def test_verification_recipe_setup_prompts_pass_through_operator_text(self):
        """Keep ZEIA/recipe setup wording; do not invent lab Put… rewrites."""
        prompts = [
            "Confirm the collection plate is in the correct bottom-stack position.",
            "Confirm the red A200 adaptor is on the deck in the correct nest and seated flat.",
            "Confirm the source cap holder for 50 mL Falcon caps is on the deck in the correct position.",
            "Confirm the yellow cap holder is on the deck in the correct position.",
            "Confirm the first capped 50 mL source tube is loaded in the tube runner.",
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt):
                spec = normalize_request_spec(
                    {
                        "request": {"intent": "recipe test"},
                        "verification_recipe": {
                            "groups": [
                                {
                                    "name": "Operator setup",
                                    "steps": [{"prompt": prompt}],
                                }
                            ],
                        },
                    }
                )
                self.assertEqual(
                    spec["verification_recipe"]["groups"][0]["steps"][0]["prompt"],
                    prompt,
                )

    def test_validation_diff_compares_request_to_ir_and_artifacts(self):
        spec = build_request_spec(
            intent="Transfer 20 uL from SourcePlate A1 to DestinationPlate B1",
            source_scripts=["DemoScript"],
            pattern_refs=["aspirate from DemoScript"],
        )
        spec["source"]["full_zeia_export"].update(
            {
                "status": "likely_full_export",
                "accepted": True,
            }
        )
        spec["source"]["host_instrument_configuration"].update(
            {
                "status": "inferred",
                "patterns": ["RGA"],
            }
        )
        spec["review"]["state"] = "approved"
        ir = {
            "id": "transfer",
            "protocol": {
                "name": "Transfer",
                "comment": "Transfer 20 uL from SourcePlate A1 to DestinationPlate B1",
            },
            "source": {
                "intent": "Transfer 20 uL from SourcePlate A1 to DestinationPlate B1",
                "selected_source_scripts": [{"object_name": "DemoScript"}],
                "selected_patterns": ["aspirate from DemoScript"],
                "host_instrument_configuration": {
                    "status": "matched",
                    "matches": ["780_liqFCA_MCA_RGA"],
                    "installed_configs": ["780_liqFCA_MCA_RGA"],
                },
            },
            "labware": [{"label": "SourcePlate"}],
            "liquid_classes": [{"name": "Water Free Single"}],
            "steps": [{"operation": "aspirate"}],
        }
        diff = build_request_validation_diff(
            request_spec=spec,
            protocol_ir=ir,
            request_spec_path=Path("request.spec.yaml"),
            protocol_ir_path=Path("protocol.ir.json"),
            generated_files={
                "ir": "protocol.ir.json",
                "python": "protocol.py",
                "recreate": "RECREATE_SCRIPT.md",
                "worktable_changes": "worktable_changes.md",
                "worktable_patch": "worktable.patch.json",
            },
            worktable_diff={"summary": {"overall_severity": "safe"}},
            validation_report={"ready": True, "passed_count": 18, "gate_count": 18, "failed_count": 0},
        )

        self.assertEqual(diff["status"], "passed")
        self.assertEqual(diff["protocol_ir"]["step_count"], 1)
        markdown = render_request_validation_diff_markdown(diff)
        self.assertIn("# Request Validation Diff", markdown)
        self.assertIn("`passed` ready_validation", markdown)

    def test_verbatim_prompt_defaults_to_intent_and_roundtrips(self):
        prompt = "Line one\n\tLine two with operator wording\nLine three"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.spec.yaml"
            spec = build_request_spec(intent=prompt)

            self.assertEqual(request_verbatim_prompt(spec), prompt)
            self.assertEqual(spec["request"]["original_user_prompt"], prompt)

            write_request_spec(spec, path)
            loaded = load_request_spec(path)

            self.assertEqual(request_verbatim_prompt(loaded), prompt)
            self.assertEqual(loaded["request"]["intent"], prompt)


class IntentCheckExtractionTests(unittest.TestCase):
    """extract_intent_checks seeds acceptance.required_checks from free-text requests."""

    INTENT = (
        "RGA Mainly Verification script\n"
        "Arm Verification\n"
        "Make sure that the fingers on the arm are parallel and not diagonal\n"
        "Check A200 positioning is correct.\n"
        "First Tube (TubeEye/Vial Gripper)\n"
        "Did the ID pop up and get read properly?\n"
        "Make sure the cap gets back on properly\n"
    )

    def test_keeps_instruction_and_question_lines(self):
        checks = extract_intent_checks(self.INTENT)
        self.assertIn("Make sure that the fingers on the arm are parallel and not diagonal", checks)
        self.assertIn("Check A200 positioning is correct.", checks)
        self.assertIn("Did the ID pop up and get read properly?", checks)
        self.assertIn("Make sure the cap gets back on properly", checks)

    def test_drops_short_section_headers(self):
        checks = extract_intent_checks(self.INTENT)
        self.assertNotIn("Arm Verification", checks)
        self.assertNotIn("First Tube (TubeEye/Vial Gripper)", checks)

    def test_deduplicates_and_handles_empty(self):
        self.assertEqual(extract_intent_checks(""), [])
        self.assertEqual(extract_intent_checks(None), [])
        dupes = extract_intent_checks("Make sure the cap closes\nMake sure the cap closes\n")
        self.assertEqual(dupes, ["Make sure the cap closes"])

    def test_normalize_defaults_required_checks_empty(self):
        spec = normalize_request_spec({"request": {"intent": "x"}})
        self.assertEqual(spec["acceptance"]["required_checks"], [])
        self.assertFalse(spec["acceptance"]["enforce_prompt_coverage"])

    def test_deck_layout_review_slot_defaults_and_reads_as_approval_record(self):
        spec = build_request_spec(intent="x")
        self.assertFalse(spec["review"]["deck_layout"])
        self.assertFalse(readiness_gate_request_spec_approved(spec, "deck_layout_consistent"))

        spec["review"]["deck_layout"] = True
        self.assertTrue(readiness_gate_request_spec_approved(spec, "deck_layout_consistent"))


class PromptOnlyTrivialPassTests(unittest.TestCase):
    """The trivial-pass 'confirm empty result' warning auto-resolves when prompt-only."""

    REPORT = {
        "ready": True,
        "passed_count": 28,
        "gate_count": 28,
        "failed_count": 0,
        "trivial_pass_gates": ["well_ranges_valid", "tip_capacity_valid"],
    }

    def test_prompt_only_default_is_none(self):
        spec = normalize_request_spec({"request": {"intent": "x"}})
        self.assertIsNone(spec["generation"]["prompt_only"])

    def test_check_keeps_warning_without_prompt_only(self):
        check = _check_ready_validation(self.REPORT)
        self.assertEqual(check["status"], "passed")
        self.assertIn("Confirm an empty result matches intent", check["summary"])

    def test_check_auto_resolves_when_prompt_only(self):
        check = _check_ready_validation(self.REPORT, prompt_only=True)
        self.assertEqual(check["status"], "passed")
        self.assertNotIn("Confirm an empty result matches intent", check["summary"])
        self.assertIn("prompt-only", check["summary"])
        self.assertTrue(check["details"].get("prompt_only"))

    def _diff_for_ir(self, ir, *, generation=None):
        spec = build_request_spec(intent="RGA verification, user prompts for everything")
        if generation:
            spec["generation"].update(generation)
        return build_request_validation_diff(
            request_spec=spec,
            protocol_ir=ir,
            request_spec_path=Path("request.spec.yaml"),
            protocol_ir_path=Path("protocol.ir.json"),
            generated_files={
                "ir": "protocol.ir.json",
                "python": "protocol.py",
                "recreate": "RECREATE_SCRIPT.md",
                "worktable_changes": "worktable_changes.md",
                "worktable_patch": "worktable.patch.json",
            },
            worktable_diff={"summary": {"overall_severity": "safe"}},
            validation_report=self.REPORT,
        )

    def test_diff_auto_detects_prompt_only_ir(self):
        ir = {
            "protocol": {"name": "RGA Verification"},
            "steps": [
                {"operation": "prompt_user"},
                {"operation": "call_subroutine"},
                {"operation": "comment"},
            ],
        }
        diff = self._diff_for_ir(ir)
        ready = next(c for c in diff["checks"] if c["id"] == "ready_validation")
        self.assertIn("prompt-only", ready["summary"])

    def test_diff_keeps_warning_for_liquid_handling_ir(self):
        ir = {
            "protocol": {"name": "Serial Dilution"},
            "steps": [{"operation": "prompt_user"}, {"operation": "aspirate"}],
        }
        diff = self._diff_for_ir(ir)
        ready = next(c for c in diff["checks"] if c["id"] == "ready_validation")
        self.assertIn("Confirm an empty result matches intent", ready["summary"])

    def test_explicit_false_declaration_overrides_detection(self):
        ir = {
            "protocol": {"name": "RGA Verification"},
            "steps": [{"operation": "prompt_user"}],
        }
        diff = self._diff_for_ir(ir, generation={"prompt_only": False})
        ready = next(c for c in diff["checks"] if c["id"] == "ready_validation")
        self.assertIn("Confirm an empty result matches intent", ready["summary"])


if __name__ == "__main__":
    unittest.main()
