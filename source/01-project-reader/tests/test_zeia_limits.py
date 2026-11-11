from __future__ import annotations

import os
import re
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
import tecan_common.zeia_limits as zeia_limits


_DRIVE_QUALIFIER_PATTERN = re.compile(r"^[A-Za-z]:($|/)")


class ZeiaMemberPathTests(unittest.TestCase):
    def test_native_drive_qualifier_matches_python_regex_edges(self) -> None:
        values = [
            "C:",
            "z:",
            "C:/",
            "D:/folder",
            "C:\n",
            "C:\r\n",
            "C:\n\n",
            "C:relative",
            "1:/folder",
            "é:/folder",
            "folder/C:/file",
            "",
        ]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            for value in values:
                expected = _DRIVE_QUALIFIER_PATTERN.match(value) is not None
                actual = native_acceleration.windows_drive_path_is_qualified(value)
                if native_acceleration.available():
                    self.assertEqual(actual, expected, repr(value))
                else:
                    self.assertIsNone(actual, repr(value))

    def test_member_normalization_preserves_python_fallback_and_rejections(self) -> None:
        values = [
            "relative/path.xscr",
            r"folder\child.xscr",
            "/absolute/file.xscr",
            r"\root\file.xscr",
            "C:",
            "C:/folder/file.xscr",
            r"C:\folder\file.xscr",
            "C:\n",
            "C:\r\n",
            "C:\n\n",
            "C:relative",
            "é:/folder/file.xscr",
            "../parent/file.xscr",
            "folder/../child.xscr",
        ]

        def outcomes() -> list[tuple[str, str]]:
            result = []
            for value in values:
                try:
                    result.append(("ok", zeia_limits.normalize_zeia_member_name(value)))
                except ValueError as exc:
                    result.append(("error", str(exc)))
            return result

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = outcomes()

        calls: list[str] = []
        native_check = native_acceleration.windows_drive_path_is_qualified

        def track_check(value):
            calls.append(value)
            return native_check(value)

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                zeia_limits,
                "_native_windows_drive_path_is_qualified",
                side_effect=track_check,
            ):
                actual = outcomes()
        self.assertEqual(actual, expected)
        self.assertTrue(calls)
        self.assertIn("C:/folder/file.xscr", calls)
        self.assertEqual(
            zeia_limits.normalize_zeia_member_name("relative/path.xscr"),
            "relative/path.xscr",
        )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                zeia_limits,
                "_native_windows_drive_path_is_qualified",
                return_value=None,
            ):
                self.assertEqual(outcomes(), expected)


if __name__ == "__main__":
    unittest.main()
