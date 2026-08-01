"""Tests for the content-addressed fluentcoder catalog cache."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fluent_pipeline import project_catalog
from fluent_pipeline import project_context
from fluent_pipeline.project_context import ProjectContext


def _make_datastore(root: Path, *, component_text: str = "<Component/>") -> Path:
    """Create a minimal extracted DataStore that project_datastore_dir accepts."""
    datastore = root / "extracted" / "DataStore"
    components = datastore / "SystemSpecific" / "Worktable" / "Components"
    workspaces = datastore / "SystemSpecific" / "Worktable" / "Workspaces"
    components.mkdir(parents=True, exist_ok=True)
    workspaces.mkdir(parents=True, exist_ok=True)
    (components / "deck.xcmp").write_text(component_text, encoding="utf-8")
    (workspaces / "layout.xwsp").write_text("<Workspace/>", encoding="utf-8")
    return datastore


def _context(root: Path, name: str = "ctx") -> ProjectContext:
    return ProjectContext(name=name, root=root, manifest={})


class CatalogContentHashTests(unittest.TestCase):
    def test_hash_is_stable_across_mtime_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            datastore = _make_datastore(Path(tmp))
            first = project_catalog._catalog_content_hash(datastore)
            # Re-extraction resets mtimes; bump them and confirm the key is stable.
            future = time.time() + 120
            for path in project_catalog._catalog_source_files(datastore):
                os.utime(path, (future, future))
            second = project_catalog._catalog_content_hash(datastore)
            self.assertIsNotNone(first)
            self.assertEqual(first, second)

    def test_hash_changes_with_content(self):
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            ds_a = _make_datastore(Path(tmp_a), component_text="<Component a='1'/>")
            ds_b = _make_datastore(Path(tmp_b), component_text="<Component a='2'/>")
            self.assertNotEqual(
                project_catalog._catalog_content_hash(ds_a),
                project_catalog._catalog_content_hash(ds_b),
            )


class CatalogSharedCacheTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name) / "cache"
        self._orig_cache = project_catalog.CATALOG_CACHE_DIR
        project_catalog.CATALOG_CACHE_DIR = self.cache_dir
        self.addCleanup(self._restore_cache)
        self.build_calls = 0
        self._orig_run = project_catalog.run_python
        project_catalog.run_python = self._fake_run_python
        self.addCleanup(self._restore_run)

    def _restore_cache(self):
        project_catalog.CATALOG_CACHE_DIR = self._orig_cache

    def _restore_run(self):
        project_catalog.run_python = self._orig_run

    def _fake_run_python(self, arguments, *, timeout: int = 120):
        # Emulate fluentcoder writing the install index DB.
        args = [str(a) for a in arguments]
        db = Path(args[args.index("--db") + 1])
        db.parent.mkdir(parents=True, exist_ok=True)
        db.write_text(f"catalog-build-{self.build_calls}", encoding="utf-8")
        self.build_calls += 1
        return SimpleNamespace(ok=True, stdout="", stderr="")

    def test_identical_inputs_reuse_cache_across_contexts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "a"
            root_b = Path(tmp) / "b"
            _make_datastore(root_a, component_text="<Component shared='yes'/>")
            _make_datastore(root_b, component_text="<Component shared='yes'/>")

            db_a = project_catalog.ensure_project_catalog(_context(root_a, "a"))
            db_b = project_catalog.ensure_project_catalog(_context(root_b, "b"))

            self.assertIsNotNone(db_a)
            self.assertIsNotNone(db_b)
            self.assertTrue(db_a.exists())
            self.assertTrue(db_b.exists())
            # Only the first context triggered a real build; the second reused it.
            self.assertEqual(self.build_calls, 1)
            self.assertEqual(db_b.read_text(encoding="utf-8"), "catalog-build-0")

    def test_fresh_context_db_is_not_rebuilt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a"
            _make_datastore(root)
            ctx = _context(root, "a")
            project_catalog.ensure_project_catalog(ctx)
            project_catalog.ensure_project_catalog(ctx)
            self.assertEqual(self.build_calls, 1)

    def test_different_inputs_rebuild(self):
        with tempfile.TemporaryDirectory() as tmp:
            root_a = Path(tmp) / "a"
            root_b = Path(tmp) / "b"
            _make_datastore(root_a, component_text="<Component v='1'/>")
            _make_datastore(root_b, component_text="<Component v='2'/>")
            project_catalog.ensure_project_catalog(_context(root_a, "a"))
            project_catalog.ensure_project_catalog(_context(root_b, "b"))
            self.assertEqual(self.build_calls, 2)

    def test_sidecar_skips_full_hash_after_mtime_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a"
            _make_datastore(root, component_text="<Component shared='yes'/>")
            ctx = _context(root, "a")

            db_path = project_catalog.ensure_project_catalog(ctx)
            self.assertIsNotNone(db_path)
            sidecar = project_catalog._catalog_sidecar_path(db_path.parent)
            self.assertTrue(sidecar.exists())

            future = time.time() + 120
            for path in project_catalog._catalog_source_files(project_catalog.project_datastore_dir(ctx)):
                os.utime(path, (future, future))

            hash_calls = 0
            original_hash = project_catalog._catalog_content_hash

            def counting_hash(datastore):
                nonlocal hash_calls
                hash_calls += 1
                return original_hash(datastore)

            project_catalog._catalog_content_hash = counting_hash
            self.addCleanup(lambda: setattr(project_catalog, "_catalog_content_hash", original_hash))

            reused = project_catalog.ensure_project_catalog(ctx)
            self.assertIsNotNone(reused)
            self.assertEqual(hash_calls, 0)
            self.assertEqual(reused.read_text(encoding="utf-8"), "catalog-build-0")

    def test_sidecar_misses_when_catalog_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "a"
            datastore = _make_datastore(root, component_text="<Component shared='yes'/>")
            ctx = _context(root, "a")
            db_path = project_catalog.ensure_project_catalog(ctx)
            self.assertIsNotNone(db_path)

            (datastore / "SystemSpecific" / "Worktable" / "Components" / "deck.xcmp").write_text(
                "<Component shared='changed'/>",
                encoding="utf-8",
            )
            future = time.time() + 120
            for path in project_catalog._catalog_source_files(datastore):
                os.utime(path, (future, future))

            hash_calls = 0
            original_hash = project_catalog._catalog_content_hash

            def counting_hash(datastore_path):
                nonlocal hash_calls
                hash_calls += 1
                return original_hash(datastore_path)

            project_catalog._catalog_content_hash = counting_hash
            self.addCleanup(lambda: setattr(project_catalog, "_catalog_content_hash", original_hash))

            project_catalog.ensure_project_catalog(ctx)
            self.assertEqual(hash_calls, 1)
            self.assertEqual(self.build_calls, 2)


if __name__ == "__main__":
    unittest.main()
