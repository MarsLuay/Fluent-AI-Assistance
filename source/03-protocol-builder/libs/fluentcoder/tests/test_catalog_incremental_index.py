"""Incremental catalog indexing tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

# Avoid the package-level ensure_index() trying to rebuild against a real FC install.
os.environ.setdefault("FLUENTCODER_NO_AUTO_REBUILD", "1")
os.environ.setdefault("FLUENTCODER_FC_INSTALL", str(Path(__file__).resolve().parent / "_missing_fc_install"))

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.catalog.indexer import (  # noqa: E402
    _source_content_fingerprint,
    build_index,
    fingerprint_matches,
)
from fluentcoder.catalog.xcmp import XcmpArrangement, XcmpComponent, XwspWorkspace  # noqa: E402


def _make_install(root: Path, *, component_text: str = "<Component/>") -> Path:
    install = root / "install"
    components = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites = install / "SystemSpecific" / "Worktable" / "Sites"
    components.mkdir(parents=True)
    workspaces.mkdir(parents=True)
    sites.mkdir(parents=True)
    (components / "deck.xcmp").write_text(component_text, encoding="utf-8")
    (workspaces / "layout.xwsp").write_text("<Workspace/>", encoding="utf-8")
    return install


def _arrangement() -> XcmpArrangement:
    return XcmpArrangement(
        sites_in_x=1,
        sites_in_y=1,
        sites_in_z=1,
        site_spacing_mm=(0.0, 0.0, 0.0),
        position_in_parent_mm=(0.0, 0.0, 0.0),
    )


def _fake_workspace(path: Path) -> XwspWorkspace:
    return XwspWorkspace(guid=f"{path.stem}-guid", name=path.stem.title(), file_path=path)


class CatalogIncrementalIndexTests(unittest.TestCase):
    def test_stat_fingerprint_ignores_mtime_reset(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = _make_install(Path(tmp))
            db_path = Path(tmp) / "index.db"
            with mock.patch("fluentcoder.catalog.indexer.load_xcmp") as load_xcmp, mock.patch(
                "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
            ):
                load_xcmp.side_effect = lambda path: XcmpComponent(
                    guid=f"{path.stem}-guid",
                    name=path.stem.title(),
                    file_path=path,
                    arrangement=_arrangement(),
                )
                build_index(install_path=install, db_path=db_path)
                self.assertTrue(fingerprint_matches(install, db_path))

                future = time.time() + 120
                for path in install.rglob("*"):
                    if path.is_file():
                        os.utime(path, (future, future))

                self.assertTrue(fingerprint_matches(install, db_path))

    def test_build_index_skips_unchanged_component_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = _make_install(Path(tmp))
            db_path = Path(tmp) / "index.db"
            load_calls = 0

            def counting_load_xcmp(path: Path) -> XcmpComponent:
                nonlocal load_calls
                load_calls += 1
                return XcmpComponent(
                    guid=f"{path.stem}-guid",
                    name=path.stem.title(),
                    file_path=path,
                    arrangement=_arrangement(),
                )

            with mock.patch("fluentcoder.catalog.indexer.load_xcmp", side_effect=counting_load_xcmp), mock.patch(
                "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
            ):
                first = build_index(install_path=install, db_path=db_path)
                self.assertEqual(first["components"], 1)
                self.assertEqual(load_calls, 1)

                load_calls = 0
                second = build_index(install_path=install, db_path=db_path)
                self.assertEqual(second["components"], 1)
                self.assertEqual(load_calls, 0)

    def test_build_index_reparses_changed_component(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = _make_install(Path(tmp))
            db_path = Path(tmp) / "index.db"
            load_calls = 0

            def counting_load_xcmp(path: Path) -> XcmpComponent:
                nonlocal load_calls
                load_calls += 1
                return XcmpComponent(
                    guid=f"{path.stem}-guid",
                    name=path.stem.title(),
                    file_path=path,
                    arrangement=_arrangement(),
                )

            with mock.patch("fluentcoder.catalog.indexer.load_xcmp", side_effect=counting_load_xcmp), mock.patch(
                "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
            ):
                build_index(install_path=install, db_path=db_path)
                self.assertEqual(load_calls, 1)

                component_path = install / "SystemSpecific" / "Worktable" / "Components" / "deck.xcmp"
                component_path.write_text("<Component changed='yes'/>", encoding="utf-8")

                load_calls = 0
                build_index(install_path=install, db_path=db_path)
                self.assertEqual(load_calls, 1)

    def test_content_fingerprint_changes_with_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            install = _make_install(Path(tmp), component_text="<Component v='1'/>")
            path = install / "SystemSpecific" / "Worktable" / "Components" / "deck.xcmp"
            first = _source_content_fingerprint(path, install)
            path.write_text("<Component v='2'/>", encoding="utf-8")
            second = _source_content_fingerprint(path, install)
            self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()

