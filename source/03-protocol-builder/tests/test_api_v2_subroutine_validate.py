import json
import os
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import fluent_pipeline.api_v2_subroutine_validate as subroutine_validate
import fluent_pipeline.subroutine_dependencies as subroutine_dependencies
from tecan_common import native_acceleration
from fluent_pipeline.api_v2_subroutine_validate import (
    extract_subroutine_path,
    load_runtime_script_inventory,
    resolve_subroutine_against_inventory,
    validate_subroutine_before_execute,
    validate_subroutine_fields,
    validate_subroutines_after_load,
)


@dataclass
class _FakeCommand:
    type_name: str
    index: int
    group: str
    payload_xml: str


_SUBROUTINE_XSCR = """<Object Type="Tecan.Core.Scripting.SubRoutineStatement">
  <SubRoutineStatement>
    <SubRoutine>"Demo\\SUB_Get_Fingers_v1.0"</SubRoutine>
    <Mode><ExecutionMode>JoinSubroutine</ExecutionMode></Mode>
  </SubRoutineStatement>
</Object>"""


class ApiV2SubroutineValidateTests(unittest.TestCase):
    def test_local_name_uses_native_helper_in_selector_fallback(self):
        tag = "{urn:example}Reference"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = subroutine_validate._local_name(tag)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                subroutine_validate,
                "_native_xml_local_name",
                wraps=native_acceleration.xml_local_name,
            ) as native_call:
                actual = subroutine_validate._local_name(tag)
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "Reference")

        class StringTag(str):
            pass

        custom_root = subroutine_validate.ET.Element("Root")
        custom_child = subroutine_validate.ET.SubElement(
            custom_root, StringTag("{urn:custom}Reference")
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_expected = subroutine_validate._find_descendant(
                custom_root, "Reference"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_elements_by_name(
                    custom_root, ("Reference",), subroutine_validate.ET.Element
                )
            )
            with patch.object(
                subroutine_validate,
                "_native_xml_local_name",
                wraps=native_acceleration.xml_local_name,
            ) as fallback_native_call:
                fallback_actual = subroutine_validate._find_descendant(
                    custom_root, "Reference"
                )
                self.assertEqual(fallback_native_call.call_count, 2)
        self.assertIs(fallback_actual, fallback_expected)
        self.assertIs(fallback_actual, custom_child)

    def test_compiled_reference_scans_use_native_selectors_and_fallback(self):
        root = subroutine_dependencies.ET.fromstring(
            '<Root xmlns:x="urn:test"><x:Reference><x:TypeId>Script</x:TypeId>'
            "<x:ObjectName>First</x:ObjectName></x:Reference>"
            "<Reference><TypeId>Other</TypeId><ObjectName>Second</ObjectName>"
            "</Reference></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_refs = subroutine_dependencies._elements_by_local_name(
                root, "Reference"
            )
            expected_text = subroutine_dependencies._first_text(root, "TypeId")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                subroutine_dependencies,
                "_native_xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as elements_call, patch.object(
                subroutine_dependencies,
                "_native_xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as first_call:
                actual_refs = subroutine_dependencies._elements_by_local_name(
                    root, "Reference"
                )
                actual_text = subroutine_dependencies._first_text(root, "TypeId")
                self.assertEqual(elements_call.call_count, 1)
                self.assertEqual(first_call.call_count, 1)
        self.assertEqual(actual_refs, expected_refs)
        self.assertEqual(actual_refs, [root[0], root[1]])
        self.assertEqual(actual_text, expected_text)
        self.assertEqual(actual_text, "Script")

        class StringTag(str):
            pass

        custom_root = subroutine_dependencies.ET.Element("Root")
        custom_ref = subroutine_dependencies.ET.SubElement(
            custom_root, StringTag("Reference")
        )
        type_node = subroutine_dependencies.ET.SubElement(
            custom_ref, StringTag("TypeId")
        )
        type_node.text = "Fallback"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_refs = subroutine_dependencies._elements_by_local_name(
                custom_root, "Reference"
            )
            fallback_text = subroutine_dependencies._first_text(custom_root, "TypeId")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_elements_by_name(
                    custom_root, ("Reference",), subroutine_dependencies.ET.Element
                )
            )
            self.assertIsNone(
                native_acceleration.xml_first_element_by_name(
                    custom_root, "TypeId", subroutine_dependencies.ET.Element
                )
            )
            actual_fallback_refs = subroutine_dependencies._elements_by_local_name(
                custom_root, "Reference"
            )
            actual_fallback_text = subroutine_dependencies._first_text(
                custom_root, "TypeId"
            )
        self.assertEqual(actual_fallback_refs, fallback_refs)
        self.assertEqual(actual_fallback_refs, [custom_ref])
        self.assertEqual(actual_fallback_text, fallback_text)
        self.assertEqual(actual_fallback_text, "Fallback")

    def test_validate_subroutine_fields_requires_folder_prefix(self):
        result = validate_subroutine_fields("BareName")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "field_validate_failed")

    def test_extract_subroutine_path_from_payload(self):
        command = _FakeCommand(
            type_name="SubRoutineStatement",
            index=0,
            group="Checks",
            payload_xml=_SUBROUTINE_XSCR,
        )
        self.assertEqual(extract_subroutine_path(command), "Demo\\SUB_Get_Fingers_v1.0")

    def test_extract_subroutine_path_uses_native_selector_and_fallback(self):
        command = _FakeCommand(
            type_name="SubRoutineStatement",
            index=0,
            group="Checks",
            payload_xml=(
                '<Root xmlns:x="urn:test"><x:SubRoutine>Folder\\First</x:SubRoutine>'
                "<SubRoutine>Folder\\Second</SubRoutine></Root>"
            ),
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = extract_subroutine_path(command)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                subroutine_validate,
                "_native_xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as native_call:
                actual = extract_subroutine_path(command)
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "Folder\\First")

        class StringTag(str):
            pass

        root = subroutine_validate.ET.Element("Root")
        tagged = subroutine_validate.ET.SubElement(root, StringTag("SubRoutine"))
        tagged.text = "Folder\\Fallback"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_element = subroutine_validate._find_descendant(root, "SubRoutine")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_first_element_by_name(
                    root, "SubRoutine", subroutine_validate.ET.Element
                )
            )
            actual_element = subroutine_validate._find_descendant(root, "SubRoutine")
        self.assertIs(actual_element, expected_element)
        self.assertIs(actual_element, tagged)

    def test_resolve_missing_on_worktable_inventory(self):
        result = resolve_subroutine_against_inventory(
            "Demo\\SUB_Missing",
            [{"object_name": "SUB_Other", "qualified_name": "Demo\\SUB_Other"}],
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "subroutine_missing_on_worktable")

    def test_resolve_unique_match_on_inventory(self):
        inventory = [
            {
                "object_name": "SUB_Get_Fingers_v1.0",
                "qualified_name": "Demo\\SUB_Get_Fingers_v1.0",
                "guid": "abc-123",
            }
        ]
        result = resolve_subroutine_against_inventory("Demo\\SUB_Get_Fingers_v1.0", inventory)
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "inventory")

    def test_validate_before_execute_skips_non_subroutine(self):
        command = _FakeCommand(
            type_name="UserPromptStatement",
            index=0,
            group="G",
            payload_xml="",
        )
        result = validate_subroutine_before_execute(command)
        self.assertTrue(result.ok)
        self.assertEqual(result.source, "skipped_non_subroutine")

    def test_validate_before_execute_fails_when_missing_on_inventory(self):
        command = _FakeCommand(
            type_name="SubRoutineStatement",
            index=1,
            group="Arm",
            payload_xml=_SUBROUTINE_XSCR,
        )
        result = validate_subroutine_before_execute(
            command,
            runtime_inventory=[{"object_name": "SUB_Other"}],
        )
        self.assertFalse(result.ok)
        self.assertIn("worktable", result.message)

    def test_batch_after_load_reports_missing(self):
        command = _FakeCommand(
            type_name="SubRoutineStatement",
            index=0,
            group="Arm",
            payload_xml=_SUBROUTINE_XSCR,
        )
        report = validate_subroutines_after_load(
            commands=[command],
            runtime_inventory=[],
            provider="test",
        )
        self.assertTrue(report.needs_review)
        self.assertEqual(report.call_count, 1)

    def test_load_runtime_script_inventory_from_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(
                json.dumps(
                    {
                        "scripts": [
                            {"object_name": "SUB_Get_Fingers_v1.0", "guid": "g1"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            os.environ["TECAN_FLUENT_RUNTIME_SCRIPT_INVENTORY"] = str(path)
            try:
                inventory = load_runtime_script_inventory()
            finally:
                os.environ.pop("TECAN_FLUENT_RUNTIME_SCRIPT_INVENTORY", None)
            self.assertEqual(len(inventory), 1)
            self.assertEqual(inventory[0]["object_name"], "SUB_Get_Fingers_v1.0")


if __name__ == "__main__":
    unittest.main()
