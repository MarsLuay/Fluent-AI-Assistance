from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from tecan_common import native_acceleration
from tecan_common import xml_compat as ET
import tecan_reader.scheduler as scheduler
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

    def test_scheduler_artifact_heuristic_uses_native_ascii_match_with_parity(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native,
            "ascii_case_insensitive_substring_match_groups",
        ):
            self.skipTest("optional Rust substring matcher is not built")
        cases = (
            ("archive/scheduler.xml", "<Root/>", True),
            ("archive/data.xml", "<Root><SCHEDULEDTask/></Root>", True),
            ("archive/data.xml", "<Root><Other/></Root>", False),
            ("archive/ſcheduler.xml", "<Root/>", True),
            ("archive/data.xml", "<Root><Taſk/></Root>", True),
            ("archive/data.xml", "x" * (256 * 1024) + "<Scheduler/>", False),
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = [
                scheduler.is_scheduler_artifact(entry, text)
                for entry, text, _ in cases
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration._native,
                "ascii_case_insensitive_substring_match_groups",
                wraps=native_acceleration._native.ascii_case_insensitive_substring_match_groups,
            ) as rust_call:
                with patch.object(
                    scheduler,
                    "_native_substring_match_groups",
                    wraps=scheduler._native_substring_match_groups,
                ) as native_call:
                    actual = [
                        scheduler.is_scheduler_artifact(entry, text)
                        for entry, text, _ in cases
                    ]
                    self.assertEqual(native_call.call_count, 10)
                    self.assertEqual(rust_call.call_count, 8)

        self.assertEqual(actual, expected)
        self.assertEqual(actual, [expected_value for _, _, expected_value in cases])

    def test_scheduler_record_selection_preserves_source_indexes_with_native_scan(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_indexed_elements_by_folded_name"
        ):
            self.skipTest("optional Rust indexed XML selector is not built")
        source = (
            '<Root><Other/><Process Name="Process A" VendorMode="z"/><Gap/>'
            '<Task Name="Task A" VendorMode="a"/>'
            '<Task Name="Task B" VendorMode="z"/></Root>'
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = scheduler.inspect_scheduler_text(
                source, source_name="synthetic.xml"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration._native,
                "xml_indexed_elements_by_folded_name",
                wraps=native_acceleration._native.xml_indexed_elements_by_folded_name,
            ) as rust_call:
                with patch.object(
                    scheduler,
                    "_native_indexed_elements_by_folded_name",
                    wraps=scheduler._native_indexed_elements_by_folded_name,
                ) as native_call:
                    with patch.object(
                        scheduler,
                        "_native_merge_unique_string_groups",
                        wraps=scheduler._native_merge_unique_string_groups,
                    ) as merge_call:
                        actual = scheduler.inspect_scheduler_text(
                            source, source_name="synthetic.xml"
                        )
                        native_call.assert_called_once()
                        merge_call.assert_called_once()
                    self.assertEqual(rust_call.call_count, 1)

        self.assertEqual(actual, expected)
        self.assertEqual(
            [item["record_index"] for item in actual["scheduler_processes"]],
            [2],
        )
        self.assertEqual(
            [item["record_index"] for item in actual["scheduler_tasks"]],
            [4, 5],
        )
        self.assertEqual(
            actual["source_metadata"]["unknown_fields"],
            {"VendorMode": ["a", "z"]},
        )

        unicode_root = ET.Element("Root")
        ET.SubElement(unicode_root, "Other")
        unicode_root.append(ET.Comment("ignored tag still occupies an index"))
        ET.SubElement(unicode_root, "Taſk")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_indexed = [
                (index, element)
                for index, element in enumerate(unicode_root.iter())
                if isinstance(element.tag, str)
                and scheduler.local_name(element.tag).casefold()
                in scheduler._SCHEDULER_NAMES
            ]
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            actual_indexed = scheduler._native_indexed_elements_by_folded_name(
                unicode_root, scheduler._SCHEDULER_NAMES, ET.Element
            )
        self.assertIsNotNone(actual_indexed)
        self.assertEqual(actual_indexed, expected_indexed)
        self.assertEqual([index for index, _ in actual_indexed], [3])

        class StringSubclass(str):
            pass

        unsupported_root = ET.Element("Root")
        unsupported_task = ET.SubElement(unsupported_root, "Task")
        unsupported_task.tag = StringSubclass("Task")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                scheduler._native_indexed_elements_by_folded_name(
                    unsupported_root, scheduler._SCHEDULER_NAMES, ET.Element
                )
            )

    def test_scheduler_record_leaf_fields_use_native_scan_with_python_fallback(self) -> None:
        if not native_acceleration.available() or not all(
            hasattr(native_acceleration._native, name)
            for name in (
                "xml_attribute_name_values",
                "xml_direct_leaf_name_texts",
            )
        ):
            self.skipTest("optional Rust XML leaf-field collector is not built")
        root = ET.Element(
            "{urn:test}Process",
            {
                "Name": "attribute label",
                "TaskID": "attribute-id",
                "VendorAttr": "source",
                "{urn:test}Étiquette": "café",
                "{urn:other}VendorAttr": "qualified-source",
            },
        )
        ET.SubElement(root, "{urn:test}Name").text = " child\u001c label "
        ET.SubElement(root, "TaskID").text = " child-id "
        ET.SubElement(root, "VendorField").text = " one\n two "
        ET.SubElement(root, "VendorField").text = "one two"
        nested = ET.SubElement(root, "Nested")
        ET.SubElement(nested, "Script").text = "not a direct leaf"
        ET.SubElement(root, "Description").text = " \t "
        root.append(ET.Comment("ignored child"))

        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = scheduler._record(
                root, index=7, kind="scheduler_task", source_name="synthetic.xml"
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration._native,
                "xml_direct_leaf_name_texts",
                wraps=native_acceleration._native.xml_direct_leaf_name_texts,
            ) as rust_call:
                with patch.object(
                    scheduler,
                    "_native_xml_direct_leaf_name_texts",
                    wraps=scheduler._native_xml_direct_leaf_name_texts,
                ) as native_call:
                    with patch.object(
                        scheduler,
                        "_native_sort_string_keys",
                        wraps=scheduler._native_sort_string_keys,
                    ) as sort_call:
                        with patch.object(
                            scheduler,
                            "_native_sorted_unique_strings",
                            wraps=scheduler._native_sorted_unique_strings,
                        ) as sorted_values_call:
                            actual = scheduler._record(
                                root,
                                index=7,
                                kind="scheduler_task",
                                source_name="synthetic.xml",
                            )
                            native_call.assert_called_once_with(root, ET.Element)
                            self.assertEqual(sort_call.call_count, 2)
                            self.assertEqual(sorted_values_call.call_count, 3)
                    self.assertEqual(rust_call.call_count, 1)

        self.assertEqual(actual, expected)
        self.assertEqual(actual["fields"]["Name"], "child label")
        self.assertEqual(actual["fields"]["TaskID"], "child-id")
        self.assertEqual(
            actual["source_metadata"]["unknown_fields"],
            {
                "VendorAttr": ["qualified-source", "source"],
                "VendorField": ["one two"],
                "Étiquette": ["café"],
            },
        )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            native_attributes = native_acceleration.xml_attribute_name_values(
                root, ET.Element
            )
            with patch.object(
                scheduler,
                "_native_xml_attribute_name_values",
                wraps=scheduler._native_xml_attribute_name_values,
            ) as attributes_call:
                actual_with_attribute_spy = scheduler._record(
                    root,
                    index=7,
                    kind="scheduler_task",
                    source_name="synthetic.xml",
                )
                attributes_call.assert_called_once_with(root, ET.Element)
        self.assertEqual(
            native_attributes,
            [
                ("Name", "attribute label"),
                ("TaskID", "attribute-id"),
                ("VendorAttr", "source"),
                ("Étiquette", "café"),
                ("VendorAttr", "qualified-source"),
            ],
        )
        self.assertEqual(actual_with_attribute_spy, actual)

        unicode_alias_root = ET.Element("Task", {"TaſkID": "casefolded"})
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_alias = scheduler._record(
                unicode_alias_root,
                index=0,
                kind="scheduler_task",
                source_name="synthetic.xml",
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                scheduler,
                "_native_casefold_alias_values",
                wraps=scheduler._native_casefold_alias_values,
            ) as alias_call:
                actual_alias = scheduler._record(
                    unicode_alias_root,
                    index=0,
                    kind="scheduler_task",
                    source_name="synthetic.xml",
                )
                alias_call.assert_called_once()
        self.assertEqual(actual_alias, expected_alias)
        self.assertEqual(actual_alias["task_id"], "casefolded")

        class StringSubclass(str):
            pass

        unsupported_root = ET.Element("Task")
        unsupported_child = ET.SubElement(unsupported_root, "VendorField")
        unsupported_child.tag = StringSubclass("VendorField")
        unsupported_child.text = StringSubclass(" preserved ")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_fallback = scheduler._record(
                unsupported_root,
                index=0,
                kind="scheduler_task",
                source_name="synthetic.xml",
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                scheduler._native_xml_direct_leaf_name_texts(
                    unsupported_root, ET.Element
                )
            )
            actual_fallback = scheduler._record(
                unsupported_root,
                index=0,
                kind="scheduler_task",
                source_name="synthetic.xml",
            )
        self.assertEqual(actual_fallback, expected_fallback)

        unsupported_text_root = ET.Element("Task")
        unsupported_text_child = ET.SubElement(unsupported_text_root, "VendorField")
        unsupported_text_child.text = StringSubclass(" preserved ")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_text_fallback = scheduler._record(
                unsupported_text_root,
                index=0,
                kind="scheduler_task",
                source_name="synthetic.xml",
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                scheduler._native_xml_direct_leaf_name_texts(
                    unsupported_text_root, ET.Element
                )
            )
            actual_text_fallback = scheduler._record(
                unsupported_text_root,
                index=0,
                kind="scheduler_task",
                source_name="synthetic.xml",
            )
        self.assertEqual(actual_text_fallback, expected_text_fallback)

        unsupported_attribute_root = ET.Element("Task")
        unsupported_attribute_root.attrib["VendorField"] = StringSubclass(" preserved ")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected_attribute_fallback = scheduler._record(
                unsupported_attribute_root,
                index=0,
                kind="scheduler_task",
                source_name="synthetic.xml",
            )
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                scheduler._native_xml_attribute_name_values(
                    unsupported_attribute_root, ET.Element
                )
            )
            actual_attribute_fallback = scheduler._record(
                unsupported_attribute_root,
                index=0,
                kind="scheduler_task",
                source_name="synthetic.xml",
            )
        self.assertEqual(actual_attribute_fallback, expected_attribute_fallback)

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

    def test_twl_xml_attribute_fields_use_native_scan_with_fallback(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_attribute_name_values"
        ):
            self.skipTest("optional Rust XML attribute collector is not built")
        root = ET.Element(
            "Task",
            {
                "Name": " attribute name ",
                "Future": " first attribute ",
                "{urn:test}Future": "second attribute",
                "Empty": "",
                "taskname": "attribute task",
            },
        )
        ET.SubElement(root, "Future").text = " child value "
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = task_input._element_record(root, index=3)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            with patch.object(
                native_acceleration._native,
                "xml_attribute_name_values",
                wraps=native_acceleration._native.xml_attribute_name_values,
            ) as rust_call:
                with patch.object(
                    task_input,
                    "_native_xml_attribute_name_values",
                    wraps=task_input._native_xml_attribute_name_values,
                ) as native_call:
                    actual = task_input._element_record(root, index=3)
                    native_call.assert_called_once_with(root, ET.Element)
                    rust_call.assert_called_once()
                native_attributes = native_acceleration.xml_attribute_name_values(
                    root, ET.Element
                )
        self.assertEqual(actual, expected)
        self.assertEqual(actual["fields"]["Name"], " attribute name ")
        self.assertEqual(actual["task_name"], "attribute task")
        self.assertEqual(
            actual["unknown_fields"],
            {
                "Empty": [""],
                "Future": [
                    " first attribute ",
                    "child value",
                    "second attribute",
                ],
            },
        )
        self.assertEqual(
            native_attributes,
            [
                ("Name", " attribute name "),
                ("Future", " first attribute "),
                ("Future", "second attribute"),
                ("Empty", ""),
                ("taskname", "attribute task"),
            ],
        )

        class StringSubclass(str):
            pass

        unsupported = ET.Element("Task")
        unsupported.attrib["Vendor"] = StringSubclass(" preserved ")
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            python_fallback = task_input._element_record(unsupported, index=0)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                task_input._native_xml_attribute_name_values(unsupported, ET.Element)
            )
            native_fallback = task_input._element_record(unsupported, index=0)
        self.assertEqual(native_fallback, python_fallback)

    def test_twl_xml_child_collector_falls_back_for_string_subclasses(self) -> None:
        if not native_acceleration.available() or not hasattr(
            native_acceleration._native, "xml_direct_record_children"
        ):
            self.skipTest("optional Rust XML child collector is not built")

        class StringSubclass(str):
            pass

        root = ET.Element("Task")
        child = ET.SubElement(root, "Field")
        child.tag = StringSubclass("Field")
        child.text = StringSubclass("  preserved\u00a0 text ")
        root.append(ET.Comment("ignored"))
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "1"}):
            expected = task_input._element_record(root, index=0)
        with patch.dict("os.environ", {"TECAN_DISABLE_RUST_ACCELERATION": "0"}):
            self.assertIsNone(
                task_input._native_xml_direct_record_children(root, ET.Element)
            )
            actual = task_input._element_record(root, index=0)
        self.assertEqual(actual, expected)
        self.assertEqual(actual["unknown_fields"], {"Field": ["preserved text"]})

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
            native_acceleration._native, "xml_direct_record_children"
        ):
            self.skipTest("optional Rust XML child collector is not built")
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
                "_native_xml_direct_record_children",
                wraps=task_input._native_xml_direct_record_children,
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
                "_native_xml_direct_record_children",
                wraps=task_input._native_xml_direct_record_children,
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
            " \u00a0,\u001c\r\n"
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
            self.assertEqual(
                native_acceleration.delimited_text_rows(plain, ","),
                [
                    ["taskname", "parameter"],
                    ["Run-1", "\u00a0β\u00a0"],
                    ["Run-2", "東京"],
                ],
            )
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
