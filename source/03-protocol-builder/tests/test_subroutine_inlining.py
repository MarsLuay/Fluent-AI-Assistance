from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.subroutine_inlining import inline_problem_subroutine_calls


SUB_PROMPT_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<VxData>
  <Payload>
    <ObjectName>SUB_CheckCap</ObjectName>
    <PayloadData>
      <Script>
        <Commands>
          <ScriptGroup>
            <Objects>
              <Object Type="Tecan.Core.Scripting.ScriptGroupDataV1">
                <ScriptGroupDataV1>
                  <Name>Tube checks</Name>
                  <Data>
                    <Statements>
                      <Object Type="Tecan.Core.Scripting.UserPromptStatement">
                        <UserPromptStatement>
                          <Prompt>Confirm the cap handler closes cleanly.</Prompt>
                          <Timeout>0</Timeout>
                          <Data><LineNumber>4</LineNumber></Data>
                        </UserPromptStatement>
                      </Object>
                    </Statements>
                  </Data>
                </ScriptGroupDataV1>
              </Object>
            </Objects>
          </ScriptGroup>
        </Commands>
      </Script>
    </PayloadData>
  </Payload>
</VxData>
"""


SUB_VARIABLE_CONFLICT_XSCR = SUB_PROMPT_XSCR.replace(
    "<PayloadData>",
    """<Properties>
      <VariableDeclarations>
        <VariableDeclarations xmlns:i="http://www.w3.org/2001/XMLSchema-instance" xmlns="http://schemas.datacontract.org/2004/07/Tecan.VisionX.Scripting.Engine">
          <VariableDeclarations xmlns:d2p1="http://schemas.microsoft.com/2003/10/Serialization/Arrays">
            <d2p1:anyType xmlns:d3p1="http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared" i:type="d3p1:VariableDefinitionHelper">
              <d3p1:Name>CaptureBarcode</d3p1:Name>
              <d3p1:Scope>Local</d3p1:Scope>
              <d3p1:TypeName>String</d3p1:TypeName>
            </d2p1:anyType>
          </VariableDeclarations>
        </VariableDeclarations>
      </VariableDeclarations>
    </Properties>
    <PayloadData>""",
)


LEGACY_ONLY_XSCR = """<?xml version="1.0" encoding="utf-8"?>
<Root>
  <Payload><ObjectName>SUB_CheckCap</ObjectName></Payload>
  <Object Type="Tecan.VisionX.LegacyDriver.LegacyDriverMacro">
    <LegacyDriverMacro Version="1" Name="BCRMicro_Read" ModuleName="BCRMicro" LineNumber="37" />
  </Object>
</Root>
"""


class SubroutineInliningTests(unittest.TestCase):
    def _ir(self, subroutine: str = "Demo\\SUB_CheckCap") -> dict:
        return {
            "protocol": {"name": "Verification"},
            "source": {},
            "dependencies": [{"kind": "subroutine", "name": subroutine, "required": True}],
            "steps": [
                {
                    "id": "step_001",
                    "index": 1,
                    "group": "Tube checks",
                    "operation": "call_subroutine",
                    "command_id": "SubRoutineStatement",
                    "parameters": {"subroutine": subroutine, "execution_mode": "JoinSubroutine"},
                }
            ],
        }

    def _manifest(self, path: Path) -> dict:
        return {
            "scripts": [
                {
                    "object_name": "SUB_CheckCap",
                    "extracted_path": str(path),
                    "guid": "11111111-1111-1111-1111-111111111111",
                }
            ]
        }

    def test_safe_subroutine_is_preserved_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub.xscr"
            path.write_text(SUB_PROMPT_XSCR, encoding="utf-8")
            ir = self._ir()

            report = inline_problem_subroutine_calls(ir, self._manifest(path))

        self.assertEqual(report["preserved_count"], 1)
        self.assertEqual([step["operation"] for step in ir["steps"]], ["call_subroutine"])

    def test_always_inline_replaces_subroutine_call_with_local_steps(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub.xscr"
            path.write_text(SUB_PROMPT_XSCR, encoding="utf-8")
            ir = self._ir()

            report = inline_problem_subroutine_calls(ir, self._manifest(path), policy="always_inline")

        self.assertEqual(report["inlined_count"], 1)
        self.assertEqual([step["operation"] for step in ir["steps"]], ["comment", "prompt_user"])
        self.assertNotIn("call_subroutine", [step["operation"] for step in ir["steps"]])
        self.assertFalse(ir["dependencies"])
        self.assertIn("Inlined local copy", ir["steps"][0]["parameters"]["comment"])
        self.assertIn("Confirm the cap handler", ir["steps"][1]["parameters"]["prompt"])

    def test_step_inline_flag_replaces_single_healthy_subroutine(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub.xscr"
            path.write_text(SUB_PROMPT_XSCR, encoding="utf-8")
            ir = self._ir()
            ir["steps"][0]["parameters"]["inline_local"] = True

            report = inline_problem_subroutine_calls(ir, self._manifest(path))

        self.assertEqual(report["inlined"][0]["reason"], "step_requested_local_inline")
        self.assertEqual([step["operation"] for step in ir["steps"]], ["comment", "prompt_user"])

    def test_missing_subroutine_is_replaced_with_local_verification_prompt(self):
        ir = self._ir("Demo\\SUB_Missing")

        report = inline_problem_subroutine_calls(ir, {"scripts": []})

        self.assertEqual(report["fallback_prompt_count"], 1)
        self.assertEqual([step["operation"] for step in ir["steps"]], ["prompt_user"])
        self.assertIn("was not emitted", ir["steps"][0]["parameters"]["prompt"])
        self.assertFalse(ir["dependencies"])

    def test_legacy_driver_subroutine_is_not_kept_as_external_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub.xscr"
            path.write_text(LEGACY_ONLY_XSCR, encoding="utf-8")
            ir = self._ir()

            report = inline_problem_subroutine_calls(ir, self._manifest(path))

        self.assertEqual(report["fallback_prompt_count"], 1)
        self.assertEqual([step["operation"] for step in ir["steps"]], ["prompt_user"])
        self.assertIn("legacy driver macro", report["fallback_prompts"][0]["message"])
        self.assertNotIn("call_subroutine", [step["operation"] for step in ir["steps"]])

    def test_variable_scope_conflict_inlines_subroutine_before_compile(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub.xscr"
            path.write_text(SUB_VARIABLE_CONFLICT_XSCR, encoding="utf-8")
            ir = self._ir()
            ir["variables"] = [{"name": "CaptureBarcode", "scope": "Global", "type": "String"}]

            report = inline_problem_subroutine_calls(ir, self._manifest(path))

        self.assertEqual(report["inlined"][0]["reason"], "subroutine_variable_scope_conflict")
        self.assertEqual(report["inlined"][0]["variable_conflicts"][0]["name"], "CaptureBarcode")
        self.assertNotIn("call_subroutine", [step["operation"] for step in ir["steps"]])

    def test_script_vs_run_scope_conflict_is_reported_as_scope_type_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sub.xscr"
            path.write_text(
                SUB_VARIABLE_CONFLICT_XSCR.replace(
                    "<d3p1:Scope>Local</d3p1:Scope>",
                    "<d3p1:Scope>Run</d3p1:Scope>",
                ),
                encoding="utf-8",
            )
            ir = self._ir()
            ir["variables"] = [{"name": "CaptureBarcode", "scope": "Script", "type": "String"}]

            report = inline_problem_subroutine_calls(ir, self._manifest(path))

        conflict = report["inlined"][0]["variable_conflicts"][0]
        self.assertEqual(report["inlined"][0]["reason"], "subroutine_variable_scope_conflict")
        self.assertEqual(conflict["main_scope"], "Script")
        self.assertEqual(conflict["sub_scope"], "Run")
        self.assertNotIn("call_subroutine", [step["operation"] for step in ir["steps"]])


if __name__ == "__main__":
    unittest.main()
