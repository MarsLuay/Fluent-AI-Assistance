import unittest
from pathlib import Path

from fluent_pipeline.protocol_ir import CANONICAL_IR_VERSION, render_python_draft, render_recreate_markdown
from fluent_pipeline.request_spec import load_request_spec
from fluent_pipeline.template_library import (
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


if __name__ == "__main__":
    unittest.main()
