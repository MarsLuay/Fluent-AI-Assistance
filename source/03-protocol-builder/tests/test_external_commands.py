from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.external_commands import (
    build_driver_usage_contracts,
    fingerprint_driver_usage_contract,
    inspect_external_command,
    select_driver_usage_contract,
    DriverCommandContractAmbiguityError,
)


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
        self.assertTrue(str(match["contract_id"]).startswith("dcc_"))
        self.assertEqual(match["referenced_variables"], ["a200startwell", "a200endwell"])
        self.assertEqual(
            match["dependency_variables"],
            ["a200startwell", "a200endwell", "NumSourceTubes"],
        )
        self.assertEqual(match["following_companion"]["name"], "ResolvexA200_WaitFinished")
        self.assertEqual(match["following_companion"]["execution_settings"], "3600")
        start = next(item for item in match["variable_declarations"] if item["name"] == "a200startwell")
        self.assertEqual(start["assignments"][0]["condition"], "NumSourceTubes < 5")
        self.assertEqual(match["source_path"], "source.xscr")

    def test_dynamic_output_path_variable_deps_and_distinct_usages(self) -> None:
        xscr = """\
<Script>
  <VariableDefinitionHelper><Name>OutputDir</Name><TypeName>File</TypeName><Scope>Script</Scope><Values><string></string></Values></VariableDefinitionHelper>
  <VariableDefinitionHelper><Name>RunStamp</Name><TypeName>String</TypeName><Scope>Script</Scope><Values><string>run</string></Values></VariableDefinitionHelper>
  <SetVariableStatement><Name>OutputDir</Name><Value>~RunStamp~\\results</Value><LineNumber>3</LineNumber></SetVariableStatement>
  <ApplicationDriverMacro Name="Vendor_Export" ModuleName="VendorDevice" ExecutionTime="PT2S" IsDisabledForExecution="false" LineNumber="10">
    <ExecutionSettings>&amp;lt;ExportParams&amp;gt;&amp;lt;OutputPath&amp;gt;~OutputDir~\\plate.csv&amp;lt;/OutputPath&amp;gt;&amp;lt;/ExportParams&amp;gt;</ExecutionSettings>
  </ApplicationDriverMacro>
  <LegacyDriverMacro Name="Vendor_WaitFinished" ModuleName="VendorDevice" ExecutionTime="PT2S" IsDisabledForExecution="false" LineNumber="11">
    <ExecutionSettings>120</ExecutionSettings>
  </LegacyDriverMacro>
  <ApplicationDriverMacro Name="Vendor_Export" ModuleName="VendorDevice" ExecutionTime="PT5S" IsDisabledForExecution="false" LineNumber="20">
    <ExecutionSettings>&amp;lt;ExportParams&amp;gt;&amp;lt;OutputPath&amp;gt;C:\\Static\\plate.csv&amp;lt;/OutputPath&amp;gt;&amp;lt;/ExportParams&amp;gt;</ExecutionSettings>
  </ApplicationDriverMacro>
</Script>
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source.xscr").write_text(xscr, encoding="utf-8")
            manifest = {
                "scripts": [
                    {"object_name": "SyntheticExport", "extracted_path": "source.xscr"}
                ]
            }
            contracts = build_driver_usage_contracts(
                manifest,
                context_root=root,
                command_name="Vendor_Export",
                module_name="VendorDevice",
            )
            again = build_driver_usage_contracts(
                manifest,
                context_root=root,
                command_name="Vendor_Export",
                module_name="VendorDevice",
            )
            self.assertEqual(
                [item["contract_id"] for item in contracts],
                [item["contract_id"] for item in again],
            )

        self.assertEqual(len(contracts), 2)
        dynamic, static = contracts
        self.assertIn("~OutputDir~", dynamic["execution_settings"])
        self.assertEqual(dynamic["referenced_variables"], ["OutputDir"])
        self.assertEqual(dynamic["dependency_variables"], ["OutputDir", "RunStamp"])
        output_decl = next(
            item for item in dynamic["variable_declarations"] if item["name"] == "OutputDir"
        )
        self.assertEqual(output_decl["type"], "File")
        self.assertEqual(output_decl["scope"], "Script")
        self.assertNotEqual(dynamic["contract_id"], static["contract_id"])
        self.assertEqual(
            fingerprint_driver_usage_contract(dynamic),
            dynamic["contract_id"],
        )
        selected = select_driver_usage_contract(
            contracts,
            macro_name="Vendor_Export",
            module_name="VendorDevice",
            contract_id=dynamic["contract_id"],
        )
        self.assertEqual(selected["contract_id"], dynamic["contract_id"])
        with self.assertRaises(DriverCommandContractAmbiguityError) as raised:
            select_driver_usage_contract(
                contracts,
                macro_name="Vendor_Export",
                module_name="VendorDevice",
            )
        self.assertEqual(
            raised.exception.contract_ids,
            [dynamic["contract_id"], static["contract_id"]],
        )
        self.assertEqual(dynamic["following_companion"]["name"], "Vendor_WaitFinished")
        self.assertIsNone(static.get("following_companion"))

    def test_multiple_modules_remain_separate_ordered_source_usages(self) -> None:
        xscr = """\
<Script>
  <ApplicationDriverMacro Name="DeviceA_Initialize" ModuleName="DeviceA" ExecutionTime="PT1S" LineNumber="10">
    <ExecutionSettings>source-a-init</ExecutionSettings>
  </ApplicationDriverMacro>
  <ApplicationDriverMacro Name="DeviceA_Read" ModuleName="DeviceA" ExecutionTime="PT1S" LineNumber="11">
    <ExecutionSettings>source-a-read</ExecutionSettings>
  </ApplicationDriverMacro>
  <LegacyDriverMacro Name="DeviceB_Status" ModuleName="DeviceB" ExecutionTime="PT1S" LineNumber="12">
    <ExecutionSettings>source-b-status</ExecutionSettings>
  </LegacyDriverMacro>
</Script>
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "source.xscr").write_text(xscr, encoding="utf-8")
            contracts = build_driver_usage_contracts(
                {"scripts": [{"object_name": "Synthetic", "extracted_path": "source.xscr"}]},
                context_root=root,
            )

        self.assertEqual(
            [(item["module_name"], item["macro_name"]) for item in contracts],
            [("DeviceA", "DeviceA_Initialize"), ("DeviceA", "DeviceA_Read"), ("DeviceB", "DeviceB_Status")],
        )
        self.assertEqual([item["command_index"] for item in contracts], [0, 1, 2])


if __name__ == "__main__":
    unittest.main()
