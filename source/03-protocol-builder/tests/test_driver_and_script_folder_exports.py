"""Tests for driver_macros + driver_command_contracts + script_folder_bindings ZEIA mining."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fluent_pipeline.driver_macros_export as driver_macros_export_module
from tecan_common import native_acceleration
from fluent_pipeline.driver_macros_export import (
    DRIVER_COMMAND_CONTRACTS_SCHEMA_VERSION,
    DRIVER_MACROS_SCHEMA_VERSION,
    build_driver_command_contracts,
    build_driver_macros_catalog,
)
import fluent_pipeline.script_folder_bindings_export as script_folder_bindings_export_module
from fluent_pipeline.script_folder_bindings_export import build_script_folder_bindings


class DriverMacrosExportTests(unittest.TestCase):
    def test_local_name_uses_native_helper_and_custom_fallback(self) -> None:
        tag = "{urn:example}LegacyDriverMacro"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = driver_macros_export_module._local_name(tag)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                driver_macros_export_module,
                "_native_xml_local_name",
                wraps=native_acceleration.xml_local_name,
            ) as native_call:
                actual = driver_macros_export_module._local_name(tag)
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "LegacyDriverMacro")

        class CustomTag(str):
            def rsplit(self, sep=None, maxsplit=-1):
                return ["", "PythonFallback"]

        custom_tag = CustomTag("{urn:example}Ignored")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_expected = driver_macros_export_module._local_name(custom_tag)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.xml_local_name(custom_tag))
            fallback_actual = driver_macros_export_module._local_name(custom_tag)
        self.assertEqual(fallback_actual, fallback_expected)
        self.assertEqual(fallback_actual, "PythonFallback")

    def test_xml_text_trimming_uses_native_unicode_and_custom_fallback(self) -> None:
        class TextNode:
            def __init__(self, text):
                self.text = text

        nodes = [
            TextNode("  value \t"),
            TextNode("\u2003value\u00a0"),
            TextNode("\u2003"),
            TextNode(None),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [driver_macros_export_module._text(node) for node in nodes]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                driver_macros_export_module,
                "_native_xml_clean_text",
                wraps=native_acceleration.xml_clean_text,
            ) as native_call:
                actual = [driver_macros_export_module._text(node) for node in nodes]
                self.assertEqual(native_call.call_count, 3)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["value", "value", "", ""])

        class CustomText(str):
            def strip(self, *args, **kwargs):
                return "python-custom-strip"

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                driver_macros_export_module,
                "_native_xml_clean_text",
                wraps=native_acceleration.xml_clean_text,
            ) as native_call:
                self.assertEqual(
                    driver_macros_export_module._text(TextNode(CustomText(" raw "))),
                    "python-custom-strip",
                )
                self.assertEqual(native_call.call_count, 1)

    def test_child_text_uses_native_first_direct_child_and_custom_fallback(self) -> None:
        root = driver_macros_export_module.ET.Element("Root")
        root.append(driver_macros_export_module.ET.Comment("skip non-element tag"))
        first = driver_macros_export_module.ET.SubElement(root, "{urn:test}Value")
        first.text = "  "
        second = driver_macros_export_module.ET.SubElement(root, "Value")
        second.text = "later value"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = driver_macros_export_module._child_text(root, "Value")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                driver_macros_export_module,
                "_native_xml_first_direct_child_by_name",
                wraps=native_acceleration.xml_first_direct_child_by_name,
            ) as native_call:
                actual = driver_macros_export_module._child_text(root, "Value")
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, "")

        class CustomTag(str):
            def rsplit(self, sep=None, maxsplit=-1):
                return ["", "Value"]

        custom_root = driver_macros_export_module.ET.Element("Root")
        custom_child = driver_macros_export_module.ET.SubElement(custom_root, "Other")
        custom_child.tag = CustomTag("{urn:test}Ignored")
        custom_child.text = " custom value "
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback_expected = driver_macros_export_module._child_text(custom_root, "Value")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                driver_macros_export_module,
                "_native_xml_first_direct_child_by_name",
                wraps=native_acceleration.xml_first_direct_child_by_name,
            ) as native_call:
                fallback_actual = driver_macros_export_module._child_text(custom_root, "Value")
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(fallback_actual, fallback_expected)
        self.assertEqual(fallback_actual, "custom value")

    def test_driver_object_path_hint_scan_uses_ascii_native_and_unicode_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entries = (
                "bundle/applicationdriver.xml",
                "bundle/applicationdriverſ.xml",
                "bundle/ordinary.xml",
            )
            for entry in entries:
                path = root / entry
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")
            manifest = {
                "extracted_dir": str(root),
                "objects": [{"kind": "misc", "entry": entry} for entry in entries],
            }
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                expected = driver_macros_export_module._driver_object_paths(manifest, None)
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                with patch.object(
                    driver_macros_export_module,
                    "_native_ascii_substring_match_groups",
                    wraps=native_acceleration.ascii_case_insensitive_substring_match_groups,
                ) as native_call:
                    actual = driver_macros_export_module._driver_object_paths(manifest, None)
                    self.assertEqual(native_call.call_count, len(entries))
                self.assertIsNone(
                    native_acceleration.ascii_case_insensitive_substring_match_groups(
                        ["misc applicationdriverſ.xml"],
                        [tuple(driver_macros_export_module._DRIVER_OBJECT_HINTS)],
                    )
                )
        self.assertEqual(actual, expected)
        self.assertEqual(
            [path.name for path in actual],
            ["applicationdriver.xml", "applicationdriverſ.xml"],
        )

    def test_mines_legacy_driver_macro_from_xscr(self) -> None:
        xml = """<?xml version="1.0"?>
<root>
  <Object Type="Tecan.VisionX.ApplicationDriver.LegacyDriverMacro">
    <LegacyDriverMacro Name="Demo_Run" ModuleName="DemoModule" />
  </Object>
  <Object Type="Tecan.VisionX.ApplicationDriver.LegacyDriverMacro">
    <LegacyDriverMacro Name="Demo_WaitFinished" ModuleName="DemoModule" />
  </Object>
</root>
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.xscr"
            path.write_text(xml, encoding="utf-8")
            catalog = build_driver_macros_catalog(
                manifest={"scripts": [{"object_name": "Demo", "extracted_path": str(path)}]},
            )
        names = {(e["macro_name"], e["module_name"]) for e in catalog["entries"]}
        self.assertIn(("Demo_Run", "DemoModule"), names)
        self.assertIn(("Demo_WaitFinished", "DemoModule"), names)
        self.assertEqual(catalog["schema_version"], DRIVER_MACROS_SCHEMA_VERSION)

    def test_catalog_keeps_distinct_recovery_policies(self) -> None:
        xml = """<root>
  <ApplicationDriverMacro Name="Demo_Run" ModuleName="DemoModule">
    <ErrorHandling><Action>Retry</Action><MaxRetries>1</MaxRetries></ErrorHandling>
  </ApplicationDriverMacro>
  <ApplicationDriverMacro Name="Demo_Run" ModuleName="DemoModule">
    <ErrorHandling><Action>Stop</Action></ErrorHandling>
  </ApplicationDriverMacro>
</root>"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "script.xscr"
            path.write_text(xml, encoding="utf-8")
            catalog = build_driver_macros_catalog(
                manifest={"scripts": [{"object_name": "Demo", "extracted_path": str(path)}]},
            )
        assert catalog["entry_count"] == 2
        assert {entry["recovery_policy"]["action"] for entry in catalog["entries"]} == {"Retry", "Stop"}

    def test_empty_when_no_macros(self) -> None:
        catalog = build_driver_macros_catalog(manifest={"scripts": []})
        self.assertEqual(catalog["entry_count"], 0)
        self.assertEqual(catalog["entries"], [])

    def test_command_contracts_keep_distinct_usages_and_variables(self) -> None:
        xml = """<?xml version="1.0"?>
<Script>
  <VariableDefinitionHelper><Name>OutputDir</Name><TypeName>File</TypeName><Scope>Script</Scope><Values><string></string></Values></VariableDefinitionHelper>
  <LegacyDriverMacro Name="Demo_Run" ModuleName="DemoModule" ExecutionTime="PT2S" IsDisabledForExecution="false" LineNumber="1">
    <ExecutionSettings>EXPORT,~OutputDir~\\a.csv</ExecutionSettings>
  </LegacyDriverMacro>
  <LegacyDriverMacro Name="Demo_Run" ModuleName="DemoModule" ExecutionTime="PT2S" IsDisabledForExecution="false" LineNumber="2">
    <ExecutionSettings>EXPORT,static.csv</ExecutionSettings>
  </LegacyDriverMacro>
</Script>
"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "script.xscr"
            path.write_text(xml, encoding="utf-8")
            shallow = build_driver_macros_catalog(
                manifest={"scripts": [{"object_name": "Demo", "extracted_path": "script.xscr"}]},
                context_root=root,
            )
            contracts = build_driver_command_contracts(
                manifest={"scripts": [{"object_name": "Demo", "extracted_path": "script.xscr"}]},
                context_root=root,
            )

        # Shallow v1 still collapses by macro/module/kind for inventory consumers.
        self.assertEqual(shallow["schema_version"], DRIVER_MACROS_SCHEMA_VERSION)
        self.assertEqual(shallow["entry_count"], 1)

        self.assertEqual(contracts["schema_version"], DRIVER_COMMAND_CONTRACTS_SCHEMA_VERSION)
        self.assertEqual(contracts["usage_count"], 2)
        settings = {usage["execution_settings"] for usage in contracts["usages"]}
        self.assertIn("EXPORT,~OutputDir~\\a.csv", settings)
        self.assertIn("EXPORT,static.csv", settings)
        dynamic = next(u for u in contracts["usages"] if "~OutputDir~" in u["execution_settings"])
        self.assertEqual(dynamic["referenced_variables"], ["OutputDir"])
        self.assertEqual(len(contracts["ambiguity_groups"]), 1)
        self.assertEqual(contracts["ambiguity_groups"][0]["macro_name"], "Demo_Run")

    def test_datastore_inventory_distinct_from_script_usages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "script.xscr"
            script.write_text(
                """<?xml version="1.0"?>
<Script>
  <LegacyDriverMacro Name="Demo_Run" ModuleName="DemoModule" ExecutionTime="PT2S" LineNumber="1">
    <ExecutionSettings>RUN</ExecutionSettings>
  </LegacyDriverMacro>
</Script>
""",
                encoding="utf-8",
            )
            drivers = root / "ApplicationDrivers"
            drivers.mkdir()
            driver_obj = drivers / "inventory.xml"
            driver_obj.write_text(
                """<?xml version="1.0"?>
<root>
  <LegacyDriverMacro Name="InventoryOnly" ModuleName="InvModule" />
</root>
""",
                encoding="utf-8",
            )
            contracts = build_driver_command_contracts(
                manifest={"scripts": [{"object_name": "Demo", "extracted_path": "script.xscr"}]},
                context_root=root,
            )

        usage_names = {u["macro_name"] for u in contracts["usages"]}
        inventory_names = {u["macro_name"] for u in contracts["datastore_inventory"]}
        self.assertIn("Demo_Run", usage_names)
        self.assertIn("InventoryOnly", inventory_names)
        self.assertNotIn("InventoryOnly", usage_names)
        self.assertTrue(all(item.get("executable") is False for item in contracts["datastore_inventory"]))


class ScriptFolderBindingsExportTests(unittest.TestCase):
    def test_folder_path_normalization_uses_native_and_python_fallback(self) -> None:
        class CustomFolder:
            def __str__(self):
                return "\u2003/custom//path\\\u00a0"

        values = [
            None,
            False,
            0,
            " /folder//child\\ \t",
            "///",
            "\u2003/é/路径/\u00a0",
            "left/right",
            CustomFolder(),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                script_folder_bindings_export_module._normalize_folder(value)
                for value in values
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                script_folder_bindings_export_module,
                "_native_normalize_script_folder_path",
                wraps=native_acceleration.normalize_script_folder_path,
            ) as native_call:
                actual = [
                    script_folder_bindings_export_module._normalize_folder(value)
                    for value in values
                ]
                self.assertEqual(native_call.call_count, len(values))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            [
                "",
                "",
                "",
                "folder\\\\child",
                "",
                "é\\路径",
                "left\\right",
                "custom\\\\path",
            ],
        )

    def test_display_name_formatting_uses_native_and_python_fallback(self) -> None:
        cases = [
            ("folder", "script", "folder\\script"),
            ("folder", "nested\\script", "nested\\script"),
            ("", "script", "script"),
            ("folder", "", "folder\\"),
            ("路径", "名字", "路径\\名字"),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                script_folder_bindings_export_module._display_name(folder, name)
                for folder, name, _ in cases
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                script_folder_bindings_export_module,
                "_native_format_script_display_name",
                wraps=native_acceleration.format_script_display_name,
            ) as native_call:
                actual = [
                    script_folder_bindings_export_module._display_name(folder, name)
                    for folder, name, _ in cases
                ]
                self.assertEqual(native_call.call_count, len(cases))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [value for _, _, value in cases])

    def test_builds_folder_tree_and_worktable_bindings(self) -> None:
        manifest = {
            "scripts": [
                {
                    "object_name": "Main",
                    "folder": "Lab\\Protocols",
                    "guid": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                    "references": [
                        {
                            "type_id": "WorktableWorkspace",
                            "object_name": "Deck_A",
                            "guid": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                        }
                    ],
                },
                {
                    "object_name": "Helper",
                    "object_subfolder_path": "Lab\\Protocols\\Subs",
                },
            ]
        }
        catalog = build_script_folder_bindings(manifest)
        self.assertGreaterEqual(catalog["script_count"], 2)
        folders = {row.get("folder") for row in catalog.get("scripts") or []}
        self.assertTrue(any(f and "Protocols" in f for f in folders if f))
        bindings = catalog.get("initialization_worktable_bindings") or []
        self.assertTrue(
            any(b.get("script") == "Main" and b.get("worktable_name") == "Deck_A" for b in bindings)
        )


if __name__ == "__main__":
    unittest.main()
