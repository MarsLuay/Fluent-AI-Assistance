from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from tecan_common import xml_compat as ET
from tecan_reader.archive import inspect_archive
from tecan_reader.common import parse_xml_text
from tecan_reader.gwl import inspect_gwl_lines
from tecan_reader.pattern_library import mine_script_patterns, search_script_patterns, summarize_script_patterns
import sqlite3
from unittest.mock import patch, MagicMock

from tecan_reader.project_index import build_project_index, search_project_index, summarize_project_index, _initialize_database
from tecan_reader.script import inspect_xscr_text


SAMPLE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="3">
  <Payload>
    <ObjectName>UnitTestScript</ObjectName>
    <Reference>
      <Guid>00000001-4321-eeee-ffff-000000000001</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>Base Worktable</ObjectName>
    </Reference>
    <PayloadData>
      <Script version="2.0">
        <Properties>
          <VariableDeclarations>
            <VariableDeclarations>
              <VariableDeclarations>
                <anyType xmlns:i="http://www.w3.org/2001/XMLSchema-instance" i:type="VariableDefinitionHelper">
                  <Name>Vol</Name>
                  <TypeName>Floating Point</TypeName>
                  <Scope>Script</Scope>
                </anyType>
              </VariableDeclarations>
            </VariableDeclarations>
          </VariableDeclarations>
        </Properties>
        <Commands>
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.Core.Scripting.CommentStatement">
                <CommentStatement><Comment>hello</Comment><LineNumber>1</LineNumber></CommentStatement>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.LiHa.Scripting.LihaAspirateScriptCommandDataV5">
                <LihaAspirateScriptCommandDataV5>
                  <LiquidClassName>Water Free Single</LiquidClassName>
                  <Volume>Vol</Volume>
                  <LabwareName>SourcePlate</LabwareName>
                  <LineNumber>2</LineNumber>
                </LihaAspirateScriptCommandDataV5>
              </Object>
            </Objects>
          </ScriptGroup>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
  <Checksum>ABC</Checksum>
</sd:VxData>
"""


WORKTABLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <ObjectName>Deck A Worktable</ObjectName>
  <TypeId>WorktableWorkspace</TypeId>
  <Name>Deck A Worktable</Name>
  <Guid>00000001-0000-0000-0000-000000000002</Guid>
</Root>
"""


CARRIER_XML = """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <ObjectName>Plate Carrier 1</ObjectName>
  <TypeId>CarrierDefinition</TypeId>
  <FunctionalGroup>Carrier</FunctionalGroup>
  <Name>Plate Carrier 1</Name>
  <Guid>00000001-0000-0000-0000-000000000003</Guid>
</Root>
"""


LIQUID_CLASS_XML = """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <ObjectName>Water Free Single</ObjectName>
  <TypeId>LiquidClass</TypeId>
  <Name>Water Free Single</Name>
  <Guid>00000001-0000-0000-0000-000000000004</Guid>
</Root>
"""


PATTERN_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<sd:VxData xmlns:sd="http://www.tecan.com/TSCC/VisionX/VX/DataStore/VxData" dataStoreVersion="3">
  <Payload>
    <ObjectName>MCA384PatternScript</ObjectName>
    <Reference>
      <Guid>00000001-4321-eeee-ffff-000000000001</Guid>
      <TypeId>WorktableWorkspace</TypeId>
      <ObjectName>Base Worktable</ObjectName>
    </Reference>
    <PayloadData>
      <Script version="2.0">
        <Commands>
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.Core.Scripting.UserPromptStatement">
                <UserPromptStatement>
                  <Name>ConfirmTips</Name>
                  <QueryPrompt>Confirm MCA384 tips are loaded</QueryPrompt>
                  <LineNumber>10</LineNumber>
                </UserPromptStatement>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.MCA384.Scripting.Mca384PickUpTipsScriptCommandDataV5">
                <Mca384PickUpTipsScriptCommandDataV5>
                  <LabwareName>MCA384TipBox</LabwareName>
                  <DeviceAlias>MCA384</DeviceAlias>
                  <LineNumber>11</LineNumber>
                </Mca384PickUpTipsScriptCommandDataV5>
              </Object>
              <Object Type="Tecan.Core.Scripting.CommentStatement">
                <CommentStatement>
                  <Comment>Confirm tip count</Comment>
                  <LineNumber>12</LineNumber>
                </CommentStatement>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.MCA384.Scripting.Mca384AspirateScriptCommandDataV5">
                <Mca384AspirateScriptCommandDataV5>
                  <LiquidClassName>Water Free Single</LiquidClassName>
                  <Volume>25</Volume>
                  <LabwareName>SourcePlate</LabwareName>
                  <DeviceAlias>MCA384</DeviceAlias>
                  <LineNumber>13</LineNumber>
                </Mca384AspirateScriptCommandDataV5>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.MCA384.Scripting.Mca384DispenseScriptCommandDataV5">
                <Mca384DispenseScriptCommandDataV5>
                  <LiquidClassName>Water Free Single</LiquidClassName>
                  <Volume>25</Volume>
                  <LabwareName>DestinationPlate</LabwareName>
                  <DeviceAlias>MCA384</DeviceAlias>
                  <LineNumber>14</LineNumber>
                </Mca384DispenseScriptCommandDataV5>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.MCA384.Scripting.Mca384DropTipsScriptCommandDataV5">
                <Mca384DropTipsScriptCommandDataV5>
                  <LabwareName>Waste</LabwareName>
                  <DeviceAlias>MCA384</DeviceAlias>
                  <LineNumber>15</LineNumber>
                </Mca384DropTipsScriptCommandDataV5>
              </Object>
              <Object Type="Tecan.Core.Instrument.Devices.CGA.Scripting.CgaDropFingersScriptCommandDataV1">
                <CgaDropFingersScriptCommandDataV1>
                  <ScriptCommandCommonDataV2>
                    <LabwareName>AdapterA200</LabwareName>
                    <DeviceAlias>Instrument=1/Device=RGA:1</DeviceAlias>
                    <LineNumber>16</LineNumber>
                  </ScriptCommandCommonDataV2>
                </CgaDropFingersScriptCommandDataV1>
              </Object>
            </Objects>
          </ScriptGroup>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
  <Checksum>PATTERN</Checksum>
</sd:VxData>
"""


class ReaderTests(unittest.TestCase):
    def test_inspect_xscr_text(self) -> None:
        report = inspect_xscr_text(SAMPLE_XSCR, source_name="sample.xscr")
        self.assertEqual(report["object_name"], "UnitTestScript")
        self.assertEqual(report["command_count"], 2)
        self.assertEqual(report["family_counts"]["LiHa/FCA"], 1)
        self.assertEqual(report["variables"][0]["name"], "Vol")
        self.assertIn("Water Free Single", report["dependencies"]["liquid_classes"])
        self.assertIn("SourcePlate", report["dependencies"]["labware_names"])

    def test_parse_xml_text_rejects_malformed_xml(self) -> None:
        with self.assertRaises(ET.ParseError):
            parse_xml_text("<root>")

    def test_parse_xml_text_rejects_entity_xml(self) -> None:
        xml = '<!DOCTYPE root [<!ENTITY x "boom">]><root>&x;</root>'
        with self.assertRaises(ET.ParseError):
            parse_xml_text(xml)

    def test_parse_xml_text_rejects_excessive_depth(self) -> None:
        xml = "<root><a><b><c /></b></a></root>"
        with self.assertRaises(ET.ParseError):
            parse_xml_text(xml, max_depth=3)

    def test_parse_xml_text_rejects_oversized_document(self) -> None:
        xml = "<root>" + ("x" * 64) + "</root>"
        with self.assertRaises(ET.ParseError):
            parse_xml_text(xml, max_bytes=32)

    def test_inspect_archive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "sample.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", SAMPLE_XSCR)
                zf.writestr("DataStore/nodedescription.xml", "<root />")
            report = inspect_archive(archive)
        self.assertEqual(report["script_count_total"], 1)
        self.assertEqual(report["script_names"], ["UnitTestScript"])

    def test_inspect_gwl_lines_uses_shared_permissive_parser(self) -> None:
        report = inspect_gwl_lines(
            [
                "A;Source;;96 Well Flat;1;;20;Water Free Single;;;",
                "W5;",
                "X;unsupported;field",
            ],
            source_name="sample.gwl",
        )

        self.assertEqual(report["record_counts"]["A"], 1)
        self.assertEqual(report["record_counts"]["W5"], 1)
        self.assertEqual(report["record_counts"]["X"], 1)
        self.assertEqual(report["pipette_examples"][0]["rack_label"], "Source")
        self.assertEqual(report["line_count"], 3)


    def test_inspect_archive_collects_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "errors.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                # Malformed XML to cause inspect_xscr_text to fail
                zf.writestr("DataStore/UserSpecific/bad.xscr", "<malformed")
                # Invalid UTF-8 to cause .decode("utf-8-sig") to fail
                zf.writestr("Worklists/bad.gwl", b"\xff\xfe")

            report = inspect_archive(archive)

        self.assertEqual(len(report["errors"]), 2)

        errors = {e["entry"]: e["error"] for e in report["errors"]}

        self.assertIn("DataStore/UserSpecific/bad.xscr", errors)
        self.assertIn("Worklists/bad.gwl", errors)

        self.assertTrue("ParseError" in errors["DataStore/UserSpecific/bad.xscr"] or "unclosed token" in errors["DataStore/UserSpecific/bad.xscr"])
        self.assertTrue("codec can't decode" in errors["Worklists/bad.gwl"])

    def test_inspect_archive_rejects_entry_count_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "too_many_entries.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", SAMPLE_XSCR)
                zf.writestr("DataStore/nodedescription.xml", "<root />")

            with self.assertRaises(zipfile.BadZipFile):
                inspect_archive(archive, max_entry_count=1)

    def test_inspect_archive_rejects_total_uncompressed_size_over_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "too_large.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", SAMPLE_XSCR)

            with self.assertRaises(zipfile.BadZipFile):
                inspect_archive(archive, max_total_uncompressed_bytes=32)

    def test_inspect_archive_indexes_custom_parts_pins_and_subroutines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "custom.zeia"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr(
                    "DataStore/UserSpecific/custom.xscr",
                    SAMPLE_XSCR.replace(
                        "</Objects>",
                        """
              <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
                <SubRoutine>"Subroutines\\GetFingers"</SubRoutine>
                <PinNumber>GIO1_Pin20</PinNumber>
                <Location>WorktablePin_MiddleFront</Location>
                <CustomDetailImageFilePath>C:\\ProgramData\\Tecan\\VisionX\\TouchToolsData\\Images\\sourcecapholder.jpg</CustomDetailImageFilePath>
                <Barcode>FakeBarcode</Barcode>
              </Object>
            </Objects>
""",
                    ),
                )
                zf.writestr(
                    "DataStore/SystemSpecific/Worktable/Connectors/custom.xcon",
                    """<?xml version="1.0"?>
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
                )
                zf.writestr("fs/1/sourcecapholder.jpg", b"not really a jpeg")

            report = inspect_archive(archive)
            deps = report["scripts"][0]["dependencies"]
            db_path = Path(tmp) / "build" / "custom.sqlite"
            build_project_index([archive], db_path, force=True)
            pin_hits = search_project_index(db_path, "GIO1_Pin20", kind="hardware_pin")
            asset_hits = search_project_index(db_path, "sourcecapholder", kind="custom_asset")
            subroutine_hits = search_project_index(db_path, "GetFingers", kind="subroutine")

        self.assertIn('"Subroutines\\GetFingers"', deps["subroutine_refs"])
        self.assertIn("GIO1_Pin20", deps["pin_refs"])
        self.assertIn("WorktablePin_MiddleFront", deps["worktable_pin_locations"])
        self.assertIn("sourcecapholder.jpg", deps["custom_asset_refs"])
        self.assertTrue(any(obj["kind"] == "connector" for obj in report["objects"]))
        self.assertTrue(any(obj["kind"] == "asset" for obj in report["objects"]))
        self.assertTrue(pin_hits["results"])
        self.assertTrue(asset_hits["results"])
        self.assertTrue(subroutine_hits["results"])

    def test_project_index_searches_multiple_zeia_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = _write_sample_archive(root / "first.zeia", SAMPLE_XSCR)
            second_script = (
                SAMPLE_XSCR
                .replace("UnitTestScript", "SecondScript")
                .replace("SourcePlate", "DestinationPlate")
                .replace("Water Free Single", "DMSO Single")
                .replace("Base Worktable", "Deck B Worktable")
            )
            _write_sample_archive(root / "nested" / "second.zeia", second_script)

            db_path = root / "build" / "project.sqlite"
            build_report = build_project_index([root], db_path, force=True)
            summary = summarize_project_index(db_path)
            liquid_hits = search_project_index(db_path, "Water Free Single", kind="liquid_class")
            sequence_hits = search_project_index(
                db_path,
                "CommentStatement > LihaAspirateScriptCommand",
                kind="command_sequence",
            )
            worktable_hits = search_project_index(db_path, "Base Worktable", kind="worktable")
            worklist_hits = search_project_index(db_path, "sample.gwl", kind="worklist")

        self.assertEqual(build_report["zeia_file_count"], 2)
        self.assertEqual(summary["script_count"], 2)
        self.assertGreaterEqual(summary["entity_counts"]["labware"], 2)
        self.assertGreaterEqual(summary["entity_counts"]["carrier"], 1)
        self.assertTrue(any(hit["name"] == "Water Free Single" for hit in liquid_hits["results"]))
        self.assertTrue(sequence_hits["results"])
        self.assertTrue(any(hit["name"] == "Base Worktable" for hit in worktable_hits["results"]))
        self.assertTrue(any(hit["name"] == "sample.gwl" for hit in worklist_hits["results"]))
        self.assertEqual(first.name, "first.zeia")

    def test_summarize_project_index(self) -> None:
        conn = sqlite3.connect(":memory:")
        _initialize_database(conn)

        # Insert test data
        conn.execute("INSERT INTO zeia_files(id, path, file_name, sha256, indexed_at) VALUES (1, '/path/to/archive.zeia', 'archive.zeia', 'hash', '2023-01-01')")
        conn.execute("INSERT INTO scripts(id, zeia_file_id, entry_path, object_name) VALUES (1, 1, 'script1.xscr', 'Script 1')")
        conn.execute("INSERT INTO scripts(id, zeia_file_id, entry_path, object_name) VALUES (2, 1, 'script2.xscr', 'Script 2')")

        conn.execute("INSERT INTO commands(id, zeia_file_id, script_id, command_index, command_type, family) VALUES (1, 1, 1, 0, 'LiquidHandling', 'pipette')")
        conn.execute("INSERT INTO commands(id, zeia_file_id, script_id, command_index, command_type, family) VALUES (2, 1, 1, 1, 'Comment', 'misc')")
        conn.execute("INSERT INTO commands(id, zeia_file_id, script_id, command_index, command_type, family) VALUES (3, 1, 2, 0, 'LiquidHandling', 'pipette')")

        conn.execute("INSERT INTO entities(id, zeia_file_id, script_id, kind, name) VALUES (1, 1, 1, 'labware', 'Plate1')")
        conn.execute("INSERT INTO entities(id, zeia_file_id, script_id, kind, name) VALUES (2, 1, 1, 'labware', 'Plate2')")
        conn.execute("INSERT INTO entities(id, zeia_file_id, script_id, kind, name) VALUES (3, 1, 2, 'liquid_class', 'Water')")

        conn.execute("INSERT INTO catalog_objects(id, zeia_file_id, entry_path, kind) VALUES (1, 1, 'plate.xml', 'plate')")
        conn.execute("INSERT INTO catalog_objects(id, zeia_file_id, entry_path, kind) VALUES (2, 1, 'tip.xml', 'tip')")

        summary = summarize_project_index(conn)

        self.assertEqual(summary["kind"], "project_index_summary")
        self.assertEqual(summary["zeia_file_count"], 1)
        self.assertEqual(summary["script_count"], 2)
        self.assertEqual(summary["command_count"], 3)
        self.assertEqual(summary["catalog_object_count"], 2)
        self.assertEqual(summary["entity_counts"]["labware"], 2)
        self.assertEqual(summary["entity_counts"]["liquid_class"], 1)
        self.assertEqual(summary["command_family_counts"]["pipette"], 2)
        self.assertEqual(summary["command_family_counts"]["misc"], 1)
        self.assertEqual(len(summary["files"]), 1)
        self.assertEqual(summary["files"][0]["file_name"], "archive.zeia")

    def test_script_pattern_library_mines_reusable_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_sample_archive(root / "patterns.zeia", PATTERN_XSCR)
            db_path = root / "build" / "project.sqlite"
            build_project_index([root], db_path, force=True)

            mine_report = mine_script_patterns(db_path)
            summary = summarize_script_patterns(db_path)
            tip_hits = search_script_patterns(db_path, "MCA384TipBox", pattern_type="pick_up_tips")
            aspirate_hits = search_script_patterns(db_path, "Water Free Single", pattern_type="aspirate")
            drop_hits = search_script_patterns(db_path, "Waste", pattern_type="drop_tips")
            gripper_hits = search_script_patterns(db_path, "AdapterA200", pattern_type="gripper")

        self.assertGreaterEqual(mine_report["pattern_type_counts"]["pick_up_tips"], 1)
        self.assertGreaterEqual(mine_report["pattern_type_counts"]["gripper"], 1)
        self.assertGreaterEqual(summary["pattern_type_counts"]["aspirate"], 1)
        self.assertGreaterEqual(summary["pattern_type_counts"]["dispense"], 1)
        self.assertTrue(tip_hits["results"])
        self.assertEqual(tip_hits["results"][0]["source_script"], "MCA384PatternScript")
        self.assertTrue(any(step["step_number"] == 1 for step in tip_hits["results"][0]["steps"]))
        self.assertTrue(any("Pick up tips" in step["summary"] for step in tip_hits["results"][0]["steps"]))
        self.assertTrue(aspirate_hits["results"])
        self.assertIn("liquid_classes", aspirate_hits["results"][0]["specifications"])
        self.assertTrue(drop_hits["results"])
        self.assertTrue(gripper_hits["results"])
        self.assertIn("Physical RGA/CGA motion pattern", gripper_hits["results"][0]["safety_notes"][-1])

    def test_build_project_index_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zeia_path = tmp_path / "test.zeia"
            with zipfile.ZipFile(zeia_path, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", "<sd:VxData />")

            db_path = tmp_path / "new_dir" / "nested" / "test.db"
            self.assertFalse(db_path.parent.exists())

            with patch("tecan_reader.project_index.inspect_archive") as mock_inspect:
                mock_inspect.return_value = {"scripts": []}
                build_project_index([zeia_path], db_path)

            self.assertTrue(db_path.parent.exists())
            self.assertTrue(db_path.exists())

    def test_build_project_index_force_deletes_existing_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zeia_path = tmp_path / "test.zeia"
            with zipfile.ZipFile(zeia_path, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", "<sd:VxData />")

            db_path = tmp_path / "test.db"
            db_path.write_text("dummy data")

            with patch("tecan_reader.project_index.inspect_archive") as mock_inspect:
                mock_inspect.return_value = {"scripts": []}
                build_project_index([zeia_path], db_path, force=True)

            # The dummy data should have been overwritten by a new sqlite database
            conn = sqlite3.connect(db_path)
            try:
                res = conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
                self.assertIsNotNone(res)
            finally:
                conn.close()

    def test_build_project_index_closes_connection_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zeia_path = tmp_path / "test.zeia"
            with zipfile.ZipFile(zeia_path, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", "<sd:VxData />")

            db_path = tmp_path / "test.db"

            with patch("tecan_reader.project_index.inspect_archive") as mock_inspect:
                mock_inspect.side_effect = ValueError("Simulated error")
                with patch("tecan_reader.project_index._connect") as mock_connect:
                    mock_conn = MagicMock()
                    mock_connect.return_value = mock_conn
                    with self.assertRaises(ValueError):
                        build_project_index([zeia_path], db_path)
                    mock_conn.close.assert_called_once()

    def test_build_project_index_passes_limits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            zeia_path = tmp_path / "test.zeia"
            with zipfile.ZipFile(zeia_path, "w") as zf:
                zf.writestr("DataStore/UserSpecific/sample.xscr", "<sd:VxData />")

            db_path = tmp_path / "test.db"

            with patch("tecan_reader.project_index.inspect_archive") as mock_inspect:
                mock_inspect.return_value = {"scripts": []}
                build_project_index([zeia_path], db_path, script_limit=42, object_limit=99)
                mock_inspect.assert_called_once_with(zeia_path.resolve(), script_limit=42, object_limit=99)


def _write_sample_archive(path: Path, script: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("DataStore/UserSpecific/sample.xscr", script)
        zf.writestr("DataStore/Worktables/deck.xwsp", WORKTABLE_XML)
        zf.writestr("DataStore/Carriers/carrier.xsit", CARRIER_XML)
        zf.writestr("DataStore/LiquidClasses/water.xlqc", LIQUID_CLASS_XML)
        zf.writestr(
            "Worklists/sample.gwl",
            "\n".join(
                [
                    "A;SourcePlate;;Plate Carrier 1;1;;10;Water Free Single",
                    "D;DestinationPlate;;Plate Carrier 1;1;;10;Water Free Single",
                ]
            ),
        )
    return path


if __name__ == "__main__":
    unittest.main()
