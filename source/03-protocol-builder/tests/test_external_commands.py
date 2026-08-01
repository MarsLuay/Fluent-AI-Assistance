from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.external_commands import inspect_external_command


class ExternalCommandContractTests(unittest.TestCase):
    def test_captures_macro_variables_assignment_conditions_and_wait_pair(self) -> None:
        xscr = """\
<Script>
  <VariableDefinitionHelper><Name>a200startwell</Name><TypeName>Floating Point</TypeName><Scope>Script</Scope><Values><string>0</string></Values></VariableDefinitionHelper>
  <VariableDefinitionHelper><Name>a200endwell</Name><TypeName>Floating Point</TypeName><Scope>Script</Scope><Values><string>0</string></Values></VariableDefinitionHelper>
  <VariableDefinitionHelper><Name>NumSourceTubes</Name><TypeName>Integer</TypeName><Scope>Script</Scope><Values><string>8</string></Values></VariableDefinitionHelper>
  <ConditionalGroup>
    <Condition>NumSourceTubes &lt; 5</Condition>
    <SetVariableStatement><Name>a200startwell</Name><Value>41</Value><LineNumber>5</LineNumber></SetVariableStatement>
  </ConditionalGroup>
  <SetVariableStatement><Name>a200endwell</Name><Value>48</Value><LineNumber>8</LineNumber></SetVariableStatement>
  <LegacyDriverMacro Name="ResolvexA200_Run" ModuleName="ResolvexA200" ExecutionTime="PT2S" IsDisabledForExecution="false" LineNumber="107">
    <ExecutionSettings>SPE 4,~a200startwell~,~a200endwell~,0</ExecutionSettings>
  </LegacyDriverMacro>
  <LegacyDriverMacro Name="ResolvexA200_WaitFinished" ModuleName="ResolvexA200" ExecutionTime="PT2S" IsDisabledForExecution="false" LineNumber="109">
    <ExecutionSettings>3600</ExecutionSettings>
  </LegacyDriverMacro>
</Script>
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.xscr"
            source.write_text(xscr, encoding="utf-8")
            report = inspect_external_command(
                {
                    "scripts": [
                        {
                            "object_name": "Demo_Incubator_Script_3_v1.7",
                            "extracted_path": "source.xscr",
                        }
                    ]
                },
                context_root=root,
                command_name="ResolvexA200_Run",
                module_name="ResolvexA200",
                source_script="Demo_Incubator_Script_3_v1.7",
            )

        self.assertEqual(report["match_count"], 1)
        match = report["matches"][0]
        self.assertEqual(match["referenced_variables"], ["a200startwell", "a200endwell"])
        self.assertEqual(
            match["dependency_variables"],
            ["a200startwell", "a200endwell", "NumSourceTubes"],
        )
        self.assertEqual(match["following_companion"]["name"], "ResolvexA200_WaitFinished")
        self.assertEqual(match["following_companion"]["execution_settings"], "3600")
        start = next(item for item in match["variable_declarations"] if item["name"] == "a200startwell")
        self.assertEqual(start["assignments"][0]["condition"], "NumSourceTubes < 5")


if __name__ == "__main__":
    unittest.main()
