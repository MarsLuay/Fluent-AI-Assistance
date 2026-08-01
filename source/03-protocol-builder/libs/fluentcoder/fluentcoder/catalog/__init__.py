"""FluentControl catalog: SQLite-backed labware/location/liquid-class lookup.

The v1.1 catalog index (`install_index.db`) is built by walking the FC
install directory and parsing each XCMP/XWSP. Lookup helpers in `catalog.py`
resolve catalog names to file paths; `xcmp.py` parses the XML on demand.

The legacy `database.py` (vendored `tecan.db`) is kept for backward
compatibility but is not used by v1.1's catalog-driven labware construction.
"""

from .catalog import (
    CatalogEntry, WorkspaceEntry, LiquidClassEntry, ConnectorEntry,
    DEFAULT_INDEX_PATH,
    open_index, index_exists,
    resolve_by_name, find_components, find_components_by_metadata, list_by_category,
    category_counts, resolve_workspace_by_name, resolve_liquid_class_by_name,
    resolve_connector_by_guid, resolve_connector_by_name,
    install_info,
)
from .database import TecanDatabase, get_database
from .dsl_recipes import (
    FakeRecipeEmbedder,
    HashingRecipeEmbedder,
    retrieve_dsl_recipes,
    seed_curated_dsl_recipes,
)
from .fc_install import default_install_bundle, rewrite_checksum_in_place
from .indexer import build_index, install_path_default, fingerprint_matches, index_connector_paths
from .inference import CATEGORIES, infer_category
from .paths import index_db_path_default
from .category_overrides import (
    category_overrides_path_default,
    clear_category_overrides_cache,
    load_category_overrides,
)
from .xcmp import (
    XcmpComponent, XcmpArrangement, XcmpPipettable, XcmpCavity,
    XsitSite,
    XwspWorkspace, WorkspaceOccupant,
    load_xcmp, load_xsit, load_xwsp,
)
from .xlqc import XlqcLiquidClass, load_xlqc
from .xcon import XconConnector, load_xcon, parse_connector

__all__ = [
    # v1.1 catalog API
    "CatalogEntry", "WorkspaceEntry", "LiquidClassEntry", "ConnectorEntry",
    "DEFAULT_INDEX_PATH",
    "open_index", "index_exists",
    "resolve_by_name", "find_components", "find_components_by_metadata", "list_by_category",
    "category_counts", "resolve_workspace_by_name", "resolve_liquid_class_by_name",
    "resolve_connector_by_guid", "resolve_connector_by_name",
    "install_info",
    "build_index", "install_path_default", "fingerprint_matches", "index_connector_paths",
    "index_db_path_default",
    "category_overrides_path_default", "load_category_overrides", "clear_category_overrides_cache",
    "CATEGORIES", "infer_category",
    "XcmpComponent", "XcmpArrangement", "XcmpPipettable", "XcmpCavity",
    "XsitSite",
    "XwspWorkspace", "WorkspaceOccupant",
    "load_xcmp", "load_xsit", "load_xwsp",
    "XlqcLiquidClass", "load_xlqc",
    "XconConnector", "load_xcon", "parse_connector",
    # Legacy
    "TecanDatabase", "get_database",
    "FakeRecipeEmbedder", "HashingRecipeEmbedder",
    "retrieve_dsl_recipes", "seed_curated_dsl_recipes",
    "default_install_bundle", "rewrite_checksum_in_place",
]


def ensure_index() -> None:
    """Build (or rebuild) the catalog index as needed.

    Called at the top of `fluentcoder/__init__.py` on first import. Behaviour:

    1. If an explicit catalog DB env var is set, leave it untouched.
    2. If the index file is missing, build it from the default install.
    3. If the index exists but its fingerprint no longer matches the
       on-disk install (FC update / new components added), rebuild — unless
       the env var `FLUENTCODER_NO_AUTO_REBUILD` is set, in which case warn
       once and leave the stale index in place.
    4. If no FC install is reachable, return silently and leave whatever
       state is on disk; the offline fallback in labware classes handles
       the rest.

    Indexing must never break imports — exceptions are caught and dropped.
    """
    import os
    import warnings

    if os.environ.get("FLUENTCODER_TEST_CATALOG_DB") or os.environ.get("FLUENTCODER_INDEX_DB"):
        return

    install = install_path_default()
    db_path = index_db_path_default(install)
    components_dir = install / "SystemSpecific" / "Worktable" / "Components"
    if not components_dir.exists():
        return  # offline-fallback territory; caller deals with empty index

    if not index_exists(db_path=db_path):
        try:
            build_index(install_path=install, db_path=db_path)
        except Exception:
            pass
        return

    # Index exists — check for drift.
    try:
        if fingerprint_matches(install, db_path=db_path):
            return
    except Exception:
        return

    if os.environ.get("FLUENTCODER_NO_AUTO_REBUILD"):
        warnings.warn(
            "Catalog index fingerprint does not match the on-disk install. "
            "Run `fluentcoder catalog refresh` to rebuild "
            "(or unset FLUENTCODER_NO_AUTO_REBUILD).",
            stacklevel=2,
        )
        return

    try:
        build_index(install_path=install, db_path=db_path)
    except Exception:
        pass
