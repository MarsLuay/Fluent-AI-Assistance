import json
import os
import tempfile
import unittest
import zipfile
from contextlib import ExitStack
from pathlib import Path
from unittest import mock

import fluent_pipeline.exports as exports
from fluent_pipeline.compiled_xscr_finalizer import FinalizationReport
from fluent_pipeline.checksum import stamp_checksum
from fluent_pipeline.runner import PipelineError
from fluent_pipeline.subroutine_dependencies import resolve_subroutine_dependencies
from fluent_pipeline.validation import scaffold_validation_report
from fluent_pipeline.zeia_filesystem import build_fs_mapping_xml


def _read_archive_text(zf: zipfile.ZipFile, entry: str) -> str:
    expected = exports._normalize_archive_entry(entry)
    for name in zf.namelist():
        if exports._normalize_archive_entry(name) == expected:
            return zf.read(name).decode("utf-8-sig")
    raise KeyError(f"There is no logical archive entry named {entry!r}")


def _ok_finalization_report() -> FinalizationReport:
    return FinalizationReport(
        ok=True,
        checksum_before="valid",
        checksum_after="valid",
        roundtrip={"matched": True},
        command_validation={"failure_count": 0},
        generic_command_validation={"failure_count": 0},
    )


class ProjectScriptPayloadTests(unittest.TestCase):
    def test_target_script_folder_overrides_existing_object_subfolder_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            xscr = Path(tmp) / "compiled.xscr"
            xscr.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Generated</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
  </Payload>
  <Checksum>valid</Checksum>
</VxData>
""",
                encoding="utf-8",
            )

            payload = exports._prepare_project_script_payload(
                xscr,
                fallback_folder="Demo",
                target_folder="Demo scripts",
            ).decode("utf-8-sig")

            self.assertIn("<ObjectSubfolderPath>Demo scripts</ObjectSubfolderPath>", payload)
            self.assertNotIn("<ObjectSubfolderPath>Demo</ObjectSubfolderPath>", payload)
            self.assertIn("<Checksum></Checksum>", payload)


class SubroutineResolutionTests(unittest.TestCase):
    def _scripts(self):
        return [
            {
                "object_name": "HelperSub",
                "qualified_name": "ctxA:HelperSub",
                "entry": "Subroutines/HelperSub.xscr",
                "extracted_path": "/a/Subroutines/HelperSub.xscr",
                "source_context": "ctxA",
                "context_root": "/a",
            },
            {
                "object_name": "HelperSub",
                "qualified_name": "ctxB:HelperSub",
                "entry": "Subroutines/HelperSub.xscr",
                "extracted_path": "/b/Subroutines/HelperSub.xscr",
                "source_context": "ctxB",
                "context_root": "/b",
            },
        ]

    def test_same_context_parent_disambiguates(self):
        scripts = self._scripts()
        match, alternatives = exports._find_subroutine_record(
            {"root": "/a"}, scripts, "HelperSub", {"source_context": "ctxB", "context_root": "/b"}
        )
        self.assertEqual(match["source_context"], "ctxB")
        self.assertEqual(alternatives, [])

    def test_cross_context_ref_is_flagged_ambiguous(self):
        scripts = self._scripts()
        # Parent context not present among matches -> genuinely ambiguous.
        match, alternatives = exports._find_subroutine_record(
            {"root": "/c"}, scripts, "HelperSub", {"source_context": "ctxC", "context_root": "/c"}
        )
        self.assertIsNotNone(match)
        self.assertEqual(len(alternatives), 1)
        self.assertEqual(match["source_context"], "ctxA")  # deterministic sort
        self.assertEqual(alternatives[0]["source_context"], "ctxB")

    def test_exact_object_name_beats_stem_collision(self):
        scripts = [
            {"object_name": "Spin", "entry": "Subroutines/SUB_Spin_Tube_v0.1.xscr", "source_context": "ctxA"},
            {"object_name": "SUB_Spin_Tube_v0.1", "entry": "Subroutines/SUB_Spin_Tube_v0.1.xscr", "source_context": "ctxA"},
        ]
        match, alternatives = exports._find_subroutine_record(
            {"root": "/a"}, scripts, "SUB_Spin_Tube_v0.1", {"source_context": "ctxA"}
        )
        self.assertEqual(match["object_name"], "SUB_Spin_Tube_v0.1")
        self.assertEqual(alternatives, [])

    def test_duplicate_context_records_with_same_guid_are_not_ambiguous(self):
        scripts = self._scripts()
        for script in scripts:
            script["guid"] = "11111111-2222-4333-8444-555555555555"
        match, alternatives = exports._find_subroutine_record(
            {"root": "/c"}, scripts, "HelperSub", {"source_context": "ctxC", "context_root": "/c"}
        )
        self.assertIsNotNone(match)
        self.assertEqual(alternatives, [])

    def test_subroutine_artifacts_dedupe_same_folder_and_object_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "first.xscr"
            second = root / "second.xscr"
            first.write_text("<VxData />", encoding="utf-8")
            second.write_text("<VxData />", encoding="utf-8")

            artifacts = exports._dedupe_subroutine_artifacts(
                [
                    {
                        "path": first,
                        "ref": r"Demo\SUB_CapBCScanHandeling_50mL_v0.2",
                        "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                        "folder": "Demo",
                        "guid": "first-guid",
                    },
                    {
                        "path": second,
                        "ref": r"Demo\SUB_CapBCScanHandeling_50mL_v0.2",
                        "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                        "folder": "Demo",
                        "guid": "second-guid",
                    },
                ]
            )

        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["guid"], "first-guid")

    def test_ir_selected_source_context_disambiguates_dependencies(self):
        scripts = self._scripts()
        ir = {
            "source": {
                "selected_source_scripts": [
                    {"source_context": "ctxB", "context_root": "/b"},
                ]
            },
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "operation": "call_subroutine",
                    "parameters": {"subroutine": "HelperSub"},
                }
            ],
        }

        report = resolve_subroutine_dependencies(ir, {"root": "/c", "scripts": scripts})

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["resolved"][0]["source_context"], "ctxB")


class ExportFinalizationTests(unittest.TestCase):
    def test_publish_bundle_replacement_retries_transient_windows_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "staged"
            published = root / "published"
            backup = root / "backup"
            staged.mkdir()
            published.mkdir()
            (staged / "state.txt").write_text("new", encoding="utf-8")
            (published / "state.txt").write_text("old", encoding="utf-8")

            real_replace = os.replace
            staged_publish_attempts = 0

            def replace_with_one_lock(source, destination):
                nonlocal staged_publish_attempts
                if Path(source) == staged and Path(destination) == published:
                    staged_publish_attempts += 1
                    if staged_publish_attempts == 1:
                        raise PermissionError(5, "Access is denied", str(source))
                return real_replace(source, destination)

            with mock.patch.object(exports.os, "replace", side_effect=replace_with_one_lock), mock.patch.object(
                exports.time, "sleep"
            ) as sleep:
                exports._publish_bundle_replacement(staged, published, backup_bundle=backup)

            self.assertEqual(staged_publish_attempts, 2)
            sleep.assert_called_once_with(0.5)
            self.assertEqual((published / "state.txt").read_text(encoding="utf-8"), "new")
            self.assertEqual((backup / "state.txt").read_text(encoding="utf-8"), "old")

    def test_next_available_bundle_name_enforces_versioned_folder_suffix(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            self.assertEqual(exports._next_available_bundle_name(root, "demo"), "demo_v1")
            (root / "demo").mkdir()
            self.assertEqual(exports._next_available_bundle_name(root, "demo"), "demo_v2")
            (root / "demo_v2").mkdir()
            self.assertEqual(exports._next_available_bundle_name(root, "demo"), "demo_v3")
            self.assertEqual(exports._next_available_bundle_name(root, "other_v2"), "other_v2")

    def test_stages_external_payloads_with_exact_manifest_paths_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "generated.zeia"
            bundle = root / "bundle"
            bundle.mkdir()
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr(
                    "fs/mapping.xml",
                    build_fs_mapping_xml(
                        [
                            (1, r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\demo_media"),
                            (16, r"C:\TecanInformation\Labware Images"),
                        ]
                    ),
                )
                output.writestr("fs/1/prompt.png", b"prompt")
                output.writestr("fs/16/shared.png", b"external")

            deployments = exports._stage_external_file_deployments(archive, bundle_dir=bundle)

            self.assertEqual(
                deployments,
                [
                    {
                        "bundle_path": "source/external-files/16/shared.png",
                        "target_path": r"C:\TecanInformation\Labware Images\shared.png",
                        "sha256": "3c4623849a49a53911c4a3e48d8cead8a1858960bccdea7a1b978d73ec2f06d7",
                    }
                ],
            )
            self.assertEqual(
                (bundle / "source" / "external-files" / "16" / "shared.png").read_bytes(),
                b"external",
            )

    def test_export_ready_to_import_runs_compiled_xscr_finalizer_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            compiled_xscr = tmp_path / "compiled.xscr"
            draft_path = tmp_path / "draft.py"
            compiled_xscr.write_text("<VxData />", encoding="utf-8")
            draft_path.write_text("def build_worktable():\n    return None\n", encoding="utf-8")
            finalization_report = FinalizationReport(
                ok=True,
                checksum_before="valid",
                checksum_after="valid",
                roundtrip={"matched": True},
                command_validation={"failure_count": 0},
                generic_command_validation={"failure_count": 0},
            )

            with mock.patch.object(exports, "finalize_compiled_xscr", return_value=finalization_report) as finalize_mock, mock.patch.object(
                exports,
                "validate_ready_to_import",
                return_value=scaffold_validation_report("synthetic validation stop"),
            ):
                with self.assertRaises(PipelineError):
                    exports.export_ready_to_import(
                        compiled_xscr,
                        draft_path=draft_path,
                        source_manifest={},
                    )

            finalize_mock.assert_called_once_with(
                compiled_xscr,
                draft_path,
                {},
                [],
                {"source_ir_origin": "export_ready_to_import"},
            )

    def test_staged_ready_bundle_receives_final_reports_before_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_ready_dir = exports.READY_TO_IMPORT_DIR
            exports.READY_TO_IMPORT_DIR = tmp_path / "ready-to-import"
            compiled_xscr = tmp_path / "compiled.xscr"
            draft_path = tmp_path / "draft.py"
            compiled_xscr.write_text("<VxData />", encoding="utf-8")
            draft_path.write_text("def build_worktable():\n    return None\n", encoding="utf-8")
            finalization_report = FinalizationReport(
                ok=True,
                checksum_before="valid",
                checksum_after="valid",
                roundtrip={"matched": True},
                command_validation={"failure_count": 0},
                generic_command_validation={"failure_count": 0},
            )
            ready_validation = {
                "ready": True,
                "gates": [],
                "validation_version": "test",
                "offline_validation": {
                    "status": "ready_to_import",
                    "summary": "All required offline readiness gates passed.",
                    "required_gate_count": 0,
                    "passed_count": 0,
                    "failed_count": 0,
                },
                "review_state": {
                    "status": "hardware_review_required",
                    "summary": "Offline validation passed; hardware review is still required.",
                    "needs_review_count": 0,
                    "gates": [],
                },
                "fluentcontrol_load_diagnostic": {
                    "status": "not_run",
                    "summary": "Optional FluentControl import/load diagnostic did not run.",
                    "requested": False,
                    "gate": "Gate 27",
                },
            }

            try:
                with mock.patch.object(exports, "finalize_compiled_xscr", return_value=finalization_report), mock.patch.object(
                    exports,
                    "validate_ready_to_import",
                    return_value=ready_validation,
                ):
                    stage = exports.export_ready_to_import(
                        compiled_xscr,
                        draft_path=draft_path,
                        source_manifest={},
                        publish=False,
                    )

                self.assertIsInstance(stage, exports.ReadyBundleStage)
                generation_manifest = tmp_path / "generation_manifest.json"
                workflow_report = tmp_path / "GENERATION_WORKFLOW.md"
                manifest_payload = {
                    "ready_to_import": True,
                    "workflow_status": "ready_to_import",
                    "packaged_bundle_dir": str(stage.final_bundle_dir),
                    "ready_to_import_artifacts": [
                        str(stage.final_bundle_dir / "source" / "protocol.ir.json"),
                        str(stage.final_bundle_dir / "source" / "metadata.json"),
                        str(stage.final_bundle_dir / "source" / "generation_manifest.json"),
                        str(stage.final_bundle_dir / "source" / "GENERATION_WORKFLOW.md"),
                    ],
                }
                generation_manifest.write_text(json.dumps(manifest_payload, indent=2), encoding="utf-8")
                workflow_report.write_text("# Generation Workflow\n", encoding="utf-8")

                attached = exports.attach_generation_reports_to_bundle(
                    stage.script_dir,
                    generation_manifest=generation_manifest,
                    workflow_report=workflow_report,
                )
                self.assertEqual(len(attached), 2)
                stage.exports.extend(attached)

                audit = exports.audit_ready_bundle(
                    stage.script_dir,
                    expected_bundle_dir=stage.final_bundle_dir,
                    require_generation_reports=True,
                )
                self.assertEqual(audit["status"], "passed")
                self.assertEqual(audit["blocking"], [])
                self.assertTrue((stage.script_dir / "source" / "generation_manifest.json").exists())
                self.assertTrue((stage.script_dir / "source" / "GENERATION_WORKFLOW.md").exists())

                zeia = stage.script_dir / "generated_project.zeia"
                zeia.write_bytes(b"generated zeia")
                stage.exports.append(exports.ExportedArtifact(zeia, zeia, "generated-project-archive"))
                (stage.script_dir / "RECREATE_SCRIPT.md").write_text("# Recreate\n", encoding="utf-8")
                (stage.script_dir / "source" / "request.spec.yaml").write_text(
                    "schema_version: tecan.request_spec.v1\nrequest:\n  intent: test\n",
                    encoding="utf-8",
                )
                if not (stage.script_dir / "source" / "protocol.ir.json").exists():
                    (stage.script_dir / "source" / "protocol.ir.json").write_text("{}", encoding="utf-8")
                if not (stage.script_dir / "source" / "protocol_draft.py").exists():
                    (stage.script_dir / "source" / "protocol_draft.py").write_text(
                        "def build_worktable():\n    pass\n",
                        encoding="utf-8",
                    )
                (stage.script_dir / "source" / "reports").mkdir(parents=True, exist_ok=True)

                published = exports.publish_ready_to_import_zeia(stage)
                published_root = published[0].destination.parent
                self.assertTrue(any(item.kind == "fluent-project-archive" for item in published))
                self.assertTrue((published_root / f"{published_root.name}.zeia").exists())
                self.assertTrue((published_root / "run_tecan_bundle_setup.bat").exists())
            finally:
                exports.READY_TO_IMPORT_DIR = old_ready_dir

    def test_publish_ready_to_import_bundle_is_removed(self):
        stage = exports.ReadyBundleStage(
            staging_root=Path("staging"),
            script_dir=Path("staging/demo"),
            bundle_name="demo",
            final_bundle_dir=Path("ready/demo"),
            failed_bundle_dir=Path("failed/demo"),
            validation_report={"ready": True},
            verification_state="offline_validated",
            exports=[],
            metadata_path=Path("staging/demo/metadata.json"),
        )
        with self.assertRaises(PipelineError) as ctx:
            exports.publish_ready_to_import_bundle(stage)
        self.assertIn("publish_ready_to_import_zeia", str(ctx.exception))

    def test_publish_ready_to_import_zeia_publishes_only_zeia(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_ready_dir = exports.READY_TO_IMPORT_DIR
            exports.READY_TO_IMPORT_DIR = tmp_path / "ready-to-import"
            try:
                staging_root = tmp_path / "staging"
                script_dir = staging_root / "demo"
                script_dir.mkdir(parents=True)
                xscr = script_dir / "protocol.xscr"
                zeia = script_dir / "generated_project.zeia"
                metadata = script_dir / "metadata.json"
                source_dir = script_dir / "source"
                reports_dir = source_dir / "reports"
                reports_dir.mkdir(parents=True)
                xscr.write_text("<Root />", encoding="utf-8")
                zeia.write_bytes(b"generated zeia")
                metadata.write_text("{}", encoding="utf-8")
                (script_dir / "RECREATE_SCRIPT.md").write_text("# Recreate\n", encoding="utf-8")
                (source_dir / "request.spec.yaml").write_text("request: {}\n", encoding="utf-8")
                (source_dir / "protocol.ir.json").write_text("{}", encoding="utf-8")
                (source_dir / "protocol_draft.py").write_text("def build_worktable():\n    pass\n", encoding="utf-8")
                (reports_dir / "validation_report.json").write_text("{}", encoding="utf-8")
                stage = exports.ReadyBundleStage(
                    staging_root=staging_root,
                    script_dir=script_dir,
                    bundle_name="demo",
                    final_bundle_dir=exports.READY_TO_IMPORT_DIR / "demo",
                    failed_bundle_dir=tmp_path / "failed" / "demo",
                    validation_report={"ready": True},
                    verification_state="offline_validated",
                    exports=[
                        exports.ExportedArtifact(xscr, xscr, "compiled-script"),
                        exports.ExportedArtifact(zeia, zeia, "generated-project-archive"),
                    ],
                    metadata_path=metadata,
                )

                published = exports.publish_ready_to_import_zeia(stage)

                self.assertEqual([item.destination.name for item in published], ["demo_v1.zeia"])
                self.assertEqual([item.destination.parent.name for item in published], ["demo_v1"])
                self.assertEqual(
                    [item.name for item in exports.READY_TO_IMPORT_DIR.iterdir()],
                    ["demo_v1"],
                )
                published_root = exports.READY_TO_IMPORT_DIR / "demo_v1"
                self.assertEqual((published_root / "demo_v1.zeia").read_bytes(), b"generated zeia")
                self.assertTrue((published_root / "RECREATE_SCRIPT.md").exists())
                self.assertFalse((published_root / "request.spec.yaml").exists())
                self.assertFalse((published_root / "protocol.ir.json").exists())
                self.assertFalse((published_root / "generated").exists())
                self.assertFalse((published_root / "reports").exists())
                self.assertFalse((published_root / "delivery_manifest.json").exists())
                self.assertTrue((published_root / "source" / "request.spec.yaml").exists())
                self.assertTrue((published_root / "source" / "protocol.ir.json").exists())
                self.assertTrue((published_root / "source" / "generated" / "protocol.py").exists())
                self.assertTrue((published_root / "source" / "reports" / "validation_report.json").exists())
                self.assertTrue((published_root / "source" / "delivery_manifest.json").exists())
                self.assertTrue((published_root / "source" / "metadata.json").exists())
                self.assertTrue((published_root / "media" / "media_manifest.json").exists())
                setup_bat = published_root / "run_tecan_bundle_setup.bat"
                self.assertEqual(
                    [path.name for path in published_root.glob("*.bat")],
                    ["run_tecan_bundle_setup.bat"],
                )
                setup_text = setup_bat.read_text(encoding="utf-8")
                helper = published_root / "source" / "collect_tecan_diagnostic_bundle.ps1"
                self.assertTrue(helper.exists())
                progress_helper = published_root / "source" / "copy_tree_with_progress.ps1"
                self.assertTrue(progress_helper.exists())
                stall_helper = published_root / "source" / "stall_watchdog.ps1"
                self.assertTrue(stall_helper.exists())
                install_helper = published_root / "source" / "install_external_files.ps1"
                self.assertTrue(install_helper.exists())
                deploy_helper = published_root / "source" / "deploy_touchtools_media.ps1"
                self.assertTrue(deploy_helper.exists())
                self.assertIn("copy_tree_with_progress.ps1", setup_text)
                self.assertIn("stall_watchdog.ps1", setup_text)
                self.assertIn("install_external_files.ps1", setup_text)
                self.assertIn("deploy_touchtools_media.ps1", setup_text)
                self.assertIn("tecan_bundle_setup_STALL.error.txt", setup_text)
                self.assertIn("--logs-only", setup_text)
                self.assertIn("--logs-menu", setup_text)
                self.assertIn("--log-profile", setup_text)
                self.assertIn("--collect-instrument", setup_text)
                self.assertIn("--collect-method-source", setup_text)
                self.assertIn("--install-external-files", setup_text)
                self.assertIn("--deploy-touchtools", setup_text)
                install_text = install_helper.read_text(encoding="utf-8")
                self.assertIn("external_file_deployments", install_text)
                self.assertIn("Get-FileHash", install_text)
                deploy_text = deploy_helper.read_text(encoding="utf-8")
                self.assertIn("Get-FileHash", deploy_text)
                self.assertIn("Write-VisibleProgress", deploy_text)
                self.assertIn("--install-instrument", setup_text)
                self.assertIn(":phase_collect_logs", setup_text)
                self.assertIn(":phase_collect_instrument", setup_text)
                self.assertIn(":phase_collect_method_source", setup_text)
                self.assertIn(":phase_install_external", setup_text)
                self.assertIn(":phase_install_instrument", setup_text)
                self.assertIn(":phase_deploy_touchtools", setup_text)
                self.assertIn("1. Collect Logs", setup_text)
                self.assertIn("2. Collect/Install Drivers and Configs", setup_text)
                self.assertIn("3. Deploy TouchTools media", setup_text)
                self.assertNotIn("--externals-only", setup_text)
                self.assertNotIn("--media-only", setup_text)
                self.assertNotIn("--verify-only", setup_text)
                self.assertNotIn(":phase_install_external_files", setup_text)
                self.assertNotIn(":phase_deploy_media", setup_text)
                self.assertIn("collect_tecan_diagnostic_bundle.ps1", setup_text)
                self.assertNotIn("deploy_touchtools_images.bat", setup_text)
                self.assertFalse(any(exports.READY_TO_IMPORT_DIR.rglob("*.xscr")))
                self.assertFalse(staging_root.exists())
            finally:
                exports.READY_TO_IMPORT_DIR = old_ready_dir

    def test_ready_bundle_publish_plan_always_returns_versioned_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            ready_root = Path(tmp) / "ready-to-import"
            ready_root.mkdir()
            (ready_root / "demo").mkdir()
            (ready_root / "demo_v2").mkdir()
            (ready_root / "demo_v3.zeia").write_bytes(b"legacy loose")

            plan = exports.plan_ready_to_import_publish(ready_root, "demo", run_id="test-run")

            self.assertEqual(plan.bundle_name, "demo_v4")
            self.assertEqual(plan.bundle_dir, ready_root.resolve() / "demo_v4")
            self.assertEqual(plan.archive_path, ready_root.resolve() / "demo_v4" / "demo_v4.zeia")
            self.assertEqual(plan.staging_dir.name, ".demo_v4.test-run.staging")
            self.assertEqual(plan.backup_dir.name, ".demo_v4.test-run.backup")

    def test_publish_ready_to_import_zeia_replaces_protocol_folder_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_ready_dir = exports.READY_TO_IMPORT_DIR
            exports.READY_TO_IMPORT_DIR = tmp_path / "ready-to-import"
            try:
                existing = exports.READY_TO_IMPORT_DIR / "demo"
                existing.mkdir(parents=True)
                (existing / "demo.zeia").write_bytes(b"old zeia")
                (exports.READY_TO_IMPORT_DIR / "demo.zeia").write_bytes(b"legacy loose")

                staging_root = tmp_path / "staging"
                script_dir = staging_root / "demo"
                script_dir.mkdir(parents=True)
                zeia = script_dir / "generated_project.zeia"
                metadata = script_dir / "metadata.json"
                source_dir = script_dir / "source"
                reports_dir = source_dir / "reports"
                reports_dir.mkdir(parents=True)
                zeia.write_bytes(b"new zeia")
                metadata.write_text("{}", encoding="utf-8")
                (script_dir / "RECREATE_SCRIPT.md").write_text("# Recreate\n", encoding="utf-8")
                (source_dir / "request.spec.yaml").write_text("request: {}\n", encoding="utf-8")
                (source_dir / "protocol.ir.json").write_text("{}", encoding="utf-8")
                (source_dir / "protocol_draft.py").write_text("def build_worktable():\n    pass\n", encoding="utf-8")
                (reports_dir / "validation_report.json").write_text("{}", encoding="utf-8")
                stage = exports.ReadyBundleStage(
                    staging_root=staging_root,
                    script_dir=script_dir,
                    bundle_name="demo_v2",
                    final_bundle_dir=exports.READY_TO_IMPORT_DIR / "demo",
                    failed_bundle_dir=tmp_path / "failed" / "demo",
                    validation_report={"ready": True},
                    verification_state="offline_validated",
                    exports=[exports.ExportedArtifact(zeia, zeia, "generated-project-archive")],
                    metadata_path=metadata,
                    protocol_name="demo",
                )

                published = exports.publish_ready_to_import_zeia(stage)

                published_root = exports.READY_TO_IMPORT_DIR / "demo_v2"
                self.assertEqual(published[0].destination.resolve(), (published_root / "demo_v2.zeia").resolve())
                self.assertEqual((published_root / "demo_v2.zeia").read_bytes(), b"new zeia")
                self.assertEqual((exports.READY_TO_IMPORT_DIR / "demo" / "demo.zeia").read_bytes(), b"old zeia")
                self.assertTrue((published_root / "RECREATE_SCRIPT.md").exists())
                self.assertTrue((published_root / "source" / "generated" / "protocol.py").exists())
                self.assertFalse((exports.READY_TO_IMPORT_DIR / "demo.zeia").exists())
                self.assertFalse(staging_root.exists())
            finally:
                exports.READY_TO_IMPORT_DIR = old_ready_dir

    def test_protocol_folder_replacement_restores_previous_folder_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            published = tmp_path / "ready-to-import" / "demo"
            staged = tmp_path / "staged-demo"
            backup = tmp_path / "ready-to-import" / ".demo.backup"
            published.mkdir(parents=True)
            staged.mkdir(parents=True)
            (published / "demo.zeia").write_bytes(b"old zeia")
            (staged / "demo.zeia").write_bytes(b"new zeia")
            real_move = exports._move_protocol_directory

            def fail_new_publish(source, destination):
                if Path(source) == staged and Path(destination) == published:
                    raise OSError("swap failed")
                return real_move(source, destination)

            with mock.patch.object(exports, "_move_protocol_directory", side_effect=fail_new_publish):
                with self.assertRaisesRegex(PipelineError, "swap failed"):
                    exports._publish_protocol_folder_replacement(
                        staged,
                        published,
                        backup_dir=backup,
                    )

            self.assertEqual((published / "demo.zeia").read_bytes(), b"old zeia")
            self.assertFalse(backup.exists())

    def test_attach_generation_reports_to_protocol_folder_updates_complete_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            ready_root = tmp_path / "ready-to-import"
            protocol_dir = ready_root / "demo"
            source_dir = protocol_dir / "source"
            reports_dir = source_dir / "reports"
            generated_dir = source_dir / "generated"
            media_dir = protocol_dir / "media"
            reports_dir.mkdir(parents=True)
            generated_dir.mkdir()
            media_dir.mkdir()
            zeia = protocol_dir / "demo.zeia"
            zeia.write_bytes(b"zeia")
            (protocol_dir / "RECREATE_SCRIPT.md").write_text("# Recreate\n", encoding="utf-8")
            (source_dir / "request.spec.yaml").write_text("request: {}\n", encoding="utf-8")
            (source_dir / "protocol.ir.json").write_text("{}", encoding="utf-8")
            (source_dir / "metadata.json").write_text("{}", encoding="utf-8")
            (generated_dir / "protocol.py").write_text("def build_worktable():\n    pass\n", encoding="utf-8")
            (protocol_dir / "run_tecan_bundle_setup.bat").write_text("@echo off\n", encoding="utf-8")
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
                        "companion_artifacts": [],
                    }
                ),
                encoding="utf-8",
            )
            generation_manifest = tmp_path / "generation_manifest.json"
            workflow_report = tmp_path / "GENERATION_WORKFLOW.md"
            validation_json = tmp_path / "validation.json"
            xscr = tmp_path / "internal.xscr"
            generation_manifest.write_text('{"workflow_status": "ready_to_import"}', encoding="utf-8")
            workflow_report.write_text("# Workflow\n", encoding="utf-8")
            validation_json.write_text('{"ready": true}', encoding="utf-8")
            xscr.write_text("<Root />", encoding="utf-8")

            attached = exports.attach_generation_reports_to_protocol_folders(
                [zeia],
                ready_root=ready_root,
                generation_manifest=generation_manifest,
                workflow_report=workflow_report,
                companion_files={
                    "reports/validation.json": validation_json,
                    "reports/internal.xscr": xscr,
                },
            )

            self.assertFalse((protocol_dir / "generation_manifest.json").exists())
            self.assertFalse((protocol_dir / "GENERATION_WORKFLOW.md").exists())
            self.assertTrue((protocol_dir / "source" / "generation_manifest.json").exists())
            self.assertTrue((protocol_dir / "source" / "GENERATION_WORKFLOW.md").exists())
            self.assertTrue((protocol_dir / "source" / "reports" / "validation.json").exists())
            self.assertFalse((protocol_dir / "source" / "reports" / "internal.xscr").exists())
            self.assertTrue(any(item.kind == "generation-manifest" for item in attached))
            delivery_manifest = json.loads((protocol_dir / "source" / "delivery_manifest.json").read_text(encoding="utf-8"))
            self.assertIn(
                {"kind": "generation_manifest", "path": "source/generation_manifest.json"},
                delivery_manifest["companion_artifacts"],
            )

    def test_publish_ready_to_import_zeia_rejects_xscr_only_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_ready_dir = exports.READY_TO_IMPORT_DIR
            exports.READY_TO_IMPORT_DIR = tmp_path / "ready-to-import"
            try:
                staging_root = tmp_path / "staging"
                script_dir = staging_root / "demo"
                script_dir.mkdir(parents=True)
                xscr = script_dir / "protocol.xscr"
                metadata = script_dir / "metadata.json"
                xscr.write_text("<Root />", encoding="utf-8")
                metadata.write_text("{}", encoding="utf-8")
                stage = exports.ReadyBundleStage(
                    staging_root=staging_root,
                    script_dir=script_dir,
                    bundle_name="demo",
                    final_bundle_dir=exports.READY_TO_IMPORT_DIR / "demo",
                    failed_bundle_dir=tmp_path / "failed" / "demo",
                    validation_report={"ready": True},
                    verification_state="offline_validated",
                    exports=[exports.ExportedArtifact(xscr, xscr, "compiled-script")],
                    metadata_path=metadata,
                )

                with self.assertRaisesRegex(PipelineError, "Only generated ZEIA archives"):
                    exports.publish_ready_to_import_zeia(stage)

                self.assertFalse(exports.READY_TO_IMPORT_DIR.exists())
                self.assertTrue(staging_root.exists())
            finally:
                exports.READY_TO_IMPORT_DIR = old_ready_dir


class UnresolvedReferenceTests(unittest.TestCase):
    LC_GUID = "0be7658e-f376-40ee-ad88-1f9e772c47be"

    def _payload(self, *, use_liquid_class: bool) -> bytes:
        usage = "<LiquidClassName>Water Free Single</LiquidClassName>" if use_liquid_class else ""
        return (
            "<VxData><Payload>"
            f"<Reference><Guid>{self.LC_GUID}</Guid><TypeId>LiquidClass</TypeId>"
            "<ObjectName>Water Free Single</ObjectName></Reference>"
            f"<Script>{usage}</Script>"
            "</Payload></VxData>"
        ).encode("utf-8")

    def test_unused_unresolved_liquid_class_is_removed_and_reported(self):
        data, findings = exports._strip_unavailable_optional_references(
            self._payload(use_liquid_class=False), {}
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["action"], "removed")
        self.assertNotIn(b"LiquidClass", data)

    def test_used_unresolved_liquid_class_is_retained_and_flagged_missing(self):
        original = self._payload(use_liquid_class=True)
        data, findings = exports._strip_unavailable_optional_references(original, {})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["action"], "retained_unresolved")
        self.assertTrue(findings[0]["used_in_script"])
        # Output is unchanged; the dependency is surfaced, not silently dropped.
        self.assertEqual(data, original)

    def test_resolved_reference_is_left_untouched(self):
        archive = {f"DataStore/SystemSpecific/LiquidClasses/{self.LC_GUID}.xlqc": b"<x/>"}
        data, findings = exports._strip_unavailable_optional_references(
            self._payload(use_liquid_class=True), archive
        )
        self.assertEqual(findings, [])

    def test_unresolved_non_liquid_class_model_is_retained_and_flagged(self):
        wt_guid = "aaaaaaaa-bbbb-4ccc-8ddd-111111111111"
        payload = (
            "<VxData><Payload>"
            f"<Reference><Guid>{wt_guid}</Guid><TypeId>WorktableWorkspace</TypeId>"
            "<ObjectName>Demo_WT</ObjectName></Reference>"
            "<Script></Script></Payload></VxData>"
        ).encode("utf-8")
        data, findings = exports._strip_unavailable_optional_references(
            payload, {}, source_label="MainScript"
        )
        # The model reference is preserved (never fabricated) but surfaced.
        self.assertEqual(data, payload)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["type_id"], "WorktableWorkspace")
        self.assertEqual(findings[0]["action"], "retained_unresolved")
        self.assertEqual(findings[0]["source_label"], "MainScript")

    def test_report_surfaces_missing_dependency_section(self):
        markdown = exports._render_project_import_report(
            [
                {
                    "relative_path": "x.zeia",
                    "source_project": "src.zeia",
                    "main_script": {"replaced_entry": "e", "object_name": "o"},
                    "zip_valid": True,
                    "base_reuse": {
                        "base_entry_count": 12,
                        "script_entries_replaced": 1,
                        "script_entries_added": 0,
                        "models_created": 0,
                        "note": "Built from the exact source ZEIA base; no models were created.",
                    },
                    "unresolved_references": [
                        {
                            "type_id": "WorktableWorkspace",
                            "guid": "aaaaaaaa-bbbb-4ccc-8ddd-111111111111",
                            "object_name": "Demo_WT",
                            "action": "retained_unresolved",
                            "used_in_script": True,
                            "source_label": "MainScript",
                        }
                    ],
                    "checksum_note": "note",
                }
            ]
        )
        self.assertIn("Missing model dependencies", markdown)
        self.assertIn("Demo_WT", markdown)
        self.assertIn("WorktableWorkspace", markdown)
        self.assertIn("created `0` model(s)", markdown)
        self.assertIn("no models were created", markdown.lower())


class FluentArchiveWriterPackagingTests(unittest.TestCase):
    def test_generated_project_archive_uses_writer_with_source_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_project = tmp_path / "source.zeia"
            compiled_xscr = tmp_path / "compiled.xscr"
            destination = tmp_path / "generated_project.zeia"
            workspace_guid = "11111111-1111-4111-8111-111111111111"
            site_guid = "33333333-3333-4333-8333-333333333333"
            script_guid = "22222222-2222-4222-8222-222222222222"

            compiled_xscr.write_text(
                f"""<?xml version="1.0" encoding="utf-8"?>
<sd:VxData dataVersion="4">
  <Payload>
    <ObjectName>GeneratedScript</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
    <Reference>
      <Guid>{workspace_guid}</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>WorkspaceOne</ObjectName>
    </Reference>
    <PayloadData><Script version="2.0"><Commands /></Script></PayloadData>
  </Payload>
  <Checksum></Checksum>
</sd:VxData>
""",
                encoding="utf-8",
            )
            with zipfile.ZipFile(source_project, "w") as zf:
                zf.writestr(
                    f"DataStore\\UserSpecific\\{script_guid}.xscr",
                    """<sd:VxData><Payload><ObjectName>SourceScript</ObjectName><ObjectSubfolderPath>Demo</ObjectSubfolderPath></Payload><Checksum>valid</Checksum></sd:VxData>""",
                )
                zf.writestr(
                    f"DataStore\\SystemSpecific\\Worktable\\Workspaces\\{workspace_guid}.xwsp",
                    f"""<sd:VxData><Payload><ObjectName>WorkspaceOne</ObjectName><Reference><Guid>{site_guid}</Guid><TypeId>WorktableSite</TypeId><ObjectName>SiteOne</ObjectName></Reference></Payload><Checksum>valid</Checksum></sd:VxData>""",
                )
                zf.writestr(
                    f"DataStore\\SystemSpecific\\Worktable\\Sites\\{site_guid}.xsite",
                    """<sd:VxData><Payload><ObjectName>SiteOne</ObjectName></Payload><Checksum>valid</Checksum></sd:VxData>""",
                )
                zf.writestr(
                    "DataStore\\nodedescription.xml",
                    f"""<NodeDescription><TypeMap><Map><Type>WorktableWorkspace</Type><Short>1</Short></Map><Map><Type>Script</Type><Short>2</Short></Map><Map><Type>WorktableSite</Type><Short>3</Short></Map></TypeMap><Payload><S isRootNode="True"><Id>{workspace_guid}</Id><N>WorkspaceOne</N><P></P><T>1</T><V>1</V><TV>1</TV><Ref>{site_guid}</Ref></S><S isRootNode="True"><Id>{site_guid}</Id><N>SiteOne</N><P></P><T>3</T><V>1</V><TV>1</TV></S><S isRootNode="True"><Id>{script_guid}</Id><N>SourceScript</N><P>Demo</P><T>2</T><V>1</V><TV>2.0</TV></S></Payload><Checksum>valid</Checksum></NodeDescription>""",
                )
                zf.writestr(
                    "meta\\content.xml",
                    f"""<ArchiveContent><Payload><DatastoreEntries><Entry>UserSpecific\\{script_guid}.xscr</Entry><Entry>SystemSpecific\\Worktable\\Workspaces\\{workspace_guid}.xwsp</Entry><Entry>SystemSpecific\\Worktable\\Sites\\{site_guid}.xsite</Entry><Entry>nodedescription.xml</Entry></DatastoreEntries></Payload><Checksum>valid</Checksum></ArchiveContent>""",
                )

            def fake_writer(*, script_path, archive_path, datastore_root, metadata_json):
                records = json.loads(Path(metadata_json).read_text(encoding="utf-8"))
                with zipfile.ZipFile(archive_path, "w") as zf:
                    for record in records:
                        relative = record["relative_path"]
                        path = datastore_root / Path(relative.replace("\\", "/"))
                        zf.writestr(f"DataStore\\{relative}", path.read_bytes())
                    node_records = []
                    content_entries = []
                    for record in records:
                        relative = record["relative_path"]
                        content_entries.append(f"<Entry>{relative}</Entry>")
                        refs = "".join(f"<Ref>{ref}</Ref>" for ref in record.get("refs") or [])
                        node_records.append(
                            f"""<S isRootNode="True"><Id>{record['guid']}</Id><N>{record['object_name']}</N><P>{record['object_path']}</P><T>{record['type']}</T><V>{record['version']}</V><TV>{record['type_version']}</TV>{refs}</S>"""
                        )
                    zf.writestr(
                        "DataStore\\nodedescription.xml",
                        f"""<NodeDescription><Payload>{''.join(node_records)}</Payload><Checksum>valid</Checksum></NodeDescription>""",
                    )
                    zf.writestr(
                        "meta\\content.xml",
                        f"""<ArchiveContent><Payload><DatastoreEntries>{''.join(content_entries)}<Entry>nodedescription.xml</Entry></DatastoreEntries></Payload><Checksum>valid</Checksum></ArchiveContent>""",
                    )
                return {"success": True, "numberOfDataobjectsInserted": len(records)}

            with mock.patch.object(exports, "_fluent_archive_writer_available", return_value=True), mock.patch.object(
                exports, "_run_fluent_archive_writer", side_effect=fake_writer
            ):
                record = exports._write_generated_project_archive(
                    source_project,
                    destination,
                    compiled_xscr=compiled_xscr,
                    bundle_root=tmp_path,
                    source_manifest={
                        "root": str(tmp_path),
                        "scripts": [
                            {
                                "entry": f"DataStore\\UserSpecific\\{script_guid}.xscr",
                                "extracted_path": "source_script.xscr",
                                "object_name": "SourceScript",
                            }
                        ],
                    },
                    source_xscr=None,
                    source_scripts=[tmp_path / "source_script.xscr"],
                    subroutine_artifacts=[],
                )

            self.assertEqual(record["packaging_method"], "fluent_archive_writer")
            self.assertEqual(record["base_reuse"]["models_created"], 0)
            self.assertEqual(record["base_reuse"]["script_entries_added"], 1)
            self.assertIn("writer_report", record)
            self.assertEqual(record["dependencies_packaged"], [])
            self.assertEqual(
                {item["guid"] for item in record["dependencies_not_packaged"]},
                {workspace_guid},
            )
            self.assertEqual(record["dependencies_not_packaged"][0]["type"], "WorktableWorkspace")
            with zipfile.ZipFile(destination) as zf:
                names = zf.namelist()
                normalized = [name.replace("/", "\\") for name in names]
                self.assertTrue(any(name.startswith("DataStore\\UserSpecific\\") for name in normalized))
                self.assertNotIn(
                    f"DataStore\\SystemSpecific\\Worktable\\Workspaces\\{workspace_guid}.xwsp",
                    normalized,
                )
                self.assertFalse(any(name.startswith("Scripts/") for name in names))
            audit = record["archive_audit"]
            self.assertTrue(audit["zip_ok"])
            self.assertEqual(audit["blocking"], [])
            self.assertEqual(len(audit["needs_review"]), 1)
            self.assertEqual(audit["needs_review"][0]["kind"], "unresolved_reference")
            self.assertEqual(audit["needs_review"][0]["guid"], workspace_guid)

    def test_dependency_archive_data_uses_supplemental_full_export_objects(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            supplemental = tmp_path / "full_export.zeia"
            main_guid = "11111111-2222-4333-8444-555555555555"
            liquid_guid = "22222222-3333-4444-8555-666666666666"
            primary_data = {
                f"DataStore\\UserSpecific\\{main_guid}.xscr": b"<VxData />",
                "DataStore\\nodedescription.xml": (
                    f"<NodeDescription><TypeMap><Map><Type>Script</Type><Short>2</Short></Map></TypeMap>"
                    f"<Payload><S isRootNode=\"True\"><Id>{main_guid}</Id><N>Main</N><P></P><T>2</T><V>1</V><TV>2.0</TV></S></Payload>"
                    "<Checksum>valid</Checksum></NodeDescription>"
                ).encode(),
            }
            with zipfile.ZipFile(supplemental, "w") as zf:
                zf.writestr(
                    f"DataStore\\SystemSpecific\\LiquidClasses\\{liquid_guid}.xlqc",
                    b"<VxData><Payload><ObjectName>Water Free Single</ObjectName></Payload></VxData>",
                )
                zf.writestr(
                    "DataStore\\nodedescription.xml",
                    (
                        "<NodeDescription><TypeMap><Map><Type>LiquidClass</Type><Short>5</Short></Map></TypeMap>"
                        f"<Payload><S isRootNode=\"True\"><Id>{liquid_guid}</Id><N>Water Free Single</N><P></P><T>5</T><V>1</V><TV>1</TV></S></Payload>"
                        "<Checksum>valid</Checksum></NodeDescription>"
                    ),
                )

            merged = exports._dependency_archive_data(primary_data, [supplemental])
            skipped: list[dict[str, str]] = []
            records = exports._archive_writer_dependency_records(
                merged,
                root_guids=[liquid_guid],
                exclude_guids=set(),
                skipped_import_unsupported=skipped,
            )

        self.assertEqual(records, [])
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0]["guid"], liquid_guid)
        self.assertEqual(skipped[0]["type"], "LiquidClass")


class PortableArchiveWriterPackagingTests(unittest.TestCase):
    def test_generated_project_archive_uses_portable_writer_when_fluent_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_project = tmp_path / "source.zeia"
            compiled_xscr = tmp_path / "compiled.xscr"
            destination = tmp_path / "generated_project.zeia"
            workspace_guid = "11111111-1111-4111-8111-111111111111"
            site_guid = "33333333-3333-4333-8333-333333333333"
            script_guid = "22222222-2222-4222-8222-222222222222"
            unrelated_guid = "44444444-4444-4444-8444-444444444444"

            compiled_xscr.write_text(
                f"""<?xml version="1.0" encoding="utf-8"?>
<sd:VxData dataVersion="4">
  <Payload>
    <ObjectName>GeneratedScript</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
    <Reference>
      <Guid>{workspace_guid}</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>WorkspaceOne</ObjectName>
    </Reference>
    <PayloadData><Script version="2.0"><Commands /></Script></PayloadData>
  </Payload>
  <Checksum></Checksum>
</sd:VxData>
""",
                encoding="utf-8",
            )
            with zipfile.ZipFile(source_project, "w") as zf:
                zf.writestr(
                    f"DataStore\\UserSpecific\\{script_guid}.xscr",
                    """<sd:VxData><Payload><ObjectName>SourceScript</ObjectName><ObjectSubfolderPath>Demo</ObjectSubfolderPath></Payload><Checksum>valid</Checksum></sd:VxData>""",
                )
                zf.writestr(
                    f"DataStore\\UserSpecific\\{unrelated_guid}.xscr",
                    """<sd:VxData><Payload><ObjectName>UnrelatedScript</ObjectName><ObjectSubfolderPath>Demo</ObjectSubfolderPath></Payload><Checksum>valid</Checksum></sd:VxData>""",
                )
                zf.writestr(
                    f"DataStore\\SystemSpecific\\Worktable\\Workspaces\\{workspace_guid}.xwsp",
                    f"""<sd:VxData><Payload><ObjectName>WorkspaceOne</ObjectName><Reference><Guid>{site_guid}</Guid><TypeId>WorktableSite</TypeId><ObjectName>SiteOne</ObjectName></Reference></Payload><Checksum>valid</Checksum></sd:VxData>""",
                )
                zf.writestr(
                    f"DataStore\\SystemSpecific\\Worktable\\Sites\\{site_guid}.xsite",
                    """<sd:VxData><Payload><ObjectName>SiteOne</ObjectName></Payload><Checksum>valid</Checksum></sd:VxData>""",
                )
                zf.writestr(
                    "DataStore\\nodedescription.xml",
                    f"""<NodeDescription><TypeMap><Map><Type>WorktableWorkspace</Type><Short>1</Short></Map><Map><Type>Script</Type><Short>2</Short></Map><Map><Type>WorktableSite</Type><Short>3</Short></Map></TypeMap><Payload><S isRootNode="True"><Id>{workspace_guid}</Id><N>WorkspaceOne</N><P></P><T>1</T><V>1</V><TV>1</TV><Ref>{site_guid}</Ref></S><S isRootNode="True"><Id>{site_guid}</Id><N>SiteOne</N><P></P><T>3</T><V>1</V><TV>1</TV></S><S isRootNode="True"><Id>{script_guid}</Id><N>SourceScript</N><P>Demo</P><T>2</T><V>1</V><TV>2.0</TV></S><S isRootNode="True"><Id>{unrelated_guid}</Id><N>UnrelatedScript</N><P>Demo</P><T>2</T><V>1</V><TV>2.0</TV></S></Payload><Checksum>valid</Checksum></NodeDescription>""",
                )
                zf.writestr(
                    "meta\\content.xml",
                    f"""<ArchiveContent><Payload><DatastoreEntries><Entry>UserSpecific\\{script_guid}.xscr</Entry><Entry>UserSpecific\\{unrelated_guid}.xscr</Entry><Entry>SystemSpecific\\Worktable\\Workspaces\\{workspace_guid}.xwsp</Entry><Entry>SystemSpecific\\Worktable\\Sites\\{site_guid}.xsite</Entry><Entry>nodedescription.xml</Entry></DatastoreEntries></Payload><Checksum>valid</Checksum></ArchiveContent>""",
                )

            with mock.patch.object(exports, "_fluent_archive_writer_available", return_value=False):
                record = exports._write_generated_project_archive(
                    source_project,
                    destination,
                    compiled_xscr=compiled_xscr,
                    bundle_root=tmp_path,
                    source_manifest={
                        "root": str(tmp_path),
                        "scripts": [
                            {
                                "entry": f"DataStore\\UserSpecific\\{script_guid}.xscr",
                                "extracted_path": "source_script.xscr",
                                "object_name": "SourceScript",
                            }
                        ],
                    },
                    source_xscr=None,
                    source_scripts=[tmp_path / "source_script.xscr"],
                    subroutine_artifacts=[],
                )

            self.assertEqual(record["packaging_method"], "portable_archive_writer")
            self.assertEqual(record["base_reuse"]["models_created"], 0)
            self.assertEqual(record["base_reuse"]["script_entries_added"], 1)
            self.assertIn("writer_report", record)
            self.assertEqual(
                {item["guid"] for item in record["dependencies_not_packaged"]},
                {workspace_guid},
            )
            self.assertEqual(record["dependencies_not_packaged"][0]["type"], "WorktableWorkspace")
            with zipfile.ZipFile(destination) as zf:
                names = [name.replace("/", "\\") for name in zf.namelist()]
                self.assertTrue(any(name.startswith("DataStore\\UserSpecific\\") for name in names))
                self.assertTrue(any(name.endswith("nodedescription.xml") for name in names))
                self.assertTrue(any(name.endswith("meta\\content.xml") or name.endswith("meta/content.xml") for name in names) or any("meta" in n and "content.xml" in n for n in names))
                self.assertNotIn(
                    f"DataStore\\SystemSpecific\\Worktable\\Workspaces\\{workspace_guid}.xwsp",
                    names,
                )
                self.assertNotIn(
                    f"DataStore\\UserSpecific\\{unrelated_guid}.xscr",
                    names,
                )
            audit = record["archive_audit"]
            self.assertTrue(audit["zip_ok"])
            blocking_owned = [
                item
                for item in (audit.get("blocking") or [])
                if not item.get("inherited_from_base_export")
            ]
            self.assertEqual(blocking_owned, [])

    def test_full_zeia_copy_only_when_env_flag_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_project = tmp_path / "source.zeia"
            compiled_xscr = tmp_path / "compiled.xscr"
            destination = tmp_path / "generated_project.zeia"
            script_guid = "22222222-2222-4222-8222-222222222222"
            unrelated_guid = "44444444-4444-4444-8444-444444444444"
            compiled_xscr.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData dataVersion="4">
  <Payload>
    <ObjectName>GeneratedScript</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
    <PayloadData><Script version="2.0"><Commands /></Script></PayloadData>
  </Payload>
  <Checksum></Checksum>
</sd:VxData>
""",
                encoding="utf-8",
            )
            with zipfile.ZipFile(source_project, "w") as zf:
                zf.writestr(
                    f"DataStore\\UserSpecific\\{script_guid}.xscr",
                    """<sd:VxData><Payload><ObjectName>SourceScript</ObjectName><ObjectSubfolderPath>Demo</ObjectSubfolderPath></Payload><Checksum>valid</Checksum></sd:VxData>""",
                )
                zf.writestr(
                    f"DataStore\\UserSpecific\\{unrelated_guid}.xscr",
                    """<sd:VxData><Payload><ObjectName>UnrelatedScript</ObjectName><ObjectSubfolderPath>Demo</ObjectSubfolderPath></Payload><Checksum>valid</Checksum></sd:VxData>""",
                )
                zf.writestr(
                    "DataStore\\nodedescription.xml",
                    f"""<NodeDescription><TypeMap><Map><Type>Script</Type><Short>2</Short></Map></TypeMap><Payload><S isRootNode="True"><Id>{script_guid}</Id><N>SourceScript</N><P>Demo</P><T>2</T><V>1</V><TV>2.0</TV></S><S isRootNode="True"><Id>{unrelated_guid}</Id><N>UnrelatedScript</N><P>Demo</P><T>2</T><V>1</V><TV>2.0</TV></S></Payload><Checksum>valid</Checksum></NodeDescription>""",
                )
                zf.writestr(
                    "meta\\content.xml",
                    f"""<ArchiveContent><Payload><DatastoreEntries><Entry>UserSpecific\\{script_guid}.xscr</Entry><Entry>UserSpecific\\{unrelated_guid}.xscr</Entry><Entry>nodedescription.xml</Entry></DatastoreEntries></Payload><Checksum>valid</Checksum></ArchiveContent>""",
                )

            with mock.patch.object(exports, "_fluent_archive_writer_available", return_value=False), _force_full_zeia_copy_env():
                record = exports._write_generated_project_archive(
                    source_project,
                    destination,
                    compiled_xscr=compiled_xscr,
                    bundle_root=tmp_path,
                    source_manifest={
                        "root": str(tmp_path),
                        "scripts": [
                            {
                                "entry": f"DataStore\\UserSpecific\\{script_guid}.xscr",
                                "extracted_path": "source_script.xscr",
                                "object_name": "SourceScript",
                            }
                        ],
                    },
                    source_xscr=None,
                    source_scripts=[tmp_path / "source_script.xscr"],
                    subroutine_artifacts=[],
                )

            self.assertEqual(record["packaging_method"], "python_zip_fallback")
            with zipfile.ZipFile(destination) as zf:
                names = [name.replace("/", "\\") for name in zf.namelist()]
                self.assertIn(f"DataStore\\UserSpecific\\{unrelated_guid}.xscr", names)


def _force_full_zeia_copy_env():
    return mock.patch.dict(os.environ, {"TECAN_PACKAGE_FULL_ZEIA_COPY": "1"}, clear=False)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self._full_copy_env = _force_full_zeia_copy_env()
        self._full_copy_env.start()

    def tearDown(self):
        self._full_copy_env.stop()

    def test_project_archives_receive_ordered_collection_filesystem_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            primary = tmp_path / "primary.zeia"
            secondary = tmp_path / "secondary.zeia"
            compiled = tmp_path / "compiled.xscr"
            for archive in (primary, secondary):
                with zipfile.ZipFile(archive, "w") as zf:
                    zf.writestr("placeholder.txt", "test")
            compiled.write_text("<VxData />", encoding="utf-8")
            exported: list[exports.ExportedArtifact] = []
            copied: list[dict[str, str]] = []

            with mock.patch.object(
                exports,
                "_write_generated_project_archive",
                return_value={"kind": "generated-project-archive"},
            ) as writer:
                records = exports._write_project_import_archives(
                    [primary],
                    filesystem_source_archives=[primary, secondary],
                    compiled_xscr=compiled,
                    destination_dir=tmp_path / "projects",
                    bundle_root=tmp_path,
                    source_manifest=None,
                    source_xscr=None,
                    source_scripts=[],
                    subroutine_artifacts=[],
                    exports=exported,
                    copied_files=copied,
                )

            self.assertEqual(len(records), 1)
            self.assertEqual(
                writer.call_args_list[0].kwargs["filesystem_source_archives"],
                [primary.resolve(), secondary.resolve()],
            )

    def test_legacy_archive_retains_payload_for_unreachable_preserved_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_project = tmp_path / "source.zeia"
            destination = tmp_path / "generated.zeia"
            compiled = tmp_path / "compiled.xscr"
            source_main = tmp_path / "source_main.xscr"
            asset_dir = r"C:\Tecan\Preserved"
            asset_path = asset_dir + r"\base-only.gif"
            source_main.write_text("<VxData />", encoding="utf-8")
            compiled.write_text(
                """<VxData><Payload>
<ObjectName>MainScript</ObjectName>
<PayloadData><Script><Commands /></Script></PayloadData>
</Payload><Checksum></Checksum></VxData>""",
                encoding="utf-8",
            )
            with zipfile.ZipFile(source_project, "w") as zf:
                zf.writestr(
                    "Scripts/main.xscr",
                    "<VxData><Payload><ObjectName>MainScript</ObjectName></Payload></VxData>",
                )
                zf.writestr(
                    "Scripts/unreachable.xscr",
                    f"""<VxData><Payload>
<ObjectName>UnreachableBaseScript</ObjectName>
<FileReference><File>{asset_path}</File></FileReference>
</Payload></VxData>""",
                )
                zf.writestr(
                    "fs/mapping.xml",
                    build_fs_mapping_xml([(1, asset_dir)]),
                )
                zf.writestr("fs/1/base-only.gif", b"preserved-script-asset")
                zf.writestr(
                    "meta/content.xml",
                    "<ArchiveContent><Payload><DatastoreEntries /></Payload>"
                    "<Checksum></Checksum></ArchiveContent>",
                )

            record = exports._write_generated_project_archive(
                source_project,
                destination,
                compiled_xscr=compiled,
                bundle_root=tmp_path,
                source_manifest={
                    "root": str(tmp_path),
                    "scripts": [
                        {
                            "entry": "Scripts/main.xscr",
                            "extracted_path": str(source_main),
                            "object_name": "MainScript",
                        }
                    ],
                },
                source_xscr=source_main,
                source_scripts=[source_main],
                subroutine_artifacts=[],
            )

            with zipfile.ZipFile(destination) as zf:
                self.assertEqual(
                    zf.read("fs/1/base-only.gif"),
                    b"preserved-script-asset",
                )
                self.assertIn("Scripts/unreachable.xscr", zf.namelist())
                archive_data = {
                    name: zf.read(name) for name in zf.namelist()
                }
            self.assertTrue(record["filesystem_packaging"]["complete"])
            self.assertEqual(
                record["filesystem_packaging"]["referenced_path_count"],
                1,
            )
            self.assertEqual(
                exports.audit_archive_filesystem(archive_data),
                [],
            )

    def test_generated_project_archive_updates_only_script_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_project = tmp_path / "source.zeia"
            compiled_xscr = tmp_path / "compiled.xscr"
            destination = tmp_path / "generated_project.zeia"
            workspace_guid = "11111111-1111-4111-8111-111111111111"
            script_guid = "22222222-2222-4222-8222-222222222222"

            compiled_xscr.write_text(
                f"""<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="4">
  <Payload>
    <ObjectName>GeneratedScript</ObjectName>
    <Reference>
      <Guid>{workspace_guid}</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>WorkspaceOne</ObjectName>
    </Reference>
    <PayloadData><Script><Commands /></Script></PayloadData>
  </Payload>
  <Checksum></Checksum>
</sd:VxData>
""",
                encoding="utf-8",
            )

            with zipfile.ZipFile(source_project, "w") as zf:
                zf.writestr(
                    f"DataStore\\UserSpecific\\{script_guid}.xscr",
                    """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="4">
  <Payload>
    <ObjectName>SourceScript</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
    <PayloadData><Script><Commands /></Script></PayloadData>
  </Payload>
  <Checksum></Checksum>
</sd:VxData>
""",
                )
                zf.writestr(
                    "DataStore\\nodedescription.xml",
                    f"""<?xml version="1.0" encoding="utf-8"?>
<NodeDescription>
  <Payload>
    <S isRootNode="True">
      <Id>{workspace_guid}</Id>
      <N>WorkspaceOne</N>
      <P></P>
      <T>1</T>
    </S>
    <S isRootNode="True">
      <Id>{script_guid}</Id>
      <N>SourceScript</N>
      <P>Demo</P>
      <T>2</T>
    </S>
  </Payload>
  <Checksum>stale</Checksum>
</NodeDescription>
""",
                )
                zf.writestr(
                    "meta\\content.xml",
                    f"""<?xml version="1.0" encoding="utf-8"?>
<ArchiveContent>
  <Payload>
    <DatastoreEntries>
      <Entry>UserSpecific\\{script_guid}.xscr</Entry>
      <Entry>nodedescription.xml</Entry>
    </DatastoreEntries>
  </Payload>
  <Checksum>stale</Checksum>
</ArchiveContent>
""",
                )

            exports._write_generated_project_archive(
                source_project,
                destination,
                compiled_xscr=compiled_xscr,
                bundle_root=tmp_path,
                source_manifest={
                    "root": str(tmp_path),
                    "scripts": [
                        {
                            "entry": f"DataStore\\UserSpecific\\{script_guid}.xscr",
                            "extracted_path": "source_script.xscr",
                            "object_name": "SourceScript",
                        }
                    ],
                },
                source_xscr=None,
                source_scripts=[tmp_path / "source_script.xscr"],
                subroutine_artifacts=[],
            )

            with zipfile.ZipFile(destination) as zf:
                node = _read_archive_text(zf, "DataStore\\nodedescription.xml")
                script = _read_archive_text(zf, f"DataStore\\UserSpecific\\{script_guid}.xscr")

            self.assertIn("<N>WorkspaceOne</N>", node)
            self.assertIn("<N>GeneratedScript</N>", node)
            self.assertNotIn("<N>SourceScript</N>", node)
            self.assertIn("<ObjectName>GeneratedScript</ObjectName>", script)
            self.assertIn("<ObjectSubfolderPath>Demo</ObjectSubfolderPath>", script)

    def test_export_ready_to_import_copies_script_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_export_dir = exports.READY_TO_IMPORT_DIR
            old_staging_dir = exports.PACKAGE_STAGING_DIR
            old_failed_dir = exports.FAILED_PACKAGES_DIR
            exports.READY_TO_IMPORT_DIR = tmp_path / "ready-to-import"
            exports.PACKAGE_STAGING_DIR = tmp_path / "build" / "package-staging"
            exports.FAILED_PACKAGES_DIR = tmp_path / "build" / "failed-packages"
            try:
                xscr = tmp_path / "draft.xscr"
                draft = tmp_path / "draft.py"
                source_project = tmp_path / "source_project.zeia"
                source_script = tmp_path / "source_script.xscr"
                subroutine_script = tmp_path / "helper_subroutine.xscr"
                nested_subroutine_script = tmp_path / "nested_subroutine.xscr"
                connector = tmp_path / "connector.xcon"
                asset = tmp_path / "sourcecapholder.jpg"
                simulation_report = tmp_path / "simulation_report.md"
                repair_plan = tmp_path / "repair_plan.md"
                compile_report = tmp_path / "compile_report.md"
                request_spec = tmp_path / "request.spec.yaml"
                validation_diff = tmp_path / "validation_diff.md"
                validation_diff_json = tmp_path / "validation_diff.json"
                xscr.write_text(
                    """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Pipeline simple transfer</ObjectName>
    <Comment>Move liquid from one plate to another</Comment>
    <Reference>
      <Guid>workspace-guid</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <Reference>
      <Guid>lc-guid</Guid>
      <TypeId>LiquidClass</TypeId>
      <ObjectName>Water Free Single</ObjectName>
    </Reference>
    <PayloadData>
      <Script>
        <Commands>
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
                <ScriptGroupDataV1>
                  <Name>Setup</Name>
                  <Data>
                    <Statements>
                      <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
                        <AddLabwareDataV1>
                          <LabwareType>96 Well Flat</LabwareType>
                          <LabwareLable>SourcePlate</LabwareLable>
                          <Location>Site</Location>
                          <Position>1</Position>
                          <Rotation>0</Rotation>
                          <HasLid>False</HasLid>
                          <Data><LineNumber>2</LineNumber></Data>
                        </AddLabwareDataV1>
                      </Object>
                    </Statements>
                  </Data>
                </ScriptGroupDataV1>
              </Object>
              <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
                <ScriptGroupDataV1>
                  <Name>Transfer</Name>
                  <Data>
                    <Statements>
                      <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384AspirateScriptCommandDataV2">
                        <Mca384AspirateScriptCommandDataV2>
                          <LiquidClassName>Water Free Single</LiquidClassName>
                          <Volume>20</Volume>
                          <ScriptCommandCommonDataV2>
                            <LabwareName>SourcePlate</LabwareName>
                            <DeviceAlias>Instrument=1/Device=MCA384:1</DeviceAlias>
                            <AvailableID>USB:TECAN,FLUENT,1/MCA384:1</AvailableID>
                            <LineNumber>3</LineNumber>
                          </ScriptCommandCommonDataV2>
                        </Mca384AspirateScriptCommandDataV2>
                      </Object>
                    </Statements>
                  </Data>
                </ScriptGroupDataV1>
              </Object>
            </Objects>
          </ScriptGroup>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
""",
                    encoding="utf-8",
                )
                draft.write_text(
                    """from fluentcoder import Plate96, Reagent, Worktable


def build_worktable():
    input_dna = Reagent("Input gDNA")
    wt = Worktable.from_workspace("780_Empty", auto_place=False)
    wt.group("Setup")
    src = wt.place(Plate96("SourcePlate", catalog="96 Well Flat"), "Site", 1)
    src.fill_all(input_dna, 50.0)
    wt.group("Transfer")
    head = wt.mca96
    head.aspirate(src, 20.0, liquid_class="Water Free Single")
    return wt
""",
                    encoding="utf-8",
                )
                with zipfile.ZipFile(source_project, "w") as zf:
                    zf.writestr("Scripts/source_script.xscr", "<Root />")
                source_script.write_text(
                    """<?xml version="1.0"?>
<Root>
  <ObjectName>ParentScript</ObjectName>
  <VariableDefinitionHelper>
    <Name>OperatorMode</Name>
    <QueryOnStartup>true</QueryOnStartup>
    <QueryOnStartupString>0=manual setup, 1=skip setup prompts</QueryOnStartupString>
    <ReadOnly>false</ReadOnly>
    <Scope>Run</Scope>
    <TypeName>Integer</TypeName>
    <Values><string>0</string></Values>
  </VariableDefinitionHelper>
  <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
    <SubRoutine>"Subroutines\\HelperSub"</SubRoutine>
  </Object>
  <Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPVariableStatement">
    <RUPVariableStatement>
      <VariableDatas>
        <VariableDataModel>
          <Instructions>Please confirm startup values</Instructions>
          <Variables>
            <RupVariableItem>
              <VariableName>OperatorMode</VariableName>
              <DisplayText>Operator mode</DisplayText>
              <DisplayType>Combobox</DisplayType>
              <AllowedValues>0;1</AllowedValues>
              <IsEnabled>true</IsEnabled>
            </RupVariableItem>
          </Variables>
        </VariableDataModel>
      </VariableDatas>
      <RUPScreenTitle>User Input</RUPScreenTitle>
      <RUPDisplayAndWait>True</RUPDisplayAndWait>
      <RUPAutoClose>False</RUPAutoClose>
      <RUPTimeOut>1</RUPTimeOut>
      <LineNumber>7</LineNumber>
    </RUPVariableStatement>
  </Object>
</Root>
""",
                    encoding="utf-8",
                )
                subroutine_script.write_text(
                    """<?xml version="1.0"?>
<Root>
  <ObjectName>HelperSub</ObjectName>
  <ObjectSubfolderPath>Subroutines</ObjectSubfolderPath>
</Root>
""",
                    encoding="utf-8",
                )
                nested_subroutine_script.write_text(
                    """<?xml version="1.0"?>
<Root>
  <ObjectName>NestedSub</ObjectName>
  <ObjectSubfolderPath>Subroutines</ObjectSubfolderPath>
</Root>
""",
                    encoding="utf-8",
                )
                connector.write_text(
                    """<?xml version="1.0"?>
<Root>
  <ObjectName>Generated connector for Worktable_Segment_WorktablePin_MiddleFront and custom cap holder</ObjectName>
  <Description>Worktable_Segment_WorktablePin_MiddleFront connector</Description>
  <ComponentGuid>component-guid</ComponentGuid>
  <SiteGuid>site-guid</SiteGuid>
</Root>
""",
                    encoding="utf-8",
                )
                asset.write_bytes(b"not really a jpeg")
                simulation_report.write_text("# Simulation", encoding="utf-8")
                repair_plan.write_text("# Repair", encoding="utf-8")
                compile_report.write_text("# Compile", encoding="utf-8")
                request_spec.write_text(
                    "schema_version: tecan.request_spec.v1\nrequest:\n  intent: preserve TouchTools prompts\n",
                    encoding="utf-8",
                )
                validation_diff.write_text("# Request Validation Diff\n", encoding="utf-8")
                validation_diff_json.write_text('{"kind": "request_validation_diff"}', encoding="utf-8")

                stage_root = exports.PACKAGE_STAGING_DIR.resolve()
                ready_root = exports.READY_TO_IMPORT_DIR.resolve()
                real_rename = exports.os.rename
                replace_calls: list[tuple[Path, Path]] = []

                def spy_replace(src, dst):
                    source_path = Path(src).resolve()
                    destination_path = Path(dst).resolve()
                    replace_calls.append((source_path, destination_path))
                    # Publish staging lives under ready-to-import as a hidden
                    # ".{bundle}.{run}.staging" directory (not PACKAGE_STAGING_DIR).
                    self.assertEqual(source_path.parent, ready_root)
                    self.assertTrue(source_path.name.startswith(".") and source_path.name.endswith(".staging"))
                    self.assertEqual(destination_path.parent, ready_root)
                    self.assertFalse(destination_path.exists())
                    return real_rename(src, dst)

                with mock.patch.object(exports.os, "rename", side_effect=spy_replace):
                    copied = exports.export_ready_to_import(
                        xscr,
                        context_name="My Project",
                        draft_path=draft,
                        source_projects=[source_project],
                        source_scripts=[source_script],
                        source_manifest={
                            "name": "My Project",
                            "root": str(tmp_path),
                            "errors": [],
                            "workspaces": [{"object_name": "780_Empty", "guids": ["workspace-guid"]}],
                            "labware_names": ["SourcePlate"],
                            "liquid_classes": ["Water Free Single"],
                            "rack_types": [],
                            "worklist_paths": [],
                            "scripts": [
                                {
                                    "object_name": "ParentScript",
                                    "entry": "Scripts/source_script.xscr",
                                    "extracted_path": str(source_script),
                                    "dependencies": {
                                        "subroutine_refs": ['"Subroutines\\HelperSub"'],
                                        "pin_refs": ["GIO1_Pin20"],
                                        "worktable_pin_locations": ["WorktablePin_MiddleFront"],
                                        "custom_asset_refs": ["C:\\ProgramData\\Tecan\\Images\\sourcecapholder.jpg"],
                                        "barcode_refs": ["FakeBarcode"],
                                    },
                                },
                                {
                                    "object_name": "HelperSub",
                                    "entry": "Subroutines/helper_subroutine.xscr",
                                    "extracted_path": str(subroutine_script),
                                    "dependencies": {"subroutine_refs": ['"Subroutines\\NestedSub"']},
                                },
                                {
                                    "object_name": "NestedSub",
                                    "entry": "Subroutines/nested_subroutine.xscr",
                                    "extracted_path": str(nested_subroutine_script),
                                    "dependencies": {},
                                },
                            ],
                            "objects": [
                                {
                                    "kind": "connector",
                                    "object_name": "Generated connector for Worktable_Segment_WorktablePin_MiddleFront and custom cap holder",
                                    "entry": "Worktable/connector.xcon",
                                    "extracted_path": str(connector),
                                    "pin_refs": ["Worktable_Segment_WorktablePin_MiddleFront"],
                                    "asset_refs": [],
                                    "custom_part": True,
                                    "component_guid": "component-guid",
                                    "site_guid": "site-guid",
                                    "description": "Worktable_Segment_WorktablePin_MiddleFront connector",
                                },
                                {
                                    "kind": "asset",
                                    "object_name": "sourcecapholder.jpg",
                                    "entry": "fs/1/sourcecapholder.jpg",
                                    "extracted_path": str(asset),
                                    "pin_refs": [],
                                    "asset_refs": ["sourcecapholder.jpg"],
                                    "custom_part": True,
                                },
                            ],
                        },
                        report_files={
                            "simulation_report": simulation_report,
                            "repair_plan": repair_plan,
                            "compile_report": compile_report,
                        },
                        request_spec=request_spec,
                        validation_diff=validation_diff,
                        validation_diff_json=validation_diff_json,
                        validation_context={
                            "simulation_passed": True,
                            "repair_plan": {"actions": []},
                            "compile_passed": True,
                            "checksums_recompute_waived": True,
                        },
                    )

                destinations = [item.destination for item in copied]
                published_root = exports.READY_TO_IMPORT_DIR / "draft_v1"
                self.assertTrue(published_root.is_dir())
                self.assertEqual(destinations, [(published_root / "draft_v1.zeia").resolve()])
                self.assertTrue((published_root / "source" / "protocol.ir.json").exists())
                self.assertTrue((published_root / "source" / "protocol_draft.py").exists())
                project_archive = published_root / "draft_v1.zeia"
                self.assertTrue(zipfile.is_zipfile(project_archive))
                with zipfile.ZipFile(project_archive) as zf:
                    generated_project_entries = zf.namelist()
                    self.assertIn("Scripts/source_script.xscr", generated_project_entries)
                    self.assertIn("Scripts/HelperSub.xscr", generated_project_entries)
                    self.assertIn("Scripts/NestedSub.xscr", generated_project_entries)
                    generated_project_script = zf.read("Scripts/source_script.xscr").decode(
                        "utf-8-sig",
                        errors="replace",
                    )
                    self.assertIn("<ObjectName>Pipeline simple transfer</ObjectName>", generated_project_script)
                # V2 delivery forbids publishing standalone XSCR trees.
                self.assertFalse((published_root / "direct-imports").exists())
                self.assertTrue((published_root / "source" / "subroutines" / "SUBROUTINES.md").exists())
                self.assertTrue((published_root / "source" / "HARDWARE_PINS.md").exists())
                self.assertTrue((published_root / "source" / "hardware" / "hardware_manifest.json").exists())
                self.assertTrue((published_root / "source" / "METHOD_TOUCHTOOLS_READINESS.md").exists())
                self.assertTrue((published_root / "source" / "reports" / "method_touchtools_readiness.json").exists())
                self.assertTrue((published_root / "source" / "reports" / "project_report.md").exists())
                self.assertTrue((published_root / "source" / "reports" / "simulation_report.md").exists())
                self.assertTrue((published_root / "source" / "reports" / "repair_plan.md").exists())
                self.assertTrue((published_root / "source" / "reports" / "compile_report.md").exists())
                self.assertTrue((published_root / "source" / "reports" / "validation_report.md").exists())
                self.assertTrue((published_root / "source" / "reports" / "validation_report.json").exists())
                self.assertTrue((published_root / "source" / "worktable_changes.md").exists())
                self.assertTrue((published_root / "source" / "worktable.patch.json").exists())
                self.assertTrue((published_root / "source" / "request.spec.yaml").exists())
                self.assertTrue((published_root / "source" / "validation_diff.md").exists())
                self.assertTrue((published_root / "source" / "metadata.json").exists())
                self.assertTrue((published_root / "RECREATE_SCRIPT.md").exists())
                self.assertTrue((published_root / "source" / "subroutines" / "SUBROUTINES.md").exists())
                self.assertTrue((published_root / "source" / "HARDWARE_PINS.md").exists())
                self.assertTrue((published_root / "source" / "hardware" / "assets").exists())
                metadata = (published_root / "source" / "metadata.json").read_text(
                    encoding="utf-8"
                )
                metadata_payload = json.loads(metadata)
                validation_payload = json.loads(
                    (published_root / "source" / "reports" / "validation_report.json").read_text(encoding="utf-8")
                )
                canonical_readiness = validation_payload["readiness"]
                self.assertIn('"bundle_schema_version": "tecan.ready_to_import.bundle.v2"', metadata)
                self.assertIn('"ready_to_import": true', metadata)
                self.assertIn('"direct_xscr_import"', metadata)
                self.assertIn('"zeia_import"', metadata)
                self.assertIn('"artifact_roles"', metadata)
                self.assertIn('"readiness_boundaries"', metadata)
                self.assertIn('"bundle_role": "ready"', metadata)
                self.assertIn('"source_export_kind": "unknown"', metadata)
                self.assertIn('"verification_state": "offline_validated"', metadata)
                self.assertIn('"lifecycle"', metadata)
                self.assertIn('"cleanup_guidance"', metadata)
                self.assertIn("Script Editor load certificate", metadata)
                self.assertIn('"kind": "direct-xscr-import"', metadata)
                self.assertIn('"kind": "generated-zeia-import"', metadata)
                self.assertIn('"relative_path": "direct-imports/scripts/full-script/generated_script.xscr"', metadata)
                self.assertNotIn("corrected_script_duplicate.xscr", metadata)
                self.assertNotIn("corrected_xscr_duplicate", metadata)
                self.assertNotIn("corrected-script-duplicate", metadata)
                self.assertIn('"relative_path": "direct-imports/scripts/subroutines/subroutine_1_HelperSub.xscr"', metadata)
                self.assertIn('"relative_path": "direct-imports/scripts/subroutines/subroutine_2_NestedSub.xscr"', metadata)
                self.assertIn('"subroutines": "direct-imports/scripts/subroutines/"', metadata)
                self.assertIn('"hardware": "source/hardware/"', metadata)
                self.assertIn('"hardware_manifest": "source/hardware/hardware_manifest.json"', metadata)
                self.assertIn('"hardware_pins_checklist": "source/HARDWARE_PINS.md"', metadata)
                self.assertIn('"method_touchtools_readiness": "source/METHOD_TOUCHTOOLS_READINESS.md"', metadata)
                self.assertIn('"method_touchtools_readiness_json": "source/reports/method_touchtools_readiness.json"', metadata)
                self.assertIn('"generated_project": "direct-imports/projects/full-project/generated_project.zeia"', metadata)
                self.assertIn('"project_imports": "direct-imports/projects/"', metadata)
                self.assertIn('"project_import_report": "source/reports/project_import_report.md"', metadata)
                self.assertIn('"relative_path": "direct-imports/projects/full-project/generated_project.zeia"', metadata)
                self.assertIn('"relative_path": "source/hardware/hardware_manifest.json"', metadata)
                self.assertIn('"relative_path": "source/HARDWARE_PINS.md"', metadata)
                self.assertIn('"relative_path": "source/METHOD_TOUCHTOOLS_READINESS.md"', metadata)
                self.assertIn('"relative_path": "source/reports/method_touchtools_readiness.json"', metadata)
                self.assertIn('"relative_path": "direct-imports/hardware-connectors/connector_1_connector.xcon"', metadata)
                self.assertIn('"relative_path": "source/hardware/assets/asset_1_sourcecapholder.jpg"', metadata)
                self.assertIn('"relative_path": "source/reports/simulation_report.md"', metadata)
                self.assertIn('"relative_path": "source/reports/validation_report.md"', metadata)
                self.assertIn('"relative_path": "source/reports/validation_report.json"', metadata)
                self.assertIn('"relative_path": "source/worktable.patch.json"', metadata)
                self.assertIn('"relative_path": "source/request.spec.yaml"', metadata)
                self.assertIn('"relative_path": "source/validation_diff.md"', metadata)
                self.assertIn('"generated_worklist_present": false', metadata)
                self.assertEqual(metadata_payload["readiness_status"], validation_payload["readiness_status"])
                self.assertEqual(metadata_payload["readiness"], canonical_readiness)
                self.assertEqual(metadata_payload["readiness"]["script_editor_load"]["status"], "not_run")
                self.assertEqual(validation_payload["readiness_status"], "import_ready_needs_review")
                self.assertEqual(len(replace_calls), 1)
                self.assertEqual(exports.READY_TO_IMPORT_DIR.resolve(), ready_root)
                self.assertTrue(stage_root.exists())
                self.assertEqual(list(stage_root.iterdir()), [])
                self.assertFalse(exports.FAILED_PACKAGES_DIR.exists())
                generation_manifest = tmp_path / "generation_manifest.json"
                workflow_report = tmp_path / "GENERATION_WORKFLOW.md"
                generation_manifest.write_text(
                    json.dumps(
                        {
                            "workflow_status": "ready_to_import",
                            "ready_to_import": True,
                            "readiness_status": validation_payload["readiness_status"],
                            "readiness": canonical_readiness,
                        }
                    ),
                    encoding="utf-8",
                )
                workflow_report.write_text("# Generation Workflow\n", encoding="utf-8")
                attached = exports.attach_generation_reports_to_ready_bundles(
                    [item.destination for item in copied],
                    ready_root=exports.READY_TO_IMPORT_DIR,
                    generation_manifest=generation_manifest,
                    workflow_report=workflow_report,
                )
                self.assertEqual(len(attached), 2)
                self.assertTrue(
                    (published_root / "source" / "generation_manifest.json").exists()
                )
                self.assertTrue(
                    (published_root / "source" / "GENERATION_WORKFLOW.md").exists()
                )
                updated_metadata = json.loads(
                    (published_root / "source" / "metadata.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    updated_metadata["layout"]["generation_manifest"],
                    "source/generation_manifest.json",
                )
                self.assertEqual(
                    updated_metadata["layout"]["workflow_report"],
                    "source/GENERATION_WORKFLOW.md",
                )
                self.assertIn(
                    "source/generation_manifest.json",
                    {item["relative_path"] for item in updated_metadata["files"]},
                )
                self.assertIn(
                    "source/GENERATION_WORKFLOW.md",
                    {item["relative_path"] for item in updated_metadata["files"]},
                )
                attached_manifest = json.loads(
                    (published_root / "source" / "generation_manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(attached_manifest["readiness"], canonical_readiness)
                self.assertEqual(updated_metadata["readiness"], canonical_readiness)
                self.assertEqual(updated_metadata["readiness_status"], validation_payload["readiness_status"])
                self.assertEqual(
                    (published_root / "source" / "reports" / "project_report.md").read_text(
                        encoding="utf-8"
                    ),
                    "# Project Report\n\nThis report was not produced for this bundle.\n",
                )
                subroutines = (published_root / "source" / "subroutines" / "SUBROUTINES.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("HelperSub", subroutines)
                self.assertIn("direct-imports/scripts/subroutines/subroutine_1_HelperSub.xscr", subroutines)
                self.assertIn("NestedSub", subroutines)
                self.assertIn("direct-imports/scripts/subroutines/subroutine_2_NestedSub.xscr", subroutines)
                hardware = (published_root / "source" / "HARDWARE_PINS.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("GIO1_Pin20", hardware)
                self.assertIn("WorktablePin_MiddleFront", hardware)
                self.assertIn("direct-imports/hardware-connectors/connector_1_connector.xcon", hardware)
                self.assertIn("sourcecapholder.jpg", hardware)
                hardware_manifest = (published_root / "source" / "hardware" / "hardware_manifest.json").read_text(
                    encoding="utf-8"
                )
                self.assertIn('"schema_version": "tecan.hardware_manifest.v1"', hardware_manifest)
                self.assertIn('"status": "connector_evidence_packaged"', hardware_manifest)
                self.assertIn('"status": "runtime_pin_verification_required"', hardware_manifest)
                self.assertIn('"status": "asset_packaged"', hardware_manifest)
                method_touchtools = (
                    published_root / "source" / "METHOD_TOUCHTOOLS_READINESS.md"
                ).read_text(encoding="utf-8")
                self.assertIn("# Method and TouchTools Readiness", method_touchtools)
                self.assertIn("Method required before TouchTools use: `yes`", method_touchtools)
                self.assertIn("TouchTools-visible method setting required: `yes`", method_touchtools)
                self.assertIn("OperatorMode", method_touchtools)
                self.assertIn("User Input", method_touchtools)
                method_touchtools_json = (
                    published_root / "source" / "reports" / "method_touchtools_readiness.json"
                ).read_text(encoding="utf-8")
                self.assertIn('"schema_version": "tecan.method_touchtools_readiness.v1"', method_touchtools_json)
                self.assertIn('"method_required_before_touchtools": true', method_touchtools_json)
                guide = (published_root / "RECREATE_SCRIPT.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("# Recreate Script: draft", guide)
                self.assertIn("This guide is generated from the same canonical protocol IR", guide)
                self.assertIn("- Source of truth: `source/protocol.ir.json`", guide)
                self.assertIn("- Script name: `draft`", guide)
                self.assertIn("- Request spec / prompt: `source/request.spec.yaml`", guide)
                self.assertIn("- Python draft: `source/protocol_draft.py`", guide)
                self.assertIn("- Direct import file: `direct-imports/scripts/full-script/generated_script.xscr`", guide)
                self.assertIn("- One-file project import: `direct-imports/projects/full-project/generated_project.zeia`", guide)
                self.assertIn("## Manual FluentControl Steps", guide)
                self.assertIn("Load worktable: `780_Empty`", guide)
                self.assertIn("Confirm labware:", guide)
                self.assertIn("## Chosen Items", guide)
                self.assertIn("`SourcePlate`", guide)
                self.assertIn("## IR Command Reference", guide)
                self.assertIn("1. Add Labware", guide)
                self.assertIn("2. Aspirate", guide)
                self.assertIn("   - Command name: `Aspirate`", guide)
                self.assertIn("   - Specifications:", guide)
                self.assertIn("Volume: `20 uL`", guide)
                self.assertIn("   - Path to find it:", guide)
                self.assertIn("draft.py -> build_worktable() -> Transfer -> head.aspirate", guide)
                worktable_changes = (published_root / "source" / "worktable_changes.md").read_text(
                    encoding="utf-8"
                )
                self.assertIn("# Worktable Changes", worktable_changes)
                self.assertIn("## Missing Labware", worktable_changes)
                self.assertIn("## Required Liquid Classes", worktable_changes)
                self.assertIn("## Manual FluentControl Setup Steps", worktable_changes)
                worktable_patch = (published_root / "source" / "worktable.patch.json").read_text(
                    encoding="utf-8"
                )
                self.assertIn('"kind": "worktable_patch"', worktable_patch)
                self.assertIn('"overall_severity"', worktable_patch)
            finally:
                exports.READY_TO_IMPORT_DIR = old_export_dir
                exports.PACKAGE_STAGING_DIR = old_staging_dir
                exports.FAILED_PACKAGES_DIR = old_failed_dir


class ExportTransactionFailureTests(unittest.TestCase):
    def setUp(self):
        self._full_copy_env = _force_full_zeia_copy_env()
        self._full_copy_env.start()

    def tearDown(self):
        self._full_copy_env.stop()

    def _restore_export_roots(self, ready_root: Path, staging_root: Path, failed_root: Path) -> None:
        exports.READY_TO_IMPORT_DIR = ready_root
        exports.PACKAGE_STAGING_DIR = staging_root
        exports.FAILED_PACKAGES_DIR = failed_root

    def _configure_export_roots(self, tmp_path: Path) -> None:
        old_roots = (
            exports.READY_TO_IMPORT_DIR,
            exports.PACKAGE_STAGING_DIR,
            exports.FAILED_PACKAGES_DIR,
        )
        exports.READY_TO_IMPORT_DIR = tmp_path / "ready-to-import"
        exports.PACKAGE_STAGING_DIR = tmp_path / "build" / "package-staging"
        exports.FAILED_PACKAGES_DIR = tmp_path / "build" / "failed-packages"
        self.addCleanup(self._restore_export_roots, *old_roots)

    def _ready_validation_report(self) -> dict[str, object]:
        return {
            "ready": True,
            "gates": [],
            "validation_version": "test",
            "required_gate_count": 0,
            "required_failed_count": 0,
            "optional_gate_count": 0,
            "optional_passed_count": 0,
            "offline_validation": {"status": "ready_to_import"},
            "review_state": {"status": "hardware_review_required"},
        }

    def _failed_post_package_validation_report(self) -> dict[str, object]:
        return {
            "ready": False,
            "gates": [
                {
                    "gate": "Gate 24",
                    "name": "packaged generated ZEIA opens, resolves references, and matches its datastore metadata",
                    "status": "failed",
                    "summary": "archive audit failed",
                }
            ],
            "validation_version": "test",
            "required_gate_count": 1,
            "required_failed_count": 1,
            "optional_gate_count": 0,
            "optional_passed_count": 0,
            "offline_validation": {"status": "validated_not_ready"},
            "review_state": {"status": "validated_not_ready"},
        }

    def _seed_existing_ready_bundle(self) -> dict[str, object]:
        bundle_dir = exports.READY_TO_IMPORT_DIR / "existing-bundle"
        source_dir = bundle_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        marker_path = source_dir / "context.txt"
        marker_path.write_text("existing context remains available\n", encoding="utf-8")
        metadata_path = source_dir / "metadata.json"
        metadata = {
            "bundle_schema_version": exports.BUNDLE_SCHEMA_VERSION,
            "script_name": "existing",
            "context_name": "Existing Project",
            "bundle_role": "ready",
            "ready_to_import": True,
            "source_export_kind": "unknown",
            "verification_state": "offline_validated",
            "readiness_status": "ready_to_import",
            "readiness": {},
            "lifecycle": {
                "schema_version": "tecan.bundle_lifecycle.v1",
                "bundle_role": "ready",
                "source_export_kind": "unknown",
                "verification_state": "offline_validated",
                "created_from": {"context_name": "Existing Project"},
                "supersedes": None,
                "superseded_by": None,
            },
            "layout": {"metadata": "source/metadata.json"},
            "files": [],
        }
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")
        return {
            "bundle_dir": bundle_dir,
            "metadata_path": metadata_path,
            "metadata_text": metadata_path.read_text(encoding="utf-8"),
            "marker_path": marker_path,
            "marker_text": marker_path.read_text(encoding="utf-8"),
        }

    def _build_fixture(self, tmp_path: Path) -> dict[str, object]:
        self._configure_export_roots(tmp_path)
        existing_bundle = self._seed_existing_ready_bundle()
        xscr = tmp_path / "draft.xscr"
        draft = tmp_path / "draft.py"
        source_project = tmp_path / "source_project.zeia"
        source_script = tmp_path / "source_script.xscr"
        helper_subroutine = tmp_path / "HelperSub.xscr"
        xscr.write_text(
            """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Pipeline simple transfer</ObjectName>
    <Reference>
      <Guid>workspace-guid</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <PayloadData><Script><Commands /></Script></PayloadData>
  </Payload>
</VxData>
""",
            encoding="utf-8",
        )
        draft.write_text(
            """from fluentcoder import Worktable


def build_worktable():
    return Worktable.from_workspace("780_Empty", auto_place=False)
""",
            encoding="utf-8",
        )
        source_script.write_text(
            """<?xml version="1.0"?>
<Root>
  <ObjectName>SourceScript</ObjectName>
</Root>
""",
            encoding="utf-8",
        )
        helper_subroutine.write_text(
            """<?xml version="1.0"?>
<Root>
  <ObjectName>HelperSub</ObjectName>
</Root>
""",
            encoding="utf-8",
        )
        with zipfile.ZipFile(source_project, "w") as zf:
            zf.writestr("Scripts/source_script.xscr", "<Root><ObjectName>SourceScript</ObjectName></Root>")

        source_manifest = {
            "name": "My Project",
            "root": str(tmp_path),
            "errors": [],
            "workspaces": [{"object_name": "780_Empty", "guids": ["workspace-guid"]}],
            "labware_names": [],
            "liquid_classes": [],
            "rack_types": [],
            "worklist_paths": [],
            "scripts": [
                {
                    "object_name": "SourceScript",
                    "entry": "Scripts/source_script.xscr",
                    "extracted_path": str(source_script),
                    "dependencies": {},
                },
                {
                    "object_name": "HelperSub",
                    "entry": "Subroutines/HelperSub.xscr",
                    "extracted_path": str(helper_subroutine),
                    "dependencies": {},
                },
            ],
            "objects": [],
        }

        return {
            "tmp_path": tmp_path,
            "xscr": xscr,
            "draft": draft,
            "source_project": source_project,
            "source_script": source_script,
            "helper_subroutine": helper_subroutine,
            "source_manifest": source_manifest,
            "ready_root": exports.READY_TO_IMPORT_DIR,
            "staging_root": exports.PACKAGE_STAGING_DIR,
            "failed_root": exports.FAILED_PACKAGES_DIR,
            "existing_bundle": existing_bundle,
        }

    def _export_kwargs(self, fixture: dict[str, object]) -> dict[str, object]:
        return {
            "context_name": "My Project",
            "draft_path": fixture["draft"],
            "source_projects": [fixture["source_project"]],
            "source_scripts": [fixture["source_script"]],
            "source_manifest": fixture["source_manifest"],
        }

    def _assert_existing_bundle_unchanged(self, existing_bundle: dict[str, object]) -> None:
        metadata_path = existing_bundle["metadata_path"]
        marker_path = existing_bundle["marker_path"]
        self.assertEqual(metadata_path.read_text(encoding="utf-8"), existing_bundle["metadata_text"])
        self.assertEqual(marker_path.read_text(encoding="utf-8"), existing_bundle["marker_text"])

    def _assert_quarantined_failure(
        self,
        fixture: dict[str, object],
        *,
        expected_ready_bundle_names: list[str],
        expected_verification_state: str,
        expected_transaction_stage: str | None = None,
        failure_message_substring: str | None = None,
    ) -> tuple[Path, dict[str, object]]:
        self.assertEqual(exports.READY_TO_IMPORT_DIR, fixture["ready_root"])
        self.assertEqual(exports.PACKAGE_STAGING_DIR, fixture["staging_root"])
        self.assertEqual(exports.FAILED_PACKAGES_DIR, fixture["failed_root"])
        self.assertTrue(fixture["staging_root"].exists())
        self.assertEqual(list(fixture["staging_root"].iterdir()), [])
        ready_names = sorted(path.name for path in fixture["ready_root"].iterdir())
        self.assertEqual(ready_names, sorted(expected_ready_bundle_names))
        self._assert_existing_bundle_unchanged(fixture["existing_bundle"])

        failed_metadata_paths = list(fixture["failed_root"].rglob("source/metadata.json"))
        self.assertEqual(len(failed_metadata_paths), 1)
        metadata_path = failed_metadata_paths[0]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["bundle_role"], "debug")
        self.assertFalse(metadata["ready_to_import"])
        self.assertEqual(metadata["verification_state"], expected_verification_state)
        self.assertEqual(metadata["lifecycle"]["bundle_role"], "debug")
        self.assertEqual(metadata["lifecycle"]["verification_state"], expected_verification_state)
        if expected_transaction_stage is not None:
            self.assertEqual(metadata["transaction_failure"]["stage"], expected_transaction_stage)
            self.assertIn(failure_message_substring, metadata["transaction_failure"]["message"])
        return metadata_path, metadata

    def _run_export_failure(
        self,
        fixture: dict[str, object],
        *,
        patchers: list[object],
        expected_exception_substring: str,
        expected_ready_bundle_names: list[str],
        expected_verification_state: str = "failed_transaction",
        expected_transaction_stage: str | None = None,
        failure_message_substring: str | None = None,
        validate_side_effect: object | None = None,
    ) -> tuple[Path, dict[str, object]]:
        with ExitStack() as stack:
            stack.enter_context(
                mock.patch.object(exports, "finalize_compiled_xscr", return_value=_ok_finalization_report())
            )
            if validate_side_effect is None:
                stack.enter_context(
                    mock.patch.object(
                        exports,
                        "validate_ready_to_import",
                        return_value=self._ready_validation_report(),
                    )
                )
            else:
                stack.enter_context(
                    mock.patch.object(
                        exports,
                        "validate_ready_to_import",
                        side_effect=validate_side_effect,
                    )
                )
            for patcher in patchers:
                stack.enter_context(patcher)
            with self.assertRaises(PipelineError) as raised:
                exports.export_ready_to_import(
                    fixture["xscr"],
                    **self._export_kwargs(fixture),
                )
        self.assertIn(expected_exception_substring, str(raised.exception))
        return self._assert_quarantined_failure(
            fixture,
            expected_ready_bundle_names=expected_ready_bundle_names,
            expected_verification_state=expected_verification_state,
            expected_transaction_stage=expected_transaction_stage,
            failure_message_substring=failure_message_substring,
        )

    def _publish_ready_bundle(self, fixture: dict[str, object]) -> list[exports.ExportedArtifact]:
        with mock.patch.object(exports, "finalize_compiled_xscr", return_value=_ok_finalization_report()), mock.patch.object(
            exports,
            "validate_ready_to_import",
            return_value=self._ready_validation_report(),
        ):
            return exports.export_ready_to_import(
                fixture["xscr"],
                **self._export_kwargs(fixture),
            )

    def test_export_quarantines_archive_copying_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._build_fixture(Path(tmp))
            real_copy = exports._copy

            def fail_original_source_copy(source: Path, destination: Path) -> None:
                if "original-sources" in destination.parts:
                    raise OSError("archive copying failed")
                real_copy(source, destination)

            self._run_export_failure(
                fixture,
                patchers=[mock.patch.object(exports, "_copy", side_effect=fail_original_source_copy)],
                expected_exception_substring="failed package moved to",
                expected_ready_bundle_names=["existing-bundle"],
                expected_transaction_stage="archive_copying",
                failure_message_substring="archive copying failed",
            )

    def test_export_quarantines_zip_extraction_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._build_fixture(Path(tmp))
            self._run_export_failure(
                fixture,
                patchers=[
                    mock.patch.object(
                        exports,
                        "_write_generated_project_archive",
                        side_effect=zipfile.BadZipFile("zip extraction failed"),
                    )
                ],
                expected_exception_substring="failed package moved to",
                expected_ready_bundle_names=["existing-bundle"],
                expected_transaction_stage="zip_extraction",
                failure_message_substring="zip extraction failed",
            )

    def test_export_quarantines_manifest_construction_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._build_fixture(Path(tmp))
            subroutine_artifact = {
                "ref": "HelperSub",
                "object_name": "HelperSub",
                "folder": "",
                "guid": "helper-guid",
                "entry": "Subroutines/HelperSub.xscr",
                "version": "1",
                "source_context": "ctx",
                "path": fixture["helper_subroutine"],
                "ambiguous": False,
                "alternatives": [],
            }
            self._run_export_failure(
                fixture,
                patchers=[
                    mock.patch.object(exports, "_resolved_subroutine_artifacts", return_value=[subroutine_artifact]),
                    mock.patch.object(
                        exports,
                        "_render_subroutine_manifest",
                        side_effect=RuntimeError("manifest construction failed"),
                    ),
                ],
                expected_exception_substring="failed package moved to",
                expected_ready_bundle_names=["existing-bundle"],
                expected_transaction_stage="manifest_construction",
                failure_message_substring="manifest construction failed",
            )

    def test_export_quarantines_generated_zeia_creation_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._build_fixture(Path(tmp))
            self._run_export_failure(
                fixture,
                patchers=[
                    mock.patch.object(
                        exports,
                        "_write_generated_project_archive",
                        side_effect=RuntimeError("generated ZEIA creation failed"),
                    )
                ],
                expected_exception_substring="failed package moved to",
                expected_ready_bundle_names=["existing-bundle"],
                expected_transaction_stage="generated_zeia_creation",
                failure_message_substring="generated ZEIA creation failed",
            )

    def test_export_quarantines_post_package_validation_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._build_fixture(Path(tmp))
            metadata_path, _ = self._run_export_failure(
                fixture,
                patchers=[],
                expected_exception_substring="failed package moved to",
                expected_ready_bundle_names=["existing-bundle"],
                expected_verification_state="failed_validation",
                validate_side_effect=[
                    self._ready_validation_report(),
                    self._failed_post_package_validation_report(),
                ],
            )
            validation_report = json.loads((metadata_path.parent / "reports" / "validation_report.json").read_text(encoding="utf-8"))
            self.assertFalse(validation_report["ready"])
            self.assertEqual(validation_report["gates"][0]["summary"], "archive audit failed")

    def test_export_quarantines_metadata_generation_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._build_fixture(Path(tmp))
            real_write_text = Path.write_text
            fail_once = {"raised": False}

            def fail_metadata_write(path_self: Path, data: str, *args, **kwargs) -> int:
                if (
                    path_self.name == "metadata.json"
                    and "package-staging" in path_self.parts
                    and not fail_once["raised"]
                ):
                    fail_once["raised"] = True
                    raise OSError("metadata generation failed")
                return real_write_text(path_self, data, *args, **kwargs)

            self._run_export_failure(
                fixture,
                patchers=[mock.patch.object(Path, "write_text", autospec=True, side_effect=fail_metadata_write)],
                expected_exception_substring="failed package moved to",
                expected_ready_bundle_names=["existing-bundle"],
                expected_transaction_stage="metadata_generation",
                failure_message_substring="metadata generation failed",
            )

    def test_export_quarantines_final_publication_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._build_fixture(Path(tmp))
            real_move_bundle_root = exports._move_bundle_root

            def fail_ready_publish(source_root: Path, destination_root: Path) -> None:
                destination = Path(destination_root)
                if destination.parent.resolve() == Path(fixture["ready_root"]).resolve():
                    raise PipelineError("final publication failed")
                return real_move_bundle_root(source_root, destination_root)

            self._run_export_failure(
                fixture,
                patchers=[mock.patch.object(exports, "_move_bundle_root", side_effect=fail_ready_publish)],
                expected_exception_substring="failed package moved to",
                expected_ready_bundle_names=["existing-bundle"],
                expected_transaction_stage="final_publication",
                failure_message_substring="final publication failed",
            )

    def test_attach_generation_reports_quarantines_generation_manifest_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._build_fixture(Path(tmp))
            published_artifacts = self._publish_ready_bundle(fixture)
            bundle_dir = fixture["ready_root"] / "draft_v1"
            bundle_metadata = bundle_dir / "source" / "metadata.json"
            metadata_before = bundle_metadata.read_text(encoding="utf-8")
            generation_manifest = fixture["tmp_path"] / "generation_manifest.json"
            workflow_report = fixture["tmp_path"] / "GENERATION_WORKFLOW.md"
            generation_manifest.write_text('{"workflow_status": "ready_to_import"}', encoding="utf-8")
            workflow_report.write_text("# Generation Workflow\n", encoding="utf-8")
            real_copy = exports._copy

            def fail_generation_manifest_copy(source: Path, destination: Path) -> None:
                if destination.name == "generation_manifest.json":
                    raise OSError("generation manifest creation failed")
                real_copy(source, destination)

            with mock.patch.object(exports, "_copy", side_effect=fail_generation_manifest_copy):
                with self.assertRaises(PipelineError) as raised:
                    exports.attach_generation_reports_to_ready_bundles(
                        [item.destination for item in published_artifacts],
                        ready_root=fixture["ready_root"],
                        generation_manifest=generation_manifest,
                        workflow_report=workflow_report,
                    )
            self.assertIn("generation manifest creation failed", str(raised.exception))
            self.assertTrue(bundle_dir.exists())
            self.assertEqual(bundle_metadata.read_text(encoding="utf-8"), metadata_before)
            self.assertFalse((bundle_dir / "source" / "generation_manifest.json").exists())
            self.assertFalse((bundle_dir / "source" / "GENERATION_WORKFLOW.md").exists())
            self._assert_quarantined_failure(
                fixture,
                expected_ready_bundle_names=["draft_v1", "existing-bundle"],
                expected_verification_state="failed_transaction",
                expected_transaction_stage="generation_manifest_creation",
                failure_message_substring="generation manifest creation failed",
            )

    def test_export_ready_to_import_moves_failed_bundles_to_failed_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            old_export_dir = exports.READY_TO_IMPORT_DIR
            old_staging_dir = exports.PACKAGE_STAGING_DIR
            old_failed_dir = exports.FAILED_PACKAGES_DIR
            exports.READY_TO_IMPORT_DIR = tmp_path / "ready-to-import"
            exports.PACKAGE_STAGING_DIR = tmp_path / "build" / "package-staging"
            exports.FAILED_PACKAGES_DIR = tmp_path / "build" / "failed-packages"
            try:
                xscr = tmp_path / "draft.xscr"
                draft = tmp_path / "draft.py"
                source_project = tmp_path / "source_project.zeia"
                source_script = tmp_path / "source_script.xscr"
                xscr.write_text(
                    """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>Pipeline simple transfer</ObjectName>
    <Reference>
      <Guid>workspace-guid</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>780_Empty</ObjectName>
    </Reference>
    <PayloadData><Script><Commands /></Script></PayloadData>
  </Payload>
</VxData>
""",
                    encoding="utf-8",
                )
                draft.write_text(
                    """from fluentcoder import Worktable


def build_worktable():
    return Worktable.from_workspace("780_Empty", auto_place=False)
""",
                    encoding="utf-8",
                )
                source_script.write_text(
                    """<?xml version="1.0"?>
<Root>
  <ObjectName>SourceScript</ObjectName>
</Root>
""",
                    encoding="utf-8",
                )
                with zipfile.ZipFile(source_project, "w") as zf:
                    zf.writestr("Scripts/source_script.xscr", "<Root><ObjectName>SourceScript</ObjectName></Root>")

                stage_root = exports.PACKAGE_STAGING_DIR.resolve()
                ready_root = exports.READY_TO_IMPORT_DIR.resolve()
                failed_root = exports.FAILED_PACKAGES_DIR.resolve()
                real_rename = exports.os.rename
                replace_calls: list[tuple[Path, Path]] = []

                def spy_replace(src, dst):
                    source_path = Path(src).resolve()
                    destination_path = Path(dst).resolve()
                    replace_calls.append((source_path, destination_path))
                    self.assertIn(stage_root, source_path.parents)
                    self.assertIn(failed_root, destination_path.parents)
                    self.assertFalse(destination_path.exists())
                    return real_rename(src, dst)

                preflight_report = {"ready": True, "gates": [], "validation_version": "test"}
                failed_report = {
                    "ready": False,
                    "gates": [
                        {
                            "gate": "Gate 24",
                            "name": "packaged generated ZEIA opens, resolves references, and matches its datastore metadata",
                            "status": "failed",
                            "summary": "archive audit failed",
                        }
                    ],
                    "validation_version": "test",
                }

                with mock.patch.object(
                    exports,
                    "validate_ready_to_import",
                    side_effect=[preflight_report, failed_report],
                ), mock.patch.object(exports.os, "rename", side_effect=spy_replace):
                    with self.assertRaisesRegex(PipelineError, "failed package moved to"):
                        exports.export_ready_to_import(
                            xscr,
                            context_name="My Project",
                            draft_path=draft,
                            source_projects=[source_project],
                            source_scripts=[source_script],
                            source_manifest={
                                "name": "My Project",
                                "root": str(tmp_path),
                                "errors": [],
                                "workspaces": [{"object_name": "780_Empty", "guids": ["workspace-guid"]}],
                                "labware_names": [],
                                "liquid_classes": [],
                                "rack_types": [],
                                "worklist_paths": [],
                                "scripts": [
                                    {
                                        "object_name": "SourceScript",
                                        "entry": "Scripts/source_script.xscr",
                                        "extracted_path": str(source_script),
                                        "dependencies": {},
                                    }
                                ],
                                "objects": [],
                            },
                        )

                self.assertEqual(len(replace_calls), 1)
                self.assertEqual(exports.READY_TO_IMPORT_DIR.resolve(), ready_root)
                self.assertTrue(stage_root.exists())
                self.assertEqual(list(stage_root.iterdir()), [])
                if ready_root.exists():
                    self.assertEqual(list(ready_root.iterdir()), [])
                failed_metadata = list(failed_root.rglob("source/metadata.json"))
                self.assertEqual(len(failed_metadata), 1)
                metadata = json.loads(failed_metadata[0].read_text(encoding="utf-8"))
                self.assertEqual(metadata["bundle_role"], "debug")
                self.assertFalse(metadata["ready_to_import"])
                self.assertEqual(metadata["verification_state"], "failed_validation")
                self.assertEqual(metadata["lifecycle"]["bundle_role"], "debug")
                self.assertEqual(metadata["lifecycle"]["verification_state"], "failed_validation")
            finally:
                exports.READY_TO_IMPORT_DIR = old_export_dir
                exports.PACKAGE_STAGING_DIR = old_staging_dir
                exports.FAILED_PACKAGES_DIR = old_failed_dir


class ArchiveVerifierTests(unittest.TestCase):
    SCRIPT_GUID = "22222222-2222-4222-8222-222222222222"
    WORKSPACE_GUID = "11111111-1111-4111-8111-111111111111"

    def setUp(self):
        self._full_copy_env = _force_full_zeia_copy_env()
        self._full_copy_env.start()

    def tearDown(self):
        self._full_copy_env.stop()

    def _write_datastore_archive(self, path: Path, *, content_entries: list[str]) -> None:
        entries_xml = "".join(f"\t\t\t<Entry>{entry}</Entry>\r\n" for entry in content_entries)
        script_payload = f"""<?xml version="1.0" encoding="utf-8"?>
<sd:VxData>
  <Payload>
    <ObjectName>MainScript</ObjectName>
    <Reference>
      <Guid>{self.WORKSPACE_GUID}</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>WorkspaceOne</ObjectName>
    </Reference>
    <PayloadData><Script><Commands /></Script></PayloadData>
  </Payload>
  <Checksum></Checksum>
</sd:VxData>
""".encode()
        script_payload = exports.recompute_checksum_bytes(script_payload)
        self.assertIsNotNone(script_payload)
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                f"DataStore\\UserSpecific\\{self.SCRIPT_GUID}.xscr",
                script_payload,
            )
            zf.writestr(
                "DataStore\\nodedescription.xml",
                f"""<?xml version="1.0" encoding="utf-8"?>
<NodeDescription>
  <Payload>
    <S isRootNode="True">
      <Id>{self.WORKSPACE_GUID}</Id>
      <N>WorkspaceOne</N>
      <T>1</T>
    </S>
    <S isRootNode="True">
      <Id>{self.SCRIPT_GUID}</Id>
      <N>MainScript</N>
      <T>2</T>
    </S>
  </Payload>
  <Checksum>valid</Checksum>
</NodeDescription>
""",
            )
            zf.writestr(
                "meta\\content.xml",
                f"""<?xml version="1.0" encoding="utf-8"?>
<ArchiveContent>
  <Payload>
    <DatastoreEntries>
{entries_xml}\t\t</DatastoreEntries>
  </Payload>
  <Checksum>valid</Checksum>
</ArchiveContent>
""",
            )

    def test_verify_clean_datastore_archive_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "generated_project.zeia"
            self._write_datastore_archive(
                archive,
                content_entries=[
                    f"UserSpecific\\{self.SCRIPT_GUID}.xscr",
                    "nodedescription.xml",
                ],
            )
            audit = exports.verify_generated_project_archive(archive, bundle_root=archive.parent)
            self.assertTrue(audit["zip_ok"])
            self.assertEqual(audit["blocking"], [])
            self.assertEqual(audit["needs_review"], [])

    def test_verify_duplicate_script_checksum_is_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "generated_project.zeia"
            self._write_datastore_archive(
                archive,
                content_entries=[
                    f"UserSpecific\\{self.SCRIPT_GUID}.xscr",
                    "nodedescription.xml",
                ],
            )
            with zipfile.ZipFile(archive, "r") as source:
                entries = {
                    info.filename: source.read(info.filename)
                    for info in source.infolist()
                }
            expected_entry = exports._normalize_archive_entry(
                f"DataStore\\UserSpecific\\{self.SCRIPT_GUID}.xscr"
            )
            script_entry = next(
                name
                for name in entries
                if exports._normalize_archive_entry(name) == expected_entry
            )
            entries[script_entry] = entries[script_entry].replace(
                b"<Checksum>",
                b"<Checksum /><Checksum>",
                1,
            )
            with zipfile.ZipFile(archive, "w") as destination:
                for name, payload in entries.items():
                    destination.writestr(name, payload)

            audit = exports.verify_generated_project_archive(archive, bundle_root=archive.parent)

            self.assertTrue(
                any(item["kind"] == "invalid_checksum" for item in audit["blocking"])
            )
    def test_verify_metadata_entry_missing_is_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "generated_project.zeia"
            self._write_datastore_archive(
                archive,
                content_entries=[
                    "UserSpecific\\missing-guid.xscr",
                    f"UserSpecific\\{self.SCRIPT_GUID}.xscr",
                    "nodedescription.xml",
                ],
            )
            audit = exports.verify_generated_project_archive(archive, bundle_root=archive.parent)
            self.assertTrue(any(item["kind"] == "metadata_entry_missing" for item in audit["blocking"]))

    def test_verify_unresolved_reference_is_needs_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "generated_project.zeia"
            lc_guid = "0be7658e-f376-40ee-ad88-1f9e772c47be"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "Scripts/main.xscr",
                    f"""<VxData><Payload>
<Reference><Guid>{lc_guid}</Guid><TypeId>LiquidClass</TypeId>
<ObjectName>Water Free Single</ObjectName></Reference>
<LiquidClassName>Water Free Single</LiquidClassName>
</Payload></VxData>""",
                )
            audit = exports.verify_generated_project_archive(archive, bundle_root=archive.parent)
            self.assertEqual(audit["blocking"], [])
            unresolved = [item for item in audit["needs_review"] if item["kind"] == "unresolved_reference"]
            self.assertEqual(len(unresolved), 1)
            self.assertEqual(unresolved[0]["guid"], lc_guid)

    def test_packaged_archive_includes_archive_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_project = tmp_path / "source.zeia"
            compiled_xscr = tmp_path / "compiled.xscr"
            destination = tmp_path / "generated_project.zeia"
            self._write_datastore_archive(
                source_project,
                content_entries=[
                    f"UserSpecific\\{self.SCRIPT_GUID}.xscr",
                    "nodedescription.xml",
                ],
            )
            compiled_xscr.write_text(
                f"""<?xml version="1.0" encoding="utf-8"?>
<sd:VxData>
  <Payload>
    <ObjectName>GeneratedScript</ObjectName>
    <Reference>
      <Guid>{self.WORKSPACE_GUID}</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>WorkspaceOne</ObjectName>
    </Reference>
    <PayloadData><Script><Commands /></Script></PayloadData>
  </Payload>
  <Checksum></Checksum>
</sd:VxData>
""",
                encoding="utf-8",
            )
            record = exports._write_generated_project_archive(
                source_project,
                destination,
                compiled_xscr=compiled_xscr,
                bundle_root=tmp_path,
                source_manifest={
                    "root": str(tmp_path),
                    "scripts": [
                        {
                            "entry": f"DataStore\\UserSpecific\\{self.SCRIPT_GUID}.xscr",
                            "extracted_path": "source_script.xscr",
                            "object_name": "MainScript",
                        }
                    ],
                },
                source_xscr=None,
                source_scripts=[tmp_path / "source_script.xscr"],
                subroutine_artifacts=[],
            )
            audit = record.get("archive_audit") or {}
            self.assertTrue(audit.get("zip_ok"))
            self.assertEqual(audit.get("blocking"), [])

    def test_project_import_report_explains_unresolved_reference_dialog(self):
        report = exports._render_project_import_report(
            [
                {
                    "relative_path": "direct-imports/projects/full-project/generated_project.zeia",
                    "source_project": "source.zeia",
                    "main_script": {"replaced_entry": "", "object_name": "GeneratedScript"},
                    "zip_valid": True,
                    "archive_audit": {
                        "zip_ok": True,
                        "entry_count": 4,
                        "blocking": [],
                        "needs_review": [
                            {
                                "kind": "unresolved_reference",
                                "type_id": "WorktableWorkspace",
                                "object_name": "WorkspaceOne",
                                "guid": self.WORKSPACE_GUID,
                                "entry": f"DataStore/UserSpecific/{self.SCRIPT_GUID}.xscr",
                            }
                        ],
                    },
                    "checksum_note": "Import-clean.",
                }
            ]
        )

        self.assertIn("missing referenced files dialog", report)
        self.assertIn("WorkspaceOne", report)
        self.assertIn("import health only", report)
        self.assertIn("Gate 27 FluentControl import/load diagnostic", report)
        self.assertIn("open the generated script in FluentControl Script Editor", report)


class ArchiveWriterPostprocessTests(unittest.TestCase):
    def test_self_closing_checksum_is_normalized_without_duplication(self):
        payload = (
            b"<VxData><Payload><ObjectName>Main</ObjectName></Payload>"
            b"<Checksum /></VxData>"
        )

        processed = exports._postprocess_archive_writer_script_payload(payload)

        self.assertEqual(processed.count(b"<Checksum"), 1)
        self.assertIn(b"<Checksum></Checksum>", processed)


class ArchiveScriptIdentityAuditTests(unittest.TestCase):
    GUID = "41e40175-5ead-5db6-ac71-5a91505bbe50"

    def _write_archive(self, path: Path, *, node_folder: str) -> None:
        script = stamp_checksum(
            f"""<sd:VxData><Payload>
<ObjectName>Verification_Script2</ObjectName>
<ObjectSubfolderPath>Demo</ObjectSubfolderPath>
</Payload><Checksum></Checksum></sd:VxData>""".encode()
        )
        assert script is not None
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"DataStore/UserSpecific/{self.GUID}.xscr", script)
            archive.writestr(
                "DataStore/nodedescription.xml",
                f"""<NodeDescription><Payload><MappingSection>
<Map><Type>Script</Type><Short>1</Short></Map>
</MappingSection><S isRootNode="True">
<Id>{self.GUID}</Id><N>Verification_Script2</N>
<P>{node_folder}</P><T>1</T><V>1</V><TV>2.0</TV>
</S></Payload><Checksum>metadata</Checksum></NodeDescription>""",
            )
            archive.writestr(
                "meta/content.xml",
                f"""<ArchiveContent><Payload><DatastoreEntries>
<Entry>UserSpecific/{self.GUID}.xscr</Entry>
<Entry>nodedescription.xml</Entry>
</DatastoreEntries></Payload><Checksum>metadata</Checksum></ArchiveContent>""",
            )

    def test_folder_mismatch_is_blocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "mismatch.zeia"
            self._write_archive(archive, node_folder="Alternate folder scripts")

            audit = exports.verify_generated_project_archive(archive, bundle_root=archive.parent)

        mismatches = [
            finding
            for finding in audit["blocking"]
            if finding.get("kind") == "script_node_identity_mismatch"
        ]
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]["field"], "folder")
        self.assertEqual(mismatches[0]["payload_value"], "Demo")
        self.assertEqual(mismatches[0]["node_value"], "Alternate folder scripts")

    def test_matching_folder_passes_identity_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "matching.zeia"
            self._write_archive(archive, node_folder="Demo")

            audit = exports.verify_generated_project_archive(archive, bundle_root=archive.parent)

        self.assertFalse(
            any(
                finding.get("kind", "").startswith("script_node_identity")
                for finding in audit["blocking"]
            )
        )


class DatastoreSubroutineMetadataTests(unittest.TestCase):
    WORKSPACE_GUID = "11111111-1111-4111-8111-111111111111"
    MAIN_GUID = "22222222-2222-4222-8222-222222222222"

    def setUp(self):
        self._full_copy_env = _force_full_zeia_copy_env()
        self._full_copy_env.start()

    def tearDown(self):
        self._full_copy_env.stop()

    def _write_source_datastore_archive(self, path: Path) -> None:
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(
                f"DataStore\\UserSpecific\\{self.MAIN_GUID}.xscr",
                f"""<?xml version="1.0" encoding="utf-8"?>
<sd:VxData>
  <Payload>
    <ObjectName>MainScript</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
    <Reference>
      <Guid>{self.WORKSPACE_GUID}</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>WorkspaceOne</ObjectName>
    </Reference>
    <PayloadData><Script><Commands /></Script></PayloadData>
  </Payload>
  <Checksum>valid</Checksum>
</sd:VxData>
""",
            )
            zf.writestr(
                "DataStore\\nodedescription.xml",
                f"""<?xml version="1.0" encoding="utf-8"?>
<NodeDescription>
\t<Payload>
\t\t<S isRootNode="True">
\t\t\t<Id>{self.WORKSPACE_GUID}</Id>
\t\t\t<N>WorkspaceOne</N>
\t\t\t<P></P>
\t\t\t<T>1</T>
\t\t\t<V>5</V>
\t\t\t<TV>1</TV>
\t\t</S>
\t\t<S isRootNode="True">
\t\t\t<Id>{self.MAIN_GUID}</Id>
\t\t\t<N>MainScript</N>
\t\t\t<P>Demo</P>
\t\t\t<T>2</T>
\t\t\t<V>7</V>
\t\t\t<TV>2.0</TV>
\t\t</S>
\t</Payload>
\t<Checksum>valid</Checksum>
</NodeDescription>
""",
            )
            zf.writestr(
                "meta\\content.xml",
                f"""<?xml version="1.0" encoding="utf-8"?>
<ArchiveContent>
\t<Payload>
\t\t<DatastoreEntries>
\t\t\t<Entry>UserSpecific\\{self.MAIN_GUID}.xscr</Entry>
\t\t\t<Entry>nodedescription.xml</Entry>
\t\t</DatastoreEntries>
\t</Payload>
\t<Checksum>valid</Checksum>
</ArchiveContent>
""",
            )

    def _write_compiled_main(self, path: Path) -> None:
        path.write_text(
            f"""<?xml version="1.0" encoding="utf-8"?>
<sd:VxData>
  <Payload>
    <ObjectName>MainScript</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
    <Reference>
      <Guid>{self.WORKSPACE_GUID}</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>WorkspaceOne</ObjectName>
    </Reference>
    <PayloadData><Script><Commands /></Script></PayloadData>
  </Payload>
  <Checksum></Checksum>
</sd:VxData>
""",
            encoding="utf-8",
        )

    def _node_block_for(self, node_xml: str, object_name: str) -> str:
        import re

        for match in re.finditer(r"<S\b[^>]*>.*?</S>", node_xml, flags=re.DOTALL):
            block = match.group(0)
            if f"<N>{object_name}</N>" in block:
                return block
        raise AssertionError(f"node for {object_name} not found")

    def test_next_nodedescription_version_uses_max_plus_one(self):
        self.assertEqual(
            exports._next_nodedescription_version("<V>5</V> <V>7</V> <V>3</V>"), 8
        )
        self.assertEqual(exports._next_nodedescription_version("<Payload></Payload>"), 1)

    def test_replace_or_insert_simple_tag_treats_windows_paths_literally(self):
        block = "<S><Id>1</Id><N>MainScript</N><P>Demo</P></S>"

        updated = exports._replace_or_insert_simple_tag(
            block,
            "P",
            r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\MainScript_media",
            after_tag="N",
        )

        self.assertIn(
            r"<P>C:\ProgramData\Tecan\VisionX\TouchToolsData\Images\MainScript_media</P>",
            updated,
        )

    def test_script_file_references_extracts_external_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "dep.xscr"
            script.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData><Payload>
  <ObjectName>WithDeps</ObjectName>
  <FileReference><File>C:\\TubeEye\\bin\\TEyeClient.exe</File></FileReference>
  <FileReference><File>C:\\Images\\plate.jpg</File></FileReference>
  <PayloadData><Script><Commands /></Script></PayloadData>
</Payload></sd:VxData>
""",
                encoding="utf-8",
            )
            self.assertEqual(
                exports._script_file_references(script),
                ["C:\\TubeEye\\bin\\TEyeClient.exe", "C:\\Images\\plate.jpg"],
            )
            no_deps = Path(tmp) / "plain.xscr"
            no_deps.write_text(
                "<sd:VxData><Payload><ObjectName>Plain</ObjectName></Payload></sd:VxData>",
                encoding="utf-8",
            )
            self.assertEqual(exports._script_file_references(no_deps), [])

    def test_legacy_generated_project_embeds_generated_touchtools_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_project = tmp_path / "source.zeia"
            destination = tmp_path / "generated_project.zeia"
            bundle_root = tmp_path / "bundle"
            source_dir = bundle_root / "source"
            reports_dir = source_dir / "reports"
            script_dir = bundle_root
            compiled_xscr = bundle_root / "direct-imports" / "scripts" / "full-script" / "generated_script.xscr"
            compiled_xscr.parent.mkdir(parents=True)
            source_dir.mkdir(parents=True)
            self._write_source_datastore_archive(source_project)
            compiled_xscr.write_text(
                f"""<?xml version="1.0" encoding="utf-8"?>
<sd:VxData>
  <Payload>
    <ObjectName>MainScript</ObjectName>
    <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
    <Reference>
      <Guid>{self.WORKSPACE_GUID}</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>WorkspaceOne</ObjectName>
    </Reference>
    <PayloadData>
      <Script>
        <Commands>
          <Object Type="Tecan.Core.Scripting.RUPStandardStatement">
            <RUPStandardStatement>
              <SelectedImagePath>media/step_001_image.png</SelectedImagePath>
            </RUPStandardStatement>
          </Object>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
  <Checksum></Checksum>
</sd:VxData>
""",
                encoding="utf-8",
            )
            protocol_ir = {
                "protocol": {"name": "MainScript"},
                "steps": [
                    {
                        "id": "step_001",
                        "operation": "prompt_user",
                        "command_id": "RUPStandardStatement",
                        "parameters": {
                            "prompt": "Check media.",
                            "rup_kind": "standard",
                            "media_placeholders": [
                                {
                                    "kind": "image",
                                    "slot": "step_001_image",
                                    "path": "media/step_001_image.png",
                                }
                            ],
                        },
                    }
                ],
            }
            protocol_ir_path = source_dir / "protocol.ir.json"
            protocol_ir_path.write_text(json.dumps(protocol_ir), encoding="utf-8")

            media_path_map, media_dir = exports._prepare_generated_touchtools_media(
                protocol_ir_path,
                compiled_xscr,
                script_dir=script_dir,
                source_dir=source_dir,
                reports_dir=reports_dir,
            )
            record = exports._write_generated_project_archive_legacy_zip(
                source_project,
                destination,
                compiled_xscr=compiled_xscr,
                bundle_root=bundle_root,
                source_manifest=None,
                source_xscr=None,
                source_scripts=[],
                subroutine_artifacts=[],
                media_dir=media_dir,
                media_path_map=media_path_map,
                filesystem_source_archives=[source_project],
            )

            self.assertEqual(record["generated_media_packaging"]["file_count"], 1)
            self.assertEqual(record["filesystem_packaging"]["unresolved_paths"], [])
            with zipfile.ZipFile(destination) as zf:
                names = {name.replace("\\", "/") for name in zf.namelist()}
                self.assertTrue(any(name.startswith("fs/") and name.endswith("/step_001_image.png") for name in names))
                script_text = _read_archive_text(zf, f"DataStore/UserSpecific/{self.MAIN_GUID}.xscr")

            expected_path = (
                r"C:\ProgramData\Tecan\VisionX\TouchToolsData\Images"
                r"\MainScript_media\step_001_image.png"
            )
            self.assertIn(f"<SelectedImagePath>{expected_path}</SelectedImagePath>", script_text)
            self.assertIn(f"<File>{expected_path}</File>", script_text)

    def test_unique_project_guid_detects_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_project = Path(tmp) / "source.zeia"
            source_project.write_bytes(b"placeholder")
            sub = Path(tmp) / "Helper.xscr"
            sub.write_text("<sd:VxData />", encoding="utf-8")

            stable = exports._stable_project_guid(source_project, "Helper", sub)
            # No collision -> reproducible/stable GUID.
            self.assertEqual(
                exports._unique_project_guid(source_project, "Helper", sub, set()),
                stable,
            )
            # uuid5 collision present -> a different, deterministic GUID is chosen.
            derived = exports._unique_project_guid(
                source_project, "Helper", sub, {stable.casefold()}
            )
            self.assertNotEqual(derived, stable)
            self.assertEqual(
                derived,
                exports._unique_project_guid(
                    source_project, "Helper", sub, {stable.casefold()}
                ),
            )

    def test_added_subroutine_node_uses_max_version_and_fileref(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_project = tmp_path / "source.zeia"
            destination = tmp_path / "generated_project.zeia"
            compiled_xscr = tmp_path / "compiled.xscr"
            self._write_source_datastore_archive(source_project)
            with zipfile.ZipFile(source_project, "a") as zf:
                zf.writestr(
                    "fs/mapping.xml",
                    build_fs_mapping_xml([(1, r"C:\Tecan\Images")]),
                )
                zf.writestr("fs/1/holder.jpg", b"subroutine-only-asset")
            self._write_compiled_main(compiled_xscr)

            sub_with_dep = tmp_path / "WithDep.xscr"
            sub_with_dep.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData><Payload>
  <ObjectName>SubWithDep</ObjectName>
  <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
  <FileReference><File>C:\\Tecan\\Images\\holder.jpg</File></FileReference>
  <PayloadData><Script><Commands /></Script></PayloadData>
</Payload></sd:VxData>
""",
                encoding="utf-8",
            )
            sub_no_dep = tmp_path / "NoDep.xscr"
            sub_no_dep.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData><Payload>
  <ObjectName>SubNoDep</ObjectName>
  <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
  <PayloadData><Script><Commands /></Script></PayloadData>
</Payload></sd:VxData>
""",
                encoding="utf-8",
            )

            record = exports._write_generated_project_archive(
                source_project,
                destination,
                compiled_xscr=compiled_xscr,
                bundle_root=tmp_path,
                source_manifest={
                    "root": str(tmp_path),
                    "scripts": [
                        {
                            "entry": f"DataStore\\UserSpecific\\{self.MAIN_GUID}.xscr",
                            "extracted_path": "source_script.xscr",
                            "object_name": "MainScript",
                        }
                    ],
                },
                source_xscr=None,
                source_scripts=[tmp_path / "source_script.xscr"],
                subroutine_artifacts=[
                    {"path": str(sub_with_dep), "object_name": "SubWithDep"},
                    {"path": str(sub_no_dep), "object_name": "SubNoDep"},
                ],
            )

            with zipfile.ZipFile(destination) as zf:
                node = _read_archive_text(zf, "DataStore\\nodedescription.xml")
                content = _read_archive_text(zf, "meta\\content.xml")
                normalized_names = {
                    name.replace("\\", "/").casefold() for name in zf.namelist()
                }
                packaged_asset = zf.read("fs/1/holder.jpg")

            # (a) Added nodes sit one past the highest existing <V> (max(5,7)+1 = 8).
            with_dep_block = self._node_block_for(node, "SubWithDep")
            no_dep_block = self._node_block_for(node, "SubNoDep")
            self.assertIn("<V>8</V>", with_dep_block)
            self.assertIn("<V>8</V>", no_dep_block)
            self.assertIn("<T>2</T>", with_dep_block)
            self.assertIn("<TV>2.0</TV>", with_dep_block)

            # (b) FileRef emitted only for the subroutine that declares a file dep.
            self.assertIn("<FileRef>C:\\Tecan\\Images\\holder.jpg</FileRef>", with_dep_block)
            self.assertNotIn("<FileRef>", no_dep_block)
            self.assertIn("fs/1/holder.jpg", normalized_names)
            self.assertEqual(packaged_asset, b"subroutine-only-asset")
            self.assertTrue(record["filesystem_packaging"]["complete"])
            self.assertEqual(record["filesystem_packaging"]["referenced_path_count"], 1)

            # content.xml registers the new datastore scripts.
            self.assertIn("SubWithDep", node)
            self.assertIn("SubNoDep", node)
            self.assertIn("<Entry>UserSpecific\\", content)

    def test_added_subroutine_audit_records_clean_additions(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            source_project = tmp_path / "source.zeia"
            destination = tmp_path / "generated_project.zeia"
            compiled_xscr = tmp_path / "compiled.xscr"
            self._write_source_datastore_archive(source_project)
            self._write_compiled_main(compiled_xscr)

            sub = tmp_path / "Helper.xscr"
            sub.write_text(
                """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData><Payload>
  <ObjectName>Helper</ObjectName>
  <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
  <PayloadData><Script><Commands /></Script></PayloadData>
</Payload></sd:VxData>
""",
                encoding="utf-8",
            )

            record = exports._write_generated_project_archive(
                source_project,
                destination,
                compiled_xscr=compiled_xscr,
                bundle_root=tmp_path,
                source_manifest={
                    "root": str(tmp_path),
                    "scripts": [
                        {
                            "entry": f"DataStore\\UserSpecific\\{self.MAIN_GUID}.xscr",
                            "extracted_path": "source_script.xscr",
                            "object_name": "MainScript",
                        }
                    ],
                },
                source_xscr=None,
                source_scripts=[tmp_path / "source_script.xscr"],
                subroutine_artifacts=[{"path": str(sub), "object_name": "Helper"}],
            )

            audit = record["subroutine_audit"]
            self.assertEqual(len(audit["added"]), 1)
            self.assertEqual(audit["added"][0]["object_name"], "Helper")
            # Clean synthesized metadata: no blocking defects, positive version recorded.
            self.assertEqual(audit["blocking"], [])
            self.assertGreater(audit["added"][0]["version"], 0)
            # The "prefer replace over add" risk note is surfaced as a warning.
            self.assertTrue(any("ADDED to the base ZEIA" in w for w in record["warnings"]))

    def test_verify_added_subroutine_metadata_flags_defects(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "generated_project.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("DataStore\\UserSpecific\\present.xscr", "<x/>")
                zf.writestr(
                    "DataStore\\nodedescription.xml",
                    "<NodeDescription><Payload></Payload></NodeDescription>",
                )
                zf.writestr(
                    "meta\\content.xml",
                    "<ArchiveContent><Payload><DatastoreEntries></DatastoreEntries></Payload></ArchiveContent>",
                )

            audit = exports.verify_added_subroutine_metadata(
                archive,
                [
                    # malformed GUID -> blocking
                    {"object_name": "BadGuid", "entry": "DataStore\\UserSpecific\\present.xscr", "guid": "not-a-guid"},
                    # entry missing from archive -> blocking
                    {
                        "object_name": "Missing",
                        "entry": "DataStore\\UserSpecific\\missing.xscr",
                        "guid": "11111111-1111-4111-8111-111111111111",
                    },
                ],
                datastore_archive=True,
            )
            kinds = {item["kind"] for item in audit["blocking"]}
            self.assertIn("malformed_guid", kinds)
            self.assertIn("added_entry_missing", kinds)


class ProjectAuditMergeTests(unittest.TestCase):
    def test_invalid_checksum_entries_feed_ready_gate_audit(self):
        merged = exports._merge_project_audits(
            [
                {
                    "relative_path": "generated_project.zeia",
                    "archive_audit": {"zip_ok": True, "blocking": [], "needs_review": []},
                    "checksum_audit": {
                        "bridge_available": True,
                        "invalid_entries": ["Scripts/main.xscr"],
                    },
                }
            ]
        )

        self.assertEqual(
            merged["project_checksum_audit"]["invalid_entries"],
            ["Scripts/main.xscr"],
        )

    def test_dependencies_packaged_scripts_feed_subroutine_gate_audit(self):
        merged = exports._merge_project_audits(
            [
                {
                    "relative_path": "generated_project.zeia",
                    "archive_audit": {"zip_ok": True, "blocking": [], "needs_review": []},
                    "checksum_audit": {"bridge_available": True},
                    "dependencies_packaged": [
                        {
                            "type": "Script",
                            "object_name": "SUB_Get_Fingers_v1.0",
                            "guid": "bc667100-b840-4a78-8c36-f849849355c4",
                        },
                        {
                            "type": "LiquidClass",
                            "object_name": "Water Free Single",
                            "guid": "e80ad9f8-534b-41b7-96fb-c2c81cfc5c03",
                        },
                    ],
                }
            ]
        )

        dependencies = merged["project_subroutine_audit"]["dependencies"]
        self.assertEqual(len(dependencies), 1)
        self.assertEqual(dependencies[0]["object_name"], "SUB_Get_Fingers_v1.0")


class ArchiveWindowsNameRestorationTests(unittest.TestCase):
    def test_rewrite_zip_filename_records_false_signature_payload(self):
        """
        Verify that `_restore_windows_datastore_zip_names` does not corrupt the ZIP
        file if a file payload happens to contain a fake `PK\\x01\\x02` or `PK\\x03\\x04`
        signature followed by deceptive bytes that look like lengths but are actually
        large values.
        """
        from fluent_pipeline.exports import _restore_windows_datastore_zip_names

        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "test.zip"
            with zipfile.ZipFile(p, "w", compression=zipfile.ZIP_STORED) as zf:
                # We craft a payload containing a fake central directory signature
                payload = bytearray(b"hello PK\x01\x02")

                # The signature starts at offset 6.
                # name_len_offset is 28, so we need to pad 28 - 4 = 24 bytes (since PK\x01\x02 is 4 bytes).
                payload.extend(b"\x00" * 24)
                payload.extend((25).to_bytes(2, "little")) # fake name_len = 25
                payload.extend((1000).to_bytes(2, "little")) # fake extra_len = 1000

                # extra_len ends at offset 32 relative to the signature.
                # name_offset is 46, so we need 46 - 32 = 14 bytes padding.
                payload.extend(b"\x00" * 14)

                # We put a string that triggers `_windows_datastore_zip_filename`
                payload.extend(b"DataStore/false_file1.txt")

                # Add enough padding so `filename_end` doesn't fall outside the file.
                payload.extend(b"\x00" * 1200)

                # Write multiple files so the central directory comes after
                zf.writestr("DataStore/file1.txt", payload)
                zf.writestr("meta/file2.txt", b"world")
                zf.writestr("other/file3.txt", b"test")

            # Check that testzip is ok before processing
            with zipfile.ZipFile(p, "r") as zf:
                self.assertIsNone(zf.testzip())

            _restore_windows_datastore_zip_names(p)

            # If the patch failed (i.e. skipped past the actual header or corrupted
            # the payload inside file1.txt), testzip will return the corrupted file name.
            with zipfile.ZipFile(p, "r") as zf:
                ret = zf.testzip()
                self.assertIsNone(ret)
                names = zf.namelist()
                # Verify that it correctly processed `meta/file2.txt` to `meta\\file2.txt`.
                self.assertIn("meta\\file2.txt", names)


if __name__ == "__main__":
    unittest.main()
