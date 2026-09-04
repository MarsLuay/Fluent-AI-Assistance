"""Tests for `fingerprint_matches` in indexer.py."""

import unittest
from pathlib import Path
from unittest import mock
import tempfile

from fluentcoder.catalog.indexer import fingerprint_matches

class FingerprintMatchesTests(unittest.TestCase):
    @mock.patch("fluentcoder.catalog.catalog.install_info")
    def test_fingerprint_matches_missing_info(self, mock_install_info):
        mock_install_info.return_value = None
        self.assertFalse(fingerprint_matches())

    @mock.patch("fluentcoder.catalog.catalog.install_info")
    @mock.patch("fluentcoder.catalog.indexer.install_path_default")
    def test_fingerprint_matches_different_path(self, mock_install_path_default, mock_install_info):
        mock_install_path_default.return_value = Path("/default/path")
        mock_install_info.return_value = {"install_path": "/some/other/path"}
        self.assertFalse(fingerprint_matches())

    @mock.patch("fluentcoder.catalog.catalog.install_info")
    @mock.patch("fluentcoder.catalog.indexer._install_stat_fingerprint")
    def test_fingerprint_matches_matching_fingerprint(self, mock_install_stat_fingerprint, mock_install_info):
        mock_install_info.return_value = {
            "install_path": "/some/install/path",
            "fingerprint": "abc"
        }
        mock_install_stat_fingerprint.return_value = "abc"
        self.assertTrue(fingerprint_matches(install_path="/some/install/path"))

    @mock.patch("fluentcoder.catalog.catalog.install_info")
    @mock.patch("fluentcoder.catalog.indexer._install_stat_fingerprint")
    def test_fingerprint_matches_mismatching_fingerprint(self, mock_install_stat_fingerprint, mock_install_info):
        mock_install_info.return_value = {
            "install_path": "/some/install/path",
            "fingerprint": "abc"
        }
        mock_install_stat_fingerprint.return_value = "def"
        self.assertFalse(fingerprint_matches(install_path="/some/install/path"))

    @mock.patch("fluentcoder.catalog.catalog.install_info")
    @mock.patch("fluentcoder.catalog.indexer._install_stat_fingerprint")
    def test_fingerprint_matches_file_not_found(self, mock_install_stat_fingerprint, mock_install_info):
        mock_install_info.return_value = {
            "install_path": "/some/install/path",
            "fingerprint": "abc"
        }
        mock_install_stat_fingerprint.side_effect = FileNotFoundError()
        self.assertFalse(fingerprint_matches(install_path="/some/install/path"))

if __name__ == "__main__":
    unittest.main()
