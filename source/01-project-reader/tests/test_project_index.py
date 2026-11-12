import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tecan_common import native_acceleration
import tecan_reader.project_index as project_index
from tecan_reader.project_index import (
    _initialize_database,
    discover_zeia_paths,
    find_entity_candidates,
    search_project_index,
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


class TestProjectIndexResultDeduplication(unittest.TestCase):
    def test_native_deduplication_preserves_first_result_and_order(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "project_index_dedupe_indices"
        ):
            self.skipTest("optional Rust project-index deduplicator is not built")
        first = {
            "kind": "entity",
            "match_type": "name",
            "name": "Alpha",
            "zeia_file": "one.zeia",
            "script": None,
            "source_path": "Objects/alpha.xml",
            "command_index": 1,
            "payload": "first",
        }
        duplicate = {**first, "payload": "duplicate"}
        results = [
            first,
            duplicate,
            {**first, "name": "Beta"},
            {**first, "command_index": 2},
            {"kind": "entity", "match_type": "name", "name": "Nulls"},
            {
                "kind": "entity",
                "match_type": "name",
                "name": "Nulls",
                "zeia_file": None,
                "script": None,
                "source_path": None,
                "command_index": None,
            },
            {**first, "name": "é"},
            {**first, "name": "é", "payload": "unicode duplicate"},
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = project_index._dedupe_results(results)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                project_index.native_acceleration,
                "project_index_dedupe_indices",
                wraps=native_acceleration.project_index_dedupe_indices,
            ) as native_call:
                actual = project_index._dedupe_results(results)
                native_call.assert_called_once_with(results)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [first, results[2], results[3], results[4], results[6]])
        self.assertIs(actual[0], first)
        self.assertIs(actual[1], results[2])

    def test_native_deduplicator_falls_back_for_unsupported_values(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "project_index_dedupe_indices"
        ):
            self.skipTest("optional Rust project-index deduplicator is not built")

        class StringSubclass(str):
            pass

        unsupported = [
            {"kind": 1, "name": "numeric"},
            {"kind": "entity", "command_index": True},
            {"kind": "entity", "command_index": 1},
            {"kind": "entity", "command_index": 2**80},
            {"kind": StringSubclass("entity"), "name": "subclass"},
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = project_index._dedupe_results(unsupported)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.project_index_dedupe_indices(unsupported))
            actual = project_index._dedupe_results(unsupported)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 4)
        self.assertIs(actual[1], unsupported[1])
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.project_index_dedupe_indices(
                    [{"command_index": 2**80}]
                )
            )

        unhashable = [{"name": ["not", "hashable"]}]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.project_index_dedupe_indices(unhashable))
            with self.assertRaises(TypeError):
                project_index._dedupe_results(unhashable)


class TestProjectIndexFileHash(unittest.TestCase):
    def test_native_streaming_hash_matches_python_for_empty_and_multi_chunk_files(self):
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "sha256_file"
        ):
            self.skipTest("optional Rust file hasher is not built")

        with tempfile.TemporaryDirectory() as tmp:
            empty_path = Path(tmp) / "empty.bin"
            empty_path.write_bytes(b"")
            large_path = Path(tmp) / "large.bin"
            large_content = bytes(range(256)) * 8193
            large_path.write_bytes(large_content)
            cases = [
                (empty_path, hashlib.sha256(b"").hexdigest()),
                (large_path, hashlib.sha256(large_content).hexdigest()),
            ]
            for path, expected in cases:
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                    with patch.object(
                        native_acceleration, "sha256_file_required", return_value=None
                    ):
                        python_result = project_index._sha256(path)
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                    self.assertEqual(native_acceleration.sha256_file(path), expected)
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                    with patch.object(
                        native_acceleration._native,
                        "sha256_file",
                        wraps=native_acceleration._native.sha256_file,
                    ) as rust_call:
                        native_result = project_index._sha256(path)
                        rust_call.assert_called_once()
                self.assertEqual(python_result, expected)
                self.assertEqual(native_result, python_result)

            missing_path = Path(tmp) / "missing.bin"
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                self.assertIsNone(native_acceleration.sha256_file(missing_path))
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                with self.assertRaises(FileNotFoundError):
                    project_index._sha256(missing_path)


class TestIndexSchemaMigration(unittest.TestCase):
    def test_existing_index_gets_completeness_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "legacy.db"
            conn = sqlite3.connect(database)
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute(
                """CREATE TABLE zeia_files (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    file_name TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    entry_count INTEGER NOT NULL DEFAULT 0,
                    script_count_total INTEGER NOT NULL DEFAULT 0,
                    script_count_summarized INTEGER NOT NULL DEFAULT 0,
                    object_count_summarized INTEGER NOT NULL DEFAULT 0,
                    gwl_count_summarized INTEGER NOT NULL DEFAULT 0,
                    extension_counts_json TEXT NOT NULL DEFAULT '{}'
                )"""
            )

            _initialize_database(conn)
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(zeia_files)")
            }
            version = conn.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()[0]
            conn.close()

        self.assertIn("completeness_json", columns)
        self.assertEqual(version, "2")


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

    def test_kind_filter_normalization_uses_required_rust_with_unicode_fallback(self):
        values = (" Liquid-Class ", "Café-Kind", "\u217c", "", None)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "ascii_casefold_trim_required", return_value=None
            ):
                expected = [
                    project_index._normalize_kind_filter(value) for value in values
                ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_trim_required",
                wraps=native_acceleration.ascii_casefold_trim_required,
            ) as required_call, patch.object(
                native_acceleration._native,
                "ascii_casefold_trim",
                wraps=native_acceleration._native.ascii_casefold_trim,
            ) as rust_call:
                actual = [project_index._normalize_kind_filter(value) for value in values]
                self.assertEqual(required_call.call_count, 3)
                self.assertEqual(rust_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual, ["liquid_class", "café_kind", "\u217c", None, None]
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_trim_required",
                return_value=None,
            ):
                fallback = [
                    project_index._normalize_kind_filter(value) for value in values
                ]
        self.assertEqual(fallback, expected)

    def test_lower_text_uses_required_rust_and_preserves_python_fallback(self):
        class LowerSubclass(str):
            def lower(self) -> str:
                return "custom-lower"

        values = ("Worktable", " Café ", "\u217c", LowerSubclass("ABC"))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration, "ascii_casefold_trim_required", return_value=None
            ):
                expected = [project_index._lower_text(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_trim_required",
                wraps=native_acceleration.ascii_casefold_trim_required,
            ) as required_call, patch.object(
                native_acceleration._native,
                "ascii_casefold_trim",
                wraps=native_acceleration._native.ascii_casefold_trim,
            ) as rust_call:
                actual = [project_index._lower_text(value) for value in values]
                self.assertEqual(required_call.call_count, 2)
                self.assertEqual(rust_call.call_count, 1)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["worktable", " café ", "\u217c", "custom-lower"])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_trim_required",
                return_value=None,
            ):
                fallback = [project_index._lower_text(value) for value in values]
        self.assertEqual(fallback, expected)

    def test_exact_entity_lookup_casefolds_with_rust_and_preserves_fallbacks(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO entities(id, zeia_file_id, script_id, kind, name, value, source_path)
               VALUES (3, 1, 1, 'worktable', 'strasse', 'synthetic', 'unicode.xscr')"""
        )
        conn.commit()
        conn.close()
        queries = (
            ("WATER", "liquid_class"),
            ("Straße", "worktable"),
            (" WATER ", "liquid_class"),
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_trim_required",
                return_value=None,
            ):
                expected = [
                    find_entity_candidates(self.db_path, identifier, kind=kind)
                    for identifier, kind in queries
                ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_trim_required",
                wraps=native_acceleration.ascii_casefold_trim_required,
            ) as required_call, patch.object(
                native_acceleration._native,
                "ascii_casefold_trim",
                wraps=native_acceleration._native.ascii_casefold_trim,
            ) as rust_call:
                actual = [
                    find_entity_candidates(self.db_path, identifier, kind=kind)
                    for identifier, kind in queries
                ]
                self.assertEqual(required_call.call_count, 5)
                self.assertEqual(rust_call.call_count, 4)
        self.assertEqual(actual, expected)
        self.assertEqual(
            [result["status"] for result in actual],
            ["unique", "unique", "missing"],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(
                native_acceleration,
                "ascii_casefold_trim_required",
                return_value=None,
            ):
                fallback = [
                    find_entity_candidates(self.db_path, identifier, kind=kind)
                    for identifier, kind in queries
                ]
        self.assertEqual(fallback, expected)

    def test_exact_entity_lookup_reports_ambiguity(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            """INSERT INTO entities(id, zeia_file_id, script_id, kind, name, value, source_path)
               VALUES (3, 1, 1, 'worktable', 'Shared', 'WorktableWorkspace', 'one.xscr')"""
        )
        conn.execute(
            """INSERT INTO entities(id, zeia_file_id, script_id, kind, name, value, source_path)
               VALUES (4, 1, 2, 'worktable', 'Shared', 'WorktableWorkspace', 'two.xscr')"""
        )
        conn.commit()
        conn.close()

        result = find_entity_candidates(self.db_path, "Shared", kind="worktable")

        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["count"], 2)

class TestIndexSchemaPolicy(unittest.TestCase):
    def test_search_rejects_stale_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "index.db"
            conn = sqlite3.connect(db)
            conn.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("INSERT INTO metadata(key, value) VALUES('schema_version', '1')")
            conn.commit()
            conn.close()
            with self.assertRaisesRegex(ValueError, "rebuild the index"):
                search_project_index(db, "x")
