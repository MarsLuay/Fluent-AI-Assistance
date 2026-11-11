"""Tests for api-v2-040 Subroutine.ToXML() identity audit."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fluent_pipeline.api_v2.subroutine_identity as subroutine_identity
from fluent_pipeline import xml_compat as ET
from tecan_common import native_acceleration
from fluent_pipeline.api_v2.commands import (
    Subroutine,
    VariableMapping,
    command_from_xscr_object,
    subroutine_from_ir_step,
)
from fluent_pipeline.api_v2.subroutine_identity import (
    audit_subroutine_identity,
    compare_subroutine_step_to_compiled,
    subroutine_identity_summary,
)

SUBROUTINE_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <Reference>
      <Guid>aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee</Guid>
      <TypeId>Script</TypeId>
      <ObjectName>SUB_Get_Fingers_v1.0</ObjectName>
      <ObjectSubfolderPath>Demo</ObjectSubfolderPath>
    </Reference>
    <PayloadData>
      <Script>
        <Commands>
          <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
            <SubRoutineStatement>
              <SubRoutine>"Demo\\SUB_Get_Fingers_v1.0"</SubRoutine>
              <Mode><ExecutionMode>JoinSubroutine</ExecutionMode></Mode>
              <VariableMappingsStart>
                <Object Type="Tecan.Core.Scripting.VariableMapping">
                  <VariableMapping>
                    <Target>FingerSelection</Target>
                    <Source>3</Source>
                  </VariableMapping>
                </Object>
              </VariableMappingsStart>
              <VariableMappingsEnd />
              <LineNumber>4</LineNumber>
            </SubRoutineStatement>
          </Object>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""


class ApiV2SubroutineIdentityTests(unittest.TestCase):
    def test_execution_mode_comparison_uses_native_ascii_and_unicode_fallback(self):
        pairs = [
            ("JoinSubroutine", "JOINSUBROUTINE", True),
            ("Straße", "STRAẞE", True),
            ("ſtart", "START", True),
            ("Parallel", "JoinSubroutine", False),
        ]
        expected = [matched for _, _, matched in pairs]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = [
                subroutine_identity._execution_modes_equal(left, right)
                for left, right, _ in pairs
            ]
        self.assertEqual(disabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                subroutine_identity,
                "_native_ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_call:
                enabled = [
                    subroutine_identity._execution_modes_equal(left, right)
                    for left, right, _ in pairs
                ]
                self.assertEqual(native_call.call_count, len(pairs))
        self.assertEqual(enabled, expected)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                subroutine_identity,
                "_native_ascii_casefold_equals",
                return_value=None,
            ):
                fallback = [
                    subroutine_identity._execution_modes_equal(left, right)
                    for left, right, _ in pairs
                ]
        self.assertEqual(fallback, expected)

    def test_local_name_uses_native_helper_and_custom_string_fallback(self):
        tag = "{urn:example}SubRoutineStatement"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = subroutine_identity._local_name(tag)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                subroutine_identity,
                "_native_xml_local_name",
                wraps=native_acceleration.xml_local_name,
            ) as native_call:
                actual = subroutine_identity._local_name(tag)
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "SubRoutineStatement")

        class CustomTag(str):
            def rsplit(self, sep=None, maxsplit=-1):
                return ["", "PythonFallback"]

        custom_tag = CustomTag("{urn:example}Ignored")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_expected = subroutine_identity._local_name(custom_tag)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.xml_local_name(custom_tag))
            fallback_actual = subroutine_identity._local_name(custom_tag)
        self.assertEqual(fallback_actual, fallback_expected)
        self.assertEqual(fallback_actual, "PythonFallback")

    def test_subroutine_identity_xml_scans_use_native_selector_and_fallback(self):
        root = ET.fromstring(
            '<Root xmlns:x="urn:test">'
            '<x:Object Type="Tecan.SubRoutineStatement" />'
            '<Object Type="Other"><x:SubRoutineStatement /></Object>'
            '<Object Type="Other"><Unrelated /></Object></Root>'
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_objects = subroutine_identity._subroutine_statement_objects(root)
            expected_descendant = subroutine_identity._find_descendant(
                root[1], "SubRoutineStatement"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                subroutine_identity,
                "_native_xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as native_call, patch.object(
                subroutine_identity,
                "_native_xml_first_element_by_name",
                wraps=native_acceleration.xml_first_element_by_name,
            ) as first_call:
                actual_objects = subroutine_identity._subroutine_statement_objects(root)
                actual_descendant = subroutine_identity._find_descendant(
                    root[1], "SubRoutineStatement"
                )
                self.assertEqual(native_call.call_count, 1)
                self.assertEqual(first_call.call_count, 3)
        self.assertEqual(actual_objects, expected_objects)
        self.assertEqual(actual_objects, [root[0], root[1]])
        self.assertIs(actual_descendant, expected_descendant)
        self.assertIs(actual_descendant, root[1][0])

        class StringTag(str):
            pass

        custom_root = ET.Element("Root")
        custom_element = ET.SubElement(custom_root, StringTag("SubRoutineStatement"))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_custom = subroutine_identity._find_descendant(
                custom_root, "SubRoutineStatement"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_first_element_by_name(
                    custom_root, "SubRoutineStatement", ET.Element
                )
            )
            actual_custom = subroutine_identity._find_descendant(
                custom_root, "SubRoutineStatement"
            )
        self.assertIs(actual_custom, expected_custom)
        self.assertIs(actual_custom, custom_element)

    def test_subroutine_child_text_uses_native_selector_and_fallback(self):
        root = ET.fromstring(
            '<Root xmlns:x="urn:test"><x:Name> </x:Name><Name>later</Name>'
            "<Nested><Name>nested</Name></Nested></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = subroutine_identity._child_text(root, "Name", "default")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                subroutine_identity,
                "_native_xml_first_direct_child_by_name",
                wraps=native_acceleration.xml_first_direct_child_by_name,
            ) as native_call:
                actual = subroutine_identity._child_text(root, "Name", "default")
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "")
        self.assertEqual(
            subroutine_identity._child_text(root, "Missing", "default"), "default"
        )
        self.assertEqual(
            subroutine_identity._child_text(None, "Name", "default"), "default"
        )

        class StringTag(str):
            pass

        custom_root = ET.Element("Root")
        custom_child = ET.SubElement(custom_root, StringTag("Name"))
        custom_child.text = "Custom"
        ET.SubElement(custom_root, "Name").text = "later"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_expected = subroutine_identity._child_text(
                custom_root, "Name", "default"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_first_direct_child_by_name(
                    custom_root, "Name", ET.Element
                )
            )
            fallback_actual = subroutine_identity._child_text(
                custom_root, "Name", "default"
            )
        self.assertEqual(fallback_actual, fallback_expected)
        self.assertEqual(fallback_actual, "Custom")

    def test_subroutine_from_ir_step_maps_fields(self):
        command = subroutine_from_ir_step(
            {
                "parameters": {
                    "subroutine": "Demo\\SUB_Get_Fingers_v1.0",
                    "execution_mode": "JoinSubroutine",
                    "variable_mappings_start": [{"target": "FingerSelection", "source": "3"}],
                }
            }
        )
        self.assertIsInstance(command, Subroutine)
        self.assertEqual(command.path, "Demo\\SUB_Get_Fingers_v1.0")
        self.assertEqual(len(command.variable_mappings_start), 1)
        self.assertIn("JoinSubroutine", command.to_xml())

    def test_command_from_xscr_parses_mode_and_mappings(self):
        root = ET.fromstring(SUBROUTINE_XSCR)
        obj = next(el for el in root.iter() if el.attrib.get("Type", "").endswith("SubRoutineStatement"))
        command = command_from_xscr_object(obj, command_id="SubRoutineStatement")
        self.assertIsInstance(command, Subroutine)
        self.assertEqual(command.execution_mode, "JoinSubroutine")
        self.assertEqual(len(command.variable_mappings_start), 1)
        self.assertEqual(command.variable_mappings_start[0].target, "FingerSelection")

    def test_compare_subroutine_step_to_compiled_matches(self):
        root = ET.fromstring(SUBROUTINE_XSCR)
        obj = next(el for el in root.iter() if el.attrib.get("Type", "").endswith("SubRoutineStatement"))
        step = {
            "operation": "call_subroutine",
            "parameters": {
                "subroutine": "Demo\\SUB_Get_Fingers_v1.0",
                "execution_mode": "JoinSubroutine",
                "variable_mappings_start": [{"target": "FingerSelection", "source": "3"}],
            },
        }
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                subroutine_identity,
                "_native_ascii_casefold_equals",
                wraps=native_acceleration.ascii_casefold_equals,
            ) as native_call:
                result = compare_subroutine_step_to_compiled(step, obj)
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(result["status"], "matched")

    def test_audit_subroutine_identity_passes_with_manifest(self):
        ir = {
            "steps": [
                {
                    "index": 1,
                    "operation": "call_subroutine",
                    "parameters": {
                        "subroutine": "Demo\\SUB_Get_Fingers_v1.0",
                        "execution_mode": "JoinSubroutine",
                        "variable_mappings_start": [{"target": "FingerSelection", "source": "3"}],
                    },
                }
            ]
        }
        manifest = {
            "scripts": [
                {
                    "object_name": "SUB_Get_Fingers_v1.0",
                    "folder": "Demo",
                    "guid": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "entry": "Demo/SUB_Get_Fingers_v1.0.xscr",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "compiled.xscr"
            path.write_text(SUBROUTINE_XSCR, encoding="utf-8")
            audit = audit_subroutine_identity(ir, path, manifest)
            summary = subroutine_identity_summary(audit)
            self.assertEqual(audit["status"], "passed")
            self.assertEqual(summary["matched_count"], 1)
            self.assertEqual(summary["reference_issue_count"], 0)

    def test_compare_subroutine_step_to_compiled_ignores_stale_capbc_mapping(self):
        capbc_subroutine = """<?xml version="1.0" encoding="utf-8"?>
<VxData xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared">
  <Payload>
    <ObjectName>SUB_CapBCScanHandeling_50mL_v0.2</ObjectName>
    <PayloadData>
      <Script>
        <Properties>
          <VariableDeclarations>
            <VariableDeclarations>
              <anyType i:type="d3p1:VariableDefinitionHelper">
                <d3p1:Name>InputNumSampleCount</d3p1:Name>
              </anyType>
              <anyType i:type="d3p1:VariableDefinitionHelper">
                <d3p1:Name>capoffset</d3p1:Name>
              </anyType>
            </VariableDeclarations>
          </VariableDeclarations>
        </Properties>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""
        compiled_xscr = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <PayloadData>
      <Script>
        <Commands>
          <Object Type="Tecan.Core.Scripting.SubRoutineStatement">
            <SubRoutineStatement>
              <SubRoutine>"Demo\\SUB_CapBCScanHandeling_50mL_v0.2"</SubRoutine>
              <Mode><ExecutionMode>JoinSubroutine</ExecutionMode></Mode>
              <VariableMappingsStart>
                <Object Type="Tecan.Core.Scripting.VariableMapping">
                  <VariableMapping><Target>InputNumSampleCount</Target><Source>1</Source></VariableMapping>
                </Object>
                <Object Type="Tecan.Core.Scripting.VariableMapping">
                  <VariableMapping><Target>capoffset</Target><Source>0</Source></VariableMapping>
                </Object>
              </VariableMappingsStart>
              <VariableMappingsEnd />
              <LineNumber>35</LineNumber>
            </SubRoutineStatement>
          </Object>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sub_path = tmp_path / "cap.xscr"
            sub_path.write_text(capbc_subroutine, encoding="utf-8")
            compiled_path = tmp_path / "compiled.xscr"
            compiled_path.write_text(compiled_xscr, encoding="utf-8")
            root = ET.fromstring(compiled_xscr)
            obj = next(el for el in root.iter() if el.attrib.get("Type", "").endswith("SubRoutineStatement"))
            step = {
                "operation": "call_subroutine",
                "parameters": {
                    "subroutine": "Demo\\SUB_CapBCScanHandeling_50mL_v0.2",
                    "execution_mode": "JoinSubroutine",
                    "variable_mappings_start": [
                        {"target": "InputNumSampleCount", "source": "1"},
                        {"target": "capholderoffset", "source": "0"},
                        {"target": "capoffset", "source": "0"},
                    ],
                },
            }
            manifest = {
                "scripts": [
                    {
                        "object_name": "SUB_CapBCScanHandeling_50mL_v0.2",
                        "folder": "Demo",
                        "extracted_path": "cap.xscr",
                    }
                ]
            }
            result = compare_subroutine_step_to_compiled(
                step,
                obj,
                source_manifest=manifest,
                context_root=tmp_path,
            )
            self.assertEqual(result["status"], "matched")
            self.assertFalse(result.get("mapping_mismatch"))


if __name__ == "__main__":
    unittest.main()
