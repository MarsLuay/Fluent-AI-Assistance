import unittest
import tempfile
import zipfile
import sqlite3
from unittest.mock import patch
from pathlib import Path
from tecan_reader.project_index import (
    discover_zeia_paths,
    build_project_index,
    search_project_index,
    _insert_entity,
    _loads,
    EntityRecord,
)

class TestDiscoverZeiaPaths(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)

        self.dir1 = self.base_path / "dir1"
        self.dir1.mkdir()
        self.file1 = self.dir1 / "test1.zeia"
        self.file1.touch()

        self.dir2 = self.base_path / "dir2"
        self.dir2.mkdir()
        self.file2 = self.dir2 / "test2.zeia"
        self.file2.touch()

        self.file3 = self.dir1 / "not_zeia.txt"
        self.file3.touch()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_discover_explicit_file(self):
        paths = discover_zeia_paths([self.file1])
        self.assertEqual(paths, [self.file1.resolve()])

        paths_str = discover_zeia_paths([str(self.file1)])
        self.assertEqual(paths_str, [self.file1.resolve()])

    def test_discover_recursive_directory(self):
        paths = discover_zeia_paths([self.base_path])
        expected = sorted([self.file1.resolve(), self.file2.resolve()], key=str)
        self.assertEqual(paths, expected)

    def test_error_not_zeia_file(self):
        with self.assertRaisesRegex(FileNotFoundError, "No .zeia file found"):
            discover_zeia_paths([self.file3])

    def test_error_nonexistent_path(self):
        nonexistent = self.base_path / "does_not_exist.zeia"
        with self.assertRaisesRegex(FileNotFoundError, "No .zeia file found"):
            discover_zeia_paths([nonexistent])

    def test_error_empty_directory(self):
        empty_dir = self.base_path / "empty"
        empty_dir.mkdir()
        with self.assertRaisesRegex(FileNotFoundError, "No .zeia files found"):
            discover_zeia_paths([empty_dir])

    def test_uniqueness_and_deduplication(self):
        # Pass the same file, the directory containing it, etc.
        paths = discover_zeia_paths([self.file1, self.dir1, str(self.file1)])
        self.assertEqual(paths, [self.file1.resolve()])

    def test_return_order(self):
        paths = discover_zeia_paths([self.file2, self.file1])
        expected = sorted([self.file1.resolve(), self.file2.resolve()], key=str)
        self.assertEqual(paths, expected)

class TestBuildProjectIndex(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.db_path = self.base_path / "test.db"
        self.zeia_path = self.base_path / "test.zeia"

        # Create a valid minimal ZEIA archive with dummy content
        with zipfile.ZipFile(self.zeia_path, "w") as zf:
            zf.writestr("DataStore/UserSpecific/sample.xscr", "<sd:VxData />")

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("tecan_reader.project_index.inspect_archive")
    def test_build_project_index_edge_cases(self, mock_inspect):
        # Setup a rich mock payload for inspect_archive to cover many edge cases
        mock_inspect.return_value = {
            "entry_count": 1,
            "scripts": [
                {
                    "source": "DataStore/UserSpecific/sample.xscr",
                    "object_name": "TestScript",
                    "query_prompts": [
                        {"name": "testPrompt", "prompt": "Enter value:", "minimum": "0", "maximum": "10"}
                    ],
                    "set_variables": [
                        {"name": "testVar", "value": "123", "line": "2"}
                    ],
                    "commands": [
                        {
                            "type": "Tecan.Core.Scripting.WorklistCommand",
                            "family": "Worklist",
                            "fields": {"FileName": "C:\\test.gwl"}
                        },
                        {
                            "type": "Tecan.Core.Scripting.DependencyCommand",
                            "family": "File",
                            "fields": {"Path": "test_dependency.txt"}
                        },
                        {
                            "type": "Tecan.Core.Scripting.FileCommand",
                            "family": "File",
                            "fields": {"FileName": ""} # Should be ignored because value is empty
                        }
                    ],
                    "dependencies": {
                        "labware_names": ["TipBox"],
                        "external_or_worklist_refs": ["test.gwl"]
                    },
                    "catalog_objects": [
                        {"kind": "component", "names": ["TipBox"]}
                    ]
                }
            ]
        }

        # Build index once
        res1 = build_project_index([self.zeia_path], self.db_path)
        self.assertIn("database", res1)
        self.assertEqual(res1["indexed_files"], [str(self.zeia_path)])

        # Re-build to trigger DELETE FROM zeia_files WHERE id = ? (line 343)
        res2 = build_project_index([self.zeia_path], self.db_path, force=False)
        self.assertEqual(res2["indexed_files"], [str(self.zeia_path)])

        # Connect to db to verify contents and hit _entity_kind_for_object for labware (914)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT name, value FROM entities WHERE kind='labware'").fetchall()
            self.assertTrue(len(rows) >= 1)
            # _insert_entity takes value as name, and dep_key as value for 'labware'
            self.assertIn(("TipBox", "labware_names"), rows)

            # verify worklist and dependency were created (644-649)
            wl_rows = conn.execute("SELECT name, value FROM entities WHERE kind='worklist'").fetchall()
            self.assertTrue(len(wl_rows) >= 1)
            self.assertIn(("C:\\test.gwl", "FileName"), wl_rows)

            dep_rows = conn.execute("SELECT name, value FROM entities WHERE kind='dependency'").fetchall()
            self.assertTrue(len(dep_rows) >= 1)
            self.assertIn(("test_dependency.txt", "Path"), dep_rows)

            # Verify variable inserts for prompts and sets (481, 499)
            var_rows = conn.execute("SELECT name, value FROM entities WHERE kind='variable' ORDER BY name").fetchall()
            self.assertEqual(len(var_rows), 2)
            self.assertEqual(var_rows[0][0], "testPrompt")
            self.assertEqual(var_rows[1][0], "testVar")

    @patch("tecan_reader.project_index.inspect_archive")
    def test_entity_kind_for_object_extra_cases(self, mock_inspect):
        mock_inspect.return_value = {
            "entry_count": 1,
            "objects": [
                {"kind": "component", "names": ["LabwareItem"]}, # labware (914)
                {"kind": "connector", "names": ["Pin1"], "pin_refs": [1,2]}, # hardware_pin (917-918)
                {"kind": "other", "names": ["UnknownItem"]} # catalog_object (919)
            ]
        }
        build_project_index([self.zeia_path], self.db_path)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT kind, value FROM entities").fetchall()
            kinds = [r[0] for r in rows]
            self.assertIn("hardware_pin", kinds)
            self.assertIn("catalog_object", kinds)


    @patch("tecan_reader.project_index.inspect_archive")
    def test_search_project_index_scripts_and_commands(self, mock_inspect):
        mock_inspect.return_value = {
            "entry_count": 1,
            "scripts": [
                {
                    "source": "DataStore/UserSpecific/sample.xscr",
                    "object_name": "UniqueTestScript123",
                    "dependencies": {},
                    "commands": [
                        {
                            "type": "UniqueCommandType456",
                            "family": "Worklist",
                            "fields": {"FileName": "C:\\test.gwl"}
                        }
                    ]
                }
            ]
        }
        build_project_index([self.zeia_path], self.db_path)

        # Test searching for script (hits lines 994-1012, 934)
        search_res = search_project_index(self.db_path, "uniquetestscript123", kind="script")
        script_results = search_res.get("results", [])
        self.assertTrue(len(script_results) >= 1)
        self.assertEqual(script_results[0]["kind"], "script")
        self.assertEqual(script_results[0]["name"], "UniqueTestScript123")

        # Test searching for command (hits lines 1035-1055, 936)
        search_res2 = search_project_index(self.db_path, "uniquecommandtype456", kind="command")
        cmd_results = search_res2.get("results", [])
        self.assertTrue(len(cmd_results) >= 1)
        self.assertEqual(cmd_results[0]["kind"], "command")
        self.assertIn("UniqueCommandType456", cmd_results[0]["name"])


class TestHelperFunctions(unittest.TestCase):
    def test_loads_edge_cases(self):
        # Empty string
        self.assertEqual(_loads(""), {})
        # Invalid JSON returns original value (lines 1159-1160)
        self.assertEqual(_loads("invalid json"), "invalid json")
        # Valid JSON
        self.assertEqual(_loads('{"valid": true}'), {"valid": True})

    def test_insert_entity_empty(self):
        # Insert entity with empty name and empty value returns early (line 871)
        # Should not raise any error, even though connection is a mock
        mock_conn = type('MockConn', (), {'execute': lambda self, *args: None})()
        _insert_entity(
            mock_conn,
            EntityRecord(1, 1, "test", "", "", "src", {}, 1)
        )
