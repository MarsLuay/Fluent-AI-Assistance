"""Tests for api-v2-040 Subroutine.ToXML() identity audit."""

from __future__ import annotations

import tempfile
import unittest
from fluent_pipeline import xml_compat as ET
from pathlib import Path

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
        result = compare_subroutine_step_to_compiled(step, obj)
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
