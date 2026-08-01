import tempfile
import unittest
import zipfile
from pathlib import Path

import fluent_pipeline.project_context as pc
from fluent_pipeline.diagnostics import (
    _approved_passthrough_command_findings,
    _error_text_findings,
    _unsupported_command_findings,
    diagnose_input,
)


class DiagnosticsTests(unittest.TestCase):
    def test_diagnose_zeia_reports_missing_worklist_and_unsupported_command(self):
        with _isolated_projects() as tmp_path:
            archive = tmp_path / "broken.zeia"
            _write_archive(
                archive,
                {
                    "Scripts/broken.xscr": _xscr(
                        "BrokenScript",
                        extra_command="""
                      <Object Type="Tecan.Core.Scripting.Commands.Custom.UnsupportedRobotCommandDataV1">
                        <UnsupportedRobotCommandDataV1>
                          <WorklistName>missing.gwl</WorklistName>
                          <Data><LineNumber>4</LineNumber></Data>
                        </UnsupportedRobotCommandDataV1>
                      </Object>
""",
                    ),
                    "Worktables/base.xwsp": _workspace("EVA[009]"),
                },
            )

            bundle = diagnose_input(
                archive,
                project_name="broken-context",
                force_import=True,
                script="BrokenScript",
                error_text="FluentControl could not open worklist file missing.gwl: not found",
                out_dir=tmp_path / "diagnosis",
            )

            finding_ids = {finding["id"] for finding in bundle.report["findings"]}
            self.assertIn("script.missing_worklist_refs", finding_ids)
            self.assertIn("commands.unsupported", finding_ids)
            self.assertIn("error.worklist", finding_ids)
            self.assertEqual(bundle.report["summary"]["status"], "likely_issue")
            self.assertTrue(bundle.report_path and bundle.report_path.exists())
            self.assertTrue(bundle.json_path and bundle.json_path.exists())
            self.assertTrue(Path(bundle.report["artifacts"]["protocol_ir"]).exists())
            self.assertTrue(Path(bundle.report["artifacts"]["worktable_changes"]).exists())

    def test_diagnose_zeia_requires_script_when_multiple_scripts_exist(self):
        with _isolated_projects() as tmp_path:
            archive = tmp_path / "multi.zeia"
            _write_archive(
                archive,
                {
                    "Scripts/first.xscr": _xscr("FirstScript"),
                    "Scripts/second.xscr": _xscr("SecondScript"),
                    "Worktables/base.xwsp": _workspace("Base Worktable"),
                },
            )

            bundle = diagnose_input(
                archive,
                project_name="multi-context",
                force_import=True,
                out_dir=tmp_path / "diagnosis",
            )

            finding_ids = {finding["id"] for finding in bundle.report["findings"]}
            self.assertIn("script.multiple_without_selection", finding_ids)
            self.assertEqual(bundle.report["summary"]["status"], "blocked")
            self.assertEqual(bundle.report["protocol_summary"], {})

    def test_unsupported_commands_ignore_registry_approved_passthroughs(self):
        script = {
            "command_counts": {
                "ConditionalGroup": 2,
                "String": 5,
                "UserPromptStatement": 1,
                "CustomUnsupportedCommand": 1,
            }
        }

        unsupported = _unsupported_command_findings(script, {"steps": [{"operation": "comment"}]})
        approved = _approved_passthrough_command_findings(script)

        self.assertEqual(len(unsupported), 1)
        self.assertEqual(
            unsupported[0]["details"]["unsupported_command_counts"],
            {"CustomUnsupportedCommand": 1},
        )
        self.assertEqual(len(approved), 1)
        self.assertEqual(
            approved[0]["details"]["support_statuses"],
            {"String": "approved_non_command"},
        )

    def test_error_text_maps_unknown_driver_and_scanner_instance(self):
        text = (
            "016, 008: Unhandled exception in script command: "
            "USB:TECAN,FLUENT2405000993/CGA:1 is not associated with a scanner instance.\n"
            '020: Command "RGA1 TransferLabware" is unknown. Please check that the '
            "corresponding driver is installed and configured properly."
        )

        findings = _error_text_findings(text, worktable_diff=None)

        ids = {finding["id"] for finding in findings}
        self.assertIn("error.scanner_instance", ids)
        self.assertIn("error.unknown_driver_command", ids)

    def test_error_text_maps_xml_checksum_import_failure(self):
        text = (
            "VX_APPFR_016_005 DataImporter failure. "
            'ExceptionMessage: XML checksum error indicates unauthorized modification of Script '
            'with name "TouchTools_Worktable_GIF_Step4_Movie_Test".'
        )

        findings = _error_text_findings(text, worktable_diff=None)

        ids = {finding["id"] for finding in findings}
        self.assertIn("error.xml_checksum", ids)

    def test_diagnose_reports_subroutine_custom_asset_and_pin_refs(self):
        with _isolated_projects() as tmp_path:
            archive = tmp_path / "custom.zeia"
            _write_archive(
                archive,
                {
                    "Scripts/main.xscr": _xscr(
                        "MainScript",
                        extra_command="""
                      <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
                        <SubRoutine>"Subroutines\\GetFingers"</SubRoutine>
                        <PinNumber>GIO1_Pin20</PinNumber>
                        <Location>WorktablePin_MiddleFront</Location>
                        <CustomDetailImageFilePath>C:\\ProgramData\\Tecan\\VisionX\\TouchToolsData\\Images\\sourcecapholder.jpg</CustomDetailImageFilePath>
                        <Barcode>FakeBarcode</Barcode>
                      </Object>
""",
                    ),
                    "Scripts/get-fingers.xscr": _xscr("GetFingers"),
                    "Worktable/connector.xcon": """<?xml version="1.0"?>
<VxData>
  <Payload>
    <ObjectName>Generated connector for Worktable_Segment_WorktablePin_MiddleFront and custom cap holder</ObjectName>
    <PayloadData>
      <WorktableConnector>
        <ConnectorTemplate>
          <ComponentGuid>component-guid</ComponentGuid>
          <SiteGuid>site-guid</SiteGuid>
          <Description>Worktable_Segment_WorktablePin_MiddleFront connector</Description>
        </ConnectorTemplate>
      </WorktableConnector>
    </PayloadData>
  </Payload>
</VxData>
""",
                    "fs/1/sourcecapholder.jpg": "not really a jpeg",
                },
            )

            bundle = diagnose_input(
                archive,
                project_name="custom-context",
                force_import=True,
                script="MainScript",
                out_dir=tmp_path / "diagnosis",
            )

            finding_ids = {finding["id"] for finding in bundle.report["findings"]}
            self.assertIn("script.subroutine_refs", finding_ids)
            self.assertIn("hardware.pin_refs", finding_ids)
            self.assertIn("custom_parts.asset_refs", finding_ids)
            self.assertIn("custom_parts.barcode_refs", finding_ids)
            self.assertIn("custom_parts.pin_connectors", finding_ids)

    def test_diagnose_imports_snapshot_context_evidence(self):
        with _isolated_projects() as tmp_path:
            archive = tmp_path / "with-snapshot.zeia"
            snapshot = tmp_path / "support-snapshot.zip"
            _write_archive(
                archive,
                {
                    "Scripts/main.xscr": _xscr("MainScript"),
                    "Worktables/base.xwsp": _workspace("Base Worktable"),
                },
            )
            _write_snapshot_archive(snapshot)

            bundle = diagnose_input(
                archive,
                project_name="snapshot-diagnosis",
                force_import=True,
                script="MainScript",
                snapshot_archives=[snapshot],
                out_dir=tmp_path / "diagnosis",
            )

            finding_ids = {finding["id"] for finding in bundle.report["findings"]}
            self.assertIn("snapshot.instrument_configuration", finding_ids)
            self.assertIn("snapshot.simulation_setup", finding_ids)
            self.assertIn("snapshot.hardware_details", finding_ids)
            self.assertIn("snapshot.troubleshooting_context", finding_ids)
            self.assertEqual(bundle.report["context"]["snapshot_summary"]["instrument_serial_numbers"], ["SN-12345"])

    def test_diagnose_snapshot_zip_without_script_is_support_context(self):
        with _isolated_projects() as tmp_path:
            snapshot = tmp_path / "support-snapshot.zip"
            _write_snapshot_archive(snapshot)

            bundle = diagnose_input(
                snapshot,
                project_name="snapshot-only",
                force_import=True,
                out_dir=tmp_path / "diagnosis",
            )

            finding_ids = {finding["id"] for finding in bundle.report["findings"]}
            self.assertIn("snapshot.no_script", finding_ids)
            self.assertIn("snapshot.instrument_configuration", finding_ids)
            self.assertNotIn("script.none_found", finding_ids)


class _isolated_projects:
    def __enter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self.tmp.name)
        self.old_projects_dir = pc.PROJECTS_DIR
        self.old_collections_dir = pc.COLLECTIONS_DIR
        self.old_active_file = pc.ACTIVE_CONTEXT_FILE
        pc.PROJECTS_DIR = tmp_path / "projects"
        pc.COLLECTIONS_DIR = pc.PROJECTS_DIR / ".collections"
        pc.ACTIVE_CONTEXT_FILE = pc.PROJECTS_DIR / ".active_context"
        return tmp_path

    def __exit__(self, exc_type, exc, tb):
        pc.PROJECTS_DIR = self.old_projects_dir
        pc.COLLECTIONS_DIR = self.old_collections_dir
        pc.ACTIVE_CONTEXT_FILE = self.old_active_file
        self.tmp.cleanup()


def _write_archive(path: Path, entries: dict[str, str]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def _write_snapshot_archive(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(
            "Instrument/system.config",
            """<?xml version="1.0"?>
<SystemConfiguration>
  <InstrumentSerialNumber>SN-12345</InstrumentSerialNumber>
  <InstrumentSize>Fluent 780</InstrumentSize>
  <ArmOrder>LiHa,MCA384,RGA</ArmOrder>
  <TipConfiguration>Disposable tips</TipConfiguration>
  <DeviceName>MCA384</DeviceName>
  <FirmwareVersion>1.2.3</FirmwareVersion>
  <DriverVersion>4.5.6</DriverVersion>
</SystemConfiguration>
""",
        )
        zf.writestr("Firmware/FirmwareVersions.txt", "FirmwareVersion: 1.2.3\nDriverVersion: 4.5.6\n")
        zf.writestr("Issue/User Description.txt", "Issue Description: MCA arm error during simulation\n")
        zf.writestr("Logs/FluentControl.log", "2026-06-08 ERROR simulated deck mismatch\n")


def _workspace(name: str) -> str:
    return f"""<?xml version="1.0"?>
<Workspace>
  <ObjectName>{name}</ObjectName>
  <Guid>workspace-guid</Guid>
  <Name>{name}</Name>
</Workspace>
"""


def _xscr(object_name: str, *, extra_command: str = "") -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>{object_name}</ObjectName>
    <Reference>
      <Guid>workspace-guid</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>Base Worktable</ObjectName>
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
                      <Object Type="Tecan.Core.Scripting.Commands.Mca384.Mca384AspirateScriptCommandDataV2">
                        <Mca384AspirateScriptCommandDataV2>
                          <LiquidClassName>Water Free Single</LiquidClassName>
                          <Volume>20</Volume>
                          <ScriptCommandCommonDataV2>
                            <LabwareName>SourcePlate</LabwareName>
                            <DeviceAlias>Instrument=1/Device=MCA384:1</DeviceAlias>
                            <LineNumber>3</LineNumber>
                          </ScriptCommandCommonDataV2>
                        </Mca384AspirateScriptCommandDataV2>
                      </Object>
{extra_command}
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
"""


if __name__ == "__main__":
    unittest.main()
