from __future__ import annotations

import unittest
from unittest.mock import patch

import fluent_pipeline.fluent_library as fluent_library
from tecan_common import native_acceleration


class FluentLibraryNativeTests(unittest.TestCase):
    def test_name_key_uses_native_ascii_compaction_with_casefold_fallback(self):
        values = ["Helper Script v2", "Straße", "ſcript", "東京", None, 0]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [fluent_library._name_key(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                fluent_library,
                "_native_ascii_lower_alnum_compact",
                wraps=native_acceleration.ascii_lower_alnum_compact,
            ) as native_call:
                actual = [fluent_library._name_key(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(native_acceleration.ascii_lower_alnum_compact("東京"))
        self.assertEqual(actual, expected)
        self.assertEqual(actual, ["helperscriptv2", "strasse", "script", "", "", ""])

    def test_safe_filename_matches_ascii_replacement_trimming_and_truncation(self):
        values = [
            "Simple-name.xscr",
            "  .._Alpha / Beta__.. ",
            "\u2003Safe Name\u00a0",
            "名字",
            "///",
            "---",
            "a" * 125,
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [fluent_library._safe_filename(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                fluent_library,
                "_native_ascii_safe_filename",
                wraps=native_acceleration.ascii_safe_filename,
            ) as native_call:
                actual = [fluent_library._safe_filename(value) for value in values]
                self.assertEqual(native_call.call_count, len(values))
            self.assertIsNone(native_acceleration.ascii_safe_filename("名字"))
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual,
            ["Simple-name.xscr", "Alpha_Beta", "Safe_Name", "", "", "---", "a" * 120],
        )


if __name__ == "__main__":
    unittest.main()
