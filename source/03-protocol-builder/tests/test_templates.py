import unittest
from copy import deepcopy
from pathlib import Path

from fluent_pipeline.protocol_ir import CANONICAL_IR_VERSION, render_python_draft, render_recreate_markdown
from fluent_pipeline.protocol_ir_schema import validate_protocol_ir_document
from fluent_pipeline.request_spec import load_request_spec
from fluent_pipeline.template_library import (
    ground_template_ir,
    list_templates,
    load_request_schema,
    load_template_ir,
    template_info,
    template_path,
)


EXPECTED_TEMPLATES = {
    "plate_transfer",
    "serial_dilution",
    "normalization",
    "reagent_addition",
    "bead_cleanup",
    "worklist_execution",
    "tip_strategy_test",
}


def _plate_transfer_request() -> dict:
    return {
        "request": {
            "intent": "Transfer the reviewed volume between imported-context labware.",
            "protocol_name": "Synthetic grounded transfer",
        },
        "template": {
            "name": "plate_transfer",
            "parameters": {
                "source_plate": "Synthetic source",
                "destination_plate": "Synthetic destination",
                "source_well": "A1",
                "destination_well": "B1",
                "transfer_volume_ul": 25,
                "liquid_class": "Synthetic liquid class",
                "tip_box": "Synthetic tips",
            },
        },
    }


def _synthetic_context(*, extra_workspace: bool = False) -> dict:
    labels = [
        "Synthetic source",
        "Synthetic destination",
        "Synthetic tips",
        "Synthetic dilution plate",
        "Synthetic diluent",
        "Synthetic input plate",
        "Synthetic normalized plate",
        "Synthetic reagent reservoir",
        "Synthetic sample plate",
        "Synthetic bead reservoir",
        "Synthetic wash reservoir",
        "Synthetic elution buffer",
        "Synthetic elution plate",
        "Synthetic waste",
        "Synthetic water reservoir",
        "Synthetic test plate",
        "Synthetic returned tips",
    ]
    workspaces = [
        {
            "name": "Synthetic worktable",
            "guid": "11111111-2222-4333-8444-555555555555",
            "placements": [
                {
                    "label": label,
                    "catalog": f"Synthetic catalog {index}",
                    "site_name": "Synthetic tip nest",
                    "position_label": index,
                }
                for index, label in enumerate(labels, start=1)
            ],
        }
    ]
    if extra_workspace:
        workspaces.append(
            {
                "name": "Other synthetic worktable",
                "guid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
                "placements": [],
            }
        )
    return {
        "name": "synthetic-context",
        "liquid_classes": ["Synthetic liquid class"],
        "worklist_paths": ["synthetic/input.gwl"],
        "worktable_geometry": {"workspaces": workspaces},
    }


def _inventory_template_requests() -> dict[str, dict]:
    common = {
        "request": {
            "intent": "Exercise one reviewed template against synthetic imported evidence.",
            "protocol_name": "Synthetic grounded template",
        }
    }
    parameters = {
        "serial_dilution": {
            "source_plate": "Synthetic source",
            "dilution_plate": "Synthetic dilution plate",
            "diluent_reservoir": "Synthetic diluent",
            "tip_box": "Synthetic tips",
            "sample_volume_ul": 12,
            "diluent_volume_ul": 88,
            "dilution_count": 6,
            "mix_cycles": 7,
            "liquid_class": "Synthetic liquid class",
        },
        "normalization": {
            "input_plate": "Synthetic input plate",
            "normalized_plate": "Synthetic normalized plate",
            "diluent_reservoir": "Synthetic diluent",
            "tip_box": "Synthetic tips",
            "target_concentration": 12,
            "final_volume_ul": 60,
            "concentration_map": "synthetic/concentrations.csv",
            "liquid_class": "Synthetic liquid class",
        },
        "reagent_addition": {
            "reagent_name": "Synthetic reagent",
            "reagent_reservoir": "Synthetic reagent reservoir",
            "destination_plate": "Synthetic destination",
            "tip_box": "Synthetic tips",
            "reagent_volume_ul": 22,
            "mix_after_addition": True,
            "liquid_class": "Synthetic liquid class",
        },
        "bead_cleanup": {
            "sample_plate": "Synthetic sample plate",
            "bead_reservoir": "Synthetic bead reservoir",
            "wash_reservoir": "Synthetic wash reservoir",
            "elution_buffer_reservoir": "Synthetic elution buffer",
            "elution_plate": "Synthetic elution plate",
            "waste_reservoir": "Synthetic waste",
            "tip_box": "Synthetic tips",
            "liquid_class": "Synthetic liquid class",
            "bead_volume_ul": 21,
            "wash_volume_ul": 140,
            "wash_count": 2,
            "elution_volume_ul": 35,
            "magnet_settle_time_min": 4,
            "dry_time_min": 2,
        },
        "worklist_execution": {
            "worklist_path": "synthetic/input.gwl",
            "worklist_format": "gwl",
            "source_plate": "Synthetic source",
            "destination_plate": "Synthetic destination",
            "tip_box": "Synthetic tips",
        },
        "tip_strategy_test": {
            "water_reservoir": "Synthetic water reservoir",
            "test_plate": "Synthetic test plate",
            "tip_box": "Synthetic tips",
            "return_tip_box": "Synthetic returned tips",
            "waste": "Synthetic waste",
            "liquid_class": "Synthetic liquid class",
            "test_volume_ul": 11,
            "tip_strategy": "return_for_reuse",
            "allow_tip_reuse": True,
            "wash_scheme": "synthetic_wash",
        },
    }
    return {
        name: {**common, "template": {"name": name, "parameters": values}}
        for name, values in parameters.items()
    }


class TemplateLibraryTests(unittest.TestCase):
    def test_template_inventory_contains_expected_shapes(self):
        templates = list_templates()
        names = {item["name"] for item in templates}

        self.assertEqual(names, EXPECTED_TEMPLATES)
        for item in templates:
            self.assertTrue(Path(item["template_ir"]).exists())
            self.assertTrue(Path(item["request_schema"]).exists())
            self.assertGreater(item["step_count"], 0)

    def test_templates_are_valid_ir_and_renderable(self):
        for name in sorted(EXPECTED_TEMPLATES):
            with self.subTest(template=name):
                ir = load_template_ir(name)
                schema = load_request_schema(name)
                info = template_info(name)

                self.assertTrue(info["valid"], info["issues"])
                self.assertEqual(ir["ir_version"], CANONICAL_IR_VERSION)
                self.assertEqual(ir["template"]["name"], name)
                self.assertEqual(schema["properties"]["template"]["properties"]["name"]["const"], name)
                self.assertGreaterEqual(len(ir["steps"]), 1)

                python = render_python_draft(ir)
                recreate = render_recreate_markdown(ir)
                self.assertIn("def build_worktable()", python)
                self.assertIn("# Recreate Script:", recreate)

    def test_template_examples_are_request_specs(self):
        for name in sorted(EXPECTED_TEMPLATES):
            with self.subTest(template=name):
                examples = sorted((template_path(name) / "examples").glob("*.request.spec.yaml"))
                self.assertTrue(examples)
                for example in examples:
                    spec = load_request_spec(example)
                    self.assertEqual(spec["schema_version"], "tecan.request_spec.v1")
                    self.assertEqual(spec["template"]["name"], name)
                    self.assertTrue(spec["request"]["intent"])

    def test_plate_transfer_grounding_uses_exact_imported_context_values(self):
        result = ground_template_ir(
            "plate_transfer",
            request_spec=_plate_transfer_request(),
            context=_synthetic_context(),
        )

        self.assertEqual(result["status"], "grounded")
        self.assertEqual(result["findings"], [])
        ir = result["ir"]
        self.assertIsNotNone(ir)
        self.assertEqual(ir["protocol"]["name"], "Synthetic grounded transfer")
        self.assertEqual(ir["worktable"]["name"], "Synthetic worktable")
        self.assertEqual(ir["worktable"]["guid"], "11111111-2222-4333-8444-555555555555")
        self.assertEqual(
            {item["label"] for item in ir["labware"]},
            {"Synthetic source", "Synthetic destination", "Synthetic tips"},
        )
        self.assertEqual(ir["liquid_classes"], [{"name": "Synthetic liquid class", "role": "pipetting"}])
        self.assertEqual(ir["steps"][4]["volume_ul"], 25)
        self.assertEqual(ir["steps"][4]["parameters"]["source_well"], "A1")
        self.assertEqual(ir["steps"][5]["parameters"]["destination_well"], "B1")

    def test_plate_transfer_template_has_no_operational_defaults(self):
        ir = load_template_ir("plate_transfer")

        self.assertEqual(ir["worktable"]["name"], "")
        self.assertEqual(ir["worktable"]["guid"], "")
        self.assertEqual(ir["liquid_classes"], [])
        self.assertTrue(all(item.get("catalog") == "" for item in ir["labware"]))
        self.assertTrue(all(item.get("position") is None for item in ir["labware"]))
        self.assertNotIn("Water Free Single", str(ir))
        self.assertNotIn("96 Well Flat", str(ir))

    def test_plate_transfer_grounding_fails_closed_without_context_evidence(self):
        result = ground_template_ir(
            "plate_transfer",
            request_spec=_plate_transfer_request(),
            context={"name": "synthetic-empty-context"},
        )

        self.assertEqual(result["status"], "needs_user")
        self.assertIsNone(result["ir"])
        codes = {finding["code"] for finding in result["findings"]}
        self.assertIn("worktable_not_found", codes)
        self.assertIn("liquid_class_not_in_context", codes)
        self.assertTrue(all(finding["next_action"] for finding in result["findings"]))

    def test_plate_transfer_grounding_infers_best_worktable_when_ambiguous(self):
        result = ground_template_ir(
            "plate_transfer",
            request_spec=_plate_transfer_request(),
            context=_synthetic_context(extra_workspace=True),
        )

        self.assertEqual(result["status"], "grounded", result["findings"])
        self.assertEqual(result["ir"]["worktable"]["name"], "Synthetic worktable")
        decision = next(
            item
            for item in result["inference"]["decisions"]
            if item["path"] == "$.template.parameters.worktable"
        )
        self.assertEqual(decision["origin"], "context_fallback")
        self.assertTrue(decision["review_required"])

    def test_inventory_templates_are_context_inert_at_rest(self):
        for name in sorted(EXPECTED_TEMPLATES - {"plate_transfer"}):
            with self.subTest(template=name):
                ir = load_template_ir(name)

                self.assertEqual(ir["worktable"]["name"], "")
                self.assertEqual(ir["worktable"]["guid"], "")
                self.assertTrue(all(item.get("catalog") == "" for item in ir["labware"]))
                self.assertTrue(all(item.get("location") == "" for item in ir["labware"]))
                self.assertTrue(all(item.get("position") is None for item in ir["labware"]))
                for step in ir["steps"]:
                    if step.get("operation") == "add_labware":
                        self.assertEqual(step["parameters"]["labware_type"], "")
                        self.assertEqual(step["parameters"]["location"], "")
                        self.assertIsNone(step["parameters"]["position"])
                self.assertNotIn("Water Free Single", str(ir))

    def test_inventory_templates_ground_and_render_from_exact_context(self):
        for name, request_spec in _inventory_template_requests().items():
            with self.subTest(template=name):
                result = ground_template_ir(
                    name,
                    request_spec=request_spec,
                    context=_synthetic_context(),
                )

                self.assertEqual(result["status"], "grounded", result["findings"])
                self.assertEqual(result["findings"], [])
                ir = result["ir"]
                self.assertIsNotNone(ir)
                self.assertEqual(ir["worktable"]["name"], "Synthetic worktable")
                self.assertTrue(all(item["catalog"].startswith("Synthetic catalog ") for item in ir["labware"]))
                self.assertNotIn("__GROUND_FROM_CONTEXT__", str(ir))
                self.assertNotIn("__GROUND_WORKLIST_FROM_CONTEXT__", str(ir))
                issues = [issue for issue in validate_protocol_ir_document(ir) if issue.severity == "error"]
                self.assertEqual(issues, [])
                self.assertIn("def build_worktable()", render_python_draft(ir))
                self.assertIn("# Recreate Script:", render_recreate_markdown(ir))

    def test_inventory_templates_fail_closed_on_missing_labware(self):
        first_labware_parameters = {
            "serial_dilution": "source_plate",
            "normalization": "input_plate",
            "reagent_addition": "reagent_reservoir",
            "bead_cleanup": "sample_plate",
            "worklist_execution": "source_plate",
            "tip_strategy_test": "water_reservoir",
        }
        for name, request_spec in _inventory_template_requests().items():
            with self.subTest(template=name):
                broken = deepcopy(request_spec)
                first_labware_parameter = first_labware_parameters[name]
                broken["template"]["parameters"][first_labware_parameter] = "Missing synthetic labware"

                result = ground_template_ir(name, request_spec=broken, context=_synthetic_context())

                self.assertEqual(result["status"], "needs_user")
                self.assertIsNone(result["ir"])
                self.assertIn("labware_not_in_worktable", {item["code"] for item in result["findings"]})

    def test_inventory_templates_infer_missing_required_parameter(self):
        for name, request_spec in _inventory_template_requests().items():
            with self.subTest(template=name):
                broken = deepcopy(request_spec)
                required = load_request_schema(name)["properties"]["template"]["properties"]["parameters"]["required"]
                broken["template"]["parameters"][required[-1]] = None

                result = ground_template_ir(name, request_spec=broken, context=_synthetic_context())

                self.assertEqual(result["status"], "grounded", result["findings"])
                self.assertIsNotNone(result["ir"])
                path = f"$.template.parameters.{required[-1]}"
                decision = next(item for item in result["inference"]["decisions"] if item["path"] == path)
                self.assertNotEqual(decision["origin"], "explicit")
                self.assertTrue(decision["review_required"])

    def test_all_templates_ground_from_task_and_context_with_omitted_parameters(self):
        for name in sorted(EXPECTED_TEMPLATES):
            with self.subTest(template=name):
                request = {
                    "request": {
                        "intent": f"Run the synthetic {name.replace('_', ' ')} task.",
                        "protocol_name": f"Synthetic inferred {name}",
                    },
                    "template": {"name": name, "parameters": {}},
                }

                result = ground_template_ir(name, request_spec=request, context=_synthetic_context())

                self.assertEqual(result["status"], "grounded", result["findings"])
                self.assertIsNotNone(result["ir"])
                self.assertGreater(result["inference"]["inferred_count"], 0)
                self.assertEqual(result["inference"]["unresolved_count"], 0)
                self.assertTrue(result["inference"]["review_required"])

    def test_worklist_template_rejects_path_not_imported_with_context(self):
        request_spec = _inventory_template_requests()["worklist_execution"]
        request_spec["template"]["parameters"]["worklist_path"] = "synthetic/missing.gwl"

        result = ground_template_ir(
            "worklist_execution",
            request_spec=request_spec,
            context=_synthetic_context(),
        )

        self.assertEqual(result["status"], "needs_user")
        self.assertIsNone(result["ir"])
        self.assertIn("worklist_not_in_context", {item["code"] for item in result["findings"]})


if __name__ == "__main__":
    unittest.main()
