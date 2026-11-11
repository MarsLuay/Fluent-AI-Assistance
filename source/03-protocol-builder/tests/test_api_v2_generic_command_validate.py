"""Tests for api-v2-016 GenericCommand.Validate()."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fluent_pipeline.api_v2.generic_command_validate as generic_validate
from tecan_common import native_acceleration
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
    def test_local_name_uses_native_helper_and_preserves_string_fallback(self):
        tag = "{urn:example}Command"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = generic_validate._local_name(tag)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                generic_validate,
                "_native_xml_local_name",
                wraps=native_acceleration.xml_local_name,
            ) as native_call:
                actual = generic_validate._local_name(tag)
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "Command")

        class CustomTag(str):
            def __str__(self):
                return "{urn:override}Fallback"

        custom_tag = CustomTag("{urn:original}Ignored")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_expected = generic_validate._local_name(custom_tag)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.xml_local_name(custom_tag))
            fallback_actual = generic_validate._local_name(custom_tag)
        self.assertEqual(fallback_actual, fallback_expected)
        self.assertEqual(fallback_actual, "Fallback")

    def test_generic_xml_candidate_scans_use_native_selector_and_fallback(self):
        root = generic_validate.ET.fromstring(
            "<Root><Record><LineNumber>9</LineNumber>"
            "<Wrapper><Payload /></Wrapper></Record></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_records = generic_validate._elements_by_local_name(root, "Record")
            expected_line = generic_validate._extract_line_number(expected_records[0])
            expected_nested = generic_validate._object_has_command_body(
                expected_records[0], "Payload"
            )
            expected_missing = generic_validate._object_has_command_body(
                expected_records[0], "Missing"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                generic_validate,
                "_native_xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call, patch.object(
                generic_validate,
                "_native_xml_direct_elements_by_name",
                wraps=native_acceleration.xml_direct_elements_by_name,
            ) as direct_call:
                actual_records = generic_validate._elements_by_local_name(root, "Record")
                actual_line = generic_validate._extract_line_number(actual_records[0])
                actual_nested = generic_validate._object_has_command_body(
                    actual_records[0], "Payload"
                )
                actual_missing = generic_validate._object_has_command_body(
                    actual_records[0], "Missing"
                )
                self.assertEqual(native_call.call_count, 4)
                self.assertEqual(direct_call.call_count, 2)
        self.assertEqual(actual_records, expected_records)
        self.assertEqual(actual_line, expected_line)
        self.assertEqual(actual_line, 9)
        self.assertEqual(actual_nested, expected_nested)
        self.assertTrue(actual_nested)
        self.assertEqual(actual_missing, expected_missing)
        self.assertFalse(actual_missing)

        class StringTag(str):
            pass

        custom_root = generic_validate.ET.Element("Root")
        custom_record = generic_validate.ET.SubElement(custom_root, StringTag("Record"))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_custom = generic_validate._elements_by_local_name(
                custom_root, "Record"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_elements_by_name(
                    custom_root, ("Record",), generic_validate.ET.Element
                )
            )
            actual_custom = generic_validate._elements_by_local_name(
                custom_root, "Record"
            )
        self.assertEqual(actual_custom, expected_custom)
        self.assertEqual(actual_custom, [custom_record])

        custom_body_parent = generic_validate.ET.Element("Record")
        custom_body = generic_validate.ET.SubElement(
            custom_body_parent, StringTag("Payload")
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_custom_body = generic_validate._object_has_command_body(
                custom_body_parent, "Payload"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_direct_elements_by_name(
                    custom_body_parent, ("Payload",), generic_validate.ET.Element
                )
            )
            actual_custom_body = generic_validate._object_has_command_body(
                custom_body_parent, "Payload"
            )
        self.assertEqual(actual_custom_body, expected_custom_body)
        self.assertTrue(actual_custom_body)
        self.assertIsNotNone(custom_body)

        invalid_first_line = generic_validate.ET.fromstring(
            "<Record><LineNumber>invalid</LineNumber>"
            "<LineNumber>9</LineNumber></Record>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_invalid_line = generic_validate._extract_line_number(
                invalid_first_line
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_invalid_line = generic_validate._extract_line_number(
                invalid_first_line
            )
        self.assertEqual(actual_invalid_line, expected_invalid_line)
        self.assertEqual(actual_invalid_line, 0)

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
