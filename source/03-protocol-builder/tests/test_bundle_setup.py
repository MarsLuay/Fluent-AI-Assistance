from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.bundle_setup import repair_powershell_pipelines, setup_bat_findings

_POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
if _POWERSHELL:
    try:
        _probe = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-Command", "exit 0"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        if _probe.returncode != 0:
            _POWERSHELL = None
    except (OSError, subprocess.SubprocessError):
        _POWERSHELL = None


class BundleSetupTests(unittest.TestCase):
    def test_published_template_is_support_menu_with_log_and_driver_submenus(self) -> None:
        path = Path(__file__).resolve().parents[1] / "tools" / "run_tecan_bundle_setup.bat"
        text = path.read_text(encoding="utf-8")
        raw = path.read_bytes()

        self.assertEqual(setup_bat_findings(path), [])
        self.assertNotIn(b"\r\r\n", raw)
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))
        for option in (
            "--logs-only",
            "--logs-menu",
            "--log-profile",
            "--logs-script-errors",
            "--logs-program-crash",
            "--logs-import-errors",
            "--collect-instrument",
            "--collect-method-source",
            "--install-instrument",
            "--install-external-files",
            "--deploy-touchtools",
        ):
            self.assertIn(option, text)
        for removed_option in (
            "--all",
            "--externals-only",
            "--media-only",
            "--verify-only",
            "--logs-likely-causes",
            "likely-causes-script",
        ):
            self.assertNotIn(removed_option, text)
        for phase in (
            ":phase_collect_logs",
            ":phase_collect_instrument",
            ":phase_collect_method_source",
            ":phase_install_external",
            ":phase_install_instrument",
            ":phase_deploy_touchtools",
        ):
            self.assertIn(phase, text)
        for removed_phase in (
            ":phase_verify_bundle",
            ":phase_install_external_files",
            ":phase_deploy_media",
        ):
            self.assertNotIn(removed_phase, text)
        self.assertNotIn("deploy_touchtools_images.bat", text)
        self.assertIn("deploy_touchtools_media.ps1", text)
        self.assertIn("install_external_files.ps1", text)
        self.assertIn("echo Bundle: %BUNDLE_ARG%", text)
        self.assertNotIn('call :setup_log "Bundle: %BUNDLE_DIR%"', text)
        self.assertIn('-OutputRoot "%TEMP_ARG%" -BundleRoot "%BUNDLE_ARG%"', text)
        self.assertNotIn('-OutputRoot "%BUNDLE_ARG%"', text)
        self.assertNotIn('-OutputRoot "%BUNDLE_DIR%"', text)
        self.assertIn(
            'if "%RUN_LOGS%%RUN_COLLECT_INSTRUMENT%%RUN_COLLECT_METHOD_SOURCE%%RUN_INSTALL_INSTRUMENT%%RUN_INSTALL_EXTERNAL%%RUN_DEPLOY_TOUCHTOOLS%"=="000000" goto :menu',
            text,
        )
        self.assertIn("if errorlevel 5 goto :menu", text)
        self.assertIn("call net session", text)
        self.assertIn("Relaunch this utility as Administrator now?", text)
        self.assertIn("Open the temp_files results folder now?", text)
        self.assertIn("Installing with progress bar:", text)
        self.assertIn("Tecan support utility", text)
        self.assertIn("1. Collect Logs", text)
        self.assertIn("2. Collect/Install Drivers and Configs", text)
        self.assertIn("3. Deploy TouchTools media", text)
        self.assertIn("4. Settings", text)
        self.assertIn("5. Exit", text)
        self.assertIn("Choose the error type you want logs for.", text)
        self.assertNotIn("Diagnostic log package", text)
        self.assertIn("1. Everything", text)
        self.assertIn("2. In-Script errors", text)
        self.assertIn("3. Tecan Program Crash", text)
        self.assertIn("4. Import errors", text)
        self.assertIn("5. Back", text)
        self.assertNotIn("Likely Causes Script", text)
        self.assertIn("Driver/config package", text)
        self.assertIn("1. Collect instrument driver/config snapshot into this bundle", text)
        self.assertIn("2. Install instrument driver/config snapshot and staged external files", text)
        self.assertIn("3. Collect Tecan method source for inspection", text)
        self.assertIn("4. Back", text)
        self.assertIn("DataBase\\UserSpecific", text)
        self.assertIn("DataBase\\SystemSpecific", text)
        self.assertIn("Current settings for this BAT:", text)
        self.assertIn("Log lookback days: %LOG_LOOKBACK_DAYS%", text)
        self.assertIn('set "TEMP_DIR=%BUNDLE_DIR%temp_files\\"', text)
        self.assertIn('set "SUPPORT_DIR=%BUNDLE_DIR%source\\"', text)
        self.assertIn('set "SETUP_LOG_OUTPUT=%TEMP_ARG%"', text)
        self.assertIn('-OutputRoot "%TEMP_ARG%"', text)
        self.assertIn('set "LOG_LOOKBACK_DAYS=1"', text)
        self.assertIn("Likely-cause max log records: %LIKELY_CAUSE_MAX_RECORDS%", text)
        self.assertIn("Windows event max records: %WINDOWS_EVENT_MAX_EVENTS%", text)
        self.assertIn("run_tecan_bundle_setup.settings.cmd", text)
        self.assertIn(":save_settings", text)
        self.assertIn("-SinceDays %LOG_LOOKBACK_DAYS%", text)
        self.assertIn("-LikelyCauseMaxRecords %LIKELY_CAUSE_MAX_RECORDS%", text)
        self.assertIn("-EventLogMaxEvents %WINDOWS_EVENT_MAX_EVENTS%", text)
        self.assertIn("collect_tecan_diagnostic_bundle.ps1", text)
        self.assertIn("copy_tree_with_progress.ps1", text)
        self.assertIn("Copying with progress bar:", text)
        self.assertIn("Removing with progress bar:", text)
        self.assertIn("First clearing any previous method-source copy", text)
        self.assertIn(":remove_tree_progress", text)
        self.assertIn("-Action Copy", text)
        self.assertIn("-Action Remove", text)
        self.assertIn("stall_watchdog.ps1", text)
        self.assertIn(":start_stall_watchdog", text)
        self.assertIn("tecan_bundle_setup_STALL.error.txt", text)
        self.assertIn("progress_heartbeat.txt", text)
        helper = path.with_name("collect_tecan_diagnostic_bundle.ps1")
        self.assertTrue(helper.is_file())
        progress_helper = path.with_name("copy_tree_with_progress.ps1")
        self.assertTrue(progress_helper.is_file())
        stall_helper = path.with_name("stall_watchdog.ps1")
        self.assertTrue(stall_helper.is_file())
        install_helper = path.with_name("install_external_files.ps1")
        self.assertTrue(install_helper.is_file())
        deploy_helper = path.with_name("deploy_touchtools_media.ps1")
        self.assertTrue(deploy_helper.is_file())
        progress_text = progress_helper.read_text(encoding="utf-8")
        self.assertIn("Write-VisibleProgress", progress_text)
        self.assertIn("Invoke-RemoveTree", progress_text)
        self.assertIn("Update-ProgressHeartbeat", progress_text)
        self.assertIn("[Console]::Out.Flush()", progress_text)
        stall_text = stall_helper.read_text(encoding="utf-8")
        self.assertIn("STALL DETECTED", stall_text)
        self.assertIn("no_progress_heartbeat", stall_text)
        self.assertIn("stale_progress_heartbeat", stall_text)
        helper_text = helper.read_text(encoding="utf-8")
        self.assertIn("Write-CollectionProgress", helper_text)
        self.assertIn("tecan.diagnostic_log_bundle.v1", helper_text)
        self.assertIn("tecan.import_error_scan.v1", helper_text)
        self.assertIn("datastore_iot_client_logs", helper_text)
        self.assertIn("IoT-Client\\MAP.Services.Logging.Service\\LogFile", helper_text)
        deploy_text = deploy_helper.read_text(encoding="utf-8")
        self.assertIn("Write-VisibleProgress", deploy_text)
        self.assertIn("Get-FileHash", deploy_text)
        install_text = install_helper.read_text(encoding="utf-8")
        self.assertIn("external_file_deployments", install_text)
        self.assertIn("Get-FileHash", install_text)
        for token in (
            "Get-ImportScanFileKind",
            "source_kind",
            "scanned_file_count",
            "InvalidChecksumException",
            "missing_subroutine",
            "FileNotFoundException",
            "zeia_import_failure",
        ):
            self.assertIn(token, helper_text)
        self.assertIn("Invoke-LikelyCauseAnalysis", helper_text)
        self.assertIn("New-VersionedErrorLogOutputPath", helper_text)
        self.assertIn("error_logs_$dateLabel", helper_text)
        self.assertNotIn("tecan_error_logs", helper_text)
        self.assertNotIn("Compress-Archive", helper_text)
        self.assertNotIn("Archive: $zip", helper_text)
        self.assertNotIn("likely-causes-script", helper_text)
        self.assertNotIn("if ($spec.LikelyCauses)", helper_text)
        self.assertIn("$manifest.likely_causes = Invoke-LikelyCauseAnalysis", helper_text)
        self.assertIn("Get-WinEvent -FilterHashtable", helper_text)
        self.assertIn("-MaxEvents $MaxEvents", helper_text)
        self.assertIn("[int]$SinceDays = 0", helper_text)
        self.assertIn("[int]$LikelyCauseMaxRecords = 200", helper_text)
        self.assertIn("[int]$EventLogMaxEvents = 2000", helper_text)
        self.assertIn("[switch]$CaptureFluentControlInfopad", helper_text)
        self.assertIn("Write-FluentControlInfopadEvidence", helper_text)
        self.assertIn("Get-FluentControlInfopadDiagnosisItems", helper_text)
        self.assertIn("tecan.fluentcontrol_infopad.v1", helper_text)
        self.assertIn("ApplicationMainWindow", helper_text)
        self.assertIn("msgText", helper_text)
        self.assertIn("fluentcontrol_infopad", helper_text)
        self.assertIn("FLUENTCONTROL_INFOPAD_ARG", text)
        self.assertIn("-CaptureFluentControlInfopad", text)
        self.assertIn("likely_cause_max_records = $LikelyCauseMaxRecords", helper_text)
        self.assertIn("event_log_max_events = $EventLogMaxEvents", helper_text)
        self.assertIn("Write-HumanReadableDiagnosis", helper_text)
        self.assertIn("tecan.diagnosis_results.v1", helper_text)
        self.assertIn("@('script', 'script')", helper_text)
        self.assertIn("@('main_script', 'main_script')", helper_text)
        self.assertIn("@('script_line', 'script_line')", helper_text)
        self.assertIn("Script line:", helper_text)
        self.assertIn("Get-DiagnosisSections", helper_text)
        self.assertIn("Get-ImportScanDiagnosisItems", helper_text)
        self.assertIn("Remove-LikelyCauseIntermediates", helper_text)
        self.assertIn("Root diagnosis source: transformed likely_causes\\analysis.json", helper_text)
        self.assertNotIn("Copy-Item -LiteralPath $analysisMd", helper_text)
        self.assertNotIn("analysis.md", helper_text)
        self.assertIn("Join-Path $analysisOut 'status.txt'", helper_text)
        self.assertIn("Join-Path $analysisOut 'diagnostics\\diagnosis.md'", helper_text)
        self.assertNotIn("likely_causes_status.txt", helper_text)
        self.assertNotIn("static_diagnosis.md", helper_text)
        self.assertNotIn("static_diagnosis.json", helper_text)

    @unittest.skipUnless(_POWERSHELL, "powershell/pwsh not on PATH")
    def test_diagnostic_helper_writes_daily_versioned_error_log_folders(self) -> None:
        helper = Path(__file__).resolve().parents[1] / "tools" / "collect_tecan_diagnostic_bundle.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_root = tmp_path / "output"
            bundle_root = tmp_path / "bundle"
            program_data = tmp_path / "programdata"
            output_root.mkdir()
            bundle_root.mkdir()
            program_data.mkdir()
            env = os.environ.copy()
            env["ProgramData"] = str(program_data)

            for _ in range(2):
                result = subprocess.run(
                    [
                        _POWERSHELL,
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-File",
                        str(helper),
                        "-Profile",
                        "script-errors",
                        "-OutputRoot",
                        str(output_root),
                        "-BundleRoot",
                        str(bundle_root),
                        "-SinceDays",
                        "3",
                        "-LikelyCauseMaxRecords",
                        "50",
                        "-EventLogMaxEvents",
                        "150",
                    ],
                    capture_output=True,
                    env=env,
                    text=True,
                    timeout=120,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            folders = sorted(path for path in output_root.iterdir() if path.is_dir())
            self.assertEqual(len(folders), 2)
            self.assertRegex(folders[0].name, r"^error_logs_\d{2}-\d{2}-\d{4}_v1$")
            self.assertRegex(folders[1].name, r"^error_logs_\d{2}-\d{2}-\d{4}_v2$")
            self.assertEqual(list(output_root.glob("*.zip")), [])

            manifest = json.loads((folders[0] / "metadata" / "collection_manifest.json").read_text(encoding="utf-8-sig"))
            self.assertEqual(manifest["profile"], "script-errors")
            self.assertEqual(Path(manifest["output"]).name, folders[0].name)
            self.assertEqual(manifest["settings"]["since_days"], 3)
            self.assertEqual(manifest["settings"]["likely_cause_max_records"], 50)
            self.assertEqual(manifest["settings"]["event_log_max_events"], 150)
            self.assertEqual(manifest["likely_causes"]["status"], "no_log")
            self.assertEqual(Path(manifest["likely_causes"]["status_file"]).name, "status.txt")
            self.assertEqual(Path(manifest["likely_causes"]["status_file"]).parent.name, "likely_causes")
            self.assertEqual(manifest["likely_causes"]["diagnosis"]["item_count"], 0)
            self.assertEqual(manifest["likely_causes"]["diagnosis"]["raw_error_count"], 0)
            self.assertTrue((folders[0] / "likely_causes" / "status.txt").is_file())
            self.assertEqual(
                sorted(path.name for path in folders[0].iterdir() if path.is_file()),
                ["diagnosis.json", "diagnosis.md"],
            )
            self.assertTrue((folders[0] / "metadata" / "collection_manifest.txt").is_file())
            self.assertFalse((folders[0] / "likely_causes_status.txt").exists())
            self.assertFalse((folders[0] / "likely_causes" / "analysis.json").exists())
            self.assertFalse((folders[0] / "likely_causes" / "diagnostics").exists())
            diagnosis_bytes = (folders[0] / "diagnosis.json").read_bytes()
            self.assertFalse(diagnosis_bytes.startswith(b"\xef\xbb\xbf"))
            diagnosis = json.loads(diagnosis_bytes.decode("utf-8"))
            self.assertEqual(diagnosis["schema_version"], "tecan.diagnosis_results.v1")
            self.assertEqual(diagnosis["status"], "no_log")
            self.assertEqual(diagnosis["summary"]["finding_count"], 0)
            self.assertEqual(diagnosis["summary"]["raw_error_count"], 0)
            self.assertEqual(diagnosis["items"], [])
            diagnosis_md = (folders[0] / "diagnosis.md").read_text(encoding="utf-8")
            self.assertIn("# Diagnosis", diagnosis_md)
            self.assertIn("## In-Script errors", diagnosis_md)
            self.assertIn("No in-script errors findings were detected", diagnosis_md)
            self.assertNotIn("## Tecan Program Crash", diagnosis_md)
            self.assertNotIn("## Import errors", diagnosis_md)

    @unittest.skipUnless(_POWERSHELL, "powershell/pwsh not on PATH")
    def test_diagnostic_helper_writes_human_readable_findings_with_raw_errors(self) -> None:
        helper = Path(__file__).resolve().parents[1] / "tools" / "collect_tecan_diagnostic_bundle.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_root = tmp_path / "output"
            bundle_root = tmp_path / "bundle"
            program_data = tmp_path / "programdata"
            log_dir = program_data / "Tecan" / "LoggingServer" / "LogFiles"
            output_root.mkdir()
            bundle_root.mkdir()
            log_dir.mkdir(parents=True)
            log_text = (
                "2026-07-07 10:00:00 ERROR Script 'Verification Script C' unable to load selected "
                "subroutine DemoSubroutine because missing subroutine reference\n"
                "2026-07-07 10:01:00 ERROR Script 'Verification Script A' Mismatching If-Else branches\n"
                "2026-07-07 10:02:00 ERROR Script 'Verification Script B' "
                "Command \"ResolvexA200_Run\" is unknown\n"
                "2026-07-07 10:02:01 ERROR Script 'Verification Script B' "
                "Command \"ResolvexA200_Run\" is unknown\n"
                + ("2026-07-07 10:03:00 INFO unrelated log entry\n" * 81)
                + "2026-07-07 10:04:00 ERROR Select a valid labware.\n"
            )
            (log_dir / "runtime.ulf").write_text(log_text, encoding="utf-8")
            env = os.environ.copy()
            env["ProgramData"] = str(program_data)

            result = subprocess.run(
                [
                    _POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-Profile",
                    "everything",
                    "-OutputRoot",
                    str(output_root),
                    "-BundleRoot",
                    str(bundle_root),
                    "-SinceDays",
                    "3",
                ],
                capture_output=True,
                env=env,
                text=True,
                timeout=120,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            folder = next(path for path in output_root.iterdir() if path.is_dir())
            diagnosis_bytes = (folder / "diagnosis.json").read_bytes()
            self.assertFalse(diagnosis_bytes.startswith(b"\xef\xbb\xbf"))
            diagnosis = json.loads(diagnosis_bytes.decode("utf-8"))
            self.assertEqual(diagnosis["schema_version"], "tecan.diagnosis_results.v1")
            self.assertEqual(diagnosis["status"], "complete")
            self.assertGreaterEqual(diagnosis["summary"]["finding_count"], 1)
            self.assertGreaterEqual(diagnosis["summary"]["raw_error_count"], 1)
            self.assertEqual(diagnosis["summary"]["script_count"], 4)
            self.assertEqual(diagnosis["summary"]["script_error_count"], 4)
            self.assertEqual(
                {item["script"] for item in diagnosis["script_errors"]},
                {
                    "Verification Script A",
                    "Verification Script B",
                    "Verification Script C",
                    "Unattributed Script/Runtime Errors",
                },
            )
            self.assertEqual(diagnosis["script_errors"][-1]["script"], "Unattributed Script/Runtime Errors")
            resolvex_group = next(item for item in diagnosis["script_errors"] if item["script"] == "Verification Script B")
            self.assertEqual(len(resolvex_group["issues"]), 1)
            self.assertIn("raw_errors", diagnosis["items"][0])
            self.assertIn("missing subroutine reference", json.dumps(diagnosis["items"]))
            diagnosis_md = (folder / "diagnosis.md").read_text(encoding="utf-8")
            self.assertIn("## In-Script errors", diagnosis_md)
            self.assertIn("## Tecan Program Crash", diagnosis_md)
            self.assertIn("## Import errors", diagnosis_md)
            self.assertIn("Script: `Verification Script A`", diagnosis_md)
            self.assertIn("Mismatching If-Else branches", diagnosis_md)
            self.assertIn("Potential cause:", diagnosis_md)
            self.assertIn("Potential fix:", diagnosis_md)
            self.assertIn("Selected subroutine dependency cannot be loaded", diagnosis_md)
            self.assertIn("- Evidence:", diagnosis_md)
            self.assertIn("missing subroutine reference", diagnosis_md)
            self.assertTrue((folder / "likely_causes" / "status.txt").is_file())
            self.assertFalse((folder / "likely_causes" / "analysis.json").exists())
            self.assertFalse((folder / "likely_causes" / "diagnostics").exists())

    @unittest.skipUnless(_POWERSHELL, "powershell/pwsh not on PATH")
    def test_script_errors_profile_keeps_runtime_missing_files_in_markdown(self) -> None:
        helper = Path(__file__).resolve().parents[1] / "tools" / "collect_tecan_diagnostic_bundle.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_root = tmp_path / "output"
            bundle_root = tmp_path / "bundle"
            program_data = tmp_path / "programdata"
            log_dir = program_data / "Tecan" / "LoggingServer" / "LogFiles"
            output_root.mkdir()
            bundle_root.mkdir()
            log_dir.mkdir(parents=True)
            missing_file_error = (
                "Failed to open file C:\\Users\\Tecan\\Desktop\\cryoEM\\CalculateAspirateVolume.vb "
                "for reading"
            )
            # Co-bucketed import-dialog noise must not hide runtime file-open
            # findings from the script-errors profile Markdown.
            import_dialog_noise = (
                "VX_IMP_001_001: The items with the following IDs are referenced "
                "by at least one of the imported components"
            )
            (log_dir / "runtime.ulf").write_text(
                (
                    f"2026-07-07 10:04:00 ERROR Script 'Verification Script D' {missing_file_error}\n"
                    f"2026-07-07 10:04:01 ERROR {import_dialog_noise}\n"
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env["ProgramData"] = str(program_data)

            result = subprocess.run(
                [
                    _POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-Profile",
                    "script-errors",
                    "-OutputRoot",
                    str(output_root),
                    "-BundleRoot",
                    str(bundle_root),
                    "-SinceDays",
                    "3",
                ],
                capture_output=True,
                env=env,
                text=True,
                timeout=120,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            folder = next(path for path in output_root.iterdir() if path.is_dir())
            diagnosis = json.loads((folder / "diagnosis.json").read_text(encoding="utf-8"))
            script_section = next(section for section in diagnosis["sections"] if section["id"] == "script-errors")
            self.assertIn("fluent_log.missing_referenced_files", json.dumps(script_section))
            self.assertIn("CalculateAspirateVolume.vb", json.dumps(script_section))
            diagnosis_md = (folder / "diagnosis.md").read_text(encoding="utf-8")
            self.assertIn("## In-Script errors", diagnosis_md)
            self.assertIn("Script: `Verification Script D`", diagnosis_md)
            self.assertIn(missing_file_error, diagnosis_md)
            self.assertNotIn("No in-script errors findings were detected", diagnosis_md)

    @unittest.skipUnless(_POWERSHELL, "powershell/pwsh not on PATH")
    def test_dump_findings_render_once_as_unattributed_script_errors(self) -> None:
        helper = Path(__file__).resolve().parents[1] / "tools" / "collect_tecan_diagnostic_bundle.ps1"
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            output_root = tmp_path / "output"
            bundle_root = tmp_path / "bundle"
            program_data = tmp_path / "programdata"
            dump_dir = program_data / "Tecan" / "VisionX" / "DumpFiles"
            output_root.mkdir()
            bundle_root.mkdir()
            dump_dir.mkdir(parents=True)
            dump_text = b"Script Editor dump context: Mismatching If-Else branches"
            (dump_dir / "SystemSW.exe.31484.dmp").write_bytes(dump_text)
            (dump_dir / "SystemSW.exe(1).31484.dmp").write_bytes(dump_text)
            env = os.environ.copy()
            env["ProgramData"] = str(program_data)

            result = subprocess.run(
                [
                    _POWERSHELL,
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(helper),
                    "-Profile",
                    "everything",
                    "-OutputRoot",
                    str(output_root),
                    "-BundleRoot",
                    str(bundle_root),
                    "-SinceDays",
                    "3",
                ],
                capture_output=True,
                env=env,
                text=True,
                timeout=120,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            folder = next(path for path in output_root.iterdir() if path.is_dir())
            diagnosis = json.loads((folder / "diagnosis.json").read_text(encoding="utf-8"))
            self.assertEqual(diagnosis["summary"]["finding_count"], 1)
            self.assertEqual(diagnosis["summary"]["raw_error_count"], 2)
            diagnosis_md = (folder / "diagnosis.md").read_text(encoding="utf-8")
            self.assertIn("## Tecan Program Crash", diagnosis_md)
            self.assertIn("### Script: `Unattributed Script/Runtime Errors`", diagnosis_md)
            self.assertNotIn("### Other Error 1. Mismatching If-Else branches", diagnosis_md)
            self.assertEqual(diagnosis_md.count("[VisionX crash dump] Mismatching If-Else branches"), 1)

    def test_repairs_literal_caret_pipes_inside_powershell_command(self) -> None:
        text = (
            '@powershell -NoProfile -Command "$items = Get-ChildItem ^| Sort-Object; '
            '$items ^| Format-Table"\n'
            "echo cmd-only ^| token\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run_tecan_bundle_setup.bat"
            path.write_text(text, encoding="utf-8")

            changes = repair_powershell_pipelines(path)

            repaired = path.read_text(encoding="utf-8")
            self.assertEqual(len(changes), 1)
            self.assertIn("Get-ChildItem | Sort-Object", repaired)
            self.assertIn("echo cmd-only ^| token", repaired)
            self.assertEqual(setup_bat_findings(path), [])

    def test_finding_blocks_unrepaired_powershell_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "run_tecan_bundle_setup.bat"
            path.write_text(
                '@powershell -Command "Get-ChildItem ^| Sort-Object"\n',
                encoding="utf-8",
            )

            findings = setup_bat_findings(path)

        self.assertEqual(findings[0]["reason"], "powershell_pipeline_cmd_escape")


if __name__ == "__main__":
    unittest.main()
