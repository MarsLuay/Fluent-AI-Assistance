"""Tests for api-v2-008 AddLabware.Validate()."""

from __future__ import annotations

import unittest

from fluent_pipeline.api_v2.commands import AddLabware, add_labware_from_ir_step
from fluent_pipeline.api_v2.types import ApiV2ValidationError
from fluent_pipeline.api_v2_add_labware_validate import (
    AddLabwareFields,
    validate_add_labware_before_execute,
    validate_add_labware_fields,
    validate_add_labware_ir_steps,
    validate_add_labware_offline,
)


class _CommandStub:
    def __init__(self, *, payload_xml: str, index: int = 0, group: str = "Setup"):
        self.type_name = "AddLabwareDataV1"
        self.index = index
        self.group = group
        self.payload_xml = payload_xml


_GOOD_PAYLOAD = """<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
  <AddLabwareDataV1>
    <LabwareType>96 Well Flat</LabwareType>
    <LabwareLable>Plate1</LabwareLable>
    <Location>NestPlatform</Location>
    <Position>1</Position>
    <Rotation>0</Rotation>
    <HasLid>False</HasLid>
  </AddLabwareDataV1>
</Object>"""


class AddLabwareValidateTests(unittest.TestCase):
    def test_valid_fields_pass(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields)
        self.assertTrue(result.ok)
        self.assertEqual(result.api_v2_issue, "api-v2-008")

    def test_missing_location_fails(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate1",
            location="",
            site=1,
        )
        result = validate_add_labware_fields(fields)
        self.assertFalse(result.ok)
        self.assertEqual(result.field, "location")

    def test_invalid_site_fails(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate1",
            location="NestPlatform",
            site=0,
        )
        result = validate_add_labware_fields(fields)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "api_v2_validate_rejected")

    def test_duplicate_label_fails(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate1",
            location="NestPlatform",
            site=2,
        )
        result = validate_add_labware_fields(
            fields,
            prior_labels={"plate1"},
            prior_slots=set(),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "duplicate_labware_label")

    def test_duplicate_slot_fails(self):
        fields = AddLabwareFields(
            labware_type="24 Filter Plate",
            labware_label="Filter1",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(
            fields,
            prior_labels=set(),
            prior_slots={("nestplatform", "1")},
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "occupied_slot")

    def test_placeholder_catalog_fails(self):
        cmd = AddLabware(
            labware_type="TODO",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
        )
        with self.assertRaises(ApiV2ValidationError):
            cmd.validate()

    def test_offline_stepped_command(self):
        result = validate_add_labware_offline(_CommandStub(payload_xml=_GOOD_PAYLOAD))
        self.assertTrue(result.ok)

    def test_ir_batch_detects_duplicate_label(self):
        ir = {
            "steps": [
                {
                    "id": "step_001",
                    "operation": "add_labware",
                    "parameters": {
                        "catalog": "96 Well Flat",
                        "label": "Plate1",
                        "location": "NestPlatform",
                        "position": 1,
                    },
                },
                {
                    "id": "step_002",
                    "operation": "add_labware",
                    "parameters": {
                        "catalog": "96 Well Flat",
                        "label": "Plate1",
                        "location": "NestPlatform",
                        "position": 2,
                    },
                },
            ]
        }
        failures = validate_add_labware_ir_steps(ir)
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].reason, "duplicate_labware_label")

    def test_add_labware_from_ir_step(self):
        step = {
            "operation": "add_labware",
            "parameters": {
                "catalog": "Adapter A200",
                "label": "AdapterA200",
                "location": "Demo_Nest_Pos",
                "site": 1,
            },
        }
        cmd = add_labware_from_ir_step(step)
        self.assertIsInstance(cmd, AddLabware)
        cmd.validate()

    def test_before_execute_skips_non_add_labware(self):
        cmd = _CommandStub(payload_xml="")
        cmd.type_name = "SomeOtherCommand"
        result = validate_add_labware_before_execute(cmd)
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "skipped_non_add_labware")

    def test_before_execute_offline_failure(self):
        bad_payload = """<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1>
            <LabwareType>96 Well Flat</LabwareType>
            <LabwareLable>Plate1</LabwareLable>
            <Location></Location>
            <Position>1</Position>
          </AddLabwareDataV1>
        </Object>"""
        cmd = _CommandStub(payload_xml=bad_payload)
        called_native = False

        def mock_native():
            nonlocal called_native
            called_native = True

        result = validate_add_labware_before_execute(cmd, native_validate=mock_native)
        self.assertFalse(result.ok)
        self.assertEqual(result.source, "offline_typed_validate")
        self.assertEqual(result.field, "location")
        self.assertFalse(called_native)

    def test_before_execute_native_passes(self):
        cmd = _CommandStub(payload_xml=_GOOD_PAYLOAD)
        called_native = False

        def mock_native():
            nonlocal called_native
            called_native = True

        result = validate_add_labware_before_execute(cmd, native_validate=mock_native)
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "native")
        self.assertTrue(called_native)

    def test_before_execute_native_fails(self):
        cmd = _CommandStub(payload_xml=_GOOD_PAYLOAD)

        def mock_native():
            raise ValueError("Instrument offline")

        result = validate_add_labware_before_execute(cmd, native_validate=mock_native)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "native_validate_failed")
        self.assertEqual(result.message, "Instrument offline")
        self.assertEqual(result.source, "native")

    def test_before_execute_native_fails_empty_error(self):
        cmd = _CommandStub(payload_xml=_GOOD_PAYLOAD)

        def mock_native():
            raise ValueError("")

        result = validate_add_labware_before_execute(cmd, native_validate=mock_native)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "native_validate_failed")
        self.assertEqual(result.message, "AddLabware.Validate() failed.")
        self.assertEqual(result.source, "native")

    def test_before_execute_no_native(self):
        cmd = _CommandStub(payload_xml=_GOOD_PAYLOAD)
        result = validate_add_labware_before_execute(cmd)
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "offline_typed_validate")


if __name__ == "__main__":
    unittest.main()
