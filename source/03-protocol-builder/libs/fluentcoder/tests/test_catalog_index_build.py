"""v1.1 acceptance: build the catalog index against the real FC install.

Skipped if the install isn't reachable (CI / dev box). When reachable,
asserts the expected component counts and key catalog entries are present.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from fluentcoder.catalog.indexer import build_index, install_path_default  # noqa: E402
from fluentcoder.catalog.catalog import (  # noqa: E402
    category_counts, install_info, resolve_by_name,
    resolve_workspace_by_guid, resolve_workspace_by_name,
)
from fluentcoder.catalog.xcmp import load_xwsp  # noqa: E402

pytestmark = pytest.mark.fluentcontrol_shell


_SYNTHETIC_INSTALL = (
    Path(__file__).resolve().parent / "fixtures" / "synthetic_catalog" / "install"
)


def _install_present() -> bool:
    install = install_path_default()
    try:
        if install.resolve() == _SYNTHETIC_INSTALL.resolve():
            return False
    except OSError:
        if install == _SYNTHETIC_INSTALL:
            return False
    return (install / "SystemSpecific" / "Worktable" / "Components").exists()


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
def test_build_index_against_real_install() -> None:
    counts = build_index()
    assert counts["components"] >= 600, f"expected ~629 components, got {counts['components']}"
    assert counts["workspaces"] > 0
    assert counts["sites"] > 0

    by_cat = category_counts()
    for required in ("plate", "trough", "tip_box", "magnet_rack", "adapter", "fixed_deck"):
        assert by_cat.get(required, 0) > 0, f"category {required!r} has no rows"


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
def test_known_catalog_entries_present() -> None:
    expected = [
        ("96 Well Flat", "plate"),
        ("MCA96, 100ul, Box", "tip_box"),
        ("100ml Trough 156mm", "trough"),
        ("24 Magnet Plate", "magnet_rack"),
        ("384 Well", "plate"),
    ]
    for name, expected_category in expected:
        entry = resolve_by_name(name)
        assert entry is not None, f"catalog entry {name!r} missing"
        assert entry.category == expected_category, (
            f"{name!r}: category={entry.category}, expected {expected_category}"
        )
        assert entry.file_path.exists(), f"{name!r}: file_path does not exist"


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
def test_install_fingerprint_recorded() -> None:
    info = install_info()
    assert info is not None
    assert "install_path" in info
    assert info["fingerprint"]
    assert info["built_at"]


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
def test_workspace_rows_match_parseable_xwsp_files() -> None:
    counts = build_index()
    workspaces_dir = install_path_default() / "SystemSpecific" / "Worktable" / "Workspaces"
    parseable = 0
    for path in workspaces_dir.glob("*.xwsp"):
        try:
            load_xwsp(path)
        except Exception:
            continue
        parseable += 1
    assert counts["workspaces"] == parseable


@pytest.mark.skipif(not _install_present(), reason="FluentControl install not reachable")
def test_known_sat_workspace_is_indexed_by_document_guid_and_name() -> None:
    path = install_path_default() / "SystemSpecific" / "Worktable" / "Workspaces" / (
        "291ba293-6361-4f8f-aa8d-7c2643d3f096.xwsp"
    )
    if not path.exists():
        pytest.skip(f"workspace fixture not installed: {path}")

    build_index()
    by_name = resolve_workspace_by_name("SAT_Fluent_780_Rev3")
    by_guid = resolve_workspace_by_guid(path.stem)

    assert by_name is not None
    assert by_guid is not None
    assert by_name.guid == path.stem
    assert by_guid.guid == path.stem
    assert by_name.file_path == path
    assert by_guid.file_path == path

