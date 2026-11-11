"""Tests for api-v2-006 ICommand.Validate() batch validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tecan_common import native_acceleration
import fluent_pipeline.api_v2.command_validate as command_validate
from fluent_pipeline.api_v2.command_validate import (
    API_V2_ISSUE_ID,
    OfflineCommandValidateProvider,
    validate_compiled_xscr_commands,
)
from fluent_pipeline.api_v2_preflight import preflight_command_validation
from fluent_pipeline.validation import _compiled_command_inventory, _gate_post_compile_xscr, validate_ready_to_import


BAD_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <PayloadData><Script>
      <Properties><VariableDeclarations><VariableDeclarations>
        <anyType xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" i:type="d3p1:VariableDefinitionHelper" xmlns:i="http://www.w3.org/2001/XMLSchema-instance"><d3p1:Name>platecount</d3p1:Name><d3p1:Values><string>1</string></d3p1:Values></anyType>
      </VariableDeclarations></VariableDeclarations></Properties>
      <Commands><ScriptGroup><Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1>
            <LabwareType>Adapter A200</LabwareType>
            <LabwareLable>AdapterA200</LabwareLable>
            <LineNumber>1</LineNumber>
          </AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1>
            <LabwareType>24 Filter Plate</LabwareType>
            <LabwareLable>FilterDWP[platecount]</LabwareLable>
            <LineNumber>2</LineNumber>
          </AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.UserPromptStatement">
          <UserPromptStatement><Prompt>Check</Prompt><Timeout>99999</Timeout><LineNumber>3</LineNumber></UserPromptStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
          <SubRoutineStatement><SubRoutine>"Demo\\\\MissingSub"</SubRoutine><LineNumber>4</LineNumber></SubRoutineStatement>
        </Object>
        <Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaGetFingersScriptCommandDataV1">
          <CgaGetFingersScriptCommandDataV1><ScriptCommandCommonDataV2><LabwareName>AdapterA200</LabwareName><LineNumber>5</LineNumber></ScriptCommandCommonDataV2></CgaGetFingersScriptCommandDataV1>
        </Object>
      </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""

GOOD_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <Reference><TypeId>Script</TypeId><ObjectName>ExistingSub</ObjectName></Reference>
    <PayloadData><Script>
      <Properties><VariableDeclarations><VariableDeclarations>
        <anyType xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" i:type="d3p1:VariableDefinitionHelper" xmlns:i="http://www.w3.org/2001/XMLSchema-instance"><d3p1:Name>platecount</d3p1:Name><d3p1:Values><string>1</string></d3p1:Values></anyType>
      </VariableDeclarations></VariableDeclarations></Properties>
      <Commands><ScriptGroup><Objects>
        <Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1><LabwareType>24 Filter Plate</LabwareType><LabwareLable>FilterDWP[platecount]</LabwareLable><Location>NestPlatform</Location><Position>3</Position><Rotation>0</Rotation><LineNumber>1</LineNumber></AddLabwareDataV1>
        </Object>
        <Object Type="Tecan.Core.Scripting.UserPromptStatement">
          <UserPromptStatement><Prompt>Check</Prompt><Timeout>1</Timeout><LineNumber>2</LineNumber></UserPromptStatement>
        </Object>
        <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
          <SubRoutineStatement><SubRoutine>"Demo\\\\ExistingSub"</SubRoutine><LineNumber>3</LineNumber></SubRoutineStatement>
        </Object>
      </Objects></ScriptGroup></Commands></Script></PayloadData>
  </Payload>
</VxData>
"""


class ApiV2CommandValidateTests(unittest.TestCase):
    def test_bracket_variable_names_use_rust_with_python_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "bracket_variable_names"
        ):
            self.skipTest("optional Rust bracket-variable scanner is not built")
        values = [
            "[A] [b_2] [A] [] [_x] [9bad] [with-dash] [[nested]] [é] [Aé]",
            "before [name9] after",
            "",
            None,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                command_validate._bracket_variable_names(value) for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                command_validate,
                "_native_bracket_variable_names",
                wraps=native_acceleration.bracket_variable_names,
            ) as native_call:
                actual = [
                    command_validate._bracket_variable_names(value) for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], {"A", "b_2", "_x", "nested"})
        self.assertEqual(actual[1], {"name9"})
        self.assertEqual(actual[2:], [set(), set()])

        class StringSubclass(str):
            pass

        unsupported = StringSubclass("[Subclass]")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.bracket_variable_names(unsupported))
            self.assertEqual(
                command_validate._bracket_variable_names(unsupported), {"Subclass"}
            )

    def test_subroutine_name_cleaning_uses_ascii_rust_path(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "clean_subroutine_name_ascii"
        ):
            self.skipTest("optional Rust subroutine-name cleaner is not built")
        values = [
            " \x1c\"Macro/Sub\"\x1f \t",
            "\" foo \"",
            "\"\"",
            None,
            "\"UPPER/Path\"",
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                command_validate._clean_subroutine_name(value) for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                command_validate,
                "_native_clean_subroutine_name_ascii",
                wraps=native_acceleration.clean_subroutine_name_ascii,
            ) as native_call:
                actual = [
                    command_validate._clean_subroutine_name(value) for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual, ["Macro\\Sub", " foo ", "", "", "UPPER\\Path"]
        )

        unicode_value = "\u00a0\"Ä/Sub\"\u00a0"

        class StringSubclass(str):
            pass

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.clean_subroutine_name_ascii(unicode_value)
            )
            self.assertIsNone(
                native_acceleration.clean_subroutine_name_ascii(
                    StringSubclass("\"Sub/Path\"")
                )
            )
            self.assertEqual(
                command_validate._clean_subroutine_name(unicode_value), "Ä\\Sub"
            )

    def test_local_name_uses_native_text_kernel(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_tag_local_name"
        ):
            self.skipTest("optional Rust XML local-name helper is not built")

        class CustomTag:
            def __init__(self, text: str) -> None:
                self.text = text
                self.string_calls = 0

            def __str__(self) -> str:
                self.string_calls += 1
                return self.text

        custom = CustomTag("{urn:test}Custom")
        values = [
            "{urn:test}Object",
            "{outer}{inner}Name",
            "Object",
            42,
            "\ud800",
            custom,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                command_validate._local_name(value) for value in values
            ]
        self.assertEqual(custom.string_calls, 1)
        custom.string_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                command_validate,
                "_native_xml_local_name",
                wraps=native_acceleration.xml_local_name,
            ) as native_call:
                actual = [command_validate._local_name(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual, ["Object", "Name", "Object", "42", "\ud800", "Custom"]
        )
        self.assertEqual(custom.string_calls, 1)

    def test_root_command_scan_uses_native_selector_and_comment_fallback(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_name"
        ):
            self.skipTest("optional Rust XML selector is not built")
        root = command_validate.ET.fromstring(
            '<Root xmlns:x="urn:test">'
            '<x:Object Type="Example.Commands.First"><Data /></x:Object>'
            '<Object Type="Example.Commands.Second" />'
            '<Object Type="Example.Unrelated" />'
            '<object Type="Example.Commands.WrongCase" />'
            "</Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = command_validate._records_from_root(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                command_validate,
                "_native_xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call:
                actual = command_validate._records_from_root(root)
                native_call.assert_called_once_with(
                    root, ("Object",), command_validate.ET.Element
                )
        self.assertEqual(actual, expected)
        self.assertEqual(
            [record["command_id"] for record in actual], ["First", "Second"]
        )

        commented = command_validate.ET.Element("Root")
        commented.append(command_validate.ET.Comment("unsupported legacy tree"))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            commented_expected = command_validate._records_from_root(commented)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_elements_by_name(
                    commented, ("Object",), command_validate.ET.Element
                )
            )
            commented_actual = command_validate._records_from_root(commented)
        self.assertEqual(commented_actual, commented_expected)
        self.assertEqual(commented_actual, [])

    def test_validation_candidate_scans_use_native_xml_selectors(self):
        root = command_validate.ET.fromstring(
            '<Root xmlns:x="urn:test" xmlns:i="urn:types">'
            '<x:anyType i:type="i:VariableDefinitionHelper">'
            "<x:Name>first</x:Name></x:anyType>"
            "<VariableDefinitionHelper><Name>second</Name></VariableDefinitionHelper>"
            '<anyType i:type="i:Unrelated"><Name>ignored</Name></anyType>'
            "<LabwareName>first</LabwareName><LabwareLable>first</LabwareLable>"
            "<LabwareLabel>second</LabwareLabel></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_variables = command_validate._compiled_variable_declarations(root)
            expected_labware = command_validate._compiled_labware_name_fields(root)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                command_validate,
                "_native_xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call, patch.object(
                command_validate,
                "_native_xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as first_call:
                actual_variables = command_validate._compiled_variable_declarations(
                    root
                )
                actual_labware = command_validate._compiled_labware_name_fields(root)
                actual_first_text = command_validate._first_text(root, "Name")
                self.assertEqual(native_call.call_count, 2)
                self.assertEqual(first_call.call_count, 3)
        self.assertEqual(actual_variables, expected_variables)
        self.assertEqual(actual_variables, {"first", "second"})
        self.assertEqual(actual_labware, expected_labware)
        self.assertEqual(actual_labware, ["first", "second"])
        self.assertEqual(actual_first_text, "first")

        commented = command_validate.ET.Element("Root")
        commented.append(command_validate.ET.Comment("fallback"))
        command_validate.ET.SubElement(commented, "Name").text = "comment-safe"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_text = command_validate._first_text(commented, "Name")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            selection = native_acceleration.xml_first_element_by_name(
                commented, "Name", command_validate.ET.Element
            )
            self.assertIsNotNone(selection)
            assert selection is not None
            self.assertIs(selection[1], commented[1])
            native_fallback_text = command_validate._first_text(commented, "Name")
        self.assertEqual(native_fallback_text, fallback_text)

        empty_first = command_validate.ET.fromstring(
            "<Root><Name> </Name><Name>later</Name></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_empty = command_validate._first_text(empty_first, "Name", "default")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_empty = command_validate._first_text(empty_first, "Name", "default")
        self.assertEqual(native_empty, fallback_empty)
        self.assertEqual(native_empty, "default")

    def test_structured_failures_for_bad_xscr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.xscr"
            path.write_text(BAD_XSCR, encoding="utf-8")
            report = validate_compiled_xscr_commands(path)

        self.assertFalse(report.ok)
        self.assertGreater(report.failure_count, 0)
        reasons = {item.reason for item in report.failures}
        self.assertIn("prompt_timeout_out_of_range", reasons)
        self.assertIn("subroutine_reference_missing", reasons)
        self.assertIn("rga_fingers_incompatible_labware", reasons)
        payload = report.failures[0].as_dict()
        self.assertEqual(payload["api_v2_issue"], API_V2_ISSUE_ID)
        self.assertEqual(payload["api_v2_method"], "ICommand.Validate()")

    def test_clean_xscr_passes_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "good.xscr"
            path.write_text(GOOD_XSCR, encoding="utf-8")
            report = OfflineCommandValidateProvider().validate_compiled_xscr(path)

        self.assertTrue(report.ok)
        self.assertEqual(report.failure_count, 0)
        self.assertGreater(report.command_count, 0)

    def test_liha_labware_section_metadata_is_not_validated_as_a_command(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
  <Object Type="Tecan.Core.Scripting.Commands.LiHa.UI.LabwareSectionInfo">
    <LabwareSectionInfo>
      <LabwareName>SourcePlate</LabwareName>
      <SelectedWellIndexes />
    </LabwareSectionInfo>
  </Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "liha_metadata.xscr"
            path.write_text(xscr, encoding="utf-8")
            report = validate_compiled_xscr_commands(path)

        self.assertTrue(report.ok)
        self.assertEqual(report.command_count, 0)

    def test_inventory_includes_command_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.xscr"
            path.write_text(BAD_XSCR, encoding="utf-8")
            inventory = _compiled_command_inventory(path)

        validation = inventory.get("command_validation") or {}
        self.assertFalse(validation.get("ok"))
        self.assertTrue(validation.get("failures"))
        finding_reasons = {item["reason"] for item in inventory["fluentcontrol_findings"]}
        failure_reasons = {item["reason"] for item in validation["failures"]}
        self.assertEqual(finding_reasons, failure_reasons)

    def test_gate_11_surfaces_command_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.xscr"
            path.write_text(BAD_XSCR, encoding="utf-8")
            inventory = _compiled_command_inventory(path)
        gate = _gate_post_compile_xscr({"steps": [{"operation": "comment"}]}, "", inventory)
        self.assertEqual(gate["status"], "failed")
        details = gate.get("details") or {}
        self.assertIn("command_validation", details)
        self.assertTrue(details.get("command_validation_failures"))

    def test_runtime_preflight_blocks_bad_xscr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.xscr"
            path.write_text(BAD_XSCR, encoding="utf-8")
            payload = preflight_command_validation(path)

        self.assertFalse(payload["ok"])
        self.assertIn("failures", payload)

    def test_user_prompt_validate_rejects_empty_message(self):
        xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Scripting.UserPromptStatement">
  <UserPromptStatement><Prompt>   </Prompt><Timeout>5</Timeout><LineNumber>1</LineNumber></UserPromptStatement>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "empty_prompt.xscr"
            path.write_text(xscr, encoding="utf-8")
            report = validate_compiled_xscr_commands(path)

        self.assertFalse(report.ok)
        typed = [item for item in report.failures if item.source == "offline_typed_validate"]
        self.assertTrue(any(item.reason == "api_v2_validate_rejected" for item in typed))


if __name__ == "__main__":
    unittest.main()
