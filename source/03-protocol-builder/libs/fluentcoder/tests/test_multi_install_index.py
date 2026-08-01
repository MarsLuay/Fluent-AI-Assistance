"""Multi-install catalog index: one DB, rows keyed by install_path hash."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest import mock

import pytest

from fluentcoder.catalog.catalog import resolve_by_name
from fluentcoder.catalog.indexer import build_index
from fluentcoder.catalog.paths import install_path_key
from fluentcoder.catalog.xcmp import XcmpArrangement, XcmpComponent, XwspWorkspace


def _arrangement() -> XcmpArrangement:
    return XcmpArrangement(
        sites_in_x=1,
        sites_in_y=1,
        sites_in_z=1,
        site_spacing_mm=(0.0, 0.0, 0.0),
        position_in_parent_mm=(0.0, 0.0, 0.0),
    )


def _make_install(root: Path, component_name: str) -> Path:
    install = root / component_name.replace(" ", "-").lower()
    components = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites = install / "SystemSpecific" / "Worktable" / "Sites"
    components.mkdir(parents=True)
    workspaces.mkdir(parents=True)
    sites.mkdir(parents=True)
    (components / "plate.xcmp").write_text("<Component/>", encoding="utf-8")
    (workspaces / "layout.xwsp").write_text("<Workspace/>", encoding="utf-8")
    return install


def _fake_workspace(path: Path) -> XwspWorkspace:
    return XwspWorkspace(guid=f"{path.stem}-guid", name=path.stem.title(), file_path=path)


def test_two_installs_share_one_db_and_resolve_by_fluentcoder_fc_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_a = _make_install(tmp_path, "Install Alpha Plate")
    install_b = _make_install(tmp_path, "Install Beta Trough")
    db_path = tmp_path / "shared-index.db"
    monkeypatch.setenv("FLUENTCODER_INDEX_DB", str(db_path))

    def fake_load_xcmp(path: Path) -> XcmpComponent:
        install_root = path.parents[3]
        if install_root == install_a:
            name = "Install Alpha Plate"
            guid = "alpha-plate-guid"
        else:
            name = "Install Beta Trough"
            guid = "beta-trough-guid"
        return XcmpComponent(
            guid=guid,
            name=name,
            file_path=path,
            arrangement=_arrangement(),
        )

    with mock.patch("fluentcoder.catalog.indexer.load_xcmp", side_effect=fake_load_xcmp), mock.patch(
        "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
    ):
        counts_a = build_index(install_path=install_a, db_path=db_path)
        counts_b = build_index(install_path=install_b, db_path=db_path)

    assert counts_a["components"] == 1
    assert counts_b["components"] == 1

    conn = sqlite3.connect(str(db_path))
    try:
        install_rows = conn.execute("SELECT install_path, install_key FROM install").fetchall()
        assert len(install_rows) == 2
        keys = {row[1] for row in install_rows}
        assert install_path_key(install_a) in keys
        assert install_path_key(install_b) in keys
        assert conn.execute("SELECT COUNT(*) FROM components").fetchone()[0] == 2
    finally:
        conn.close()

    monkeypatch.setenv("FLUENTCODER_FC_INSTALL", str(install_a))
    entry_a = resolve_by_name("Install Alpha Plate", db_path=db_path)
    assert entry_a is not None
    assert entry_a.guid == "alpha-plate-guid"
    assert resolve_by_name("Install Beta Trough", db_path=db_path) is None

    monkeypatch.setenv("FLUENTCODER_FC_INSTALL", str(install_b))
    entry_b = resolve_by_name("Install Beta Trough", db_path=db_path)
    assert entry_b is not None
    assert entry_b.guid == "beta-trough-guid"
    assert resolve_by_name("Install Alpha Plate", db_path=db_path) is None


def test_rebuild_one_install_slice_preserves_other_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_a = _make_install(tmp_path, "Slice Alpha")
    install_b = _make_install(tmp_path, "Slice Beta")
    db_path = tmp_path / "shared-index.db"

    names = {
        str(install_a): ("Slice Alpha", "alpha-guid"),
        str(install_b): ("Slice Beta", "beta-guid"),
    }

    def fake_load_xcmp(path: Path) -> XcmpComponent:
        install_root = path.parents[3]
        name, guid = names[str(install_root)]
        return XcmpComponent(
            guid=guid,
            name=name,
            file_path=path,
            arrangement=_arrangement(),
        )

    with mock.patch("fluentcoder.catalog.indexer.load_xcmp", side_effect=fake_load_xcmp), mock.patch(
        "fluentcoder.catalog.indexer.load_xwsp", side_effect=_fake_workspace
    ):
        build_index(install_path=install_a, db_path=db_path)
        build_index(install_path=install_b, db_path=db_path)

        names[str(install_a)] = ("Slice Alpha Updated", "alpha-guid")
        component_path = install_a / "SystemSpecific" / "Worktable" / "Components" / "plate.xcmp"
        component_path.write_text("<Component updated='yes'/>", encoding="utf-8")
        build_index(install_path=install_a, db_path=db_path)

    monkeypatch.setenv("FLUENTCODER_FC_INSTALL", str(install_a))
    updated = resolve_by_name("Slice Alpha Updated", db_path=db_path)
    assert updated is not None

    monkeypatch.setenv("FLUENTCODER_FC_INSTALL", str(install_b))
    beta = resolve_by_name("Slice Beta", db_path=db_path)
    assert beta is not None
    assert beta.guid == "beta-guid"

