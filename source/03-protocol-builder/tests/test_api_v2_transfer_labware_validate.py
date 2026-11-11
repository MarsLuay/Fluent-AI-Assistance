import unittest
from unittest.mock import patch

import fluent_pipeline.api_v2_transfer_labware_validate as transfer_validate
from tecan_common import native_acceleration
from fluent_pipeline.api_v2.commands import TransferLabware
from fluent_pipeline.api_v2.types import ApiV2ValidationError
from fluent_pipeline.api_v2_transfer_labware_validate import (
    TransferLabwareFields,
    extract_transfer_labware_fields,
    record_successful_transfer,
    validate_transfer_labware_before_execute,
    validate_transfer_labware_fields,
)


_TRANSFER_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">
  <ApplicationDriverMacro Version="1" Name="RGA1_TransferLabware" ModuleName="RGA1">
    <ExecutionSettings>&amp;lt;TransferLabwareCommandParameters&amp;gt;&amp;lt;Labware&amp;gt;Plate1&amp;lt;/Labware&amp;gt;&amp;lt;Location&amp;gt;NestPlatform&amp;lt;/Location&amp;gt;&amp;lt;Site&amp;gt;2&amp;lt;/Site&amp;gt;&amp;lt;MoveToBase&amp;gt;false&amp;lt;/MoveToBase&amp;gt;&amp;lt;FixedSite&amp;gt;true&amp;lt;/FixedSite&amp;gt;&amp;lt;/TransferLabwareCommandParameters&amp;gt;</ExecutionSettings>
  </ApplicationDriverMacro>
</Object>
"""


class _CommandStub:
    def __init__(self, *, type_name: str, payload_xml: str, index: int = 0, group: str = "Moves"):
        self.type_name = type_name
        self.payload_xml = payload_xml
        self.index = index
        self.group = group


class TransferLabwareValidateTests(unittest.TestCase):
    def test_extract_transfer_fields_from_xscr_payload(self):
        command = _CommandStub(type_name="ApplicationDriverMacro", payload_xml=_TRANSFER_XSCR)
        fields = extract_transfer_labware_fields(command)
        self.assertIsNotNone(fields)
        assert fields is not None
        self.assertEqual(fields.labware, "Plate1")
        self.assertEqual(fields.location, "NestPlatform")
        self.assertEqual(fields.site, "2")

    def test_child_text_uses_native_direct_selector_and_python_fallback(self):
        root = transfer_validate.ET.fromstring(
            '<Root xmlns:x="urn:test"><x:Name> </x:Name><Name>later</Name></Root>'
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = transfer_validate._child_text(root, "Name", "default")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                transfer_validate,
                "_native_xml_first_direct_child_by_name",
                wraps=native_acceleration.xml_first_direct_child_by_name,
            ) as native_call:
                actual = transfer_validate._child_text(root, "Name", "default")
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "")
        self.assertEqual(
            transfer_validate._child_text(root, "Missing", "default"), "default"
        )
        self.assertEqual(
            transfer_validate._child_text(None, "Name", "default"), "default"
        )

        class StringTag(str):
            pass

        custom_root = transfer_validate.ET.Element("Root")
        custom_child = transfer_validate.ET.SubElement(custom_root, StringTag("Name"))
        custom_child.text = "Custom"
        transfer_validate.ET.SubElement(custom_root, "Name").text = "Later"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_expected = transfer_validate._child_text(
                custom_root, "Name", "default"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_first_direct_child_by_name(
                    custom_root, "Name", transfer_validate.ET.Element
                )
            )
            fallback_actual = transfer_validate._child_text(
                custom_root, "Name", "default"
            )
        self.assertEqual(fallback_actual, fallback_expected)
        self.assertEqual(fallback_actual, "Custom")

    def test_transfer_macro_lookup_uses_native_selector_and_python_fallback(self):
        command = _CommandStub(
            type_name="ApplicationDriverMacro",
            payload_xml=(
                '<Root xmlns:x="urn:test"><x:ApplicationDriverMacro>'
                "<Name>RGA1_TransferLabware</Name>"
                "<ExecutionSettings>&amp;lt;TransferLabwareCommandParameters&amp;gt;"
                "&amp;lt;Labware&amp;gt;First&amp;lt;/Labware&amp;gt;"
                "&amp;lt;/TransferLabwareCommandParameters&amp;gt;</ExecutionSettings>"
                "</x:ApplicationDriverMacro><ApplicationDriverMacro>"
                "<Name>other</Name></ApplicationDriverMacro></Root>"
            ),
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = extract_transfer_labware_fields(command)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                transfer_validate,
                "_native_xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as native_call:
                actual = extract_transfer_labware_fields(command)
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertIsNotNone(actual)
        assert actual is not None
        self.assertEqual(actual.labware, "First")

        class StringTag(str):
            pass

        root = transfer_validate.ET.Element("Root")
        macro = transfer_validate.ET.SubElement(
            root, StringTag("ApplicationDriverMacro")
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_macro = transfer_validate._find_descendant(
                root, "ApplicationDriverMacro"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_first_element_by_name(
                    root, "ApplicationDriverMacro", transfer_validate.ET.Element
                )
            )
            actual_macro = transfer_validate._find_descendant(
                root, "ApplicationDriverMacro"
            )
        self.assertIs(actual_macro, expected_macro)
        self.assertIs(actual_macro, macro)

    def test_rejects_missing_labware_on_deck(self):
        fields = TransferLabwareFields(
            labware="MissingPlate",
            location="NestPlatform",
            site="2",
        )
        result = validate_transfer_labware_fields(fields, deck_labels={"plate1"})
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "labware_not_on_deck")

    def test_rejects_occupied_destination_slot(self):
        fields = TransferLabwareFields(
            labware="Plate1",
            location="NestPlatform",
            site="2",
        )
        result = validate_transfer_labware_fields(
            fields,
            deck_labels={"plate1"},
            deck_slots={"plate1": ("NestPlatform", "1")},
            occupied_slots={("NestPlatform", "1"), ("NestPlatform", "2")},
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "occupied_destination_slot")

    def test_updates_simulated_deck_after_successful_transfer(self):
        fields = TransferLabwareFields(
            labware="Plate1",
            location="NestPlatform",
            site="2",
        )
        deck_labels = {"plate1"}
        deck_slots = {"plate1": ("NestPlatform", "1")}
        occupied = {("NestPlatform", "1")}
        result = validate_transfer_labware_fields(
            fields,
            deck_labels=deck_labels,
            deck_slots=deck_slots,
            occupied_slots=occupied,
        )
        self.assertTrue(result.ok)
        record_successful_transfer(
            fields,
            deck_labels=deck_labels,
            deck_slots=deck_slots,
            occupied_slots=occupied,
        )
        self.assertEqual(deck_slots["plate1"], ("NestPlatform", "2"))
        self.assertIn(("NestPlatform", "2"), occupied)
        self.assertNotIn(("NestPlatform", "1"), occupied)

    def test_typed_transfer_labware_validate_raises_on_empty_labware(self):
        with self.assertRaises(ApiV2ValidationError):
            TransferLabware(labware="", location="NestPlatform", site="1").validate()

    def test_typed_transfer_labware_renders_without_deck_context(self):
        settings = TransferLabware(
            labware="AdapterA200",
            location="Demo_Device_Pos",
            site="1",
            module_name="RGA 1",
        ).to_execution_settings()
        self.assertIn("AdapterA200", settings)
        self.assertIn("Demo_Device_Pos", settings)

    def test_stepped_preflight_skips_non_transfer_commands(self):
        command = _CommandStub(type_name="UserPromptStatement", payload_xml="<Prompt>Ready?</Prompt>")
        result = validate_transfer_labware_before_execute(command)
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "skipped_non_transfer")


if __name__ == "__main__":
    unittest.main()
