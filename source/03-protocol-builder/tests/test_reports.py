import unittest
from pathlib import Path

from fluent_pipeline.reports import compact_simulation, render_simulation_markdown
from fluent_pipeline.runner import CommandResult


class ReportTests(unittest.TestCase):
    def test_compact_simulation_extracts_key_fields(self):
        data = {
            "status": "passed",
            "total_executed_steps": 3,
            "fully_simulated_steps": 3,
            "modeled_coverage": 1.0,
            "raw_xml_generic_steps": 0,
            "warnings": [],
            "unsupported_command_ids": {
                "ConditionalGroup": 2,
                "CustomUnsupportedCommand": 1,
            },
            "final_labware": [{"label": "Plate"}],
            "state_summary": {"labware_volumes": {"Plate": {"A1": 20.0}}},
        }

        summary = compact_simulation(data)

        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["final_labware_count"], 1)
        self.assertEqual(summary["labware_volumes"]["Plate"]["A1"], 20.0)
        self.assertEqual(summary["unsupported_command_ids"], {"CustomUnsupportedCommand": 1})
        self.assertEqual(summary["approved_opaque_command_ids"], {"ConditionalGroup": 2})
        self.assertEqual(summary["approved_opaque_support_statuses"], {"ConditionalGroup": "mapped"})

    def test_simulation_markdown_handles_load_failure(self):
        result = CommandResult(
            command=("python", "-m", "fluentcoder.cli", "simulate"),
            cwd=Path("repo"),
            returncode=1,
            stdout="",
            stderr="load failed",
        )

        report = render_simulation_markdown(Path("draft.py"), None, result)

        self.assertIn("No JSON simulation payload", report)
        self.assertIn("load failed", report)

    def test_simulation_markdown_splits_approved_opaque_commands(self):
        result = CommandResult(
            command=("python", "-m", "fluentcoder.cli", "simulate"),
            cwd=Path("repo"),
            returncode=0,
            stdout="",
            stderr="",
        )
        data = {
            "status": "passed_with_opaque",
            "total_executed_steps": 2,
            "fully_simulated_steps": 0,
            "modeled_coverage": 0.0,
            "raw_xml_generic_steps": 2,
            "warnings": [],
            "unsupported_command_ids": {
                "ConditionalGroup": 1,
                "CustomUnsupportedCommand": 1,
            },
            "final_labware": [],
            "state_summary": {},
        }

        report = render_simulation_markdown(Path("draft.py"), data, result)

        self.assertIn("## Unsupported Commands", report)
        self.assertIn("CustomUnsupportedCommand", report)
        self.assertIn("## Approved Opaque Commands", report)
        self.assertIn("ConditionalGroup", report)

    def test_simulation_markdown_lists_opaque_subroutine_call_labels(self):
        result = CommandResult(
            command=("python", "-m", "fluentcoder.cli", "simulate"),
            cwd=Path("repo"),
            returncode=0,
            stdout="",
            stderr="",
        )
        data = {
            "status": "passed_with_opaque",
            "total_executed_steps": 1,
            "fully_simulated_steps": 0,
            "modeled_coverage": 0.0,
            "raw_xml_generic_steps": 0,
            "warnings": [],
            "unsupported_command_ids": {},
            "opaque_events": [
                {
                    "step_index": 4,
                    "command_id": "SubRoutineStatement",
                    "step_type": "SubRoutineStep",
                    "message": "subroutine 'Demo\\SUB_Does_Not_Exist' not found in registry",
                }
            ],
            "final_labware": [],
            "state_summary": {},
        }
        ir = {
            "steps": [
                {
                    "index": 4,
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "Demo\\SUB_Does_Not_Exist"},
                }
            ]
        }

        report = render_simulation_markdown(Path("draft.py"), data, result, protocol_ir=ir)

        self.assertIn("## Opaque Subroutine Calls", report)
        self.assertIn("SUB_Does_Not_Exist", report)
        self.assertIn("not found in registry", report)


if __name__ == "__main__":
    unittest.main()
