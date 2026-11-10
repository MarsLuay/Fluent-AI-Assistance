from __future__ import annotations

import unittest
from unittest.mock import patch

from tecan_common import native_acceleration
import tecan_reader.render as render


class RenderListTests(unittest.TestCase):
    def test_native_append_list_matches_python_slicing_and_formatting(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_append_list"
        ):
            self.skipTest("optional Rust list renderer is not built")

        cases = [
            ("Names", ["alpha", "beta"], 20),
            ("Unicode λ", ("α", "β", "γ"), 2),
            ("Zero", ["one", "two"], 0),
            ("Negative", ["one", "two", "three"], -1),
            ("Very negative", ["one", "two", "three"], -8),
            ("No remainder", ["one", "two", "three"], 3),
        ]

        def render_case(
            title: str, values: list[str] | tuple[str, ...], limit: int
        ) -> list[str]:
            lines = ["existing"]
            render._append_list(lines, title, values, limit=limit)
            return lines

        for title, values, limit in cases:
            with self.subTest(title=title, limit=limit):
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                    expected = render_case(title, values, limit)
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                    direct = native_acceleration.render_append_list(title, values, limit)
                    self.assertEqual(direct, expected[1:])
                    with patch.object(
                        render,
                        "_native_render_append_list",
                        wraps=render._native_render_append_list,
                    ) as native_call:
                        actual = render_case(title, values, limit)
                        native_call.assert_called_once()
                self.assertEqual(actual, expected)

    def test_native_append_dict_matches_python_order_formatting_and_limits(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_append_dict"
        ):
            self.skipTest("optional Rust mapping renderer is not built")
        values = {
            "first": "one",
            "second": 2,
            "third": True,
            "fourth": None,
            "fifth": 3.5,
        }
        cases = [
            ("Counts", values, 3),
            ("Unicode λ", {"α": "β", "γ": "δ"}, 20),
            ("Zero", values, 0),
            ("Negative", values, -1),
            ("All", values, 20),
        ]

        def render_case(title: str, mapping: dict[str, object], limit: int) -> list[str]:
            lines = ["existing"]
            render._append_dict(lines, title, mapping, limit=limit)
            return lines

        for title, mapping, limit in cases:
            with self.subTest(title=title, limit=limit):
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
                    expected = render_case(title, mapping, limit)
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                    direct = native_acceleration.render_append_dict(title, mapping, limit)
                    self.assertEqual(direct, expected[1:])
                    with patch.object(
                        render,
                        "_native_render_append_dict",
                        wraps=render._native_render_append_dict,
                    ) as native_call:
                        actual = render_case(title, mapping, limit)
                        native_call.assert_called_once()
                self.assertEqual(actual, expected)

    def test_native_append_dict_falls_back_without_stringifying_unrendered_values(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust renderer is not built")

        class TextValue:
            calls = 0

            def __str__(self) -> str:
                self.calls += 1
                return "custom"

        hidden = TextValue()
        mapping = {"shown": "plain", "hidden": hidden}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = ["existing"]
            render._append_dict(expected, "Fallback", mapping, limit=1)
        self.assertEqual(hidden.calls, 0)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.render_append_dict("Fallback", mapping, 1)
            )
            actual = ["existing"]
            render._append_dict(actual, "Fallback", mapping, limit=1)
        self.assertEqual(hidden.calls, 0)
        self.assertEqual(actual, expected)

    def test_native_append_list_keeps_python_fallback_for_unsupported_values(self) -> None:
        if not native_acceleration.available():
            self.skipTest("optional Rust renderer is not built")

        class StringSubclass(str):
            pass

        values = [StringSubclass("custom"), "plain"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = ["existing"]
            render._append_list(expected, "Fallback", values, limit=1)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.render_append_list("Fallback", values, 1))
            actual = ["existing"]
            render._append_list(actual, "Fallback", values, limit=1)
        self.assertEqual(actual, expected)

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.render_append_list("Bool", ["a", "b"], True))
            self.assertIsNone(
                native_acceleration.render_append_list("Huge", ["a", "b"], 1 << 100)
            )
            no_values = ["unchanged"]
            render._append_list(no_values, "Empty", [], limit=5)
            self.assertEqual(no_values, ["unchanged"])


if __name__ == "__main__":
    unittest.main()
