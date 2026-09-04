import unittest
import tempfile
import sqlite3
from pathlib import Path
from tecan_reader.project_index import (
    discover_zeia_paths,
    summarize_project_index,
    _initialize_database,
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


class TestSummarizeProjectIndex(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        _initialize_database(self.conn)

        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.sqlite"
        self.file_conn = sqlite3.connect(self.db_path)
        self.file_conn.row_factory = sqlite3.Row
        _initialize_database(self.file_conn)

    def tearDown(self):
        self.conn.close()
        self.file_conn.close()
        self.temp_dir.cleanup()

    def test_summarize_empty_database(self):
        summary = summarize_project_index(self.conn)
        self.assertEqual(summary["kind"], "project_index_summary")
        self.assertEqual(summary["schema_version"], "1")
        self.assertEqual(summary["zeia_file_count"], 0)
        self.assertEqual(summary["script_count"], 0)
        self.assertEqual(summary["command_count"], 0)
        self.assertEqual(summary["catalog_object_count"], 0)
        self.assertEqual(summary["worklist_count"], 0)
        self.assertEqual(summary["command_sequence_count"], 0)
        self.assertEqual(summary["entity_counts"], {})
        self.assertEqual(summary["command_family_counts"], {})
        self.assertEqual(summary["files"], [])

    def test_summarize_populated_database(self):
        # Insert mock data
        self.conn.execute("""
            INSERT INTO zeia_files(path, file_name, sha256, indexed_at, entry_count, script_count_total)
            VALUES ('/fake/path.zeia', 'path.zeia', 'hash', '2023-10-01', 1, 1)
        """)
        self.conn.execute("INSERT INTO scripts(zeia_file_id, entry_path) VALUES (1, 'script1.esc')")
        self.conn.execute("INSERT INTO scripts(zeia_file_id, entry_path) VALUES (1, 'script2.esc')")
        self.conn.execute("INSERT INTO commands(zeia_file_id, script_id, command_index, family) VALUES (1, 1, 1, 'Pipetting')")
        self.conn.execute("INSERT INTO commands(zeia_file_id, script_id, command_index, family) VALUES (1, 1, 2, 'Pipetting')")
        self.conn.execute("INSERT INTO commands(zeia_file_id, script_id, command_index, family) VALUES (1, 1, 3, 'System')")
        self.conn.execute("INSERT INTO entities(zeia_file_id, script_id, kind, name) VALUES (1, 1, 'labware', 'Plate')")
        self.conn.execute("INSERT INTO entities(zeia_file_id, script_id, kind, name) VALUES (1, 1, 'variable', 'Counter')")
        self.conn.commit()

        summary = summarize_project_index(self.conn)

        self.assertEqual(summary["zeia_file_count"], 1)
        self.assertEqual(summary["script_count"], 2)
        self.assertEqual(summary["command_count"], 3)
        self.assertEqual(summary["entity_counts"], {"labware": 1, "variable": 1})
        self.assertEqual(summary["command_family_counts"], {"Pipetting": 2, "System": 1})
        self.assertEqual(len(summary["files"]), 1)
        self.assertEqual(summary["files"][0]["file_name"], "path.zeia")
        self.assertEqual(summary["files"][0]["script_count_total"], 1)

    def test_summarize_with_path(self):
        # Insert mock data into file-based db
        self.file_conn.execute("""
            INSERT INTO zeia_files(path, file_name, sha256, indexed_at, entry_count)
            VALUES ('/fake/path.zeia', 'path.zeia', 'hash', '2023-10-01', 1)
        """)
        self.file_conn.commit()

        # Test with string path
        summary_str = summarize_project_index(str(self.db_path))
        self.assertEqual(summary_str["zeia_file_count"], 1)
        self.assertEqual(summary_str["database"], str(self.db_path))

        # Test with Path object
        summary_path = summarize_project_index(self.db_path)
        self.assertEqual(summary_path["zeia_file_count"], 1)
        self.assertEqual(summary_path["database"], str(self.db_path))
