from __future__ import annotations

from collections import Counter
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_reader import cli


class ProjectReaderCliTests(unittest.TestCase):
    def test_suffix_lowering_uses_rust_with_exact_python_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "ascii_casefold_trim"
        ):
            self.skipTest("optional Rust ASCII lowercase helper is not built")

        class LowerSubclass(str):
            def lower(self) -> str:
                return "custom-lower"

        values = (".XSCR", ".XſSCR", " .XSCR ", LowerSubclass(".GWL"))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [cli._lower_suffix(value) for value in values]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                cli.native_acceleration,
                "ascii_casefold_trim",
                wraps=native_acceleration.ascii_casefold_trim,
            ) as native_call:
                actual = [cli._lower_suffix(value) for value in values]
                self.assertEqual(native_call.call_count, 2)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, [".xscr", ".xſscr", " .xscr ", "custom-lower"])

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                cli.native_acceleration, "ascii_casefold_trim", return_value=None
            ):
                fallback = [cli._lower_suffix(value) for value in values]
        self.assertEqual(fallback, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(cli, "inspect_xscr", return_value={"kind": "script"}) as inspect:
                result = cli.inspect_path(
                    Path("synthetic.XSCR"), script_limit=None, object_limit=None
                )
        inspect.assert_called_once()
        self.assertEqual(result, {"kind": "script"})

    def test_directory_counter_aggregation_uses_native_with_python_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "most_common_string_counts"
        ):
            self.skipTest("optional Rust counter ordering is not built")
        script_report = {
            "object_name": "First",
            "family_counts": {"Zeta": 2, "Alpha": 2, "Beta": 1},
            "command_counts": {f"Command{index:02d}": 1 for index in range(42)},
            "warnings": ["external", "external", "warning"],
        }
        archive_report = {
            "script_names": ["Second"],
            "family_counts": {"Beta": 1},
            "command_counts": {"Command00": 1},
            "warning_counts": {"warning": 1},
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "first.xscr").touch()
            (root / "second.zeia").touch()
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                with patch.object(cli, "inspect_xscr", return_value=script_report), patch.object(
                    cli, "inspect_archive", return_value=archive_report
                ):
                    expected = cli._inspect_directory(
                        root, script_limit=None, object_limit=None
                    )
            with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                with patch.object(cli, "inspect_xscr", return_value=script_report), patch.object(
                    cli, "inspect_archive", return_value=archive_report
                ), patch.object(
                    cli.native_acceleration,
                    "most_common_string_counts",
                    wraps=native_acceleration.most_common_string_counts,
                ) as native_call:
                    actual = cli._inspect_directory(
                        root, script_limit=None, object_limit=None
                    )
                    self.assertEqual(native_call.call_count, 3)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["file_count"], 2)
        self.assertEqual(actual["command_counts"], expected["command_counts"])
        self.assertEqual(len(actual["command_counts"]), 40)

        unicode_counts = Counter({"é": 2, "a": 2})
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_unicode_counts = native_acceleration.most_common_string_counts(
                dict(unicode_counts)
            )
            self.assertIsNotNone(native_unicode_counts)
            self.assertEqual(
                list(native_unicode_counts.items()),
                list(unicode_counts.most_common()),
            )
            self.assertEqual(
                cli._most_common_counts(unicode_counts),
                dict(unicode_counts.most_common()),
            )
            self.assertIsNone(
                native_acceleration.most_common_string_counts({chr(0xD800): 1})
            )


if __name__ == "__main__":
    unittest.main()
