from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fluent_pipeline.fluent_library as fluent_library
from tecan_common import native_acceleration


class FluentLibraryNativeTests(unittest.TestCase):
    def test_record_sort_key_uses_native_ascii_and_unicode_fallback(self):
        values = [" Alpha", "Straße", "OMEGA"]
        expected = [value.casefold() for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            disabled = [fluent_library._casefold_key(value) for value in values]
        self.assertEqual(disabled, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                fluent_library,
                "_native_ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                enabled = [fluent_library._casefold_key(value) for value in values]
                self.assertEqual(native_call.call_count, 2)
        self.assertEqual(enabled, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                fluent_library,
                "_native_ascii_casefold_trim",
                return_value=None,
            ):
                fallback = [fluent_library._casefold_key(value) for value in values]
        self.assertEqual(fallback, expected)

        with tempfile.TemporaryDirectory(prefix="fluent_library_") as tmp:
            root = Path(tmp)
            for scope, filename, name, folder in (
                ("UserSpecific", "z.xscr", "zeta", "C"),
                ("SystemSpecific", "s.xscr", "Straße", "A"),
                ("UserSpecific", "a.xscr", "ALPHA", "B"),
            ):
                directory = root / scope
                directory.mkdir(parents=True, exist_ok=True)
                (directory / filename).write_text(
                    "<VxData><Payload>"
                    f"<ObjectName>{name}</ObjectName>"
                    f"<ObjectSubfolderPath>{folder}</ObjectSubfolderPath>"
                    "</Payload></VxData>",
                    encoding="utf-8",
                )

            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                disabled_records = fluent_library.list_local_fluent_scripts(root)
            self.assertEqual(
                [item["object_name"] for item in disabled_records],
                ["ALPHA", "Straße", "zeta"],
            )

            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                with patch.object(
                    fluent_library,
                    "_native_ascii_casefold_trim",
                    wraps=native_acceleration.ascii_casefold_trim,
                ) as native_call:
                    enabled_records = fluent_library.list_local_fluent_scripts(root)
                    self.assertGreater(native_call.call_count, 0)
            self.assertEqual(enabled_records, disabled_records)

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

    def test_xml_text_whitespace_uses_native_collapse_with_python_fallback(self):
        text = "<ObjectName> \tAlpha\nBeta\u2003 </ObjectName>"
        expected = "Alpha Beta"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            fallback = fluent_library._first_xml_text(text, "ObjectName")
        self.assertEqual(fallback, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                fluent_library,
                "_native_collapse_ascii_whitespace",
                wraps=native_acceleration.collapse_ascii_whitespace,
            ) as native_call:
                actual = fluent_library._first_xml_text(text, "ObjectName")
                self.assertEqual(native_call.call_count, 1)
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                fluent_library,
                "_native_collapse_ascii_whitespace",
                return_value=None,
            ):
                unsupported_fallback = fluent_library._first_xml_text(
                    text, "ObjectName"
                )
        self.assertEqual(unsupported_fallback, expected)
        self.assertEqual(
            fluent_library._first_xml_text("<Other>ignored</Other>", "ObjectName"),
            "",
        )

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
