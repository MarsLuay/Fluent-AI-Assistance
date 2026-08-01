import unittest

from fluent_pipeline.generation_workflow import _recipe_step_to_ir, build_ir_from_recipe
from fluent_pipeline.interactive_script import (
    prepare_interactive_recipe,
    request_wants_interactive_script,
    variable_name_from_question,
)
from fluent_pipeline.protocol_ir import render_python_draft


class InteractiveScriptTests(unittest.TestCase):
    def test_request_wants_interactive_script_from_generation_flag(self):
        self.assertTrue(
            request_wants_interactive_script(
                {"generation": {"interactive": True}},
                intent="verify deck layout",
            )
        )

    def test_request_wants_interactive_script_from_intent(self):
        self.assertTrue(
            request_wants_interactive_script(
                {},
                intent="Make a script that asks the operator how many tubes to run",
            )
        )

    def test_prepare_interactive_recipe_promotes_question_prompt(self):
        recipe = {
            "groups": [
                {
                    "name": "Input",
                    "steps": [
                        {"prompt": "How many tubes will you run?"},
                        {"prompt": "Confirm the deck looks correct.", "deck_presence_check": True},
                    ],
                }
            ],
            "variables": [],
        }
        prepared = prepare_interactive_recipe(
            recipe,
            request_spec={"generation": {"interactive": True}},
            intent="interactive tube count script",
        )
        steps = prepared["groups"][0]["steps"]
        self.assertEqual(steps[0]["type"], "query_variable")
        self.assertEqual(steps[0]["variable"], "HowManyTubesWillYouRun")
        self.assertEqual(steps[1]["prompt"], "Confirm the deck looks correct.")

    def test_recipe_query_variable_step_builds_ir(self):
        step = {
            "query_variable": {
                "variable": "TubeCount",
                "prompt": "How many tubes?",
                "minimum": 1,
                "maximum": 96,
            }
        }
        built = _recipe_step_to_ir(step, "Input", lambda: (1, "step_001"), [])
        self.assertIsNotNone(built)
        assert built is not None
        self.assertEqual(built["operation"], "query_variable")
        self.assertEqual(built["parameters"]["variable"], "TubeCount")
        self.assertEqual(built["parameters"]["minimum"], 1)

    def test_build_ir_from_interactive_recipe_renders_query_variable_python(self):
        recipe = prepare_interactive_recipe(
            {
                "worktable": "StubWorkspace",
                "groups": [{"name": "Input", "steps": [{"prompt": "How many plates?"}]}],
                "variables": [],
            },
            request_spec={"generation": {"interactive": True}},
            intent="interactive plate count",
        )
        ir = build_ir_from_recipe(recipe, intent="interactive plate count", context=None)
        query_steps = [step for step in ir["steps"] if step.get("operation") == "query_variable"]
        self.assertEqual(len(query_steps), 1)
        rendered = render_python_draft(ir)
        self.assertIn("wt.raw_xml_step('QueryVariableStatement'", rendered)
        self.assertIn("HowManyPlates", rendered)
        self.assertIn("How many plates?", rendered)

    def test_variable_name_from_question(self):
        self.assertEqual(
            variable_name_from_question("How many tubes will you run?", fallback="OperatorInput"),
            "HowManyTubesWillYouRun",
        )


if __name__ == "__main__":
    unittest.main()
