"""Tests for api-v2-008 AddLabware.Validate()."""

from __future__ import annotations

import unittest
from unittest.mock import patch

import fluent_pipeline.api_v2_add_labware_validate as add_labware_validate
from tecan_common import native_acceleration
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
    def test_normalized_keys_use_native_ascii_and_python_unicode_fallback(self):
        values = [" Label ", "NEST", "Straße", "  Straße  ", "", None, 0]
        expected = [str(value or "").strip().casefold() for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = [add_labware_validate._norm(value) for value in values]
        self.assertEqual(disabled, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                add_labware_validate,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = [add_labware_validate._norm(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                add_labware_validate,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                forced_fallback = [
                    add_labware_validate._norm(value) for value in values
                ]
        self.assertEqual(forced_fallback, expected)

    def test_has_lid_boolean_uses_native_ascii_parser_and_python_lower_fallback(self):
        values = ["true", "TRUE", "tRuE", "False", " truſe ", ""]
        for raw in values:
            with self.subTest(raw=raw):
                payload = (
                    "<Object><AddLabwareDataV1><LabwareType>Type</LabwareType>"
                    "<LabwareLable>Label</LabwareLable><Location>Site</Location>"
                    f"<HasLid>{raw}</HasLid>"
                    "</AddLabwareDataV1></Object>"
                )
                command = _CommandStub(payload_xml=payload)
                expected = raw.strip().lower() == "true"

                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                    fallback = add_labware_validate.extract_add_labware_fields(command)
                self.assertIsNotNone(fallback)
                assert fallback is not None
                self.assertEqual(fallback.has_lid, expected)

                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                    with patch.object(
                        add_labware_validate,
                        "_native_parse_ascii_bool_text",
                        wraps=native_acceleration.parse_ascii_bool_text,
                    ) as native_call:
                        actual = add_labware_validate.extract_add_labware_fields(command)
                        self.assertEqual(native_call.call_count, 1)
                self.assertEqual(actual, fallback)

                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                    with patch.object(
                        add_labware_validate,
                        "_native_parse_ascii_bool_text",
                        return_value=None,
                    ):
                        unsupported_fallback = add_labware_validate.extract_add_labware_fields(
                            command
                        )
                self.assertEqual(unsupported_fallback, fallback)

    def test_local_name_uses_native_helper_and_preserves_custom_fallback(self):
        tag = "{urn:example}Labware"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = add_labware_validate._local_name(tag)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                add_labware_validate,
                "_native_xml_local_name",
                wraps=native_acceleration.xml_local_name,
            ) as native_call:
                actual = add_labware_validate._local_name(tag)
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "Labware")

        class CustomTag(str):
            def rsplit(self, sep=None, maxsplit=-1):
                return ["", "PythonFallback"]

        custom_tag = CustomTag("{urn:example}ignored")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_expected = add_labware_validate._local_name(custom_tag)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.xml_local_name(custom_tag))
            fallback_actual = add_labware_validate._local_name(custom_tag)
        self.assertEqual(fallback_actual, fallback_expected)
        self.assertEqual(fallback_actual, "PythonFallback")

    def test_placeholder_normalization_uses_native_ascii_and_unicode_fallback(self):
        values = ["TODO", "Fill\tIn", "***", "A-B", "Straße", "東京", " \u001c "]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                add_labware_validate._looks_like_placeholder(value)
                for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                add_labware_validate,
                "_native_ascii_collapse_casefold_whitespace",
                wraps=native_acceleration.ascii_collapse_casefold_whitespace,
            ) as native_call:
                actual = [
                    add_labware_validate._looks_like_placeholder(value)
                    for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(
                native_acceleration.ascii_collapse_casefold_whitespace("東京")
            )
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [True, True, True, False, False, False, True])

    def test_fc_variable_name_validation_uses_native_ascii_pattern(self):
        values = [
            "Name_2",
            "_x",
            "2name",
            "A-B",
            "",
            "Name with space",
            "Name\n",
            "é",
            "ſcript",
        ]

        def outcome(name: str) -> str | None:
            try:
                add_labware_validate._validate_fc_variable_name("field", name)
            except ApiV2ValidationError as error:
                return str(error)
            return None

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [outcome(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                add_labware_validate,
                "_native_ascii_variable_name_is_valid",
                wraps=native_acceleration.ascii_variable_name_is_valid,
            ) as native_call:
                actual = [outcome(value) for value in values]
                self.assertEqual(native_call.call_count, len(values) - 1)
            self.assertIsNone(
                native_acceleration.ascii_variable_name_is_valid("ſcript")
            )
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0:2], [None, None])
        self.assertTrue(all(message is not None for message in actual[2:]))

    def test_bracket_variable_names_use_shared_native_scanner(self):
        values = [
            "[A] [b_2] [A] [] [_x] [9bad] [with-dash] [[nested]] [é] [Aé]",
            "before [name9] after",
            "unicode [é] [Aé] [okay_1]",
            "",
            None,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                add_labware_validate._bracket_variable_names(value)
                for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                add_labware_validate,
                "_native_bracket_variable_names",
                wraps=native_acceleration.bracket_variable_names,
            ) as native_call:
                actual = [
                    add_labware_validate._bracket_variable_names(value)
                    for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0], {"A", "b_2", "_x", "nested"})
        self.assertEqual(actual[1], {"name9"})
        self.assertEqual(actual[2], {"okay_1"})
        self.assertEqual(actual[3:], [set(), set()])

    def test_add_labware_descendant_lookup_uses_native_selector_and_fallback(self):
        command = _CommandStub(
            payload_xml=(
                '<Root xmlns:x="urn:test"><x:AddLabwareDataV1>'
                "<x:LabwareType>first type</x:LabwareType>"
                "<x:LabwareLable>First</x:LabwareLable>"
                "<x:Location>Nest</x:Location><x:Position>2</x:Position>"
                "</x:AddLabwareDataV1><AddLabwareDataV1>"
                "<LabwareType>second type</LabwareType></AddLabwareDataV1></Root>"
            )
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = add_labware_validate.extract_add_labware_fields(command)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                add_labware_validate,
                "_native_xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as native_call:
                actual = add_labware_validate.extract_add_labware_fields(command)
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertIsNotNone(actual)
        assert actual is not None
        self.assertEqual(actual.labware_type, "first type")
        self.assertEqual(actual.labware_label, "First")

        class StringTag(str):
            pass

        root = add_labware_validate.ET.Element("Root")
        target = add_labware_validate.ET.SubElement(
            root, StringTag("AddLabwareDataV1")
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_target = add_labware_validate._find_descendant(
                root, "AddLabwareDataV1"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_first_element_by_name(
                    root, "AddLabwareDataV1", add_labware_validate.ET.Element
                )
            )
            actual_target = add_labware_validate._find_descendant(
                root, "AddLabwareDataV1"
            )
        self.assertIs(actual_target, expected_target)
        self.assertIs(actual_target, target)

    def test_child_text_uses_native_direct_selector_and_fallback(self):
        root = add_labware_validate.ET.fromstring(
            '<Root xmlns:x="urn:test"><x:Name> </x:Name><Name>later</Name></Root>'
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = add_labware_validate._child_text(root, "Name", "default")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                add_labware_validate,
                "_native_xml_first_direct_child_by_name",
                wraps=native_acceleration.xml_first_direct_child_by_name,
            ) as native_call:
                actual = add_labware_validate._child_text(root, "Name", "default")
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "")
        self.assertEqual(
            add_labware_validate._child_text(root, "Missing", "default"), "default"
        )
        self.assertEqual(
            add_labware_validate._child_text(None, "Name", "default"), "default"
        )

        class StringTag(str):
            pass

        custom_root = add_labware_validate.ET.Element("Root")
        custom_child = add_labware_validate.ET.SubElement(
            custom_root, StringTag("Name")
        )
        custom_child.text = "Custom"
        add_labware_validate.ET.SubElement(custom_root, "Name").text = "Later"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_expected = add_labware_validate._child_text(
                custom_root, "Name", "default"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_first_direct_child_by_name(
                    custom_root, "Name", add_labware_validate.ET.Element
                )
            )
            fallback_actual = add_labware_validate._child_text(
                custom_root, "Name", "default"
            )
        self.assertEqual(fallback_actual, fallback_expected)
        self.assertEqual(fallback_actual, "Custom")

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
            labware_type="TBD",
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
        result = validate_add_labware_fields(
            fields, check_bracket_variables=True, declared_variables=set()
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "undeclared_variable")

    def test_declared_bracket_variable_in_label_passes(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="Plate_[id]",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(
            fields, check_bracket_variables=True, declared_variables={"id"}
        )
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
        result = validate_add_labware_fields(
            fields, check_bracket_variables=True, declared_variables=set()
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.field, "labware_type")

    def test_declared_bracket_variable_in_labware_type_passes(self):
        fields = AddLabwareFields(
            labware_type="[type_var]",
            labware_label="Plate1",
            location="NestPlatform",
            site=1,
        )
        result = validate_add_labware_fields(
            fields, check_bracket_variables=True, declared_variables={"type_var"}
        )
        self.assertTrue(result.ok)

    def test_placeholder_labware_label_fails(self):
        fields = AddLabwareFields(
            labware_type="96 Well Flat",
            labware_label="TBD",
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
        self.assertEqual(
            result.message, "No AddLabware payload available for offline validation."
        )

    def test_offline_invalid_payload(self):
        result = validate_add_labware_offline(
            _CommandStub(payload_xml="<Object>Invalid XML</Object")
        )
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
                        "catalog": "[UNDECLARED_CAT]",
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
                        "catalog": "96 Well Flat",
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
            ],
        }

        failures = validate_add_labware_ir_steps(
            ir, declared_variables={"PRE_DECLARED"}
        )
        self.assertEqual(len(failures), 4)

        # Test missing catalog in bracket (results in "api_v2_validate_rejected")
        self.assertEqual(failures[0].reason, "api_v2_validate_rejected")
        self.assertIn("UNDECLARED_CAT", failures[0].message)

        # Test missing label in bracket (results in "undeclared_variable")
        self.assertEqual(failures[1].reason, "undeclared_variable")
        self.assertIn("UNDECLARED_LBL", failures[1].message)

        self.assertEqual(failures[2].reason, "duplicate_labware_label")
        self.assertEqual(failures[2].field, "labware_label")

        self.assertEqual(failures[3].reason, "occupied_slot")
        self.assertEqual(failures[3].field, "site")

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

    def test_add_labware_fields_as_dict_alt(self):
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

    def test_add_labware_validate_result_as_dict_minimal(self):
        from fluent_pipeline.api_v2_add_labware_validate import (
            AddLabwareValidateResult,
            API_V2_METHOD,
            API_V2_ISSUE_ID,
        )

        result = AddLabwareValidateResult(ok=True)
        expected = {
            "ok": True,
            "source": "offline",
            "api_v2_method": API_V2_METHOD,
            "api_v2_issue": API_V2_ISSUE_ID,
        }
        self.assertEqual(result.as_dict(), expected)

    def test_add_labware_validate_result_as_dict_full(self):
        from fluent_pipeline.api_v2_add_labware_validate import (
            AddLabwareValidateResult,
            API_V2_METHOD,
            API_V2_ISSUE_ID,
        )

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
