"""Tests for api-v2-018 GetFingers.Validate()."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fluent_pipeline.api_v2.commands as commands_module
from tecan_common import native_acceleration
from fluent_pipeline.api_v2.commands import (
    DropFingers,
    GetFingers,
    get_fingers_from_xscr_element,
    validate_command,
)
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
    def test_casefold_helpers_use_native_ascii_and_unicode_fallback(self):
        contains_cases = [
            ("AdapterA200", "adapter", True),
            ("Straße", "STRASSE", True),
            ("Labware", "adapter", False),
        ]
        equal_cases = [
            ("CGA", "cga", True),
            ("Straße", "STRASSE", True),
            ("CGA", "RGA", False),
        ]
        expected = (
            [matched for _, _, matched in contains_cases],
            [matched for _, _, matched in equal_cases],
        )

        def evaluate():
            return (
                [
                    commands_module._casefolded_contains(value, needle)
                    for value, needle, _ in contains_cases
                ],
                [
                    commands_module._casefold_equal(left, right)
                    for left, right, _ in equal_cases
                ],
            )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            self.assertEqual(evaluate(), expected)
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                commands_module,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_trim:
                with patch.object(
                    commands_module,
                    "_native_ascii_casefold_equals",
                    wraps=native_acceleration.ascii_casefold_equals,
                ) as native_equals:
                    self.assertEqual(evaluate(), expected)
                    self.assertEqual(native_trim.call_count, len(contains_cases))
                    self.assertEqual(native_equals.call_count, len(equal_cases))
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                commands_module, "_native_ascii_casefold_trim", return_value=None
            ):
                with patch.object(
                    commands_module,
                    "_native_ascii_casefold_equals",
                    return_value=None,
                ):
                    self.assertEqual(evaluate(), expected)

    def test_finger_validators_dispatch_to_native_casefold_helpers(self):
        valid = GetFingers(
            labware_name="Eccentric[001]",
            device_alias="Instrument=1/Device=CGA:1",
            available_id="USB:TECAN,FLUENT,2405000993/CGA:1",
        )
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                commands_module,
                "_native_ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_equals:
                valid.validate()
                self.assertEqual(native_equals.call_count, 1)

        for command in (
            GetFingers(
                labware_name="AdapterA200",
                device_alias="Instrument=1/Device=CGA:1",
                available_id="USB:TECAN,FLUENT,2405000993/CGA:1",
            ),
            DropFingers(
                labware_name="AdapterA200",
                device_alias="Instrument=1/Device=CGA:1",
                available_id="USB:TECAN,FLUENT,2405000993/CGA:1",
            ),
        ):
            with self.subTest(command=type(command).__name__):
                with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                    with patch.object(
                        commands_module,
                        "_native_ascii_casefold_trim",
                        wraps=native_acceleration.ascii_casefold_trim,
                    ) as native_trim:
                        with self.assertRaises(ApiV2ValidationError):
                            command.validate()
                        self.assertEqual(native_trim.call_count, 1)

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
