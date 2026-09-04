import unittest
import tempfile
import os
from pathlib import Path
from unittest.mock import patch
from tecan_reader.project_index import discover_zeia_paths

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

    def test_expanduser(self):
        with tempfile.TemporaryDirectory() as fake_home_str:
            fake_home = Path(fake_home_str)
            mock_file = fake_home / "test.zeia"
            mock_file.touch()
            with patch('pathlib.Path.home', return_value=fake_home), patch.dict(os.environ, {"HOME": fake_home_str, "USERPROFILE": fake_home_str}):
                paths = discover_zeia_paths(["~/test.zeia"])
                self.assertEqual(paths, [mock_file.resolve()])

    def test_case_insensitive_extension(self):
        file_upper = self.dir1 / "TEST.ZEIA"
        file_upper.touch()
        paths = discover_zeia_paths([file_upper])
        self.assertEqual(paths, [file_upper.resolve()])

    def test_empty_paths_list(self):
        with self.assertRaisesRegex(FileNotFoundError, "No .zeia files found"):
            discover_zeia_paths([])
