import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fluent_pipeline.application_services import GenerationResult
from fluent_pipeline.cli import _cmd_analyze, _cmd_generate, _cmd_map_media, _resolve_generation_event_log, _resolve_ir_source
from fluent_pipeline.cli import runtime as cli_runtime
from fluent_pipeline.cli.commands import generation as generation_commands
from fluent_pipeline.cli.commands import simulator as simulator_commands
from fluent_pipeline.cli.rendering import generation_exit_code, print_generation_result
from fluent_pipeline.cli.requests import (
    generation_request_from_cli,
    merge_generate_spec_args,
    request_spec_create_request_from_cli,
)
from fluent_pipeline.cli.parser import _build_parser
from fluent_pipeline.compiled_xscr_finalizer import FinalizationReport
from fluent_pipeline.config import workflow_event_log_path
from fluent_pipeline.fluentcoder_cli_args import build_compile_command, build_simulate_command
from fluent_pipeline.generation_options import GenerationOptions
from fluent_pipeline.progress import ProgressEvent
from fluent_pipeline.protocol_ir import (
    CANONICAL_IR_VERSION,
    annotate_verification_prompts_with_media,
    collect_media_placeholders,
    write_protocol_ir,
)
from fluent_pipeline.runner import PipelineError


def _ok_command_result(command: str) -> SimpleNamespace:
    return SimpleNamespace(
        ok=True,
        returncode=0,
        stdout="",
        stderr="",
        command_line=lambda: command,
    )


def _ok_finalization_report() -> FinalizationReport:
    return FinalizationReport(
        ok=True,
        checksum_before="valid",
        checksum_after="valid",
        roundtrip={"matched": True},
        command_validation={"failure_count": 0},
        generic_command_validation={"failure_count": 0},
    )


class CliPathResolutionTests(unittest.TestCase):
    def test_resolve_spec_command_is_registered_for_regeneration_preflight(self):
        args = _build_parser().parse_args(
            ["resolve-spec", "latest:Verification_Script1"]
        )

        self.assertEqual(args.cmd, "resolve-spec")
        self.assertEqual(args.spec, "latest:Verification_Script1")
        self.assertEqual(args.func.__name__, "_cmd_resolve_spec")

    def test_request_spec_default_uses_project_temp_files(self):
        args = _build_parser().parse_args(
            ["request-spec", "Create a demo protocol", "--protocol-name", "Demo Protocol"]
        )
        with tempfile.TemporaryDirectory() as tmp, mock.patch(
            "fluent_pipeline.cli.requests.READY_TO_IMPORT_DIR",
            Path(tmp) / "ready-to-import",
        ):
            request = request_spec_create_request_from_cli(args)

        self.assertEqual(
            request.output_path,
            (Path(tmp) / "ready-to-import" / "demo-protocol" / "temp_files" / "build" / "request.spec.yaml").resolve(),
        )

    def test_artifact_outputs_require_ready_workspace_temp_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ready_dir = root / "ready-to-import"
            temp_dir = ready_dir / "demo" / "temp_files"
            with mock.patch.object(cli_runtime, "READY_TO_IMPORT_DIR", ready_dir):
                self.assertEqual(
                    cli_runtime._resolve_artifact_output_path(temp_dir / "draft"),
                    (temp_dir / "draft").resolve(),
                )
                with self.assertRaisesRegex(PipelineError, "must be written under"):
                    cli_runtime._resolve_artifact_output_path(root / "elsewhere" / "draft.py")
                with self.assertRaisesRegex(PipelineError, "must be written under"):
                    cli_runtime._resolve_artifact_output_path(ready_dir / "bundle")

    def test_simulate_command_resolves_context_before_running(self):
        args = SimpleNamespace(
            context=None,
            input=Path("protocol.py"),
            watch_log=False,
            strict=False,
            fail_on_opaque=False,
            min_coverage=None,
            json_out=None,
            report=None,
        )
        simulation = {"status": "passed"}

        with mock.patch.object(simulator_commands, "_command_context", return_value=None), mock.patch.object(
            simulator_commands, "ensure_project_catalog", return_value=None
        ), mock.patch.object(
            simulator_commands, "_simulate_protocol", return_value=(_ok_command_result("simulate"), simulation)
        ), mock.patch.object(
            simulator_commands,
            "compact_simulation",
            return_value={
                "status": "passed",
                "total_executed_steps": 1,
                "modeled_coverage": 1.0,
                "raw_xml_generic_steps": 0,
                "warnings": [],
                "failure": None,
            },
        ) as compact:
            rc = simulator_commands._cmd_simulate(args)

        self.assertEqual(rc, 0)
        compact.assert_called_once_with(simulation)

    def test_generate_summary_never_reports_xscr_as_deliverable(self):
        result = GenerationResult(
            request=SimpleNamespace(),
            manifest={
                "workflow_status": "validated_not_ready",
                "readiness_status": "validated_not_ready",
                "ready_to_import": False,
                "workflow_report": "workflow.md",
                "generation_manifest": "generation_manifest.json",
                "request_spec": "request.spec.yaml",
                "compiled_xscr": "build/demo.xscr",
                "published_zeia_path": None,
                "ready_to_import_artifacts": [],
            },
        )
        output = io.StringIO()

        print_generation_result(result, stream=output)

        rendered = output.getvalue()
        self.assertNotIn("Compiled XSCR:", rendered)
        self.assertIn("No ready-to-import ZEIA was published.", rendered)
        self.assertIn("standalone XSCR files are internal only", rendered)
        self.assertEqual(generation_exit_code(result), 1)

    def test_generate_summary_reports_ready_protocol_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            protocol_folder = Path(tmp) / "ready-to-import" / "demo"
            protocol_folder.mkdir(parents=True)
            published_zeia = protocol_folder / "demo.zeia"
            published_zeia.write_bytes(b"zeia")
            (protocol_folder / "RECREATE_SCRIPT.md").write_text("# Recreate\n", encoding="utf-8")
            (protocol_folder / "run_tecan_bundle_setup.bat").write_text("@echo off\n", encoding="utf-8")
            source_dir = protocol_folder / "source"
            generated_dir = source_dir / "generated"
            generated_dir.mkdir(parents=True)
            (source_dir / "generation_manifest.json").write_text("{}", encoding="utf-8")
            (source_dir / "GENERATION_WORKFLOW.md").write_text("# Workflow\n", encoding="utf-8")
            (source_dir / "request.spec.yaml").write_text("request: {}\n", encoding="utf-8")
            (source_dir / "protocol.ir.json").write_text("{}", encoding="utf-8")
            (source_dir / "metadata.json").write_text("{}", encoding="utf-8")
            (generated_dir / "protocol.py").write_text("def build_worktable():\n    pass\n", encoding="utf-8")
            (source_dir / "reports").mkdir()
            (protocol_folder / "media").mkdir()
            for helper in (
                "collect_tecan_diagnostic_bundle.ps1",
                "copy_tree_with_progress.ps1",
                "deploy_touchtools_media.ps1",
                "install_external_files.ps1",
                "stall_watchdog.ps1",
            ):
                (source_dir / helper).write_text("# helper\n", encoding="utf-8")
            (source_dir / "delivery_manifest.json").write_text(
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

            result = GenerationResult(
                request=SimpleNamespace(),
                manifest={
                    "workflow_status": "ready_to_import",
                    "readiness_status": "ready_to_import",
                    "ready_to_import": True,
                    "workflow_report": "workflow.md",
                    "generation_manifest": "generation_manifest.json",
                    "request_spec": "request.spec.yaml",
                    "compiled_xscr": "build/demo.xscr",
                    "published_protocol_folder": str(protocol_folder),
                    "published_zeia_path": str(published_zeia),
                    "ready_to_import_artifacts": [str(published_zeia)],
                },
            )
            output = io.StringIO()

            print_generation_result(result, stream=output)

            rendered = output.getvalue()
            self.assertIn(f"Ready-to-import protocol folder: {protocol_folder}", rendered)
            self.assertIn(f"Ready-to-import ZEIA: {published_zeia}", rendered)
            self.assertNotIn("Compiled XSCR:", rendered)
            self.assertEqual(generation_exit_code(result), 0)

    def test_generate_summary_uses_canonical_handoff_status_and_actions(self):
        result = GenerationResult(
            request=SimpleNamespace(),
            manifest={
                "workflow_status": "ready_to_import",
                "readiness_status": "ready_to_import",
                "ready_to_import": True,
                "workflow_report": "workflow.md",
                "generation_manifest": "generation_manifest.json",
                "request_spec": "request.spec.yaml",
                "readiness": {
                    "fluentcontrol_load_diagnostic": {
                        "status": "failed",
                        "next_action": "Resolve the Gate 27 diagnostic result.",
                    },
                    "script_editor_load": {
                        "status": "passed",
                        "next_action": "No action: Script Editor load is confirmed.",
                    },
                    "hardware_run": {
                        "status": "needs_review",
                        "next_action": "Review the target system before running.",
                    },
                },
            },
        )
        output = io.StringIO()

        print_generation_result(result, stream=output)

        rendered = output.getvalue()
        self.assertIn("Status: ready_to_import (READY TO IMPORT)", rendered)
        self.assertNotIn("LOAD NOT VERIFIED", rendered)
        self.assertNotIn("LOAD FAILED", rendered)
        self.assertIn("Next (FluentControl load diagnostic): Resolve the Gate 27 diagnostic result.", rendered)
        self.assertIn("Next (Script Editor load): No action: Script Editor load is confirmed.", rendered)
        self.assertIn("Next (Hardware run): Review the target system before running.", rendered)

    def test_explicit_collection_overrides_spec_source_context_defaults(self):
        args = SimpleNamespace(
            intent=None,
            protocol_name=None,
            project_archive=[],
            context=[],
            collection="fresh-collection",
            source_script=[],
            pattern=[],
            index_db=None,
            pattern_id=[],
            pattern_query=[],
            source_script_rank=1,
            no_simulate=False,
            no_compile=False,
            max_repair_iterations=None,
            strict_readiness=False,
            apply_modeling=False,
            approve_partial_zeia=False,
            approve_deck_layout=False,
            approve_command_inventory=False,
            waive_checksum_recompute=False,
            fluent_context_check=False,
            fluent_provider=None,
            fluent_timeout=None,
            fluent_method=None,
        )
        spec = {
            "request": {
                "intent": "Regenerate a verification script",
                "protocol_name": "Verification",
            },
            "source": {
                "context": "stale-context",
                "contexts": [{"name": "stale-a"}, {"name": "stale-b"}],
                "project_archives": ["stale.zeia"],
                "source_scripts": ["SourceScript"],
            },
            "generation": {},
        }

        merged = merge_generate_spec_args(args, spec)

        self.assertEqual(merged.collection, "fresh-collection")
        self.assertEqual(merged.context, [])
        self.assertEqual(merged.project_archive, [])
        self.assertEqual(merged.source_script, ["SourceScript"])

    def test_generation_request_resolves_latest_spec_alias(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready_root = Path(tmp) / "ready-to-import"
            workspace_root = ready_root / "unscoped" / "temp_files"
            spec_path = ready_root / "demo" / "source" / "request.spec.yaml"
            spec_path.parent.mkdir(parents=True)
            spec_path.write_text("request: {}\n", encoding="utf-8")
            args = SimpleNamespace(
                spec="latest:Demo",
                pin_spec=False,
                intent=None,
                protocol_name=None,
                project_archive=[],
                context=[],
                collection=None,
                name=None,
                force_import=False,
                source_script=[],
                pattern=[],
                index_db=None,
                pattern_id=[],
                pattern_query=[],
                source_script_rank=1,
                ir=None,
                out_dir=workspace_root / "build" / "out",
                no_simulate=False,
                no_compile=False,
                max_repair_iterations=None,
                strict_readiness=False,
                apply_modeling=False,
                approve_partial_zeia=False,
                approve_deck_layout=False,
                approve_command_inventory=False,
                approve_unsupported_raw_xml=False,
                approved_unsupported_command_ids=[],
                waive_checksum_recompute=False,
                preserve_failed_artifacts=False,
                preserve_regeneration_baseline=False,
                fluent_context_check=False,
                fluent_provider=None,
                fluent_timeout=None,
                subroutine_dir=[],
                record_snapshots=None,
                deterministic_compile=False,
                fluent_method=None,
                fluent_command=None,
                fluent_host="127.0.0.1",
                fluent_port=50052,
                fluent_insecure=False,
            )
            spec = {
                "request": {
                    "intent": "Regenerate from latest",
                    "protocol_name": "Demo",
                },
                "source": {},
                "generation": {},
            }
            fake_cli = SimpleNamespace(
                _generation_context_from_args=lambda _args: (None, None),
                _resolve_ir_source=lambda _ctx, value: value,
            )

            with mock.patch.object(cli_runtime, "READY_TO_IMPORT_DIR", ready_root), mock.patch(
                "fluent_pipeline.cli.requests.resolve_request_spec_path",
                return_value=(spec_path, {"reason": "latest_alias"}),
            ) as resolve_spec, mock.patch(
                "fluent_pipeline.cli.requests.load_request_spec",
                return_value=spec,
            ), mock.patch(
                "fluent_pipeline.cli.requests.cli_module",
                return_value=fake_cli,
            ):
                request = generation_request_from_cli(args)

        resolve_spec.assert_called_once()
        self.assertEqual(request.request_spec_path, spec_path)
        self.assertEqual(request.intent, "Regenerate from latest")

    def test_generation_request_default_out_dir_uses_protocol_name_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            spec_path = tmp_path / "request.spec.yaml"
            spec_path.write_text("request: {}\n", encoding="utf-8")
            build_dir = tmp_path / "build"
            (build_dir / "generations" / "demo-protocol").mkdir(parents=True)
            ctx = SimpleNamespace(name="ctx", build_dir=build_dir)
            args = SimpleNamespace(
                spec=spec_path,
                pin_spec=True,
                intent=None,
                protocol_name=None,
                project_archive=[],
                context=[],
                collection=None,
                name=None,
                force_import=False,
                source_script=[],
                pattern=[],
                index_db=None,
                pattern_id=[],
                pattern_query=[],
                source_script_rank=1,
                ir=None,
                out_dir=None,
                no_simulate=False,
                no_compile=False,
                max_repair_iterations=None,
                strict_readiness=False,
                apply_modeling=False,
                approve_partial_zeia=False,
                approve_deck_layout=False,
                approve_command_inventory=False,
                approve_unsupported_raw_xml=False,
                approved_unsupported_command_ids=[],
                waive_checksum_recompute=False,
                preserve_failed_artifacts=False,
                preserve_regeneration_baseline=False,
                fluent_context_check=False,
                fluent_provider=None,
                fluent_timeout=None,
                subroutine_dir=[],
                record_snapshots=None,
                deterministic_compile=False,
                fluent_method=None,
                fluent_command=None,
                fluent_host="127.0.0.1",
                fluent_port=50052,
                fluent_insecure=False,
            )
            spec = {
                "request": {
                    "intent": "Long detailed prompt that should not name the folder",
                    "protocol_name": "Demo Protocol",
                },
                "source": {},
                "generation": {},
            }
            fake_cli = SimpleNamespace(
                _generation_context_from_args=lambda _args: (ctx, None),
                _resolve_ir_source=lambda _ctx, value: value,
            )

            with mock.patch(
                "fluent_pipeline.cli.requests.resolve_request_spec_path",
                return_value=(spec_path, {"reason": "pinned"}),
            ), mock.patch(
                "fluent_pipeline.cli.requests.load_request_spec",
                return_value=spec,
            ), mock.patch(
                "fluent_pipeline.cli.requests.cli_module",
                return_value=fake_cli,
            ):
                request = generation_request_from_cli(args)

        self.assertEqual(request.output_directory.name, "demo-protocol_v2")

    def test_project_collection_spec_defaults_to_collection_not_plain_context(self):
        args = SimpleNamespace(
            intent=None,
            protocol_name=None,
            project_archive=[],
            context=[],
            collection=None,
            source_script=[],
            pattern=[],
            index_db=None,
            pattern_id=[],
            pattern_query=[],
            source_script_rank=1,
            no_simulate=False,
            no_compile=False,
            max_repair_iterations=None,
            strict_readiness=False,
            apply_modeling=False,
            approve_partial_zeia=False,
            approve_deck_layout=False,
            approve_command_inventory=False,
            approve_unsupported_raw_xml=False,
            approved_unsupported_command_ids=[],
            waive_checksum_recompute=False,
            preserve_failed_artifacts=False,
            preserve_regeneration_baseline=False,
            fluent_context_check=False,
            fluent_provider=None,
            fluent_timeout=None,
            fluent_method=None,
        )
        spec = {
            "request": {
                "intent": "Regenerate a verification script",
                "protocol_name": "Verification",
            },
            "source": {
                "context": "verification-sources",
                "context_kind": "project_collection",
                "contexts": [{"name": "script"}, {"name": "full-export"}],
                "project_archives": ["script.zeia", "full.zeia"],
            },
            "generation": {},
        }

        merged = merge_generate_spec_args(args, spec)

        self.assertEqual(merged.collection, "verification-sources")
        self.assertEqual(merged.context, [])
        self.assertEqual(merged.project_archive, [])

    def test_ir_source_prefers_existing_cwd_relative_path_over_context_build_path(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cwd_root = tmp_path / "cwd"
            context_root = tmp_path / "context"
            workspace_root = cwd_root / "ready-to-import" / "unscoped" / "temp_files"
            local_ir = workspace_root / "build" / "generated" / "protocol-ir.json"
            context_ir = context_root / "build" / "generated" / "protocol-ir.json"
            local_ir.parent.mkdir(parents=True)
            context_ir.parent.mkdir(parents=True)
            local_ir.write_text("{}", encoding="utf-8")
            context_ir.write_text("{}", encoding="utf-8")

            os.chdir(cwd_root)
            try:
                resolved = _resolve_ir_source(
                    SimpleNamespace(root=context_root),
                    Path("ready-to-import/unscoped/temp_files/build/generated/protocol-ir.json"),
                )
            finally:
                os.chdir(original_cwd)

        self.assertEqual(resolved, local_ir.resolve())

    def test_generate_command_resolves_explicit_ir_and_out_dir_from_cwd(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cwd_root = tmp_path / "cwd"
            context_root = tmp_path / "context"
            local_ir = cwd_root / "ready-to-import" / "unscoped" / "temp_files" / "build" / "generated" / "protocol-ir.json"
            context_ir = context_root / "build" / "generated" / "protocol-ir.json"
            local_ir.parent.mkdir(parents=True)
            context_ir.parent.mkdir(parents=True)
            local_ir.write_text("{}", encoding="utf-8")
            context_ir.write_text("{}", encoding="utf-8")

            captured = {}

            def fake_generate_protocol(request):
                captured["request"] = request
                return GenerationResult(
                    request=request,
                    manifest={
                    "workflow_status": "scaffold_not_validated",
                    "ready_to_import": False,
                    "workflow_report": "workflow.md",
                    "generation_manifest": "generation_manifest.json",
                    "request_spec": "request.spec.yaml",
                    "protocol_ir": "protocol.ir.json",
                    "python_draft": "draft.py",
                    "recreate_script": "RECREATE_SCRIPT.md",
                    "worktable_changes": "worktable_changes.md",
                    "worktable_patch": "worktable.patch.json",
                    "validation_diff": "validation_diff.md",
                    "compiled_xscr": None,
                    },
                )

            args = SimpleNamespace(
                spec=None,
                intent="Generate a script",
                ir=Path("ready-to-import/unscoped/temp_files/build/generated/protocol-ir.json"),
                out_dir=Path("ready-to-import/unscoped/temp_files/build/generated/final"),
                index_db=None,
                name=None,
                force_import=False,
                source_script=[],
                pattern=[],
                pattern_id=[],
                pattern_query=[],
                source_script_rank=1,
                protocol_name=None,
                no_simulate=True,
                no_compile=True,
                apply_modeling=False,
                fluent_method=None,
                launch_simulator=False,
                simulator_host="127.0.0.1",
                simulator_port=5173,
                simulator_strict_port=False,
                simulator_no_open=False,
                simulator_skip_install=False,
                subroutine_dir=[],
                record_snapshots=None,
                deterministic_compile=False,
                event_log=None,
                no_event_log=True,
                progress=False,
                event_log_stderr=False,
            )

            os.chdir(cwd_root)
            try:
                with mock.patch.object(cli_runtime, "READY_TO_IMPORT_DIR", cwd_root / "ready-to-import"), mock.patch(
                    "fluent_pipeline.cli._generation_context_from_args",
                    return_value=(SimpleNamespace(root=context_root), None),
                ), mock.patch("fluent_pipeline.cli.generate_protocol", side_effect=fake_generate_protocol):
                    _cmd_generate(args)
            finally:
                os.chdir(original_cwd)

        self.assertEqual(captured["request"].protocol_ir, local_ir.resolve())
        self.assertEqual(
            captured["request"].output_directory,
            (local_ir.parent / "final").resolve(),
        )

    def test_generate_progress_writes_to_stderr_and_result_to_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd_root = Path(tmp)
            workspace_root = cwd_root / "ready-to-import" / "unscoped" / "temp_files"

            def fake_generate_protocol(request, *, progress_callback=None):
                if progress_callback is not None:
                    progress_callback(
                        ProgressEvent(
                            stage_id="load_context",
                            stage_name="Loading project context",
                            status="completed",
                            current_stage=1,
                            total_stages=10,
                            elapsed_seconds=0.4,
                        )
                    )
                return GenerationResult(
                    request=request,
                    manifest={
                        "workflow_status": "scaffold_not_validated",
                        "ready_to_import": False,
                        "workflow_report": "workflow.md",
                        "generation_manifest": "generation_manifest.json",
                        "request_spec": "request.spec.yaml",
                        "protocol_ir": "protocol.ir.json",
                        "python_draft": "draft.py",
                        "recreate_script": "RECREATE_SCRIPT.md",
                        "worktable_changes": "worktable_changes.md",
                        "worktable_patch": "worktable.patch.json",
                        "validation_diff": "validation_diff.md",
                        "compiled_xscr": None,
                    },
                )

            args = SimpleNamespace(
                spec=None,
                intent="Generate a script",
                ir=None,
                out_dir=Path("ready-to-import/unscoped/temp_files/build/generated/final"),
                index_db=None,
                name=None,
                force_import=False,
                source_script=[],
                pattern=[],
                pattern_id=[],
                pattern_query=[],
                source_script_rank=1,
                protocol_name=None,
                no_simulate=True,
                no_compile=True,
                apply_modeling=False,
                fluent_method=None,
                launch_simulator=False,
                simulator_host="127.0.0.1",
                simulator_port=5173,
                simulator_strict_port=False,
                simulator_no_open=False,
                simulator_skip_install=False,
                subroutine_dir=[],
                record_snapshots=None,
                deterministic_compile=False,
                progress="plain",
            )

            original_cwd = Path.cwd()
            os.chdir(cwd_root)
            try:
                with mock.patch.object(cli_runtime, "READY_TO_IMPORT_DIR", cwd_root / "ready-to-import"), mock.patch(
                    "fluent_pipeline.cli._generation_context_from_args",
                    return_value=(None, None),
                ), mock.patch("fluent_pipeline.cli.generate_protocol", side_effect=fake_generate_protocol), mock.patch(
                    "sys.stdout", new_callable=io.StringIO
                ) as stdout, mock.patch(
                    "sys.stderr", new_callable=io.StringIO
                ) as stderr:
                    _cmd_generate(args)
            finally:
                os.chdir(original_cwd)

        self.assertIn("Status: scaffold_not_validated", stdout.getvalue())
        self.assertNotIn("[1/10]", stdout.getvalue())
        self.assertIn("[1/10] Loading project context... done (0.4s)", stderr.getvalue())
        self.assertNotIn("Status: scaffold_not_validated", stderr.getvalue())

    def test_generate_command_passes_fluentcoder_simulate_and_compile_flags(self):
        original_cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cwd_root = tmp_path / "cwd"
            cwd_root.mkdir()
            workspace_root = cwd_root / "ready-to-import" / "unscoped" / "temp_files"
            sub_dir = cwd_root / "subroutines"
            sub_dir.mkdir()
            captured = {}

            def fake_generate_protocol(request):
                captured["request"] = request
                return GenerationResult(
                    request=request,
                    manifest={
                    "workflow_status": "scaffold_not_validated",
                    "ready_to_import": False,
                    "workflow_report": "workflow.md",
                    "generation_manifest": "generation_manifest.json",
                    "request_spec": "request.spec.yaml",
                    "protocol_ir": "protocol.ir.json",
                    "python_draft": "draft.py",
                    "recreate_script": "RECREATE_SCRIPT.md",
                    "worktable_changes": "worktable_changes.md",
                    "worktable_patch": "worktable.patch.json",
                    "validation_diff": "validation_diff.md",
                    "compiled_xscr": None,
                    },
                )

            args = SimpleNamespace(
                spec=None,
                intent="Generate a script",
                ir=None,
                out_dir=Path("ready-to-import/unscoped/temp_files/build/generated/final"),
                index_db=None,
                name=None,
                force_import=False,
                source_script=[],
                pattern=[],
                pattern_id=[],
                pattern_query=[],
                source_script_rank=1,
                protocol_name=None,
                no_simulate=False,
                no_compile=False,
                apply_modeling=False,
                fluent_method=None,
                launch_simulator=False,
                simulator_host="127.0.0.1",
                simulator_port=5173,
                simulator_strict_port=False,
                simulator_no_open=False,
                simulator_skip_install=False,
                subroutine_dir=[sub_dir],
                record_snapshots=False,
                deterministic_compile=True,
                event_log=None,
                no_event_log=True,
                progress=False,
                event_log_stderr=False,
            )

            os.chdir(cwd_root)
            try:
                with mock.patch.object(cli_runtime, "READY_TO_IMPORT_DIR", cwd_root / "ready-to-import"), mock.patch(
                    "fluent_pipeline.cli._generation_context_from_args",
                    return_value=(None, None),
                ), mock.patch("fluent_pipeline.cli.generate_protocol", side_effect=fake_generate_protocol):
                    _cmd_generate(args)
            finally:
                os.chdir(original_cwd)

        self.assertEqual(captured["request"].options.subroutine_dirs, (sub_dir.resolve(),))
        self.assertFalse(captured["request"].options.record_snapshots)
        self.assertTrue(captured["request"].options.deterministic_compile)
        self.assertEqual(
            captured["request"].options,
            GenerationOptions(
                subroutine_dirs=(sub_dir.resolve(),),
                record_snapshots=False,
                deterministic_compile=True,
            ),
        )

    def test_compile_command_finalizes_compiled_xscr(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            protocol = tmp_path / "draft.py"
            workspace_root = tmp_path / "ready-to-import" / "unscoped" / "temp_files"
            output = workspace_root / "build" / "draft.xscr"
            protocol.write_text("def build_worktable():\n    return None\n", encoding="utf-8")
            args = SimpleNamespace(
                context=None,
                input=protocol,
                output=output,
                fluent_context_check=False,
                fluent_method=None,
                fluent_provider="auto",
                fluent_command=None,
                fluent_host="127.0.0.1",
                fluent_port=50052,
                fluent_insecure=False,
                fluent_timeout=180.0,
            )

            def fake_compile(command, catalog_db=None):
                Path(command[-1]).write_text("<VxData><Payload /></VxData>", encoding="utf-8")
                return _ok_command_result("compile")

            with mock.patch.object(cli_runtime, "READY_TO_IMPORT_DIR", tmp_path / "ready-to-import"), mock.patch.object(
                generation_commands, "_command_context", return_value=None
            ), mock.patch.object(
                generation_commands, "ensure_project_catalog", return_value=None
            ), mock.patch.object(
                generation_commands, "run_fluentcoder", side_effect=fake_compile
            ), mock.patch.object(
                generation_commands, "_print_process"
            ), mock.patch.object(
                generation_commands, "finalize_compiled_xscr", return_value=_ok_finalization_report()
            ) as finalize_mock, mock.patch.object(
                generation_commands, "_run_cli_fluent_context_check", return_value=(None, None, None)
            ), mock.patch(
                "fluent_pipeline.exports.export_ready_to_import",
                side_effect=AssertionError("compile must not publish ready-to-import bundles"),
            ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = generation_commands._cmd_compile(args)

            self.assertEqual(rc, 0)
            self.assertEqual(finalize_mock.call_count, 1)
            final_args = finalize_mock.call_args.args
            self.assertEqual(Path(final_args[0]).resolve(), output.resolve())
            self.assertEqual(Path(final_args[1]).resolve(), protocol.resolve())
            self.assertIsNone(final_args[2])
            self.assertIsNone(final_args[3])
            self.assertEqual(final_args[4], {"source_ir_origin": "compile_input"})
            normalized_stdout = stdout.getvalue().replace("/private/var/", "/var/")
            self.assertIn(f"Compiled XSCR: {output}", normalized_stdout)
            self.assertIn(f"Compile report: {output.with_suffix('.compile.md')}", normalized_stdout)
            self.assertNotIn("Ready to import", normalized_stdout)
            self.assertIn("Compiled XSCR Finalization", output.with_suffix(".compile.md").read_text(encoding="utf-8"))

    def test_ir_build_finalizes_compiled_xscr(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ir = {
                "ir_version": CANONICAL_IR_VERSION,
                "protocol": {"name": "Demo IR Build"},
                "worktable": {"name": "780_Empty", "auto_place": False},
                "labware": [],
                "reagents": [],
                "steps": [],
                "dependencies": [],
                "source": {},
            }

            def fake_compile(command, catalog_db=None):
                Path(command[-1]).write_text("<VxData><Payload /></VxData>", encoding="utf-8")
                return _ok_command_result("compile")

            with mock.patch.object(generation_commands, "run_fluentcoder", side_effect=fake_compile), mock.patch.object(
                generation_commands, "finalize_compiled_xscr", return_value=_ok_finalization_report()
            ) as finalize_mock, mock.patch.object(
                generation_commands, "_run_cli_fluent_context_check", return_value=(None, None, None)
            ), mock.patch(
                "fluent_pipeline.exports.export_ready_to_import",
                side_effect=AssertionError("ir-build must not publish ready-to-import bundles"),
            ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = generation_commands._build_ir_artifacts(
                    ir,
                    tmp_path / "out",
                    compile_xscr=True,
                    source_manifest=None,
                    catalog_db=None,
                    fluent_args=SimpleNamespace(fluent_context_check=False),
                )

            self.assertEqual(rc, 0)
            self.assertEqual(finalize_mock.call_count, 1)
            final_args = finalize_mock.call_args.args
            xscr_path = next((tmp_path / "out").glob("*.xscr"))
            self.assertEqual(Path(final_args[0]).resolve(), xscr_path.resolve())
            self.assertEqual(final_args[1], ir)
            self.assertIsNone(final_args[2])
            self.assertIsNone(final_args[3])
            self.assertEqual(final_args[4], {"source_ir_origin": "ir_build"})
            normalized_stdout = stdout.getvalue().replace("/private/var/", "/var/")
            self.assertIn(f"Compiled XSCR: {xscr_path}", normalized_stdout)
            self.assertIn(f"Compile report: {xscr_path.with_suffix('.compile.md')}", normalized_stdout)
            self.assertNotIn("Ready to import", normalized_stdout)
            self.assertIn("Compiled XSCR Finalization", xscr_path.with_suffix(".compile.md").read_text(encoding="utf-8"))

    def test_roundtrip_command_finalizes_compiled_xscr(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source = tmp_path / "source.xscr"
            source.write_text("<VxData><Payload /></VxData>", encoding="utf-8")
            workspace_root = tmp_path / "ready-to-import" / "unscoped" / "temp_files"
            out_dir = workspace_root / "roundtrip"
            args = SimpleNamespace(
                context=None,
                input=source,
                out_dir=out_dir,
                strict_decompile=False,
                strict_simulate=False,
                fail_on_opaque=False,
                min_coverage=0.0,
                fluent_context_check=False,
                fluent_method=None,
                fluent_provider="auto",
                fluent_command=None,
                fluent_host="127.0.0.1",
                fluent_port=50052,
                fluent_insecure=False,
                fluent_timeout=180.0,
            )

            def fake_fluentcoder(command, catalog_db=None):
                output_path = Path(command[-1])
                if command[0] == "decompile":
                    output_path.write_text("def build_worktable():\n    return None\n", encoding="utf-8")
                    return _ok_command_result("decompile")
                output_path.write_text("<VxData><Payload /></VxData>", encoding="utf-8")
                return _ok_command_result("compile")

            fake_plan = SimpleNamespace(to_dict=lambda: {"actions": []})

            with mock.patch.object(cli_runtime, "READY_TO_IMPORT_DIR", tmp_path / "ready-to-import"), mock.patch.object(
                generation_commands, "_command_context", return_value=None
            ), mock.patch.object(
                generation_commands, "ensure_project_catalog", return_value=None
            ), mock.patch.object(
                generation_commands, "run_fluentcoder", side_effect=fake_fluentcoder
            ), mock.patch.object(
                generation_commands, "_simulate_protocol", return_value=(_ok_command_result("simulate"), {"ok": True})
            ), mock.patch.object(
                generation_commands, "build_repair_plan", return_value=fake_plan
            ), mock.patch.object(
                generation_commands, "render_repair_markdown", return_value="# Repair\n"
            ), mock.patch.object(
                generation_commands, "_write_roundtrip_report"
            ), mock.patch.object(
                generation_commands, "finalize_compiled_xscr", return_value=_ok_finalization_report()
            ) as finalize_mock, mock.patch.object(
                generation_commands, "_run_cli_fluent_context_check", return_value=(None, None, None)
            ), mock.patch(
                "fluent_pipeline.exports.export_ready_to_import",
                side_effect=AssertionError("roundtrip must not publish ready-to-import bundles"),
            ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
                rc = generation_commands._cmd_roundtrip(args)

            self.assertEqual(rc, 0)
            compiled = out_dir / f"{source.stem}_roundtrip.xscr"
            self.assertEqual(finalize_mock.call_count, 1)
            final_args = finalize_mock.call_args.args
            self.assertEqual(Path(final_args[0]).resolve(), compiled.resolve())
            self.assertEqual(Path(final_args[1]).resolve(), source.resolve())
            self.assertIsNone(final_args[2])
            self.assertEqual([Path(item).resolve() for item in final_args[3]], [source.resolve()])
            self.assertEqual(final_args[4], {"source_ir_origin": "roundtrip_source_xscr"})
            normalized_stdout = stdout.getvalue().replace("/private/var/", "/var/")
            self.assertIn(f"Compiled XSCR: {compiled}", normalized_stdout)
            self.assertIn(f"Compile report: {out_dir / f'{source.stem}_compile.md'}", normalized_stdout)
            self.assertNotIn("Ready to import", normalized_stdout)
            self.assertIn("Compiled XSCR Finalization", (out_dir / f"{source.stem}_compile.md").read_text(encoding="utf-8"))


class AnalyzeCommandTests(unittest.TestCase):
    def test_analyze_writes_combined_report_from_existing_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            script_path = tmp_path / "script.xscr"
            script_path.write_text("<Root />", encoding="utf-8")
            out_dir = tmp_path / "analysis"
            ctx = SimpleNamespace(
                name="analysis-context",
                root=tmp_path,
                reports_dir=tmp_path / "reports",
                manifest={"scripts": [{"extracted_path": str(script_path), "object_name": "DemoScript"}]},
            )
            diagnosis = SimpleNamespace(
                report={
                    "summary": {
                        "status": "needs_review",
                        "finding_count": 1,
                        "top_likely_causes": [
                            {"id": "demo.finding", "severity": "medium", "title": "Review custom deck position"}
                        ],
                    },
                    "findings": [],
                },
                report_path=out_dir / "diagnostics" / "diagnosis.md",
                json_path=out_dir / "diagnostics" / "diagnosis.json",
            )
            script_report = {
                "what_it_does": ["Moves a plate for verification."],
                "potential_improvements": [
                    {"severity": "medium", "title": "Add an operator confirmation prompt"}
                ],
                "artifacts": {"script_analysis_markdown": str(out_dir / "script_report" / "demo.md")},
            }
            args = SimpleNamespace(
                input=script_path,
                script=None,
                script_index=1,
                context="analysis-context",
                fluent_script=None,
                fluent_folder=None,
                fluent_database=None,
                name=None,
                force_import=False,
                snapshot=[],
                error_text=None,
                error_file=None,
                log=None,
                latest_log=False,
                since_hours=48.0,
                max_files=12,
                max_records=80,
                out_dir=out_dir,
                max_commands=120,
                as_json=False,
            )

            with mock.patch("fluent_pipeline.cli._command_context", return_value=ctx), mock.patch(
                "fluent_pipeline.cli.diagnose_input", return_value=diagnosis
            ), mock.patch("fluent_pipeline.cli.analyze_script", return_value=script_report):
                rc = _cmd_analyze(args)

            self.assertEqual(rc, 0)
            analysis_md = out_dir / "analysis.md"
            analysis_json = out_dir / "analysis.json"
            self.assertTrue(analysis_md.exists())
            self.assertTrue(analysis_json.exists())
            text = analysis_md.read_text(encoding="utf-8")
            self.assertIn("Fluent AI-Assistance Analysis", text)
            self.assertIn("Review custom deck position", text)
            self.assertIn("Add an operator confirmation prompt", text)
            data = json.loads(analysis_json.read_text(encoding="utf-8"))
            self.assertEqual(data["analysis_version"], "tecan.analysis_report.v1")
            self.assertEqual(data["summary"]["status"], "needs_review")
            self.assertEqual(data["summary"]["improvement_count"], 1)

    def test_analyze_reads_saved_fluentcontrol_script_from_database(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            database = tmp_path / "DataBase"
            user_specific = database / "UserSpecific"
            user_specific.mkdir(parents=True)
            guid = "11111111-2222-4333-8444-555555555555"
            saved_script = user_specific / f"{guid}.xscr"
            saved_script.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>SavedMethod</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
  </Payload>
</VxData>
""",
                encoding="utf-8",
            )
            out_dir = tmp_path / "analysis"
            diagnosis = SimpleNamespace(
                report={"summary": {"status": "no_clear_static_fault", "finding_count": 0, "top_likely_causes": []}},
                report_path=out_dir / "diagnostics" / "diagnosis.md",
                json_path=out_dir / "diagnostics" / "diagnosis.json",
            )
            captured = {}

            def fake_diagnose(input_path, **kwargs):
                captured["diagnose_input"] = Path(input_path)
                return diagnosis

            def fake_analyze(ctx, **kwargs):
                captured["context"] = ctx
                return {
                    "what_it_does": ["Analyzed staged saved method."],
                    "potential_improvements": [],
                    "artifacts": {},
                }

            args = SimpleNamespace(
                input=None,
                script=None,
                script_index=1,
                context=None,
                fluent_script="SavedMethod",
                fluent_folder="Demo",
                fluent_database=database,
                name=None,
                force_import=False,
                snapshot=[],
                error_text=None,
                error_file=None,
                log=None,
                latest_log=False,
                since_hours=48.0,
                max_files=12,
                max_records=80,
                out_dir=out_dir,
                max_commands=120,
                as_json=False,
            )

            with mock.patch("fluent_pipeline.cli.diagnose_input", side_effect=fake_diagnose), mock.patch(
                "fluent_pipeline.cli.analyze_script", side_effect=fake_analyze
            ):
                rc = _cmd_analyze(args)

            self.assertEqual(rc, 0)
            staged = captured["diagnose_input"]
            self.assertTrue(staged.exists())
            self.assertNotEqual(staged.resolve(), saved_script.resolve())
            self.assertIn("local-fluent-script", str(staged))
            ctx = captured["context"]
            self.assertEqual(ctx.name, "local-fluent-database")
            self.assertEqual(ctx.manifest["local_fluent_script"]["object_name"], "SavedMethod")
            text = (out_dir / "analysis.md").read_text(encoding="utf-8")
            self.assertIn("Local FluentControl Source", text)
            self.assertIn("SavedMethod", text)
            data = json.loads((out_dir / "analysis.json").read_text(encoding="utf-8"))
            self.assertEqual(data["local_fluent_script"]["guid"], guid)
            self.assertEqual(data["input"]["path"], str(staged))


class WorkspaceLogPathTests(unittest.TestCase):
    def test_default_generation_dir_enforces_versioned_folder_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            build_dir = root / "build"
            generations = build_dir / "generations"
            ctx = SimpleNamespace(build_dir=build_dir)

            self.assertEqual(cli_runtime._default_generation_dir(ctx, "Demo Run").name, "demo-run_v1")
            (generations / "demo-run").mkdir(parents=True)
            self.assertEqual(cli_runtime._default_generation_dir(ctx, "Demo Run").name, "demo-run_v2")
            (generations / "demo-run_v2").mkdir()
            self.assertEqual(cli_runtime._default_generation_dir(ctx, "Demo Run").name, "demo-run_v3")

    def test_workflow_event_log_path_uses_logs_dir_and_out_dir_label(self):
        path = workflow_event_log_path("generated_script_final")
        self.assertEqual(path.name, "generated-script-final.events.jsonl")
        self.assertEqual(path.parent.name, "logs")

    def test_resolve_generation_event_log_defaults_to_generation_temp_logs(self):
        args = SimpleNamespace(event_log=None, no_event_log=False)
        out_dir = Path("ready-to-import/demo/temp_files/build/generations/generated_script")
        resolved = _resolve_generation_event_log(args, out_dir)
        self.assertEqual(resolved, (out_dir / "logs" / "generation.events.jsonl").resolve())

    def test_resolve_generation_event_log_honors_no_event_log(self):
        args = SimpleNamespace(event_log=None, no_event_log=True)
        self.assertIsNone(_resolve_generation_event_log(args, Path("build/generated_script")))


class FluentcoderCliArgsTests(unittest.TestCase):
    def test_build_simulate_command_includes_subroutine_dir_and_no_snapshots(self):
        protocol = Path("draft.py")
        sub_dir = Path("/tmp/subroutines")
        command = build_simulate_command(
            protocol,
            subroutine_dirs=[sub_dir],
            record_snapshots=False,
        )
        self.assertEqual(command[:3], ["simulate", protocol, "--json"])
        self.assertIn("--subroutine-dir", command)
        self.assertIn(str(sub_dir), command)
        self.assertIn("--no-snapshots", command)

    def test_build_simulate_command_includes_delta_snapshots(self):
        command = build_simulate_command(
            Path("draft.py"),
            record_snapshots="delta",
        )
        self.assertEqual(command[:3], ["simulate", Path("draft.py"), "--json"])
        self.assertIn("--delta-snapshots", command)

    def test_build_compile_command_includes_deterministic_flag(self):
        command = build_compile_command(
            Path("draft.py"),
            Path("out.xscr"),
            deterministic=True,
        )
        self.assertEqual(command, ["compile", Path("draft.py"), "-o", Path("out.xscr"), "--deterministic"])


class MapMediaCommandTests(unittest.TestCase):
    TOUCHTOOLS = r"C:\ProgramData\Tecan\VisionX\TouchToolsData\X\Script Files"

    def _media_ir(self):
        return annotate_verification_prompts_with_media(
            {
                "ir_version": CANONICAL_IR_VERSION,
                "id": "map_media_test",
                "protocol": {"name": "Map media test"},
                "source": {"format": "test", "path": ""},
                "worktable": {"name": "780_Empty"},
                "labware": [],
                "steps": [
                    {
                        "index": 1,
                        "id": "step_001",
                        "group": "Arm verification",
                        "operation": "prompt_user",
                        "command_id": "UserPromptStatement",
                        "name": "Prompt User",
                        "parameters": {"prompt": "Confirm the RGA fingers are parallel.", "timeout": 0},
                    }
                ],
            }
        )

    def _args(self, input_path, out_dir=None, subfolder=None, as_json=False):
        return SimpleNamespace(
            input=input_path,
            touchtools_dir=self.TOUCHTOOLS,
            subfolder=subfolder,
            context=None,
            output=out_dir,
            as_json=as_json,
        )

    def test_map_media_writes_reports_from_out_dir_ir(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            ir = self._media_ir()
            write_protocol_ir(ir, out_dir / "map_media_test.protocol-ir.json")
            with mock.patch("fluent_pipeline.cli._command_context", return_value=None):
                rc = _cmd_map_media(self._args(out_dir))
            self.assertEqual(rc, 0)
            md = (out_dir / "media_path_map.md").read_text(encoding="utf-8")
            data = json.loads((out_dir / "media_path_map.json").read_text(encoding="utf-8"))
            self.assertIn("media/step_001_image.png", md)
            self.assertEqual(data["touchtools_dir"], self.TOUCHTOOLS)
            self.assertEqual(data["image_count"], 1)
            self.assertEqual(data["video_count"], 1)
            image = next(e for e in data["entries"] if e["kind"] == "image")
            self.assertEqual(
                image["absolute_path"],
                self.TOUCHTOOLS + r"\Map_media_test_media\step_001_image.png",
            )

    def test_map_media_reads_media_placeholders_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            ir = self._media_ir()
            rows = collect_media_placeholders(ir)
            (out_dir / "media_placeholders.json").write_text(
                json.dumps({"protocol": "Map media test", "prompts": rows}, indent=2),
                encoding="utf-8",
            )
            with mock.patch("fluent_pipeline.cli._command_context", return_value=None):
                rc = _cmd_map_media(self._args(out_dir))
            self.assertEqual(rc, 0)
            data = json.loads((out_dir / "media_path_map.json").read_text(encoding="utf-8"))
            self.assertEqual(data["protocol"], "Map media test")
            self.assertEqual(data["image_count"], 1)

    def test_map_media_json_mode_does_not_write_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            ir = self._media_ir()
            write_protocol_ir(ir, out_dir / "map_media_test.protocol-ir.json")
            with mock.patch("fluent_pipeline.cli._command_context", return_value=None):
                rc = _cmd_map_media(self._args(out_dir, as_json=True))
            self.assertEqual(rc, 0)
            self.assertFalse((out_dir / "media_path_map.md").exists())
            self.assertFalse((out_dir / "media_path_map.json").exists())


if __name__ == "__main__":
    unittest.main()
