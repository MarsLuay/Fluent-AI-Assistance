from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fluent_pipeline.application_services import GenerationResult
from fluent_pipeline.generation_options import GenerationOptions
from fluent_pipeline.mcp_gateway import ProtocolBuilderGateway
from fluent_pipeline.protocol_ir import (
    CANONICAL_IR_VERSION,
    annotate_verification_prompts_with_media,
    write_placeholder_image_slot,
    write_protocol_ir,
)
from fluent_pipeline.runner import PipelineError


def _build_media_ir() -> dict:
    ir = {
        "ir_version": CANONICAL_IR_VERSION,
        "id": "process-media-test",
        "protocol": {"name": "ProcessMediaTest"},
        "source": {"format": "test", "path": ""},
        "worktable": {"name": "780_Empty"},
        "labware": [],
        "reagents": [],
        "liquid_classes": [],
        "variables": [],
        "steps": [
            {
                "id": "step_001",
                "operation": "prompt_user",
                "command_id": "RUPStandardStatement",
                "parameters": {
                    "prompt": "1/1) Media capture check.",
                    "rup_kind": "standard",
                },
            }
        ],
    }
    return annotate_verification_prompts_with_media(ir, default_rup_kind="standard")


def _build_empty_ir() -> dict:
    return {
        "ir_version": CANONICAL_IR_VERSION,
        "id": "empty-media-test",
        "protocol": {"name": "EmptyMediaTest"},
        "source": {"format": "test", "path": ""},
        "worktable": {"name": "780_Empty"},
        "labware": [],
        "reagents": [],
        "liquid_classes": [],
        "variables": [],
        "steps": [],
    }


def _write_test_png(path: Path, color: tuple[int, int, int]) -> None:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - Pillow is available in CI
        raise unittest.SkipTest("Pillow is required for media-processing tests") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), color).save(path, format="PNG")


class McpGatewayTests(unittest.TestCase):
    def test_force_import_requires_explicit_confirmation(self):
        gateway = ProtocolBuilderGateway()

        with self.assertRaisesRegex(PipelineError, "confirm_replace"):
            gateway.import_archive("missing.zeia", force=True)

    def test_final_generation_requires_explicit_confirmation(self):
        gateway = ProtocolBuilderGateway()

        with self.assertRaisesRegex(PipelineError, "confirm_final"):
            gateway.generate("missing.yaml", mode="final")

    def test_media_processing_requires_explicit_confirmation(self):
        gateway = ProtocolBuilderGateway()

        with self.assertRaisesRegex(PipelineError, "confirm_in_place"):
            gateway.process_media("missing")

    def test_media_processing_requires_explicit_ir_when_multiple_candidates_exist(self):
        gateway = ProtocolBuilderGateway()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            media_dir = root / "media"
            unprocessed_dir = media_dir / "unprocessed"
            reports_dir = root / "source" / "reports"
            unprocessed_dir.mkdir(parents=True)
            reports_dir.mkdir(parents=True)

            good_ir_path = root / "good.protocol-ir.json"
            write_protocol_ir(_build_media_ir(), good_ir_path)
            bad_ir_path = root / "aaa-empty.protocol-ir.json"
            write_protocol_ir(_build_empty_ir(), bad_ir_path)

            write_placeholder_image_slot(media_dir / "step_001_image.png")
            _write_test_png(unprocessed_dir / "step_001_image.png", (40, 180, 60))

            with self.assertRaisesRegex(PipelineError, "ir_path / --ir"):
                gateway.process_media(str(root), confirm_in_place=True)

            report = gateway.process_media(
                str(root),
                ir_path=str(good_ir_path),
                confirm_in_place=True,
            )
            self.assertTrue(report["ok"])
            self.assertGreater(int(report["report"].get("resolved_count") or 0), 0)

    def test_output_path_must_stay_in_write_roots(self):
        gateway = ProtocolBuilderGateway()
        with tempfile.TemporaryDirectory() as temp:
            gateway.write_roots = [(Path(temp) / "allowed").resolve()]

            with self.assertRaisesRegex(PipelineError, "outside configured roots"):
                gateway._output_path(str(Path(temp) / "elsewhere"), default=Path(temp))

    def test_output_path_requires_project_temp_files_under_ready_to_import(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch(
            "fluent_pipeline.mcp_gateway.READY_TO_IMPORT_DIR",
            Path(temp) / "ready-to-import",
        ):
            ready_root = Path(temp) / "ready-to-import"
            gateway = ProtocolBuilderGateway()
            with self.assertRaisesRegex(PipelineError, "temp_files"):
                gateway._output_path(str(ready_root / "demo"), default=ready_root / "demo")
            self.assertEqual(
                gateway._output_path(
                    str(ready_root / "demo" / "temp_files" / "reports"),
                    default=ready_root / "demo" / "temp_files" / "reports",
                ),
                (ready_root / "demo" / "temp_files" / "reports").resolve(),
            )

    def test_scaffold_generation_uses_safe_cli_flags(self):
        gateway = ProtocolBuilderGateway()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = root / "request.spec.yaml"
            spec.write_text("intent: test\n", encoding="utf-8")
            output = root / "allowed" / "build"
            gateway.write_roots = [(root / "allowed").resolve()]
            manifest = {
                "workflow_status": "scaffold_not_validated",
                "readiness_status": "scaffold_not_validated",
                "readiness": {"script_editor_load": {"status": "not_run"}},
            }
            captured = {}

            def fake_generate_protocol(request):
                captured["request"] = request
                return GenerationResult(request=request, manifest=manifest)

            with (
                mock.patch(
                    "fluent_pipeline.mcp_gateway.load_request_spec",
                    return_value={
                        "request": {"intent": "test"},
                        "source": {},
                        "generation": {},
                    },
                ),
                mock.patch(
                    "fluent_pipeline.mcp_gateway.generate_protocol",
                    side_effect=fake_generate_protocol,
                ) as run,
            ):
                response = gateway.generate(
                    str(spec),
                    output_directory=str(output),
                    mode="scaffold",
                )

        self.assertEqual(
            captured["request"].options,
            GenerationOptions(simulate=False, compile_xscr=False),
        )
        self.assertEqual(captured["request"].request_spec_path, spec.resolve())
        self.assertFalse(captured["request"].use_active_context)
        self.assertTrue(response["ok"])
        self.assertEqual(response["readiness_status"], "scaffold_not_validated")

    def test_final_generation_forces_simulation_and_compilation(self):
        gateway = ProtocolBuilderGateway()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spec = root / "request.spec.yaml"
            spec.write_text("intent: test\n", encoding="utf-8")
            output = root / "allowed" / "build"
            gateway.write_roots = [(root / "allowed").resolve()]
            protocol_folder = root / "allowed" / "ready-to-import" / "demo"
            (protocol_folder / "source").mkdir(parents=True)
            (protocol_folder / "media").mkdir()
            (protocol_folder / "reports").mkdir()
            (protocol_folder / "generated").mkdir()
            (protocol_folder / "demo.zeia").write_bytes(b"zeia")
            (protocol_folder / "run_tecan_bundle_setup.bat").write_text("@echo off\n", encoding="utf-8")
            (protocol_folder / "RECREATE_SCRIPT.md").write_text("# Recreate\n", encoding="utf-8")
            (protocol_folder / "request.spec.yaml").write_text("request: {}\n", encoding="utf-8")
            (protocol_folder / "protocol.ir.json").write_text("{}\n", encoding="utf-8")
            (protocol_folder / "generated" / "protocol.py").write_text("def build_worktable():\n    pass\n", encoding="utf-8")
            (protocol_folder / "generation_manifest.json").write_text("{}\n", encoding="utf-8")
            (protocol_folder / "GENERATION_WORKFLOW.md").write_text("# Workflow\n", encoding="utf-8")
            (protocol_folder / "delivery_manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": "tecan.protocol_delivery.v2",
                        "bundle_schema_version": "tecan.ready_to_import.bundle.v2",
                        "protocol_name": "demo",
                        "external_file_deployments": [],
                        "deliverables": [{"kind": "fluent_project_archive", "path": "demo.zeia"}],
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "workflow_status": "ready_to_import",
                "ready_to_import": True,
                "readiness_status": "load_failed",
                "readiness": {"script_editor_load": {"status": "load_failed"}},
                "published_protocol_folder": str(protocol_folder),
                "published_zeia_path": str(protocol_folder / "demo.zeia"),
            }
            captured = {}

            def fake_generate_protocol(request):
                captured["request"] = request
                return GenerationResult(request=request, manifest=manifest)

            with (
                mock.patch(
                    "fluent_pipeline.mcp_gateway.load_request_spec",
                    return_value={
                        "request": {"intent": "test"},
                        "source": {},
                        "generation": {"simulate": False, "compile_xscr": False},
                    },
                ),
                mock.patch(
                    "fluent_pipeline.mcp_gateway.generate_protocol",
                    side_effect=fake_generate_protocol,
                ) as run,
            ):
                response = gateway.generate(
                    str(spec),
                    output_directory=str(output),
                    mode="final",
                    confirm_final=True,
                )

        self.assertEqual(
            captured["request"].options,
            GenerationOptions(simulate=True, compile_xscr=True),
        )
        self.assertEqual(captured["request"].request_spec_path, spec.resolve())
        self.assertFalse(captured["request"].use_active_context)
        self.assertTrue(response["ok"])
        self.assertEqual(response["readiness_status"], "load_failed")
        self.assertEqual(response["readiness"], manifest["readiness"])

    def test_plan_repair_routes_through_shared_service(self):
        gateway = ProtocolBuilderGateway()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            draft = root / "draft.py"
            draft.write_text("print('demo')\n", encoding="utf-8")
            output = root / "allowed" / "repair"
            gateway.write_roots = [(root / "allowed").resolve()]

            with mock.patch(
                "fluent_pipeline.mcp_gateway.plan_repair_service",
                return_value=SimpleNamespace(
                    to_dict=lambda: {"plan": {"actions": []}, "report_path": str(output / "repair_plan.md")},
                ),
            ):
                response = gateway.plan_repair(str(draft), output_directory=str(output))

        self.assertIn("plan", response)

    def test_verify_bundle_routes_through_shared_service(self):
        gateway = ProtocolBuilderGateway()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            compiled = root / "bundle.xscr"
            compiled.write_text("<VxData />", encoding="utf-8")
            output = root / "allowed" / "verify"
            gateway.write_roots = [(root / "allowed").resolve()]

            with mock.patch(
                "fluent_pipeline.mcp_gateway.verify_bundle_service",
                return_value=SimpleNamespace(
                    to_dict=lambda: {"ok": True, "ready": True, "report": {"ready": True}},
                ),
            ):
                response = gateway.verify_bundle(str(compiled), output_directory=str(output))

        self.assertTrue(response["ok"])

    def test_cli_capability_inventory_classifies_every_registered_command(self):
        capabilities = ProtocolBuilderGateway().cli_capabilities()

        self.assertEqual(capabilities["unclassified_cli_commands"], [])
        self.assertEqual(capabilities["stale_classifications"], [])
        self.assertEqual(capabilities["registered_cli_command_count"], 49)
        self.assertEqual(capabilities["commands"]["simulate"]["mode"], "opt_in")
        self.assertEqual(capabilities["commands"]["fluent-prepare-check"]["mode"], "opt_in")
        self.assertEqual(capabilities["commands"]["worktable-diff"]["mode"], "bridge")

    def test_safe_cli_bridge_runs_only_classified_offline_command(self):
        gateway = ProtocolBuilderGateway()

        result = gateway.run_safe_cli("ir-schema", ["--versions"])

        self.assertTrue(result["ok"])
        self.assertIn("versions", result["stdout"])
        with self.assertRaisesRegex(PipelineError, "opt-in MCP operation"):
            gateway.run_safe_cli("simulate", [])
        with self.assertRaisesRegex(PipelineError, "confirm_mutation"):
            gateway.run_safe_cli("clear-project", [])

    def test_opt_in_cli_requires_server_environment_and_confirmation(self):
        gateway = ProtocolBuilderGateway()

        with self.assertRaisesRegex(PipelineError, "TECAN_MCP_ENABLE_DRAFT_EXECUTION=1"):
            gateway.run_opt_in_cli("simulate", ["draft.py"], confirm_execution=True)

        with (
            mock.patch.dict("os.environ", {"TECAN_MCP_ENABLE_DRAFT_EXECUTION": "1"}),
            mock.patch.object(gateway, "_run_cli_bridge", return_value={"ok": True}) as run,
        ):
            response = gateway.run_opt_in_cli("simulate", ["draft.py"], confirm_execution=True)

        self.assertTrue(response["ok"])
        run.assert_called_once_with(
            "simulate",
            ["draft.py"],
            expected_mode="opt_in",
            confirmed=True,
            confirmation_name="confirm_execution",
        )

    def test_safe_cli_bridge_rejects_live_provider_and_unsafe_default_outputs(self):
        gateway = ProtocolBuilderGateway()

        with self.assertRaisesRegex(PipelineError, "does not allow"):
            gateway.run_safe_cli("decompile", ["draft.xscr", "--fluent-context-check"])
        with self.assertRaisesRegex(PipelineError, "requires --json or an explicit --output"):
            gateway.run_safe_cli("worktable-diff", ["protocol.protocol-ir.json"])

    def test_worktable_diff_is_offline_and_writes_only_to_allowed_output(self):
        gateway = ProtocolBuilderGateway()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            protocol = root / "protocol.protocol-ir.json"
            write_protocol_ir(_build_empty_ir(), protocol)
            output = root / "allowed" / "worktable"
            gateway.write_roots = [(root / "allowed").resolve()]

            response = gateway.diff_worktable(
                str(protocol),
                output_directory=str(output),
            )

        self.assertTrue(response["ok"])
        self.assertEqual(len(response["artifacts"]), 2)
        self.assertIn("diff", response)

    def test_simulation_summary_reads_json_without_executing_a_draft(self):
        gateway = ProtocolBuilderGateway()
        with tempfile.TemporaryDirectory() as temp:
            simulation = Path(temp) / "simulation.json"
            simulation.write_text('{"status": "passed"}\n', encoding="utf-8")
            with mock.patch(
                "fluent_pipeline.mcp_gateway.compact_simulation",
                return_value={"status": "passed", "modeled_coverage": 1.0},
            ) as summarize:
                response = gateway.summarize_simulation(str(simulation))

        self.assertTrue(response["ok"])
        self.assertEqual(response["summary"]["modeled_coverage"], 1.0)
        summarize.assert_called_once()

    def test_status_declares_no_hardware_operations(self):
        status = ProtocolBuilderGateway().status()

        self.assertFalse(status["hardware_operations_exposed"])
        self.assertEqual(status["mcp_role"], "thin adapter over fluent_pipeline")
        self.assertIn("CLI", status["interfaces"])


if __name__ == "__main__":
    unittest.main()
