from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fluent_pipeline.application_services import (
    BundleVerificationRequest,
    BundleVerificationResult,
    GenerationResult,
    RepairPlanRequest,
    RepairPlanResult,
    RequestSpecValidationRequest,
    RequestSpecValidationResult,
)
from fluent_pipeline.authoring_status import AuthoringState
from fluent_pipeline.cli.rendering import print_authoring_status
from fluent_pipeline.mcp_gateway import ProtocolBuilderGateway
from fluent_pipeline.repair import RepairAction, RepairPlan
from fluent_pipeline.spec_lint import LintResult


def _invalid_spec_result(spec_path: Path = Path("request.spec.yaml")) -> RequestSpecValidationResult:
    lint = LintResult()
    lint.add("error", "request.intent", "Synthetic request intent is required.")
    return RequestSpecValidationResult(
        request=RequestSpecValidationRequest(spec_path=spec_path),
        result=lint,
    )


def _generation_result(manifest: dict) -> GenerationResult:
    return GenerationResult(request=SimpleNamespace(), manifest=manifest)


def _scaffold_result() -> GenerationResult:
    return _generation_result(
        {
            "workflow_status": "scaffold_not_validated",
            "ready_to_import": False,
            "request_spec": "build/request.spec.yaml",
            "protocol_ir": "build/protocol.ir.json",
            "inference_report": "build/inference.md",
            "inference_json": "build/inference.json",
            "inference": {
                "status": "resolved",
                "inferred_count": 2,
                "unresolved_count": 0,
                "review_required": True,
            },
        }
    )


def _blocked_generation_result() -> GenerationResult:
    return _generation_result(
        {
            "workflow_status": "validated_not_ready",
            "readiness_status": "validated_not_ready",
            "ready_to_import": False,
            "ready_validation": "build/ready_validation.md",
            "readiness": {
                "offline_validation": {
                    "status": "validated_not_ready",
                    "summary": "A synthetic readiness gate is blocking publication.",
                    "blocking_gates": ["synthetic_gate"],
                }
            },
        }
    )


def _repair_result(*, ready: bool) -> RepairPlanResult:
    actions = (
        [RepairAction(kind="synthetic_fix", status="ready", summary="Apply the synthetic fix.")]
        if ready
        else []
    )
    request = RepairPlanRequest(draft_path=Path("draft.py"), report_path=Path("repair_plan.md"))
    return RepairPlanResult(
        request=request,
        plan=RepairPlan(
            draft_path=request.draft_path,
            context_name=None,
            simulation_json_path=None,
            actions=actions,
        ),
        report_path=request.report_path,
    )


def _blocked_verification_result() -> BundleVerificationResult:
    request = BundleVerificationRequest(
        compiled_xscr=Path("compiled.xscr"),
        report_path=Path("ready_validation.md"),
        json_path=Path("ready_validation.json"),
    )
    return BundleVerificationResult(
        request=request,
        report={
            "ready": False,
            "readiness_status": "validated_not_ready",
            "gates": [
                {
                    "id": "synthetic_gate",
                    "status": "failed",
                    "summary": "The synthetic readiness gate failed.",
                }
            ],
        },
        report_path=request.report_path,
        json_path=request.json_path,
    )


def _ready_generation_result() -> GenerationResult:
    return _generation_result(
        {
            "workflow_status": "ready_to_import",
            "readiness_status": "ready_to_import",
            "ready_to_import": True,
            "published_zeia_path": "delivery/protocol.zeia",
            "readiness": {
                "fluentcontrol_load_diagnostic": {
                    "status": "load_clean",
                    "summary": "The optional load diagnostic passed.",
                },
                "script_editor_load": {
                    "status": "passed",
                    "summary": "Script Editor load was confirmed.",
                },
                "hardware_run": {
                    "status": "needs_review",
                    "summary": "Target-system review remains required.",
                    "next_action": "Review the synthetic target setup before running.",
                },
            },
        }
    )


class AuthoringStatusContractTests(unittest.TestCase):
    def test_required_states_have_one_complete_contract(self):
        cases = (
            ("invalid request", _invalid_spec_result(), AuthoringState.REQUEST_SPEC_INVALID),
            ("scaffold review", _scaffold_result(), AuthoringState.SCAFFOLD_NEEDS_REVIEW),
            ("final blocked", _blocked_generation_result(), AuthoringState.FINAL_GENERATION_BLOCKED),
            ("repair ready", _repair_result(ready=True), AuthoringState.REPAIR_READY),
            ("repair no-op", _repair_result(ready=False), AuthoringState.REPAIR_NOOP),
            ("verification blocked", _blocked_verification_result(), AuthoringState.VERIFICATION_BLOCKED),
            ("final handoff", _ready_generation_result(), AuthoringState.FINAL_READY_HANDOFF),
        )

        for label, result, expected_state in cases:
            with self.subTest(label=label):
                contract = result.authoring_status
                payload = contract.to_dict()
                self.assertEqual(contract.status, expected_state)
                self.assertEqual(payload["status"], expected_state.value)
                self.assertIsInstance(payload["findings"], list)
                self.assertIsInstance(payload["artifacts"], list)
                self.assertTrue(payload["allowed_action"])
                self.assertTrue(payload["next_action"])
                self.assertEqual(result.to_dict()["authoring_status"], payload)

    def test_final_handoff_normalizes_legacy_load_clean_and_keeps_live_boundaries(self):
        contract = _ready_generation_result().authoring_status

        self.assertEqual(
            [action.status for action in contract.handoff_actions],
            ["passed", "passed", "needs_review"],
        )
        self.assertEqual(contract.allowed_action, "complete_live_handoff")
        self.assertIn("target setup", contract.next_action)
        self.assertEqual([finding.code for finding in contract.findings], ["hardware_run"])

    def test_cli_renders_the_exact_shared_contract_fields(self):
        for result in (
            _invalid_spec_result(),
            _scaffold_result(),
            _blocked_generation_result(),
            _repair_result(ready=True),
            _repair_result(ready=False),
            _blocked_verification_result(),
            _ready_generation_result(),
        ):
            contract = result.authoring_status
            output = io.StringIO()

            print_authoring_status(contract, stream=output)

            rendered = output.getvalue()
            self.assertIn(f"Authoring status: {contract.status.value}", rendered)
            self.assertIn(f"Allowed action: {contract.allowed_action}", rendered)
            self.assertIn(f"Next: {contract.next_action}", rendered)
            self.assertIn(
                f"Authoring artifacts: {json.dumps(list(contract.artifacts))}",
                rendered,
            )

    def test_mcp_validation_returns_application_contract_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            spec = Path(temp) / "request.spec.yaml"
            spec.write_text("request: {}\n", encoding="utf-8")
            result = _invalid_spec_result(spec)
            gateway = ProtocolBuilderGateway()
            with mock.patch(
                "fluent_pipeline.mcp_gateway.validate_request_spec_service",
                return_value=result,
            ):
                payload = gateway.validate_request_spec(str(spec))

        self.assertEqual(payload["authoring_status"], result.authoring_status.to_dict())

    def test_mcp_generation_returns_scaffold_blocked_and_ready_contracts_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = root / "request.spec.yaml"
            spec.write_text("request: {}\n", encoding="utf-8")
            output = root / "allowed" / "build"
            gateway = ProtocolBuilderGateway()
            gateway.write_roots = [(root / "allowed").resolve()]
            cases = (
                ("scaffold", False, _scaffold_result()),
                ("final", True, _blocked_generation_result()),
                ("final", True, _ready_generation_result()),
            )
            for mode, confirm_final, result in cases:
                with self.subTest(status=result.authoring_status.status.value), mock.patch(
                    "fluent_pipeline.mcp_gateway.load_request_spec",
                    return_value={"request": {"intent": "synthetic"}, "source": {}, "generation": {}},
                ), mock.patch(
                    "fluent_pipeline.mcp_gateway.generate_protocol",
                    return_value=result,
                ), mock.patch(
                    "fluent_pipeline.mcp_gateway._delivery_bundle_validation_from_manifest",
                    return_value={"ok": bool(result.manifest.get("ready_to_import"))},
                ):
                    payload = gateway.generate(
                        str(spec),
                        output_directory=str(output),
                        mode=mode,
                        confirm_final=confirm_final,
                    )
                self.assertEqual(payload["authoring_status"], result.authoring_status.to_dict())

    def test_mcp_repair_and_verification_return_application_contracts_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            allowed = root / "allowed"
            draft = root / "draft.py"
            compiled = root / "compiled.xscr"
            draft.write_text("pass\n", encoding="utf-8")
            compiled.write_text("<VxData />\n", encoding="utf-8")
            gateway = ProtocolBuilderGateway()
            gateway.write_roots = [allowed.resolve()]
            verification_result = _blocked_verification_result()
            repair_payloads = []
            repair_results = (_repair_result(ready=True), _repair_result(ready=False))
            for repair_result in repair_results:
                with mock.patch(
                    "fluent_pipeline.mcp_gateway.plan_repair_service",
                    return_value=repair_result,
                ):
                    repair_payloads.append(
                        gateway.plan_repair(
                            str(draft),
                            output_directory=str(allowed / "repair"),
                        )
                    )
            with mock.patch(
                "fluent_pipeline.mcp_gateway.verify_bundle_service",
                return_value=verification_result,
            ):
                verification_payload = gateway.verify_bundle(
                    str(compiled),
                    output_directory=str(allowed / "verify"),
                )

        for repair_payload, repair_result in zip(repair_payloads, repair_results):
            self.assertEqual(
                repair_payload["authoring_status"],
                repair_result.authoring_status.to_dict(),
            )
        self.assertEqual(
            verification_payload["authoring_status"],
            verification_result.authoring_status.to_dict(),
        )

    def test_mcp_server_returns_gateway_contract_without_adapter_derivation(self):
        from fluent_pipeline import mcp_server

        payload = _invalid_spec_result().to_dict()
        with mock.patch.object(mcp_server.gateway, "validate_request_spec", return_value=payload):
            response = mcp_server.fluent_validate_request_spec("request.spec.yaml")

        self.assertIs(response, payload)


if __name__ == "__main__":
    unittest.main()
