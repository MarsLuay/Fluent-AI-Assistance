from __future__ import annotations

import os
import sys
import types
from pathlib import Path

from fluent_pipeline import fluentcoder_project_runner


def test_project_runner_points_all_catalog_paths_at_requested_db(monkeypatch, tmp_path: Path) -> None:
    catalog_db = tmp_path / "project" / "install_index.db"
    catalog_db.parent.mkdir()
    catalog_db.write_bytes(b"")

    observed: dict[str, object] = {}

    fluentcoder_package = types.ModuleType("fluentcoder")
    fluentcoder_package.__path__ = []  # type: ignore[attr-defined]

    catalog_package = types.ModuleType("fluentcoder.catalog")
    catalog_package.__path__ = []  # type: ignore[attr-defined]
    catalog_package.DEFAULT_INDEX_PATH = Path("package-default.db")

    catalog_module = types.ModuleType("fluentcoder.catalog.catalog")
    catalog_module.DEFAULT_INDEX_PATH = Path("module-default.db")

    paths_module = types.ModuleType("fluentcoder.catalog.paths")
    paths_module.DEFAULT_INDEX_PATH = Path("paths-default.db")

    cli_module = types.ModuleType("fluentcoder.cli")

    def fake_main(args: list[str]) -> int:
        observed["args"] = args
        observed["env"] = os.environ.get("FLUENTCODER_INDEX_DB")
        observed["package_path"] = catalog_package.DEFAULT_INDEX_PATH
        observed["module_path"] = catalog_module.DEFAULT_INDEX_PATH
        observed["paths_path"] = paths_module.DEFAULT_INDEX_PATH
        return 23

    cli_module.main = fake_main  # type: ignore[attr-defined]
    fluentcoder_package.cli = cli_module  # type: ignore[attr-defined]
    catalog_package.catalog = catalog_module  # type: ignore[attr-defined]
    catalog_package.paths = paths_module  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "fluentcoder", fluentcoder_package)
    monkeypatch.setitem(sys.modules, "fluentcoder.catalog", catalog_package)
    monkeypatch.setitem(sys.modules, "fluentcoder.catalog.catalog", catalog_module)
    monkeypatch.setitem(sys.modules, "fluentcoder.catalog.paths", paths_module)
    monkeypatch.setitem(sys.modules, "fluentcoder.cli", cli_module)
    monkeypatch.delenv("FLUENTCODER_INDEX_DB", raising=False)

    rc = fluentcoder_project_runner.main(
        ["--catalog-db", str(catalog_db), "--", "simulate", "protocol.py"]
    )

    assert rc == 23
    assert observed == {
        "args": ["simulate", "protocol.py"],
        "env": str(catalog_db.resolve()),
        "package_path": catalog_db.resolve(),
        "module_path": catalog_db.resolve(),
        "paths_path": catalog_db.resolve(),
    }
