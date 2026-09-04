import sqlite3
import tempfile
import unittest
from pathlib import Path

from tecan_reader.project_index import discover_zeia_paths, search_project_index, _initialize_database


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


class TestSearchProjectIndex(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self._populate_db()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _populate_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        _initialize_database(conn)

        # zeia_files
        conn.execute(
            """INSERT INTO zeia_files(id, path, file_name, sha256, indexed_at)
               VALUES (1, '/fake/test.zeia', 'test.zeia', 'abc', '2021-01-01T00:00:00Z')"""
        )
        # scripts
        conn.execute(
            """INSERT INTO scripts(id, zeia_file_id, entry_path, object_name)
               VALUES (1, 1, 'script1.xscr', 'MyTestScript')"""
        )
        conn.execute(
            """INSERT INTO scripts(id, zeia_file_id, entry_path, object_name)
               VALUES (2, 1, 'script2.xscr', 'AnotherScript')"""
        )
        # commands
        conn.execute(
            """INSERT INTO commands(id, zeia_file_id, script_id, command_index, command_type, family, name)
               VALUES (1, 1, 1, 0, 'LiquidHandling', 'pipette', 'Aspirate')"""
        )
        # entities
        conn.execute(
            """INSERT INTO entities(id, zeia_file_id, script_id, kind, name, value, source_path)
               VALUES (1, 1, 1, 'labware', 'SourcePlate', 'Plate', 'script1.xscr')"""
        )
        conn.execute(
            """INSERT INTO entities(id, zeia_file_id, script_id, kind, name, value, source_path)
               VALUES (2, 1, 2, 'liquid_class', 'Water', 'Water', 'script2.xscr')"""
        )
        # sequences
        conn.execute(
            """INSERT INTO command_sequences(id, zeia_file_id, script_id, start_index, length, command_names, command_families, source_path)
               VALUES (1, 1, 1, 0, 1, 'Aspirate', 'pipette', 'script1.xscr')"""
        )
        conn.commit()
        conn.close()

    def test_search_all_kinds(self):
        result = search_project_index(self.db_path, "test")
        self.assertEqual(result["kind"], "project_index_search")
        self.assertEqual(result["query"], "test")
        # Should match 'MyTestScript' (script)
        self.assertGreaterEqual(result["result_count"], 1)

        result_names = [r["name"] for r in result["results"]]
        self.assertIn("MyTestScript", result_names)

    def test_search_by_kind_script(self):
        result = search_project_index(self.db_path, "script", kind="script")
        self.assertEqual(result["kind_filter"], "script")
        # Should match 'MyTestScript' and 'AnotherScript'
        self.assertEqual(result["result_count"], 2)
        names = {r["name"] for r in result["results"]}
        self.assertEqual(names, {"MyTestScript", "AnotherScript"})

    def test_search_by_kind_command(self):
        result = search_project_index(self.db_path, "Aspirate", kind="command")
        self.assertEqual(result["kind_filter"], "command")
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0]["name"], "LiquidHandling")
        self.assertEqual(result["results"][0]["value"], "Aspirate")

    def test_search_by_kind_entity(self):
        result = search_project_index(self.db_path, "Water", kind="liquid_class")
        self.assertEqual(result["kind_filter"], "liquid_class")
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0]["name"], "Water")

    def test_search_by_kind_sequence(self):
        result = search_project_index(self.db_path, "Aspirate", kind="command_sequence")
        self.assertEqual(result["kind_filter"], "command_sequence")
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0]["name"], "Aspirate")
        self.assertEqual(result["results"][0]["value"], "pipette")

    def test_search_limit(self):
        result = search_project_index(self.db_path, "script", kind="script", limit=1)
        self.assertEqual(result["result_count"], 1)

    def test_search_normalization(self):
        # 'Liquid-Class ' -> 'liquid_class'
        result = search_project_index(self.db_path, "Water", kind="Liquid-Class ")
        self.assertEqual(result["kind_filter"], "liquid_class")
        self.assertEqual(result["result_count"], 1)
        self.assertEqual(result["results"][0]["name"], "Water")
