"""Tests for api-v2-016 GenericCommand.Validate()."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.api_v2.commands import GenericCommand
from fluent_pipeline.api_v2.generic_command_validate import (
    validate_generic_command_payload,
    validate_passthrough_commands_from_xscr,
)
from fluent_pipeline.api_v2.types import ApiV2ValidationError
from fluent_pipeline.api_v2.generic_passthrough import validate_generic_passthrough_execute_xml
from fluent_pipeline.validation import _compiled_command_inventory


CGA_DROP_FINGERS_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaDropFingersScriptCommandDataV1">
  <CgaDropFingersScriptCommandDataV1>
    <LabwareName>TipBox1</LabwareName>
    <LineNumber>5</LineNumber>
  </CgaDropFingersScriptCommandDataV1>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""

BROKEN_PASSTHROUGH_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData><Payload><PayloadData><Script><Commands><ScriptGroup><Objects>
<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaDropFingersScriptCommandDataV1">
  <WrongInnerTag>
    <LabwareName>TipBox1</LabwareName>
  </WrongInnerTag>
</Object>
</Objects></ScriptGroup></Commands></Script></PayloadData></Payload></VxData>
"""


class GenericCommandValidateTests(unittest.TestCase):
    def test_valid_passthrough_payload_passes(self):
        command = GenericCommand(
            object_type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaDropFingersScriptCommandDataV1",
            command_id="CgaDropFingersScriptCommandDataV1",
            payload_xml=(
                '<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaDropFingersScriptCommandDataV1">'
                "<CgaDropFingersScriptCommandDataV1><LabwareName>TipBox1</LabwareName>"
                "<LineNumber>5</LineNumber></CgaDropFingersScriptCommandDataV1></Object>"
            ),
            line_number=5,
        )
        validate_generic_command_payload(command)

    def test_malformed_payload_raises(self):
        command = GenericCommand(
            command_id="CgaDropFingersScriptCommandDataV1",
            payload_xml="<Object><unclosed>",
        )
        with self.assertRaises(ApiV2ValidationError) as ctx:
            validate_generic_command_payload(command)
        self.assertIn("well-formed", str(ctx.exception).lower())

    def test_inner_tag_mismatch_raises(self):
        command = GenericCommand(
            object_type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaDropFingersScriptCommandDataV1",
            command_id="CgaDropFingersScriptCommandDataV1",
            payload_xml=(
                '<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaDropFingersScriptCommandDataV1">'
                "<WrongInnerTag /></Object>"
            ),
        )
        with self.assertRaises(ApiV2ValidationError):
            validate_generic_command_payload(command)

    def test_xscr_batch_validation_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protocol.xscr"
            path.write_text(CGA_DROP_FINGERS_XSCR, encoding="utf-8")
            report = validate_passthrough_commands_from_xscr(path)
            self.assertTrue(report.ok)
            self.assertGreaterEqual(report.passthrough_count, 1)

    def test_xscr_batch_validation_catches_broken_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protocol.xscr"
            path.write_text(BROKEN_PASSTHROUGH_XSCR, encoding="utf-8")
            report = validate_passthrough_commands_from_xscr(path)
            self.assertFalse(report.ok)
            self.assertGreaterEqual(report.failure_count, 1)

    def test_pre_execute_passthrough_validation(self):
        payload = (
            '<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaDropFingersScriptCommandDataV1">'
            "<WrongInnerTag /></Object>"
        )
        error = validate_generic_passthrough_execute_xml(
            type_name="CgaDropFingersScriptCommandDataV1",
            api_v2_type="GenericCommand",
            execute_xml=payload,
            payload_xml=payload,
            line_number="5",
        )
        self.assertIsNotNone(error)
        self.assertIn("WrongInnerTag", error)

    def test_compiled_inventory_includes_generic_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "protocol.xscr"
            path.write_text(CGA_DROP_FINGERS_XSCR, encoding="utf-8")
            inventory = _compiled_command_inventory(path)
            generic = inventory.get("generic_command_validation") or {}
            self.assertIn("ok", generic)
            self.assertEqual(generic.get("api_v2_issue"), "api-v2-016")


if __name__ == "__main__":
    unittest.main()
