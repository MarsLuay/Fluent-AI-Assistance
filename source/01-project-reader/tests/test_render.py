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

    def test_markdown_report_output_matches_python_with_native_sections(self) -> None:
        reports = [
            {
                "kind": "xscr",
                "object_name": "Synthetic Object",
                "source": "synthetic.xscr",
                "script_version": "test",
                "command_count": 1,
                "references": [
                    {"type_id": "reference", "object_name": "Example", "guid": "id"}
                ],
                "family_counts": {"category_a": 1},
                "command_counts": {"example": 1},
                "variables": [{"name": "value", "type": "text"}],
                "query_prompts": [
                    {"name": "prompt", "prompt": "value", "minimum": 0, "maximum": 1}
                ],
                "dependencies": {"files": ["alpha", "beta"]},
                "comments": ["note"],
                "warnings": ["warning"],
                "commands": [
                    {
                        "name": "example",
                        "line": 1,
                        "index": 0,
                        "type": "ExampleCommand",
                        "family": "category_a",
                    }
                ],
            },
            {
                "kind": "zeia",
                "source": "synthetic.zeia",
                "entry_count": 2,
                "script_count_total": 1,
                "script_count_summarized": 1,
                "object_count_summarized": 1,
                "gwl_count_summarized": 0,
                "extension_counts": {".xml": 1},
                "family_counts": {"category_a": 1},
                "command_counts": {"example": 1},
                "warning_counts": {"warning": 1},
                "script_names": ["Synthetic Script"],
                "scripts": [
                    {
                        "object_name": "Synthetic Script",
                        "command_count": 1,
                        "family_counts": {"category_a": 1},
                    }
                ],
                "errors": [{"entry": "synthetic.xml", "error": "parse error"}],
            },
            {
                "kind": "directory",
                "source": "synthetic-directory",
                "file_count": 1,
                "extension_counts": {".xml": 1},
                "family_counts": {"category_a": 1},
                "command_counts": {"example": 1},
                "warning_counts": {"warning": 1},
                "script_names": ["Synthetic Script"],
                "items": [{"kind": "xscr", "source": "synthetic.xscr"}],
            },
            {
                "kind": "zeia_compare",
                "left": "synthetic-left",
                "right": "synthetic-right",
                "left_entry_count": 1,
                "right_entry_count": 2,
                "script_names_common_count": 1,
                "script_names_added": ["Added"],
                "script_names_removed": ["Removed"],
                "left_family_counts": {"category_a": 1},
                "right_family_counts": {"category_b": 2},
            },
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [render.render_markdown(report) for report in reports]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with (
                patch.object(
                    render,
                    "_native_render_append_list",
                    wraps=render._native_render_append_list,
                ) as native_list,
                patch.object(
                    render,
                    "_native_render_append_dict",
                    wraps=render._native_render_append_dict,
                ) as native_dict,
            ):
                actual = [render.render_markdown(report) for report in reports]
            self.assertGreater(native_list.call_count, 0)
            self.assertGreater(native_dict.call_count, 0)
        self.assertEqual(actual, expected)

    def test_native_markdown_join_matches_python_with_fallbacks(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_join_lines"
        ):
            self.skipTest("optional Rust Markdown join helper is not built")
        cases = [[], [""], ["first", "second"], ["λ", "line\nbreak", "tail"]]
        for lines in cases:
            with self.subTest(line_count=len(lines)):
                expected = "\n".join(lines) + "\n"
                with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
                    self.assertEqual(native_acceleration.render_join_lines(lines), expected)
                    with patch.object(
                        render,
                        "_native_render_join_lines",
                        wraps=render._native_render_join_lines,
                    ) as native_call:
                        self.assertEqual(render._finish_markdown(lines), expected)
                        native_call.assert_called_once()

        class StringSubclass(str):
            pass

        class ListSubclass(list):
            pass

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.render_join_lines([StringSubclass("x")]))
            self.assertIsNone(native_acceleration.render_join_lines(ListSubclass(["x"])))
            self.assertEqual(render._finish_markdown([StringSubclass("x")]), "x\n")
            self.assertEqual(render._finish_markdown(ListSubclass(["x"])), "x\n")

    def test_native_xml_object_report_matches_python_and_limits_lists(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_object_report"
        ):
            self.skipTest("optional Rust XML-object renderer is not built")
        report = {
            "object_name": "Synthetic Object",
            "source": "synthetic.xml",
            "kind": "component",
            "type_id": "example-type",
            "functional_group": None,
            "footprint": 0,
            "names": [f"name-{index}" for index in range(22)],
            "guids": ["guid-a", "guid-b"],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = render._render_object(report)
        fields = [
            str(report.get("kind")),
            str(report.get("source")),
            str(report.get("type_id")),
            str(report.get("functional_group")),
            str(report.get("footprint")),
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            direct = native_acceleration.render_object_report(
                str(report.get("object_name") or report.get("source")),
                fields,
                report["names"],
                report["guids"],
            )
            self.assertEqual(direct, expected)
            with patch.object(
                render,
                "_native_render_object_report",
                wraps=render._native_render_object_report,
            ) as native_call:
                actual = render._render_object(report)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertIn("- ... 2 more", actual)

        class TextValue:
            def __init__(self) -> None:
                self.str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom value"

        custom = TextValue()
        unsupported = {"object_name": "Synthetic", "kind": custom}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render._render_object(unsupported)
        self.assertEqual(custom.str_calls, 1)
        custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.render_object_report(
                    "Synthetic", [custom, "None", "None", "None", "None"], [], []
                )
            )
            self.assertEqual(custom.str_calls, 0)
            actual_fallback = render._render_object(unsupported)
        self.assertEqual(custom.str_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

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
