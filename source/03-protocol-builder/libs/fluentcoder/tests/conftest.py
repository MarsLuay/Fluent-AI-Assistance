"""Shared pytest fixtures for fluentcoder offline CI."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Keep first import from touching a real FluentControl install.
os.environ.setdefault("FLUENTCODER_NO_AUTO_REBUILD", "1")
os.environ.setdefault(
    "FLUENTCODER_FC_INSTALL",
    str(Path(__file__).resolve().parent / "_missing_fc_install"),
)

REPO_ROOT = Path(__file__).resolve().parent.parent
from tests.fixtures.synthetic_catalog import bootstrap as _bootstrap

build_synthetic_index = _bootstrap.build_synthetic_index
SYNTHETIC_CATALOG_DB = _bootstrap.INDEX_DB
SYNTHETIC_FC_INSTALL = _bootstrap.INSTALL_ROOT

# Offline authoring bindings for unit tests (not product invent — explicit test fixtures).
OFFLINE_WORKSPACE_GUID = "11111111-1234-aaaa-ffff-000000000222"
OFFLINE_WORKSPACE_NAME = "Synthetic Offline Workspace"
OFFLINE_DEVICE_ALIAS = "Instrument=1/Device=MCA384:1"
OFFLINE_AVAILABLE_ID = "USB:TECAN,FLUENT,2203009762/MCA384:1"
# Explicit fixture ModuleName matching common FluentControl RGA naming — not a
# renderer invent path; authors/tests must set this (or pass module_name=).
OFFLINE_RGA_MODULE_NAME = "RGA 1"


def bind_offline_authoring(wt, *, with_device: bool = True):
    """Attach synthetic workspace (+ optional device) so render/compile is fail-closed-safe."""
    if not getattr(wt, "workspace_guid", None):
        wt.workspace_guid = OFFLINE_WORKSPACE_GUID
    if not getattr(wt, "workspace_name", None):
        wt.workspace_name = OFFLINE_WORKSPACE_NAME
    if with_device:
        if not getattr(wt, "device_alias", None):
            wt.device_alias = OFFLINE_DEVICE_ALIAS
        if not getattr(wt, "available_id", None):
            wt.available_id = OFFLINE_AVAILABLE_ID
        if not getattr(wt, "rga_module_name", None):
            wt.rga_module_name = OFFLINE_RGA_MODULE_NAME
    return wt


def install_synthetic_catalog(
    monkeypatch: pytest.MonkeyPatch,
    db_path: Path | None = None,
) -> Path:
    """Point catalog queries at the tiny offline install + index for one test."""
    catalog_db = db_path or build_synthetic_index()
    monkeypatch.setenv("FLUENTCODER_TEST_CATALOG_DB", str(catalog_db))
    monkeypatch.setenv("FLUENTCODER_FC_INSTALL", str(SYNTHETIC_FC_INSTALL))
    monkeypatch.setenv("FLUENTCODER_NO_AUTO_REBUILD", "1")

    import fluentcoder.catalog.catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "DEFAULT_INDEX_PATH", catalog_db)

    return catalog_db


@pytest.fixture(autouse=True)
def _reset_catalog_defaults() -> None:
    """Keep module-level catalog defaults from leaking between tests."""
    from fluentcoder.defaults import clear_catalog_defaults

    clear_catalog_defaults()
    yield
    clear_catalog_defaults()


@pytest.fixture(scope="session")
def synthetic_catalog_db() -> Path:
    """Path to the tiny generated install_index.db used in CI."""
    return build_synthetic_index()


@pytest.fixture
def synthetic_catalog(monkeypatch: pytest.MonkeyPatch, synthetic_catalog_db: Path) -> Path:
    """Point catalog queries at the synthetic install index for one test."""
    return install_synthetic_catalog(monkeypatch, synthetic_catalog_db)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "synthetic_catalog: use offline synthetic catalog/workspace fixtures",
    )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Bootstrap the synthetic catalog when no usable offline index exists."""
    if os.environ.get("FLUENTCODER_TEST_CATALOG_DB") or os.environ.get("FLUENTCODER_INDEX_DB"):
        build_synthetic_index()
        return

    import shutil

    import fluentcoder.catalog.catalog as catalog_mod
    from fluentcoder.catalog.paths import index_db_path_default

    os.environ["FLUENTCODER_FC_INSTALL"] = str(SYNTHETIC_FC_INSTALL)
    target_db = index_db_path_default(SYNTHETIC_FC_INSTALL)
    if catalog_mod.index_exists(target_db):
        return

    source_db = build_synthetic_index()
    target_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_db, target_db)
