from __future__ import annotations

from io import BytesIO
import os
import re
import unittest
import zipfile
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

    def test_archive_duplicate_casefold_uses_rust_with_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_casefold_trim"
        ):
            self.skipTest("optional Rust ASCII casefold helper is not built")

        def inventory_outcome(names: tuple[str, ...]) -> tuple[object, ...]:
            payload = BytesIO()
            with zipfile.ZipFile(payload, "w") as archive:
                for name in names:
                    archive.writestr(name, b"synthetic")
            payload.seek(0)
            try:
                with zipfile.ZipFile(payload) as archive:
                    inventory = zeia_limits.build_zeia_archive_inventory(archive)
            except zeia_limits.ZeiaArchiveValidationError as exc:
                return ("error", exc.reason, exc.entry_name, exc.previous_entry_name)
            return ("ok", inventory.names)

        cases = (
            ("Folder/Case.xscr", "folder/case.XSCR"),
            ("Straße.xscr", "STRASSE.xscr"),
            (" leading.xscr", "leading.xscr"),
        )
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [inventory_outcome(names) for names in cases]
        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                zeia_limits,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = [inventory_outcome(names) for names in cases]
                self.assertEqual(native_call.call_count, 5)
        self.assertEqual(actual, expected)
        self.assertEqual(actual[0][0:2], ("error", "duplicate_member"))
        self.assertEqual(actual[1][0:2], ("error", "duplicate_member"))
        self.assertEqual(
            actual[2], ("ok", (" leading.xscr", "leading.xscr"))
        )

        with patch.dict(os.environ, {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                zeia_limits, "_native_ascii_casefold_trim", return_value=None
            ):
                fallback = [inventory_outcome(names) for names in cases]
        self.assertEqual(fallback, expected)

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
