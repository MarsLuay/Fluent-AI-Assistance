"""Tests for api-v2-008 AddLabware.Validate()."""

from __future__ import annotations

import unittest

from fluent_pipeline.api_v2.commands import AddLabware, add_labware_from_ir_step
from fluent_pipeline.api_v2.types import ApiV2ValidationError
from fluent_pipeline.api_v2_add_labware_validate import (
    AddLabwareFields,
    extract_add_labware_fields,
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


class ExtractAddLabwareFieldsTests(unittest.TestCase):
    def test_empty_payload(self):
        cmd = _CommandStub(payload_xml="")
        self.assertIsNone(extract_add_labware_fields(cmd))

    def test_invalid_xml(self):
        cmd = _CommandStub(payload_xml="<Not>Valid<XML")
        self.assertIsNone(extract_add_labware_fields(cmd))

    def test_missing_add_labware_data_node(self):
        cmd = _CommandStub(payload_xml='<Object Type="Other"><OtherData/></Object>')
        self.assertIsNone(extract_add_labware_fields(cmd))

    def test_valid_payload_all_fields(self):
        cmd = _CommandStub(payload_xml=_GOOD_PAYLOAD)
        fields = extract_add_labware_fields(cmd)
        self.assertIsNotNone(fields)
        self.assertEqual(fields.labware_type, "96 Well Flat")
        self.assertEqual(fields.labware_label, "Plate1")
        self.assertEqual(fields.location, "NestPlatform")
        self.assertEqual(fields.site, "1")
        self.assertEqual(fields.rotation, "0")
        self.assertFalse(fields.has_lid)

    def test_default_values(self):
        payload = """<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">
          <AddLabwareDataV1>
            <LabwareType>384 Well</LabwareType>
            <LabwareLabel>Plate2</LabwareLabel>
            <Location>Site1</Location>
          </AddLabwareDataV1>
        </Object>"""
        cmd = _CommandStub(payload_xml=payload)
        fields = extract_add_labware_fields(cmd)
        self.assertIsNotNone(fields)
        self.assertEqual(fields.labware_type, "384 Well")
        self.assertEqual(fields.labware_label, "Plate2")
        self.assertEqual(fields.location, "Site1")
        self.assertEqual(fields.site, "1")
        self.assertEqual(fields.rotation, "0")
        self.assertFalse(fields.has_lid)

    def test_labware_label_spelling_variants(self):
        # typo LabwareLable
        payload1 = """<Object>
          <AddLabwareDataV1>
            <LabwareLable>TypoLabel</LabwareLable>
          </AddLabwareDataV1>
        </Object>"""
        fields1 = extract_add_labware_fields(_CommandStub(payload_xml=payload1))
        self.assertEqual(fields1.labware_label, "TypoLabel")

        # correct LabwareLabel
        payload2 = """<Object>
          <AddLabwareDataV1>
            <LabwareLabel>CorrectLabel</LabwareLabel>
          </AddLabwareDataV1>
        </Object>"""
        fields2 = extract_add_labware_fields(_CommandStub(payload_xml=payload2))
        self.assertEqual(fields2.labware_label, "CorrectLabel")

    def test_has_lid_parsing(self):
        payload = """<Object>
          <AddLabwareDataV1>
            <HasLid>True</HasLid>
          </AddLabwareDataV1>
        </Object>"""
        fields = extract_add_labware_fields(_CommandStub(payload_xml=payload))
        self.assertTrue(fields.has_lid)

        payload_lower = """<Object>
          <AddLabwareDataV1>
            <HasLid>true</HasLid>
          </AddLabwareDataV1>
        </Object>"""
        fields_lower = extract_add_labware_fields(_CommandStub(payload_xml=payload_lower))
        self.assertTrue(fields_lower.has_lid)


if __name__ == "__main__":
    unittest.main()
