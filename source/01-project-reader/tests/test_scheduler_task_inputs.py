from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import zipfile

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
