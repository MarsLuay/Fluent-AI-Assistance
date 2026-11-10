from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_common import xml_compat as ET
import tecan_reader.task_input as task_input
from tecan_reader import inspect_twl_text
from tecan_reader.zeia_adapters import ingest_zeia


class SchedulerAndTaskInputTests(unittest.TestCase):
    @staticmethod
    def _archive(root: Path, entries: dict[str, str]) -> Path:
        path = root / "scheduler.zeia"
        with zipfile.ZipFile(path, "w") as archive:
            for name, content in entries.items():
                archive.writestr(name, content)
        return path

    def test_base_worktable_identity_preserves_same_name_guid_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(
                Path(tmp),
                {
                    "Objects/one.xwsp": (
                        "<Workspace><ObjectName>Deck</ObjectName>"
                        "<BaseWorktableName>Shared Deck</BaseWorktableName>"
                        "<BaseWorktableGuid>guid-a</BaseWorktableGuid></Workspace>"
                    ),
                    "Objects/two.xwsp": (
                        "<Workspace><ObjectName>Deck</ObjectName>"
                        "<BaseWorktableName>Shared Deck</BaseWorktableName>"
                        "<BaseWorktableGuid>guid-b</BaseWorktableGuid></Workspace>"
                    ),
                },
            )
            model = ingest_zeia(archive)

        identity = model.base_worktable_identity
        self.assertEqual(identity["status"], "ambiguous")
        self.assertEqual(
            {(item["name"], item["guid"]) for item in identity["candidates"]},
            {("Shared Deck", "guid-a"), ("Shared Deck", "guid-b")},
        )
        self.assertEqual(model.objects[0]["base_worktable_name"], "Shared Deck")
        self.assertEqual(model.objects[0]["provenance"]["original_identifiers"]["base_worktable_guid"], "guid-a")

    def test_scheduler_processes_tasks_are_distinct_from_gwl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            archive = self._archive(
                Path(tmp),
                {
                    "Scheduler/schedule.xml": (
                        "<Scheduler><Process Name=\"Process A\" FutureMode=\"hold\">"
                        "<Task Name=\"Task A\" VendorField=\"preserve\"><Script>Run</Script>"
                        "</Task></Process></Scheduler>"
                    ),
                    "Inputs/run.twl": "TaskName,Value\nTask A,42\n",
                    "Worklists/run.gwl": "A;Source;;Rack;1;;10;Water\n",
                },
            )
            model = ingest_zeia(archive)

        self.assertEqual(len(model.worklists), 1)
        self.assertEqual(len(model.scheduler_processes), 1)
        self.assertEqual(len(model.scheduler_tasks), 1)
        self.assertEqual(len(model.task_inputs), 1)
        self.assertEqual(model.scheduler_processes[0]["kind"], "scheduler_process")
        self.assertEqual(model.scheduler_tasks[0]["source_metadata"]["unknown_fields"]["VendorField"], ["preserve"])
        self.assertEqual(model.task_inputs[0]["records"][0]["fields"]["TaskName"], "Task A")
        self.assertNotEqual(model.task_inputs[0]["kind"], "worklist")

    def test_twl_record_selection_matches_python_casefold_for_unicode_tags(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_elements_by_folded_name_with_children"
        ):
            self.skipTest("optional Rust XML record selector is not built")
        source = (
            '<TaskInput><Task Name="first"><Value>1</Value></Task>'
            "<taskrecord><TaskName>second</TaskName><Value>2</Value></taskrecord>"
            "<ITEM><Name>third</Name></ITEM><Input/><Other><Value>ignored</Value>"
            "</Other></TaskInput>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = inspect_twl_text(source, source_name="Inputs/records.twl")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "xml_elements_by_folded_name_with_children",
                wraps=native_acceleration.xml_elements_by_folded_name_with_children,
            ) as native_call:
                actual = inspect_twl_text(source, source_name="Inputs/records.twl")
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(actual["record_count"], 3)
        self.assertEqual(
            [record["fields"].get("Name") or record["fields"].get("TaskName") for record in actual["records"]],
            ["first", "second", "third"],
        )

        unicode_root = ET.Element("TaskInput")
        unicode_task = ET.SubElement(unicode_root, "Taſk")
        ET.SubElement(unicode_task, "Name").text = "unicode"
        record_names = {"task", "taskrecord", "input", "item", "record"}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_nodes = native_acceleration.xml_elements_by_folded_name_with_children(
                unicode_root, record_names, ET.Element
            )
            fallback_nodes = [
                element
                for element in unicode_root.iter()
                if isinstance(element.tag, str)
                and task_input.local_name(element.tag).casefold() in record_names
                and list(element)
            ]
        self.assertEqual(native_nodes, fallback_nodes)
        self.assertEqual(fallback_nodes, [unicode_task])
        self.assertEqual(
            task_input._element_record(unicode_task, index=0)["fields"]["Name"],
            "unicode",
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            with patch.object(task_input, "parse_xml_text", return_value=unicode_root):
                expected_unicode = task_input._inspect_xml(
                    "<unused/>", source_name="Inputs/unicode-record.twl"
                )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(task_input, "parse_xml_text", return_value=unicode_root):
                actual_unicode = task_input._inspect_xml(
                    "<unused/>", source_name="Inputs/unicode-record.twl"
                )
        self.assertEqual(actual_unicode, expected_unicode)
        self.assertEqual(actual_unicode["record_count"], 1)

    def test_twl_xml_whitespace_normalization_uses_native_unicode(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "collapse_ascii_whitespace"
        ):
            self.skipTest("optional Rust whitespace normalizer is not built")
        source = (
            "<TaskInput><Task><Name>  first\t\t task </Name>"
            "<Value>\n  1 \t 2 \r\n</Value><Future> alpha \t beta </Future>"
            "</Task></TaskInput>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = inspect_twl_text(source, source_name="Inputs/spacing.twl")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "collapse_ascii_whitespace",
                wraps=native_acceleration.collapse_ascii_whitespace,
            ) as native_call:
                actual = inspect_twl_text(source, source_name="Inputs/spacing.twl")
                self.assertGreater(native_call.call_count, 0)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual["records"][0]["unknown_fields"]["Future"], ["alpha beta"]
        )

        unicode_text = "café\N{NO-BREAK SPACE} user"
        unicode_source = f"<TaskInput><Task><Name>{unicode_text}</Name></Task></TaskInput>"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = inspect_twl_text(
                unicode_source, source_name="Inputs/unicode.twl"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.collapse_ascii_whitespace(unicode_text),
                "café user",
            )
            with patch.object(
                task_input,
                "collapse_ascii_whitespace",
                wraps=native_acceleration.collapse_ascii_whitespace,
            ) as native_call:
                unicode_actual = inspect_twl_text(
                    unicode_source, source_name="Inputs/unicode.twl"
                )
                self.assertGreater(native_call.call_count, 0)
        self.assertEqual(unicode_actual, unicode_expected)
        self.assertEqual(
            unicode_actual["records"][0]["fields"]["Name"], "café user"
        )

        whitespace_chars = [
            chr(codepoint)
            for codepoint in range(0x110000)
            if chr(codepoint).isspace()
        ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            for character in whitespace_chars:
                text = f"α{character}{character}β"
                self.assertEqual(
                    native_acceleration.collapse_ascii_whitespace(text),
                    " ".join(text.split()),
                    f"whitespace U+{ord(character):04X}",
                )
            self.assertIsNone(
                native_acceleration.collapse_ascii_whitespace(chr(0xD800))
            )

    def test_delimited_row_field_normalization_matches_python_for_unicode(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "delimited_row_fields"
        ):
            self.skipTest("optional Rust delimited-row normalizer is not built")
        row = ["  first  ", "\x1c", "\t42\r", "extra"]
        header = ["duplicate", "duplicate", ""]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = task_input._row_fields(row, header, True)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "delimited_row_fields",
                wraps=native_acceleration.delimited_row_fields,
            ) as native_call:
                actual = task_input._row_fields(row, header, True)
                native_call.assert_called_once_with(row, header, True)
        self.assertEqual(actual, expected)
        self.assertEqual(actual, {"duplicate": "first", "": "42", "column_4": "extra"})

        unicode_row = ["\u00a0text\u00a0", "\x1cβ\x1f", "東京", "\u2003"]
        unicode_header = ["列", "列", "", "ignored"]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = task_input._row_fields(
                unicode_row, unicode_header, True
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.delimited_row_fields(
                    unicode_row, unicode_header, True
                ),
                unicode_expected,
            )
            self.assertEqual(
                task_input._row_fields(unicode_row, unicode_header, True),
                unicode_expected,
            )
        self.assertEqual(unicode_expected, {"列": "β", "": "東京"})

        whitespace_chars = [
            chr(codepoint)
            for codepoint in range(0x110000)
            if chr(codepoint).isspace()
        ]
        whitespace_row = [
            f"{character}value-{index}{character}"
            for index, character in enumerate(whitespace_chars)
        ]
        whitespace_header = [f"field_{index}" for index in range(len(whitespace_row))]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            whitespace_expected = task_input._row_fields(
                whitespace_row, whitespace_header, True
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertEqual(
                native_acceleration.delimited_row_fields(
                    whitespace_row, whitespace_header, True
                ),
                whitespace_expected,
            )
        self.assertEqual(
            list(whitespace_expected.values()),
            [f"value-{index}" for index in range(len(whitespace_chars))],
        )

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.delimited_row_fields([chr(0xD800)], [], False)
            )
            self.assertEqual(
                task_input._row_fields([chr(0xD800)], [], False),
                {"column_1": chr(0xD800)},
            )

    def test_plain_twl_delimited_rows_use_native_with_quoted_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "delimited_text_rows"
        ):
            self.skipTest("optional Rust delimited-text reader is not built")
        plain = (
            "taskname,parameter\r\n"
            "Run-1,\u00a0β\u00a0\r\n"
            "\r\n"
            "Run-2,東京\r\n"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = inspect_twl_text(plain, source_name="Inputs/plain.twl")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "delimited_text_rows",
                wraps=native_acceleration.delimited_text_rows,
            ) as native_call:
                actual = inspect_twl_text(plain, source_name="Inputs/plain.twl")
                native_call.assert_called_once_with(plain, ",")
        self.assertEqual(actual, expected)
        self.assertEqual(actual["record_count"], 2)
        self.assertEqual(actual["records"][0]["parameter"], "β")
        self.assertEqual(actual["records"][1]["task_name"], "Run-2")

        tab_text = "TASKNAME\tPARAMETER\nRun-3\tλ\n"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            tab_expected = inspect_twl_text(
                tab_text, source_name="Inputs/tab-separated.twl"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            tab_actual = inspect_twl_text(
                tab_text, source_name="Inputs/tab-separated.twl"
            )
            self.assertIsNone(
                native_acceleration.delimited_text_rows(
                    "taskname,parameter\rRun-4,value", ","
                )
            )
        self.assertEqual(tab_actual, tab_expected)
        self.assertEqual(tab_actual["records"][0]["parameter"], "λ")

        quoted = 'taskname,parameter\nRun-1,"alpha,beta"\n'
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            quoted_expected = inspect_twl_text(quoted, source_name="Inputs/quoted.twl")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(native_acceleration.delimited_text_rows(quoted, ","))
            with patch.object(
                task_input,
                "delimited_text_rows",
                wraps=native_acceleration.delimited_text_rows,
            ) as native_call:
                quoted_actual = inspect_twl_text(
                    quoted, source_name="Inputs/quoted.twl"
                )
                native_call.assert_called_once_with(quoted, ",")
        self.assertEqual(quoted_actual, quoted_expected)
        self.assertEqual(quoted_actual["records"][0]["parameter"], "alpha,beta")

    def test_xml_unknown_task_fields_sort_natively_with_python_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "sorted_unique_strings"
        ):
            self.skipTest("optional Rust string ordering helpers are not built")
        xml = (
            "<Tasks><Task><TaskName>Import</TaskName>"
            "<VendorField>zeta</VendorField><VendorField>alpha</VendorField>"
            "<VendorField>zeta</VendorField>"
            "<Alpha>β</Alpha><Alpha>α</Alpha></Task></Tasks>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = inspect_twl_text(xml, source_name="Inputs/unknown-fields.twl")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with (
                patch.object(
                    task_input,
                    "sort_string_keys",
                    wraps=native_acceleration.sort_string_keys,
                ) as sort_call,
                patch.object(
                    task_input,
                    "sorted_unique_strings",
                    wraps=native_acceleration.sorted_unique_strings,
                ) as unique_call,
            ):
                actual = inspect_twl_text(
                    xml, source_name="Inputs/unknown-fields.twl"
                )
                self.assertGreaterEqual(sort_call.call_count, 2)
                self.assertEqual(unique_call.call_count, 2)
        self.assertEqual(actual, expected)
        self.assertEqual(
            actual["records"][0]["unknown_fields"],
            {"Alpha": ["α", "β"], "VendorField": ["alpha", "zeta"]},
        )

    def test_delimited_header_detection_uses_native_ascii_and_unicode_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "any_ascii_casefold_in"
        ):
            self.skipTest("optional Rust header matcher is not built")
        source = "TASKNAME,VALUE\nRun,42\n"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = inspect_twl_text(source, source_name="Inputs/header.twl")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "any_ascii_casefold_in",
                wraps=native_acceleration.any_ascii_casefold_in,
            ) as native_call:
                actual = inspect_twl_text(source, source_name="Inputs/header.twl")
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(actual["header"], ["TASKNAME", "VALUE"])

        unicode_source = "TâskName,Payload\nRun,42\n"
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = inspect_twl_text(
                unicode_source, source_name="Inputs/unicode-header.twl"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                native_acceleration.any_ascii_casefold_in(
                    ["TâskName", "Payload"], task_input._KNOWN_FIELDS
                )
            )
            unicode_actual = inspect_twl_text(
                unicode_source, source_name="Inputs/unicode-header.twl"
            )
        self.assertEqual(unicode_actual, unicode_expected)
        self.assertEqual(unicode_actual["header"], [])

    def test_task_field_key_sorting_uses_rust_and_preserves_values(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "sort_string_keys"
        ):
            self.skipTest("optional Rust field-key sorter is not built")
        nested = {"preserve": "identity"}
        fields = {"zeta": nested, "alpha": "first"}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = task_input._sorted_fields(fields)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "sort_string_keys",
                wraps=native_acceleration.sort_string_keys,
            ) as native_call:
                actual = task_input._sorted_fields(fields)
                native_call.assert_called_once_with(fields)
        self.assertEqual(list(actual), ["alpha", "zeta"])
        self.assertEqual(actual, expected)
        self.assertIs(actual["zeta"], nested)

        unicode_fields = {"éclair": "unicode", "alpha": "first"}
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_sorted = native_acceleration.sort_string_keys(unicode_fields)
            self.assertIsNotNone(native_sorted)
            self.assertEqual(
                list(native_sorted), list(dict(sorted(unicode_fields.items())))
            )
            self.assertEqual(
                task_input._sorted_fields(unicode_fields),
                dict(sorted(unicode_fields.items())),
            )

    def test_task_unknown_field_groups_merge_natively_with_unicode(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "merge_unique_string_groups"
        ):
            self.skipTest("optional Rust unknown-field merger is not built")
        values = "".join(f"<Future>value-{index:02d}</Future>" for index in range(23))
        source = (
            f"<TaskInput><Task AlphaField=\"kept\">{values}</Task>"
            "<Task><Future>value-00</Future><Future>later</Future></Task></TaskInput>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = inspect_twl_text(source, source_name="Inputs/unknowns.twl")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "merge_unique_string_groups",
                wraps=native_acceleration.merge_unique_string_groups,
            ) as native_call:
                actual = inspect_twl_text(source, source_name="Inputs/unknowns.twl")
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(list(actual["unknown_fields"]), ["AlphaField", "Future"])
        self.assertEqual(
            actual["unknown_fields"]["Future"],
            [f"value-{index:02d}" for index in range(20)],
        )
        self.assertIsNot(
            actual["unknown_fields"], actual["source_metadata"]["unknown_fields"]
        )

        unicode_groups = [
            {"étiquette": ["café", "α", "café"]},
            {"étiquette": ["東京"], "Ω": ["器"], "😀": ["emoji"]},
        ]
        unicode_expected = {
            "étiquette": ["café", "α", "東京"],
            "Ω": ["器"],
            "😀": ["emoji"],
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            unicode_merged = native_acceleration.merge_unique_string_groups(
                unicode_groups, limit=20
            )
            self.assertEqual(unicode_merged, unicode_expected)
            self.assertEqual(list(unicode_merged), list(unicode_expected))
            self.assertEqual(
                native_acceleration.merge_unique_string_groups(
                    unicode_groups, limit=2
                )["étiquette"],
                ["café", "α"],
            )
            self.assertIsNone(
                native_acceleration.merge_unique_string_groups(
                    [{chr(0xD800): ["value"]}], limit=20
                )
            )
            self.assertIsNone(
                native_acceleration.merge_unique_string_groups(
                    [{"field": [chr(0xD800)]}], limit=20
                )
            )

        unicode_source = (
            "<TaskInput><Task><Étiquette>café</Étiquette>"
            "<Future>α</Future></Task><Task><Étiquette>東京</Étiquette>"
            "</Task></TaskInput>"
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = inspect_twl_text(
                unicode_source, source_name="Inputs/unicode-unknowns.twl"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "merge_unique_string_groups",
                wraps=native_acceleration.merge_unique_string_groups,
            ) as native_call:
                unicode_actual = inspect_twl_text(
                    unicode_source, source_name="Inputs/unicode-unknowns.twl"
                )
                native_call.assert_called_once()
        self.assertEqual(unicode_actual, unicode_expected)
        self.assertEqual(
            unicode_actual["unknown_fields"],
            {"Future": ["α"], "Étiquette": ["café", "東京"]},
        )
        self.assertEqual(list(unicode_actual["unknown_fields"]), ["Future", "Étiquette"])

    def test_task_field_alias_lookup_uses_native_for_ascii_and_falls_back(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "casefold_alias_values"
        ):
            self.skipTest("optional Rust field-alias lookup is not built")
        nested_value = {"nested": "preserved"}
        values = {
            "TaskID": "older",
            "taskid": "latest",
            "TaskName": "",
            "Task_Name": "Run assay",
            "Process": "Scheduler A",
            "DueDateTime": "",
            "Due": "tomorrow",
            "Parameter": nested_value,
            "Comment": "review",
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = task_input._normalized_task_fields(values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "casefold_alias_values",
                wraps=native_acceleration.casefold_alias_values,
            ) as native_call:
                actual = task_input._normalized_task_fields(values)
                native_call.assert_called_once()
        self.assertEqual(actual, expected)
        self.assertEqual(actual["task_id"], "latest")
        self.assertEqual(actual["task_name"], "Run assay")
        self.assertEqual(actual["due_time"], "tomorrow")
        self.assertIs(actual["parameter"], nested_value)

        unicode_values = {
            "Task_Name": "café",
            "taskname": "",
            "TâskName": "unmatched",
            "TaſkName": "casefolded",
        }
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            unicode_expected = task_input._normalized_task_fields(unicode_values)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                task_input,
                "casefold_alias_values",
                wraps=native_acceleration.casefold_alias_values,
            ) as native_call:
                unicode_actual = task_input._normalized_task_fields(unicode_values)
                native_call.assert_called_once()
        self.assertEqual(unicode_actual, unicode_expected)
        self.assertEqual(unicode_actual["task_name"], "casefolded")

    def test_twl_xml_preserves_additive_record_fields_and_order(self) -> None:
        result = inspect_twl_text(
            "<TaskInput><Task Name=\"one\" FutureField=\"x\"><Value>7</Value>"
            "</Task><Task Name=\"two\"><Value>8</Value></Task></TaskInput>",
            source_name="Inputs/example.twl",
        )
        self.assertEqual(result["format"], "xml")
        self.assertEqual([record["index"] for record in result["records"]], [0, 1])
        self.assertEqual(result["records"][0]["unknown_fields"]["FutureField"], ["x"])
        self.assertEqual(result["unknown_fields"]["FutureField"], ["x"])


if __name__ == "__main__":
    unittest.main()
