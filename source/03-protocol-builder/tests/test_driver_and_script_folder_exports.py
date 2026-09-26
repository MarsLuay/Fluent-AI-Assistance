"""Tests for driver_macros + driver_command_contracts + script_folder_bindings ZEIA mining."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.driver_macros_export import (
    DRIVER_COMMAND_CONTRACTS_SCHEMA_VERSION,
    DRIVER_MACROS_SCHEMA_VERSION,
    build_driver_command_contracts,
    build_driver_macros_catalog,
)
from fluent_pipeline.script_folder_bindings_export import build_script_folder_bindings


class DriverMacrosExportTests(unittest.TestCase):
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
