"""Tests for api-v2-008 AddLabware.Validate()."""

from __future__ import annotations

import unittest

from fluent_pipeline.api_v2.commands import AddLabware, add_labware_from_ir_step
from fluent_pipeline.api_v2.types import ApiV2ValidationError
from fluent_pipeline.api_v2_add_labware_validate import (
    AddLabwareFields,
    AddLabwareValidateResult,
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

    def test_label_key_normalization(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="  MyPlate  ",
            location="NestPlatform",
            site=1,
        )
        self.assertEqual(fields.label_key(), "myplate")

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

    def test_add_labware_fields_as_dict(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
            rotation=90,
            has_lid=True,
        )
        self.assertEqual(
            fields.as_dict(),
            {
                "labware_type": "96 Well Flat",
                "labware_label": "Plate1",
                "location": "NestPlatform",
                "site": 1,
                "rotation": 90,
                "has_lid": True,
            },
        )

    def test_add_labware_validate_result_as_dict(self):
        result_ok = AddLabwareValidateResult(ok=True)
        self.assertEqual(
            result_ok.as_dict(),
            {
                "ok": True,
                "source": "offline",
                "api_v2_method": "AddLabware.Validate()",
                "api_v2_issue": "api-v2-008",
            },
        )

        result_error = AddLabwareValidateResult(
            ok=False,
            message="Error message",
            reason="invalid_field",
            field="labware_type",
            fields={"labware_type": "Invalid"},
            source="native",
        )
        self.assertEqual(
            result_error.as_dict(),
            {
                "ok": False,
                "source": "native",
                "api_v2_method": "AddLabware.Validate()",
                "api_v2_issue": "api-v2-008",
                "message": "Error message",
                "reason": "invalid_field",
                "field": "labware_type",
                "fields": {"labware_type": "Invalid"},
            },
        )



    def test_add_labware_fields_as_dict(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate1",
            location="NestPlatform",
            site=2,
            rotation=180,
            has_lid=True,
        )
        expected = {
            "labware_type": "96 Well Flat",
            "labware_label": "Plate1",
            "location": "NestPlatform",
            "site": 2,
            "rotation": 180,
            "has_lid": True,
        }
        self.assertEqual(fields.as_dict(), expected)

    def test_add_labware_validate_result_as_dict_minimal(self):
        from fluent_pipeline.api_v2_add_labware_validate import AddLabwareValidateResult, API_V2_METHOD, API_V2_ISSUE_ID
        result = AddLabwareValidateResult(ok=True)
        expected = {
            "ok": True,
            "source": "offline",
            "api_v2_method": API_V2_METHOD,
            "api_v2_issue": API_V2_ISSUE_ID,
        }
        self.assertEqual(result.as_dict(), expected)

    def test_add_labware_validate_result_as_dict_full(self):
        from fluent_pipeline.api_v2_add_labware_validate import AddLabwareValidateResult, API_V2_METHOD, API_V2_ISSUE_ID
        result = AddLabwareValidateResult(
            ok=False,
            message="Something went wrong",
            reason="duplicate_label",
            field="labware_label",
            source="native",
            fields={"labware_type": "96 Well Flat"},
        )
        expected = {
            "ok": False,
            "source": "native",
            "api_v2_method": API_V2_METHOD,
            "api_v2_issue": API_V2_ISSUE_ID,
            "message": "Something went wrong",
            "reason": "duplicate_label",
            "field": "labware_label",
            "fields": {"labware_type": "96 Well Flat"},
        }
        self.assertEqual(result.as_dict(), expected)


if __name__ == "__main__":
    unittest.main()
