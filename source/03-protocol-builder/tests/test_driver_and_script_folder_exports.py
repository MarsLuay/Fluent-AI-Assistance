"""Tests for driver_macros + script_folder_bindings ZEIA mining."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fluent_pipeline.driver_macros_export import build_driver_macros_catalog
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

    def test_empty_when_no_macros(self) -> None:
        catalog = build_driver_macros_catalog(manifest={"scripts": []})
        self.assertEqual(catalog["entry_count"], 0)
        self.assertEqual(catalog["entries"], [])


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
