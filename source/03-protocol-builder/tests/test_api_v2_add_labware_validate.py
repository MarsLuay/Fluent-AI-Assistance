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
    def test_slot_key(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate1",
            location="  NestPlatform  ",
            site=" 1 ",
        )
        self.assertEqual(fields.slot_key(), ("nestplatform", "1"))

    def test_label_key(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="  Plate1  ",
            location="NestPlatform",
            site=1,
        )
        self.assertEqual(fields.label_key(), "plate1")

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


    def test_missing_labware_type_fails(self):
        fields = AddLabwareFields(
            labware_type="",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields)
        self.assertFalse(result.ok)
        self.assertEqual(result.field, "labware_type")

    def test_missing_labware_label_fails(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields)
        self.assertFalse(result.ok)
        self.assertEqual(result.field, "labware_label")

    def test_invalid_rotation_fails(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
            rotation=400,
        )
        result = validate_add_labware_fields(fields)
        self.assertFalse(result.ok)
        self.assertEqual(result.field, "rotation")

    def test_undeclared_bracket_variable_in_label_fails(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate_[id]",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields, check_bracket_variables=True, declared_variables=set())
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "undeclared_variable")

    def test_declared_bracket_variable_in_label_passes(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate_[id]",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields, check_bracket_variables=True, declared_variables={"id"})
        self.assertTrue(result.ok)

    def test_fc_var_labware_type_invalid_name(self):
        fields = AddLabwareFields(
            labware_type="fc_var:",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields)
        self.assertFalse(result.ok)
        self.assertEqual(result.field, "labware_type")

    def test_fc_var_labware_type_valid_name(self):
        fields = AddLabwareFields(
            labware_type="fc_var:MyLabwareType",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields)
        self.assertTrue(result.ok)

    def test_undeclared_bracket_variable_in_labware_type_fails(self):
        fields = AddLabwareFields(
            labware_type="[type_var]",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields, check_bracket_variables=True, declared_variables=set())
        self.assertFalse(result.ok)
        self.assertEqual(result.field, "labware_type")

    def test_declared_bracket_variable_in_labware_type_passes(self):
        fields = AddLabwareFields(
            labware_type="[type_var]",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields, check_bracket_variables=True, declared_variables={"type_var"})
        self.assertTrue(result.ok)

    def test_placeholder_labware_label_fails(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="TODO",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(fields)
        self.assertFalse(result.ok)
        self.assertEqual(result.field, "labware_label")

    def test_offline_stepped_command(self):
        result = validate_add_labware_offline(_CommandStub(payload_xml=_GOOD_PAYLOAD))
        self.assertTrue(result.ok)

    def test_offline_non_add_labware(self):
        class NonAddLabwareCommand:
            type_name = "SomeOtherCommand"
            index = 0
            group = "Setup"
            payload_xml = ""

        result = validate_add_labware_offline(NonAddLabwareCommand())
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "skipped_non_add_labware")

    def test_offline_no_payload(self):
        result = validate_add_labware_offline(_CommandStub(payload_xml=""))
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "skipped_no_payload")
        self.assertEqual(result.message, "No AddLabware payload available for offline validation.")

    def test_offline_invalid_payload(self):
        result = validate_add_labware_offline(_CommandStub(payload_xml="<Object>Invalid XML</Object"))
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "skipped_no_payload")

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

    def test_validate_add_labware_ir_steps_coverage(self):
        ir = {
            "variables": [{"name": "GLOBAL_VAR"}, "not-a-dict", {}],
            "steps": [
                "not-a-dict",
                {
                    "operation": "some_other_op",
                },
                {
                    "id": "step_001",
                    "operation": "add_labware",
                    "parameters": {
                        "catalog": "[VAR_FROM_STEP]",
                        "label": "[GLOBAL_VAR]",
                        "location": "NestPlatform",
                        "position": 1,
                        "declared_variables": ["VAR_FROM_STEP"],
                    },
                },
                {
                    "id": "step_002",
                    "operation": "add_labware",
                    "parameters": {
                        "catalog": "[UNDECLARED_CAT]",
                        "label": "Plate2",
                        "location": "NestPlatform",
                        "position": 2,
                    },
                },
                {
                    "id": "step_003",
                    "operation": "add_labware",
                    "parameters": {
                        "catalog": "96 Well Flat",
                        "label": "[UNDECLARED_LBL]",
                        "location": "NestPlatform",
                        "position": 3,
                    },
                },
                {
                    "id": "step_004",
                    "operation": "add_labware",
                    "parameters": {
                        "catalog": "96 Well Flat",
                        "label": "Plate1",
                        "location": "NestPlatform",
                        "position": 4,
                    },
                },
                {
                    "id": "step_005",
                    "operation": "add_labware",
                    "parameters": {
                        "catalog": "96 Well Flat",
                        "label": "Plate1",
                        "location": "NestPlatform",
                        "position": 5,
                    },
                },
                {
                    "id": "step_006",
                    "operation": "add_labware",
                    "parameters": {
                        "catalog": "96 Well Flat",
                        "label": "Plate6",
                        "location": "NestPlatform",
                        "position": 4,
                    },
                },
            ]
        }

        failures = validate_add_labware_ir_steps(ir, declared_variables={"PRE_DECLARED"})
        self.assertEqual(len(failures), 3)

        # Test missing label in bracket (results in "undeclared_variable")
        self.assertEqual(failures[0].reason, "undeclared_variable")
        self.assertIn("UNDECLARED_LBL", failures[0].message)

        self.assertEqual(failures[1].reason, "duplicate_labware_label")
        self.assertEqual(failures[1].field, "labware_label")

        self.assertEqual(failures[2].reason, "occupied_slot")
        self.assertEqual(failures[2].field, "site")

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
