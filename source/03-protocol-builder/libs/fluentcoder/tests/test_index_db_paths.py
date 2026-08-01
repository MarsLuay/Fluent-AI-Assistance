"""Per-install and explicit catalog index DB path resolution."""

from __future__ import annotations

from pathlib import Path

from fluentcoder.catalog.catalog import DEFAULT_INDEX_PATH
from fluentcoder.catalog.paths import index_db_path_default, install_path_key


def test_index_db_path_default_uses_package_db_for_canonical_install() -> None:
    canonical = Path(r"C:\ProgramData\Tecan\VisionX\Database")
    assert index_db_path_default(canonical) == DEFAULT_INDEX_PATH


def test_index_db_path_default_keys_non_default_install(tmp_path: Path) -> None:
    install = tmp_path / "fc-a"
    expected = DEFAULT_INDEX_PATH.parent / "indexes" / f"install_{install_path_key(install)}.db"
    assert index_db_path_default(install) == expected


def test_index_db_path_default_honors_fluentcoder_test_catalog_db(monkeypatch, tmp_path: Path) -> None:
    explicit = tmp_path / "synthetic.db"
    monkeypatch.setenv("FLUENTCODER_TEST_CATALOG_DB", str(explicit))
    assert index_db_path_default(tmp_path / "any-install") == explicit


def test_index_db_path_default_honors_fluentcoder_index_db(monkeypatch, tmp_path: Path) -> None:
    explicit = tmp_path / "project" / "catalog.db"
    monkeypatch.setenv("FLUENTCODER_INDEX_DB", str(explicit))
    assert index_db_path_default(tmp_path / "any-install") == explicit


def test_different_installs_get_different_index_paths(tmp_path: Path) -> None:
    install_a = tmp_path / "fc-a"
    install_b = tmp_path / "fc-b"
    path_a = index_db_path_default(install_a)
    path_b = index_db_path_default(install_b)
    assert path_a != path_b

