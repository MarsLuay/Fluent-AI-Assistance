import subprocess
import sys
import unittest
from pathlib import Path

from fluent_pipeline.readiness_gates import (
    readiness_gate_registry_version,
    optional_diagnostic_gate_count,
    readiness_gate,
    readiness_gate_request_spec_approved,
    readiness_gates,
    registered_readiness_gate_evaluators,
    render_readiness_gate_registry_markdown,
    required_offline_gate_count,
)


ROOT = Path(__file__).resolve().parents[1]


class ReadinessGateRegistryTests(unittest.TestCase):
    def test_registry_counts_and_optional_gate_are_stable(self):
        gates = readiness_gates()
        fluent_gate = readiness_gate("fluent_context_check")
        deck_layout_gate = readiness_gate("deck_layout_consistent")

        self.assertEqual(len(gates), 27)
        self.assertEqual(required_offline_gate_count(), 26)
        self.assertEqual(optional_diagnostic_gate_count(), 1)
        self.assertEqual(readiness_gate_registry_version(), "tecan.readiness_gate_registry.v2")
        self.assertEqual(fluent_gate.gate_number, 27)
        self.assertTrue(fluent_gate.is_optional_diagnostic)
        self.assertIn("not required for offline ready-to-import status", fluent_gate.description)
        self.assertEqual(
            deck_layout_gate.implementation,
            "fluent_pipeline.gates.worktable:evaluate_deck_layout",
        )
        self.assertEqual(deck_layout_gate.review_policy, "blocking")
        self.assertEqual(deck_layout_gate.approval_key, "deck_layout_changes")
        self.assertEqual(deck_layout_gate.cli_flag, "--approve-deck-layout")
        self.assertEqual(deck_layout_gate.mcp_capability, "approve_deck_layout")
        self.assertEqual(deck_layout_gate.request_spec_path, "review.deck_layout")
        self.assertEqual(deck_layout_gate.remediation, "protocol-builder worktable-diff")
        self.assertEqual(deck_layout_gate.artifact_inputs, ("protocol.ir.json", "source manifest"))
        self.assertEqual(deck_layout_gate.approval_context_key, "deck_layout_changes_approved")
        self.assertTrue(
            readiness_gate_request_spec_approved(
                {"review": {"deck_layout": True}},
                "deck_layout_consistent",
            )
        )

    def test_registered_evaluators_match_declared_inputs(self):
        evaluators = registered_readiness_gate_evaluators()
        self.assertEqual(
            [evaluator.gate_id for evaluator in evaluators],
            [
                "zeia_parsed",
                "protocol_ir_schema",
                "tip_boxes_resolve",
                "carriers_resolve",
                "device_aliases_resolve",
                "deck_layout_consistent",
            ],
        )
        for evaluator in evaluators:
            definition = readiness_gate(evaluator.gate_id)
            with self.subTest(gate=evaluator.gate_id):
                self.assertEqual(evaluator.implementation, definition.implementation)
                self.assertEqual(evaluator.artifact_inputs, definition.artifact_inputs)
                self.assertTrue(callable(evaluator.evaluate))

    def test_registry_markdown_calls_out_optional_diagnostic(self):
        markdown = render_readiness_gate_registry_markdown()
        self.assertIn("Required offline ready-to-import gates: `26`", markdown)
        self.assertIn("Optional diagnostics: `1`", markdown)
        self.assertIn("Stable IDs are the contract", markdown)
        self.assertIn("`--approve-deck-layout`", markdown)
        self.assertIn("`review.deck_layout`", markdown)
        self.assertIn("protocol.ir.json, source manifest", markdown)
        self.assertIn("| Gate 27 | `fluent_context_check` | Optional diagnostic |", markdown)

    def test_sync_script_reports_generated_files_are_current(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sync_readiness_gate_registry", "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        output = "\n".join(part for part in (result.stdout, result.stderr) if part.strip())
        self.assertEqual(result.returncode, 0, msg=output)


if __name__ == "__main__":
    unittest.main()
