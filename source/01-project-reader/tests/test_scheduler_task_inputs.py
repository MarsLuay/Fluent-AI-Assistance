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

    def test_twl_record_selection_uses_native_casefold_with_unicode_fallback(self) -> None:
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
            self.assertIsNone(
                native_acceleration.xml_elements_by_folded_name_with_children(
                    unicode_root, record_names, ET.Element
                )
            )
            fallback_nodes = [
                element
                for element in unicode_root.iter()
                if isinstance(element.tag, str)
                and task_input.local_name(element.tag).casefold() in record_names
                and list(element)
            ]
        self.assertEqual(fallback_nodes, [unicode_task])
        self.assertEqual(
            task_input._element_record(unicode_task, index=0)["fields"]["Name"],
            "unicode",
        )

    def test_twl_xml_whitespace_normalization_uses_native_ascii_and_unicode_fallback(self) -> None:
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
            self.assertIsNone(native_acceleration.collapse_ascii_whitespace(unicode_text))
            unicode_actual = inspect_twl_text(
                unicode_source, source_name="Inputs/unicode.twl"
            )
        self.assertEqual(unicode_actual, unicode_expected)
        self.assertEqual(
            unicode_actual["records"][0]["fields"]["Name"], "café user"
        )

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
