"""Tests for api-v2-018 GetFingers.Validate()."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.api_v2.commands import GetFingers, get_fingers_from_xscr_element, validate_command
from fluent_pipeline.api_v2.command_validate import (
    API_V2_GET_FINGERS_ISSUE_ID,
    validate_compiled_xscr_commands,
)
from fluent_pipeline.api_v2.types import ApiV2ValidationError


def _get_fingers_xscr(*, labware: str, alias: str, available_id: str, line: int = 5) -> str:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaGetFingersScriptCommandDataV1">
  <CgaGetFingersScriptCommandDataV1>
    <Data Type="Tecan.Core.Instrument.Helpers.Scripting.ScriptCommandCommonDataV2">
      <ScriptCommandCommonDataV2>
        <LabwareName>{labware}</LabwareName>
        <Data Type="Tecan.Core.Instrument.Helpers.Scripting.DeviceAliasStatementBaseDataV1">
          <DeviceAliasStatementBaseDataV1>
            <Alias Type="Tecan.Core.Instrument.DeviceAlias.DeviceAlias">
              <DeviceAlias>{alias}</DeviceAlias>
            </Alias>
            <ID>
              <AvailableID>{available_id}</AvailableID>
            </ID>
            <Data Type="Tecan.Core.Scripting.Helpers.ScriptStatementBaseDataV1">
              <ScriptStatementBaseDataV1>
                <LineNumber>{line}</LineNumber>
              </ScriptStatementBaseDataV1>
            </Data>
          </DeviceAliasStatementBaseDataV1>
        </Data>
      </ScriptCommandCommonDataV2>
    </Data>
  </CgaGetFingersScriptCommandDataV1>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""


class GetFingersValidateTests(unittest.TestCase):
    def test_valid_eccentric_fingers_pass(self):
        command = GetFingers(
            labware_name="Eccentric[001]",
            device_alias="Instrument=1/Device=CGA:1",
            available_id="USB:TECAN,FLUENT,2405000993/CGA:1",
        )
        validate_command(command)

    def test_rejects_adapter_labware(self):
        command = GetFingers(
            labware_name="AdapterA200",
            device_alias="Instrument=1/Device=CGA:1",
            available_id="USB:TECAN,FLUENT,2405000993/CGA:1",
        )
        with self.assertRaises(ApiV2ValidationError):
            command.validate()

    def test_rejects_unknown_finger_labware(self):
        command = GetFingers(
            labware_name="FES Centric Nest[001]",
            device_alias="Instrument=1/Device=CGA:1",
            available_id="USB:TECAN,FLUENT,2405000993/CGA:1",
        )
        with self.assertRaises(ApiV2ValidationError):
            command.validate()

    def test_rejects_alias_id_device_mismatch(self):
        command = GetFingers(
            labware_name="Centric[001]",
            device_alias="Instrument=1/Device=CGA:1",
            available_id="USB:TECAN,FLUENT,2405000993/RGA:1",
        )
        with self.assertRaises(ApiV2ValidationError):
            command.validate()

    def test_rejects_stale_usb_serial(self):
        command = GetFingers(
            labware_name="Tube[001]",
            device_alias="Instrument=1/Device=CGA:1",
            available_id="USB:TECAN,FLUENT,0000000000/CGA:1",
        )
        with self.assertRaises(ApiV2ValidationError):
            command.validate()

    def test_compiled_xscr_validation_tags_api_v2_018(self):
        xscr = _get_fingers_xscr(
            labware="AdapterA200",
            alias="Instrument=1/Device=CGA:1",
            available_id="USB:TECAN,FLUENT,2405000993/CGA:1",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.xscr"
            path.write_text(xscr, encoding="utf-8")
            report = validate_compiled_xscr_commands(path)
        self.assertFalse(report.ok)
        issues = {item.api_v2_issue for item in report.failures}
        self.assertIn(API_V2_GET_FINGERS_ISSUE_ID, issues)

    def test_get_fingers_from_xscr_element_parses_nested_fields(self):
        from fluent_pipeline import xml_compat as ET

        element = ET.fromstring(
            _get_fingers_xscr(
                labware="Eccentric[001]",
                alias="Instrument=1/Device=CGA:1",
                available_id="USB:TECAN,FLUENT,2405000993/CGA:1",
            )
        ).find(".//{*}CgaGetFingersScriptCommandDataV1")
        self.assertIsNotNone(element)
        command = get_fingers_from_xscr_element(element)
        self.assertEqual(command.labware_name, "Eccentric[001]")
        self.assertEqual(command.device_alias, "Instrument=1/Device=CGA:1")


if __name__ == "__main__":
    unittest.main()
