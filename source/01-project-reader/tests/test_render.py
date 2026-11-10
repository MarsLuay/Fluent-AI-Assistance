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

    def test_native_pattern_specs_preserve_order_limits_and_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_pattern_specs"
        ):
            self.skipTest("optional Rust pattern-spec renderer is not built")
        specs = {
            "empty": [],
            "dimensions": [f"value-{index}" for index in range(14)],
            "unicode": ("β", "γ"),
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = ["existing"]
            render._append_pattern_specs(expected, specs)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(native_acceleration.render_pattern_specs(specs), expected[1:])
            actual = ["existing"]
            with patch.object(
                render,
                "_native_render_pattern_specs",
                wraps=render._native_render_pattern_specs,
            ) as native_call:
                render._append_pattern_specs(actual, specs)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)

        class TextValue:
            def __init__(self) -> None:
                self.str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom"

        custom = TextValue()
        unsupported = {"custom": [custom]}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = []
            render._append_pattern_specs(expected_fallback, unsupported)
        self.assertEqual(custom.str_calls, 1)
        custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.render_pattern_specs(unsupported))
            self.assertEqual(custom.str_calls, 0)
            actual_fallback = []
            render._append_pattern_specs(actual_fallback, unsupported)
        self.assertEqual(custom.str_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

    def test_native_zeia_report_matches_python_limits_and_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_zeia_report"
        ):
            self.skipTest("optional Rust ZEIA report renderer is not built")
        report = {
            "kind": "zeia",
            "source": "source-x",
            "entry_count": 100,
            "script_count_total": 42,
            "script_count_summarized": 40,
            "object_count_summarized": 8,
            "gwl_count_summarized": 2,
            "extension_counts": {f"extension-{index}": index for index in range(41)},
            "family_counts": {f"family-{index}": index for index in range(31)},
            "command_counts": {f"command-{index}": index for index in range(31)},
            "warning_counts": {f"warning-{index}": index for index in range(21)},
            "script_names": [f"script-{index}" for index in range(82)],
            "scripts": [
                {
                    "object_name": f"object-{index}",
                    "command_count": index,
                    "family_counts": {f"key-{key}": key for key in range(6)},
                }
                for index in range(42)
            ],
            "errors": [
                {"entry": f"entry-{index}", "error": f"error-{index}"}
                for index in range(22)
            ],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = render.render_markdown(report)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            direct = native_acceleration.render_zeia_report(report)
            self.assertEqual(direct, expected)
            with patch.object(
                render,
                "_native_render_zeia_report",
                wraps=render._native_render_zeia_report,
            ) as native_call:
                actual = render.render_markdown(report)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertIn("object-39", actual)
        self.assertNotIn("object-40", actual)
        self.assertIn("key-4=4", actual)
        self.assertNotIn("key-5=5", actual)
        self.assertIn("entry-19", actual)
        self.assertNotIn("entry-20", actual)

        class TextValue:
            def __init__(self) -> None:
                self.str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom error"

        custom = TextValue()
        unsupported = {
            "kind": "zeia",
            "errors": [{"entry": "entry-x", "error": custom}],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

    def test_native_script_pattern_search_matches_nested_python_output(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_script_pattern_search"
        ):
            self.skipTest("optional Rust pattern-search renderer is not built")
        report = {
            "kind": "script_pattern_search",
            "database": "database-x",
            "query": "query-β",
            "pattern_type_filter": "",
            "source_script_filter": None,
            "result_count": 2,
            "results": [
                {
                    "name": "pattern-x",
                    "pattern_type": "type-x",
                    "label": "label-x",
                    "source_script": "script-x",
                    "start_command_index": 1,
                    "end_command_index": 2,
                    "zeia_file": "zeia-x",
                    "source_path": "path-x",
                    "specifications": {"key-x": ["value-α", "value-β"]},
                    "safety_notes": ["note-x"],
                    "steps": [
                        {
                            "step_number": 1,
                            "summary": "summary-x",
                            "command_name": "command-x",
                            "command_index": 3,
                        }
                    ],
                },
                {
                    "name": "pattern-y",
                    "pattern_type": "type-y",
                    "label": "label-y",
                    "source_script": "script-y",
                    "start_command_index": 4,
                    "end_command_index": 5,
                    "zeia_file": "zeia-y",
                    "source_path": "path-y",
                    "specifications": {"empty": []},
                    "safety_notes": [],
                    "steps": [],
                },
            ],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = render.render_markdown(report)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            direct = native_acceleration.render_script_pattern_search(report)
            self.assertEqual(direct, expected)
            with patch.object(
                render,
                "_native_render_script_pattern_search",
                wraps=render._native_render_script_pattern_search,
            ) as native_call:
                actual = render.render_markdown(report)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertIn("1. Pattern: `pattern-x`", actual)
        self.assertIn("2. Pattern: `pattern-y`", actual)
        self.assertIn("     - key-x: `value-α`, `value-β`", actual)

        class TruthyFilter:
            def __init__(self) -> None:
                self.bool_calls = 0

            def __bool__(self) -> bool:
                self.bool_calls += 1
                return True

        custom = TruthyFilter()
        unsupported = {**report, "pattern_type_filter": custom}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.bool_calls, 1)
        custom.bool_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.bool_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

    def test_native_directory_report_matches_python_limits_and_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_directory_report"
        ):
            self.skipTest("optional Rust directory report renderer is not built")
        items = [
            {
                "kind": "xscr",
                "source": f"path-{index}",
                "object_name": f"object-{index}",
                "command_count": index,
                "family_counts": {f"key-{key}": key for key in range(6)},
            }
            for index in range(82)
        ]
        items.extend(
            {"kind": "other", "source": f"path-{index}"}
            for index in range(82, 102)
        )
        report = {
            "kind": "directory",
            "source": "source-x",
            "file_count": 102,
            "extension_counts": {f"extension-{index}": index for index in range(21)},
            "family_counts": {f"key-{index}": index for index in range(31)},
            "command_counts": {f"command-{index}": index for index in range(31)},
            "warning_counts": {f"warning-{index}": index for index in range(21)},
            "script_names": [f"script-{index}" for index in range(102)],
            "items": items,
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = render.render_markdown(report)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            direct = native_acceleration.render_directory_report(report)
            self.assertEqual(direct, expected)
            with patch.object(
                render,
                "_native_render_directory_report",
                wraps=render._native_render_directory_report,
            ) as native_call:
                actual = render.render_markdown(report)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertIn("object-79", actual)
        self.assertNotIn("object-80", actual)
        self.assertIn("key-4=4", actual)
        self.assertNotIn("key-5=5", actual)
        self.assertIn("- ... 2 more", actual)

        class TextValue:
            def __init__(self) -> None:
                self.str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom file source"

        custom = TextValue()
        unsupported = {
            "kind": "directory",
            "source": "source-x",
            "file_count": 1,
            "items": [{"kind": "other", "source": custom}],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

    def test_native_project_index_summary_matches_build_modes_and_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_project_index_summary"
        ):
            self.skipTest("optional Rust project-index summary renderer is not built")
        build_report = {
            "kind": "project_index_build",
            "database": "database-x",
            "schema_version": "schema-x",
            "zeia_file_count": 2,
            "script_count": 3,
            "command_count": 4,
            "command_sequence_count": 5,
            "catalog_object_count": 6,
            "worklist_count": 7,
            "indexed_files": [f"indexed-{index}" for index in range(102)],
            "entity_counts": {f"entity-{index}": index for index in range(42)},
            "command_family_counts": {f"key-{index}": index for index in range(32)},
            "files": [
                {
                    "file_name": f"file-{index}",
                    "script_count_summarized": index,
                    "object_count_summarized": index + 1,
                    "gwl_count_summarized": index + 2,
                    "path": f"path-{index}",
                }
                for index in range(102)
            ],
        }
        summary_report = {
            "kind": "project_index_summary",
            "database": "database-y",
            "schema_version": "schema-y",
            "zeia_file_count": 0,
            "script_count": 0,
            "command_count": 0,
            "command_sequence_count": 0,
            "catalog_object_count": 0,
            "worklist_count": 0,
        }
        reports = [build_report, summary_report]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [render.render_markdown(report) for report in reports]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                render,
                "_native_render_project_index_summary",
                wraps=render._native_render_project_index_summary,
            ) as native_call:
                actual = [render.render_markdown(report) for report in reports]
                self.assertEqual(native_call.call_count, len(reports))
        self.assertEqual(actual, expected)
        self.assertIn("# Tecan Project Index Build", actual[0])
        self.assertIn("- ... 2 more", actual[0])
        self.assertNotIn("# Tecan Project Index Build", actual[1])
        self.assertEqual(actual[1].count("## "), 0)

        class TextValue:
            def __init__(self) -> None:
                self.str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom file"

        custom = TextValue()
        unsupported = {
            **summary_report,
            "files": [
                {
                    "file_name": custom,
                    "script_count_summarized": 1,
                    "object_count_summarized": 1,
                    "gwl_count_summarized": 1,
                    "path": "path-x",
                }
            ],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

    def test_native_project_index_search_matches_python_and_preserves_truthiness(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_project_index_search"
        ):
            self.skipTest("optional Rust project-index search renderer is not built")
        report = {
            "kind": "project_index_search",
            "database": "database-x",
            "query": "query-β",
            "kind_filter": None,
            "result_count": 3,
            "results": [
                {
                    "kind": "kind-x",
                    "name": "",
                    "value": "title-from-value",
                    "source_path": "path-x",
                    "script": "script-x",
                    "command_index": 0,
                    "zeia_file": "file-x",
                },
                {
                    "kind": "kind-y",
                    "name": "title-y",
                    "value": "",
                    "source_path": "path-y",
                    "script": False,
                    "command_index": None,
                    "zeia_file": "file-y",
                },
                {
                    "kind": "kind-z",
                    "name": None,
                    "value": 0,
                    "source_path": "path-z",
                    "command_index": "",
                },
            ],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = render.render_markdown(report)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            direct = native_acceleration.render_project_index_search(report)
            self.assertEqual(direct, expected)
            with patch.object(
                render,
                "_native_render_project_index_search",
                wraps=render._native_render_project_index_search,
            ) as native_call:
                actual = render.render_markdown(report)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertIn("- Kind filter: `any`", actual)
        self.assertIn("   - Command: `0`", actual)
        self.assertIn("   - Command: ``", actual)
        self.assertNotIn("   - Script: `False`", actual)

        class TruthyText:
            def __init__(self) -> None:
                self.bool_calls = 0
                self.str_calls = 0

            def __bool__(self) -> bool:
                self.bool_calls += 1
                return True

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom title"

        custom = TruthyText()
        unsupported = {
            "kind": "project_index_search",
            "results": [
                {
                    "kind": "kind-x",
                    "name": custom,
                    "value": None,
                    "source_path": "path-x",
                    "script": None,
                    "command_index": None,
                    "zeia_file": "file-x",
                }
            ],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render.render_markdown(unsupported)
        self.assertEqual((custom.bool_calls, custom.str_calls), (1, 1))
        custom.bool_calls = custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_fallback = render.render_markdown(unsupported)
        self.assertEqual((custom.bool_calls, custom.str_calls), (1, 1))
        self.assertEqual(actual_fallback, expected_fallback)

    def test_native_script_pattern_summary_matches_both_modes_and_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_script_pattern_summary"
        ):
            self.skipTest("optional Rust pattern-summary renderer is not built")
        reports = [
            {
                "kind": "script_pattern_mining",
                "database": "database-x",
                "pattern_schema_version": "schema-x",
                "pattern_count": 41,
                "step_count": 8,
                "mined_pattern_count": 3,
                "replaced_existing": False,
                "pattern_type_counts": {f"key-{index}": index for index in range(42)},
                "top_sources": [
                    {
                        "source_script": "source-x",
                        "pattern_count": 2,
                        "zeia_file": "file-x",
                    }
                ],
            },
            {
                "kind": "script_pattern_summary",
                "database": "database-y",
                "pattern_schema_version": "schema-y",
                "pattern_count": 0,
                "step_count": 0,
                "pattern_type_counts": {},
                "top_sources": [],
            },
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [render.render_markdown(report) for report in reports]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                render,
                "_native_render_script_pattern_summary",
                wraps=render._native_render_script_pattern_summary,
            ) as native_call:
                actual = [render.render_markdown(report) for report in reports]
                self.assertEqual(native_call.call_count, len(reports))
        self.assertEqual(actual, expected)
        self.assertIn("- ... 2 more", actual[0])
        self.assertNotIn("Mined this run", actual[1])

        class TextValue:
            def __init__(self) -> None:
                self.str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom source"

        custom = TextValue()
        unsupported = {
            **reports[0],
            "top_sources": [
                {
                    "source_script": custom,
                    "pattern_count": 1,
                    "zeia_file": "file-x",
                }
            ],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

    def test_native_pattern_types_report_matches_python_and_falls_back(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_pattern_types_report"
        ):
            self.skipTest("optional Rust pattern-type report renderer is not built")
        report = {
            "kind": "script_pattern_types",
            "pattern_types": [
                {
                    "pattern_type": "type-x",
                    "label": "label-x",
                    "description": "description-β",
                },
                {},
            ],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = render.render_markdown(report)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            direct = native_acceleration.render_pattern_types_report(
                report["pattern_types"]
            )
            self.assertEqual(direct, expected)
            with patch.object(
                render,
                "_native_render_pattern_types_report",
                wraps=render._native_render_pattern_types_report,
            ) as native_call:
                actual = render.render_markdown(report)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)

        class TextValue:
            def __init__(self) -> None:
                self.str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom label"

        custom = TextValue()
        unsupported = {
            "kind": "script_pattern_types",
            "pattern_types": [{"pattern_type": "type-x", "label": custom}],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

    def test_native_compare_report_matches_python_limits_and_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_compare_report"
        ):
            self.skipTest("optional Rust compare-report renderer is not built")
        report = {
            "kind": "zeia_compare",
            "left": "left-x",
            "right": "right-x",
            "left_entry_count": 2,
            "right_entry_count": 3,
            "script_names_common_count": 1,
            "script_names_added": [f"added-{index}" for index in range(102)],
            "script_names_removed": ("removed-β",),
            "left_family_counts": {f"key-{index}": index for index in range(22)},
            "right_family_counts": {"key-z": "last"},
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = render.render_markdown(report)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            direct = native_acceleration.render_compare_report(
                report["left"],
                report["right"],
                report["left_entry_count"],
                report["right_entry_count"],
                report["script_names_common_count"],
                report["script_names_added"],
                report["script_names_removed"],
                report["left_family_counts"],
                report["right_family_counts"],
            )
            self.assertEqual(direct, expected)
            with patch.object(
                render,
                "_native_render_compare_report",
                wraps=render._native_render_compare_report,
            ) as native_call:
                actual = render.render_markdown(report)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(actual.count("- ... 2 more"), 2)

        class TextValue:
            def __init__(self) -> None:
                self.str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom left"

        custom = TextValue()
        unsupported = {**report, "left": custom}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.render_compare_report(
                    custom,
                    report["right"],
                    report["left_entry_count"],
                    report["right_entry_count"],
                    report["script_names_common_count"],
                    report["script_names_added"],
                    report["script_names_removed"],
                    report["left_family_counts"],
                    report["right_family_counts"],
                )
            )
            self.assertEqual(custom.str_calls, 0)
            actual_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

    def test_native_gwl_report_matches_python_and_falls_back_safely(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "render_gwl_report"
        ):
            self.skipTest("optional Rust GWL report renderer is not built")
        report = {
            "kind": "gwl",
            "source": "synthetic-source",
            "line_count": 3,
            "transfer_pairs_estimate": 1,
            "record_counts": {"record-a": 2, "record-b": 1},
            "pipette_examples": [
                {
                    "line": 7,
                    "operation": "operation-x",
                    "rack_label": "rack-x",
                    "position": "position-x",
                    "volume": "volume-x",
                    "liquid_class": "class-x",
                }
            ],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = render.render_markdown(report)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            direct = native_acceleration.render_gwl_report(
                report["source"],
                report["line_count"],
                report["transfer_pairs_estimate"],
                report["record_counts"],
                report["pipette_examples"],
            )
            self.assertEqual(direct, expected)
            with patch.object(
                render,
                "_native_render_gwl_report",
                wraps=render._native_render_gwl_report,
            ) as native_call:
                actual = render.render_markdown(report)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)

        class TextValue:
            def __init__(self) -> None:
                self.str_calls = 0

            def __str__(self) -> str:
                self.str_calls += 1
                return "custom source"

        custom = TextValue()
        unsupported = {
            "kind": "gwl",
            "source": custom,
            "line_count": 0,
            "transfer_pairs_estimate": 0,
            "record_counts": {},
            "pipette_examples": [],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        custom.str_calls = 0
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_fallback = render.render_markdown(unsupported)
        self.assertEqual(custom.str_calls, 1)
        self.assertEqual(actual_fallback, expected_fallback)

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
