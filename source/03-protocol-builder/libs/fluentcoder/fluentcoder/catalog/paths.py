"""Default paths for the catalog index database."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from .catalog import DEFAULT_INDEX_PATH

_DEFAULT_INSTALL_PATH = Path(r"C:\ProgramData\Tecan\VisionX\Database")


def _normalized_install_key(install: Path) -> str:
    try:
        value = install.resolve()
    except OSError:
        value = install
    return os.path.normcase(str(value))


_DEFAULT_INSTALL_KEY = _normalized_install_key(_DEFAULT_INSTALL_PATH)


def install_path_key(install_path: Path | str) -> str:
    """Stable short hash key for an FluentControl install directory."""
    install_key = _normalized_install_key(Path(install_path))
    return hashlib.sha256(install_key.encode("utf-8")).hexdigest()[:16]


def index_db_path_default(install_path: Path | str | None = None) -> Path:
    """Resolve the catalog index DB path for an FluentControl install.

    Priority:

    1. ``FLUENTCODER_TEST_CATALOG_DB`` — synthetic/offline test fixture (CI)
    2. ``FLUENTCODER_INDEX_DB`` — explicit path (project-local or shared cache)
    3. Canonical default install → packaged ``install_index.db``
    4. Any other install path → ``catalog/indexes/install_<hash>.db`` keyed by
       the resolved install directory so multiple FC installs can coexist.
    """
    for key in ("FLUENTCODER_TEST_CATALOG_DB", "FLUENTCODER_INDEX_DB"):
        env = os.environ.get(key)
        if env:
            return Path(env)

    if install_path is None:
        from .indexer import install_path_default

        install = install_path_default()
    else:
        install = Path(install_path)
    if _normalized_install_key(install) == _DEFAULT_INSTALL_KEY:
        return DEFAULT_INDEX_PATH

    digest = install_path_key(install)
    return DEFAULT_INDEX_PATH.parent / "indexes" / f"install_{digest}.db"
