from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fluent_pipeline import external_commands as external_commands_module
from tecan_common import native_acceleration
from fluent_pipeline import xml_compat as ET
from fluent_pipeline.external_commands import (
    build_driver_usage_contracts,
    fingerprint_driver_usage_contract,
    inspect_external_command,
    select_driver_usage_contract,
    DriverCommandContractAmbiguityError,
)


class ExternalCommandContractTests(unittest.TestCase):
    def test_xml_selection_helpers_use_native_paths_and_preserve_fallback(self) -> None:
        root = ET.fromstring(
            '<Root xmlns:x="urn:test"><x:LegacyDriverMacro />'
            "<ApplicationDriverMacro /><Other /></Root>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = external_commands_module._elements_by_local_names(
                root, ("LegacyDriverMacro", "ApplicationDriverMacro")
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                external_commands_module,
                "_native_xml_elements_by_name",
                wraps=native_acceleration.xml_elements_by_name,
            ) as elements_call:
                actual = external_commands_module._elements_by_local_names(
                    root, ("LegacyDriverMacro", "ApplicationDriverMacro")
                )
                self.assertEqual(elements_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [root[0], root[1]])

        direct_root = ET.fromstring(
            '<Root xmlns:x="urn:test"><x:Name> </x:Name><Name>later</Name></Root>'
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_text = external_commands_module._child_text(direct_root, "Name")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                external_commands_module,
                "_native_xml_first_direct_child_by_name",
                wraps=native_acceleration.xml_first_direct_child_by_name,
            ) as direct_call:
                actual_text = external_commands_module._child_text(direct_root, "Name")
                self.assertEqual(direct_call.call_count, 1)
        self.assertEqual(actual_text, expected_text)
        self.assertEqual(actual_text, " ")

        class StringTag(str):
            pass

        custom_root = ET.Element("Root")
        custom_macro = ET.SubElement(custom_root, StringTag("LegacyDriverMacro"))
        custom_child = ET.SubElement(custom_macro, StringTag("Name"))
        custom_child.text = "Custom"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_elements = external_commands_module._elements_by_local_names(
                custom_root, ("LegacyDriverMacro",)
            )
            fallback_text = external_commands_module._child_text(custom_macro, "Name")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.xml_elements_by_name(
                    custom_root, ("LegacyDriverMacro",), ET.Element
                )
            )
            self.assertIsNone(
                native_acceleration.xml_first_direct_child_by_name(
                    custom_macro, "Name", ET.Element
                )
            )
            actual_fallback_elements = external_commands_module._elements_by_local_names(
                custom_root, ("LegacyDriverMacro",)
            )
            actual_fallback_text = external_commands_module._child_text(
                custom_macro, "Name"
            )
        self.assertEqual(actual_fallback_elements, fallback_elements)
        self.assertEqual(actual_fallback_elements, [custom_macro])
        self.assertEqual(actual_fallback_text, fallback_text)
        self.assertEqual(actual_fallback_text, "Custom")

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
