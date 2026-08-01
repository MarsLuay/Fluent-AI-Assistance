"""Phase C.1 — fingerprint-based auto-rebuild.

When the catalog index's stored fingerprint no longer matches the
on-disk FC install, ``ensure_index()`` must rebuild silently —
unless ``FLUENTCODER_NO_AUTO_REBUILD`` is set.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import warnings
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

import fluentcoder.catalog as catalog_package  # noqa: E402
from fluentcoder.catalog import (  # noqa: E402
    ensure_index,
    fingerprint_matches,
    index_exists,
    install_path_default,
    open_index,
)
from fluentcoder.catalog.paths import index_db_path_default  # noqa: E402

_SYNTHETIC_INSTALL = (
    Path(__file__).resolve().parent / "fixtures" / "synthetic_catalog" / "install"
)


def _active_index_path() -> Path:
    return index_db_path_default()


def _is_synthetic_install() -> bool:
    install = install_path_default()
    try:
        return install.resolve() == _SYNTHETIC_INSTALL.resolve()
    except OSError:
        return install == _SYNTHETIC_INSTALL


def _read_install_row(db_path: Path) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT install_path, fingerprint, built_at FROM install LIMIT 1"
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _set_fingerprint(db_path: Path, fingerprint: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("UPDATE install SET fingerprint = ?", (fingerprint,))
        conn.commit()
    finally:
        conn.close()


def test_explicit_index_db_skips_auto_rebuild(monkeypatch, tmp_path: Path) -> None:
    """An explicit project/test catalog DB is query-only during import-time setup."""
    db_path = tmp_path / "explicit" / "install_index.db"
    with open_index(db_path) as conn:
        conn.execute(
            "INSERT INTO components (install_key, guid, name, category, file_path) "
            "VALUES ('explicit', 'component-guid', 'Component', 'Microplate', 'component.xcmp')"
        )
        conn.commit()

    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fail_if_called(*args: object, **kwargs: object) -> dict[str, int]:
        calls.append((args, kwargs))
        raise AssertionError("explicit catalog DB should not be rebuilt on import")

    monkeypatch.setenv("FLUENTCODER_INDEX_DB", str(db_path))
    monkeypatch.setattr(catalog_package, "build_index", fail_if_called)

    ensure_index()

    assert calls == []


@pytest.mark.skipif(
    not index_exists() or _is_synthetic_install(),
    reason="catalog index empty or synthetic offline install",
)
def test_fingerprint_mismatch_triggers_rebuild() -> None:
    """Forcing a stale fingerprint causes ensure_index() to rebuild."""
    db_path = _active_index_path()
    before = _read_install_row(db_path)
    assert before, "expected an install row in the index"
    real_fingerprint = before["fingerprint"]
    real_built_at = before["built_at"]

    # Poison the stored fingerprint so it no longer matches the on-disk install.
    _set_fingerprint(db_path, "stale-fingerprint-test-marker")
    assert not fingerprint_matches(install_path_default())

    # ensure_index() should detect the drift and rebuild.
    os.environ.pop("FLUENTCODER_NO_AUTO_REBUILD", None)
    ensure_index()

    after = _read_install_row(db_path)
    assert after["fingerprint"] == real_fingerprint, (
        "fingerprint should be restored to the on-disk install's hash"
    )
    assert after["built_at"] != real_built_at or after["fingerprint"] != "stale-fingerprint-test-marker"
    assert fingerprint_matches(install_path_default())


@pytest.mark.skipif(
    not index_exists() or _is_synthetic_install(),
    reason="catalog index empty or synthetic offline install",
)
def test_no_auto_rebuild_env_var_keeps_stale_index() -> None:
    """With FLUENTCODER_NO_AUTO_REBUILD=1, drift is warned but not rebuilt."""
    db_path = _active_index_path()
    before = _read_install_row(db_path)
    assert before
    real_fingerprint = before["fingerprint"]

    # Snapshot real fingerprint, then poison and set the env var.
    _set_fingerprint(db_path, "another-stale-marker")

    os.environ["FLUENTCODER_NO_AUTO_REBUILD"] = "1"
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ensure_index()
        warning_msgs = [str(w.message) for w in caught]
        assert any("fingerprint" in m.lower() for m in warning_msgs), (
            f"expected a fingerprint-drift warning; got {warning_msgs!r}"
        )
        # Index was NOT rebuilt — fingerprint is still the marker.
        intermediate = _read_install_row(db_path)
        assert intermediate["fingerprint"] == "another-stale-marker"
    finally:
        os.environ.pop("FLUENTCODER_NO_AUTO_REBUILD", None)
        # Restore the index to a healthy state for downstream tests.
        _set_fingerprint(db_path, real_fingerprint)

