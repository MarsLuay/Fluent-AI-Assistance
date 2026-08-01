"""Tests for api-v2-006 ICommand.Validate() batch validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
