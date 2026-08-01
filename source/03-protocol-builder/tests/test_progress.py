import io
import json
import threading
import unittest
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fluent_pipeline.cli.commands.projects import _cmd_create_collection
from fluent_pipeline.cli.parser import _build_parser
from fluent_pipeline.progress import ProgressEmitter, ProgressEvent, ProgressStage, render_plain_progress_event


class ProgressTests(unittest.TestCase):
    def test_create_collection_progress_defaults_to_auto(self):
        args = _build_parser().parse_args(
            ["create-collection", "combined", "--context", "first"]
        )

        self.assertEqual(args.progress, "auto")

    def test_create_collection_cli_splits_progress_and_result_streams(self):
        args = SimpleNamespace(
            name="combined",
            context=["first", "second"],
            force=False,
            progress="plain",
        )

        def create_collection(name, contexts, *, force, progress_callback):
            progress_callback(
                ProgressEvent(
                    operation_id="create_collection",
                    stage_id="merge_objects",
                    stage_name="Merging objects",
                    status="running",
                    current_stage=5,
                    total_stages=7,
                    completed_units=1000,
                    total_units=15079,
                    unit_name="objects",
                )
            )
            return SimpleNamespace(
                name=name,
                root=Path("collections") / name,
                manifest={
                    "source_projects": [{}, {}],
                    "scripts": [{}, {}],
                    "objects": [{}],
                    "workspaces": [],
                    "snapshot_evidence": [],
                },
            )

        with mock.patch(
            "fluent_pipeline.cli.commands.projects.create_project_collection",
            side_effect=create_collection,
        ), mock.patch("sys.stdout", new_callable=io.StringIO) as stdout, mock.patch(
            "sys.stderr", new_callable=io.StringIO
        ) as stderr:
            result = _cmd_create_collection(args)

        self.assertEqual(result, 0)
        self.assertIn("Collection created successfully.", stdout.getvalue())
        self.assertNotIn("[5/7]", stdout.getvalue())
        self.assertIn("Creating collection: combined", stderr.getvalue())
        self.assertIn("[5/7] Merging objects: 1,000/15,079 objects", stderr.getvalue())

    def test_counted_event_renders_item_progress(self):
        event = ProgressEvent(
            operation_id="create_collection",
            stage_id="merge_objects",
            stage_name="Merging objects",
            status="running",
            current_stage=5,
            total_stages=7,
            elapsed_seconds=135.0,
            completed_units=6400,
            total_units=15079,
            unit_name="objects",
        )

        self.assertEqual(
            render_plain_progress_event(event),
            "[5/7] Merging objects: 6,400/15,079 objects",
        )
        payload = json.loads(json.dumps(asdict(event)))
        self.assertEqual(payload["operation_id"], "create_collection")
        self.assertEqual(payload["completed_units"], 6400)

    def test_heartbeat_emits_running_event(self):
        events = []
        heartbeat_seen = threading.Event()

        def collect(event):
            events.append(event)
            if event.status == "running":
                heartbeat_seen.set()

        progress = ProgressEmitter(
            (ProgressStage("validate", "Validating collection"),),
            collect,
            operation_id="create_collection",
        )
        progress.started("validate")
        with progress.heartbeat("validate", interval_seconds=0.001):
            self.assertTrue(heartbeat_seen.wait(0.2))
        progress.completed("validate")

        self.assertTrue(any(event.status == "running" for event in events))
        self.assertTrue(all(event.operation_id == "create_collection" for event in events))


if __name__ == "__main__":
    unittest.main()
