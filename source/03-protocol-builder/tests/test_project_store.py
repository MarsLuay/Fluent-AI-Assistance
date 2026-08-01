import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluent_pipeline.project_store import ProjectStore


class ProjectStoreTests(unittest.TestCase):
    def test_write_text_fsyncs_then_replaces_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "nested" / "state.txt"
            target.parent.mkdir(parents=True)
            target.write_text("old", encoding="utf-8")
            store = ProjectStore(active_context_file=root / ".active_context")
            observed: list[tuple[str, str]] = []
            replace = os.replace

            def inspect_replace(source: str | Path, destination: str | Path) -> None:
                observed.append(
                    (
                        Path(source).read_text(encoding="utf-8"),
                        Path(destination).read_text(encoding="utf-8"),
                    )
                )
                replace(source, destination)

            with mock.patch("fluent_pipeline.project_store.os.replace", side_effect=inspect_replace), mock.patch(
                "fluent_pipeline.project_store.os.fsync",
                wraps=os.fsync,
            ) as fsync:
                store.write_text(target, "new")

            self.assertEqual(observed, [("new", "old")])
            self.assertTrue(fsync.called)
            self.assertEqual(target.read_text(encoding="utf-8"), "new")
            self.assertEqual(list(target.parent.glob(".state.txt.*.tmp")), [])
            self.assertTrue((target.parent / ".state.txt.lock").exists())

    def test_json_and_active_context_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ProjectStore(active_context_file=root / "projects" / ".active_context")
            manifest = root / "projects" / "demo" / "manifest.json"

            store.write_json(manifest, {"name": "demo", "scripts": ["demo.xscr"]})
            store.set_active_context("demo")

            self.assertEqual(store.read_json(manifest)["name"], "demo")
            self.assertEqual(store.active_context_name(), "demo")

            store.clear_active_context()
            self.assertIsNone(store.active_context_name())
