"""Catalog indexer — scans a FluentControl install and writes the SQL index.

One public entry point: `build_index(install_path, db_path)`. Walks the
`SystemSpecific/Worktable/Components/`, `Workspaces/`, and `Sites/`
sub-directories of the install, parses each XCMP/XWSP/XSIT, infers the
component category, and inserts rows into `install_index.db`. Connectors are
indexed from site-referenced ``.xcon`` files by default; opt in to a full
``Connectors/*.xcon`` walk via ``include_all_connectors=True`` or
``FLUENTCODER_INDEX_ALL_CONNECTORS=1``.

Incremental: unchanged source files reuse existing indexed rows via per-file
content fingerprints stored in `indexed_sources`. Re-imports that only reset
mtimes hit the stat fingerprint fast path and skip parsing entirely.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .catalog import open_index
from .paths import index_db_path_default, install_path_key
from .inference import component_taxonomy, infer_category
from .xcmp import load_xcmp, load_xsit, load_xwsp


DEFAULT_INSTALL_PATH = Path(r"C:\ProgramData\Tecan\VisionX\Database")
_HASH_CHUNK = 1024 * 1024


def install_path_default() -> Path:
    """Resolve the default FluentControl install path.

    Priority: `FLUENTCODER_FC_INSTALL` env var > hard-coded default.
    """
    env = os.environ.get("FLUENTCODER_FC_INSTALL")
    return Path(env) if env else DEFAULT_INSTALL_PATH


def _index_all_connectors_enabled(include_all_connectors: Optional[bool] = None) -> bool:
    """True when the full ``Connectors/*.xcon`` tree should be walked."""
    if include_all_connectors is not None:
        return include_all_connectors
    return os.environ.get("FLUENTCODER_INDEX_ALL_CONNECTORS", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def build_index(
    install_path: Optional[Path | str] = None,
    db_path: Optional[Path | str] = None,
    *,
    include_all_connectors: Optional[bool] = None,
) -> dict[str, int]:
    """Walk the install, parse, infer, write rows. Returns row counts.

    Counts dict keys: `components`, `workspaces`, `sites`, plus per-category.

    By default only connectors referenced by indexed ``.xsit`` files are stored.
    Pass ``include_all_connectors=True`` or set ``FLUENTCODER_INDEX_ALL_CONNECTORS=1``
    to walk ``SystemSpecific/Worktable/Connectors/*.xcon`` (14k+ on a full install).
    """
    install = Path(install_path) if install_path else install_path_default()
    db = Path(db_path) if db_path else index_db_path_default(install)
    install_key = install_path_key(install)
    index_all_connectors = _index_all_connectors_enabled(include_all_connectors)

    components_dir = install / "SystemSpecific" / "Worktable" / "Components"
    workspaces_dir = install / "SystemSpecific" / "Worktable" / "Workspaces"
    sites_dir = install / "SystemSpecific" / "Worktable" / "Sites"
    liquid_classes_dir = install / "SystemSpecific" / "LiquidClasses"

    if not components_dir.exists():
        raise FileNotFoundError(
            f"Components directory not found at {components_dir!s}. "
            f"Set FLUENTCODER_FC_INSTALL or pass install_path explicitly."
        )

    stat_fingerprint = _install_stat_fingerprint(
        install,
        include_all_connectors=index_all_connectors,
    )

    with open_index(db) as conn:
        install_row = conn.execute(
            "SELECT install_path, fingerprint FROM install WHERE install_path = ?",
            (str(install),),
        ).fetchone()
        if (
            install_row
            and install_row["fingerprint"] == stat_fingerprint
            and conn.execute(
                "SELECT COUNT(*) AS n FROM components WHERE install_key = ?",
                (install_key,),
            ).fetchone()["n"]
            > 0
        ):
            return _read_counts(conn, install_key)

        existing_sources = _load_indexed_sources(conn, install_key)
        seen_paths: set[str] = set()

        for path in sorted(components_dir.glob("*.xcmp"), key=lambda p: p.as_posix()):
            rel = _relative_install_path(path, install)
            seen_paths.add(rel)
            content_fp = _source_content_fingerprint(path, install)
            cached = existing_sources.get(rel)
            if (
                cached
                and cached["source_fingerprint"] == content_fp
                and cached["entity_table"] == "components"
            ):
                row = conn.execute(
                    "SELECT category FROM components WHERE install_key = ? AND guid = ?",
                    (install_key, cached["entity_key"]),
                ).fetchone()
                if row is not None:
                    continue

            try:
                comp = load_xcmp(path)
            except Exception:
                continue
            category = infer_category(comp)

            grid_x, grid_y = _component_grid(comp)
            dim = comp.dim_mm or (None, None, None)
            site_count = comp.arrangement.site_count if comp.arrangement else None
            functional_group, component_kind, component_subtype = component_taxonomy(
                comp.functional_group
            )

            conn.execute(
                """INSERT OR REPLACE INTO components
                   (install_key, guid, name, category, file_path, grid_x, grid_y,
                    dim_x_mm, dim_y_mm, dim_z_mm, site_count,
                    functional_group, component_kind, component_subtype)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    install_key,
                    comp.guid,
                    comp.name,
                    category,
                    str(comp.file_path),
                    grid_x,
                    grid_y,
                    dim[0],
                    dim[1],
                    dim[2],
                    site_count,
                    functional_group,
                    component_kind,
                    component_subtype,
                ),
            )
            _upsert_indexed_source(
                conn,
                install_key=install_key,
                relative_path=rel,
                source_fingerprint=content_fp,
                entity_table="components",
                entity_key=comp.guid,
            )

        if workspaces_dir.exists():
            for path in sorted(workspaces_dir.glob("*.xwsp"), key=lambda p: p.as_posix()):
                rel = _relative_install_path(path, install)
                seen_paths.add(rel)
                content_fp = _source_content_fingerprint(path, install)
                cached = existing_sources.get(rel)
                if (
                    cached
                    and cached["source_fingerprint"] == content_fp
                    and cached["entity_table"] == "workspaces"
                ):
                    if conn.execute(
                        "SELECT 1 FROM workspaces WHERE install_key = ? AND guid = ?",
                        (install_key, cached["entity_key"]),
                    ).fetchone():
                        continue

                try:
                    ws = load_xwsp(path)
                except Exception:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO workspaces (install_key, guid, name, file_path) VALUES (?, ?, ?, ?)",
                    (install_key, ws.guid, ws.name, str(ws.file_path)),
                )
                _upsert_indexed_source(
                    conn,
                    install_key=install_key,
                    relative_path=rel,
                    source_fingerprint=content_fp,
                    entity_table="workspaces",
                    entity_key=ws.guid,
                )

            workspace_count = conn.execute(
                "SELECT COUNT(*) AS n FROM workspaces WHERE install_key = ?",
                (install_key,),
            ).fetchone()["n"]
            indexed_workspace_count = conn.execute(
                "SELECT COUNT(*) AS n FROM indexed_sources "
                "WHERE install_key = ? AND entity_table = 'workspaces'",
                (install_key,),
            ).fetchone()["n"]
            if indexed_workspace_count and workspace_count != indexed_workspace_count:
                raise RuntimeError(
                    "Workspace index build collapsed parsed `.xwsp` files into fewer rows. "
                    "Check workspace GUID extraction for collisions."
                )

        if sites_dir.exists():
            for path in sorted(sites_dir.glob("*.xsit"), key=lambda p: p.as_posix()):
                rel = _relative_install_path(path, install)
                seen_paths.add(rel)
                content_fp = _source_content_fingerprint(path, install)
                cached = existing_sources.get(rel)
                guid = path.stem
                if (
                    cached
                    and cached["source_fingerprint"] == content_fp
                    and cached["entity_table"] == "sites"
                ):
                    if conn.execute(
                        "SELECT 1 FROM sites WHERE install_key = ? AND guid = ?",
                        (install_key, cached["entity_key"]),
                    ).fetchone():
                        continue

                conn.execute(
                    "INSERT OR REPLACE INTO sites (install_key, guid, file_path) VALUES (?, ?, ?)",
                    (install_key, guid, str(path)),
                )
                _upsert_indexed_source(
                    conn,
                    install_key=install_key,
                    relative_path=rel,
                    source_fingerprint=content_fp,
                    entity_table="sites",
                    entity_key=guid,
                )

        connectors_dir = install / "SystemSpecific" / "Worktable" / "Connectors"
        if connectors_dir.exists():
            if index_all_connectors:
                _index_all_connectors(
                    conn,
                    install=install,
                    install_key=install_key,
                    connectors_dir=connectors_dir,
                    seen_paths=seen_paths,
                    existing_sources=existing_sources,
                )
            elif sites_dir.exists():
                _index_site_referenced_connectors(
                    conn,
                    install=install,
                    install_key=install_key,
                    sites_dir=sites_dir,
                    connectors_dir=connectors_dir,
                    seen_paths=seen_paths,
                    existing_sources=existing_sources,
                )

        if liquid_classes_dir.exists():
            from .xlqc import load_xlqc

            for path in sorted(liquid_classes_dir.glob("*.xlqc"), key=lambda p: p.as_posix()):
                rel = _relative_install_path(path, install)
                seen_paths.add(rel)
                content_fp = _source_content_fingerprint(path, install)
                cached = existing_sources.get(rel)
                if (
                    cached
                    and cached["source_fingerprint"] == content_fp
                    and cached["entity_table"] == "liquid_classes"
                ):
                    if conn.execute(
                        "SELECT 1 FROM liquid_classes WHERE install_key = ? AND guid = ?",
                        (install_key, cached["entity_key"]),
                    ).fetchone():
                        continue

                try:
                    lc = load_xlqc(path)
                except Exception:
                    continue
                conn.execute(
                    "INSERT OR REPLACE INTO liquid_classes "
                    "(install_key, guid, name, head, supported_heads, file_path) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        install_key,
                        lc.guid,
                        lc.name,
                        lc.head,
                        json.dumps(list(lc.supported_heads)),
                        str(lc.file_path),
                    ),
                )
                _upsert_indexed_source(
                    conn,
                    install_key=install_key,
                    relative_path=rel,
                    source_fingerprint=content_fp,
                    entity_table="liquid_classes",
                    entity_key=lc.guid,
                )

        _purge_stale_sources(conn, seen_paths, install_key)

        conn.execute(
            "INSERT OR REPLACE INTO install (install_path, install_key, fingerprint, built_at) "
            "VALUES (?, ?, ?, ?)",
            (
                str(install),
                install_key,
                stat_fingerprint,
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
        return _read_counts(conn, install_key)


def index_connector_paths(
    paths: list[Path | str],
    *,
    install_path: Optional[Path | str] = None,
    db_path: Optional[Path | str] = None,
) -> int:
    """Parse and upsert explicit ``.xcon`` paths into the connectors table.

    Use this for targeted connector indexing without walking the full install
    connector tree (~14k files).
    """
    from .xcon import load_xcon

    install = Path(install_path) if install_path else install_path_default()
    db = Path(db_path) if db_path else index_db_path_default(install)
    install_key = install_path_key(install)
    indexed = 0

    with open_index(db) as conn:
        for raw in paths:
            path = Path(raw).resolve()
            if not path.exists():
                continue
            try:
                connector = load_xcon(path)
            except Exception:
                continue
            try:
                rel = _relative_install_path(path, install)
                content_fp = _source_content_fingerprint(path, install)
            except ValueError:
                rel = path.name
                content_fp = ""
            conn.execute(
                """INSERT OR REPLACE INTO connectors
                   (install_key, guid, name, component_guid, site_guid, is_default, file_path)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    install_key,
                    connector.guid,
                    connector.name,
                    connector.component_guid,
                    connector.site_guid,
                    int(connector.is_default),
                    str(connector.file_path),
                ),
            )
            if content_fp:
                _upsert_indexed_source(
                    conn,
                    install_key=install_key,
                    relative_path=rel,
                    source_fingerprint=content_fp,
                    entity_table="connectors",
                    entity_key=connector.guid,
                )
            indexed += 1
        conn.commit()
    return indexed


def _index_connector_file(
    conn,
    *,
    install: Path,
    install_key: str,
    path: Path,
    seen_paths: set[str],
    existing_sources: dict[str, dict[str, str]],
) -> None:
    """Parse and upsert one ``.xcon`` connector file when changed or missing."""
    from .xcon import load_xcon

    rel = _relative_install_path(path, install)
    seen_paths.add(rel)
    content_fp = _source_content_fingerprint(path, install)
    cached = existing_sources.get(rel)
    if (
        cached
        and cached["source_fingerprint"] == content_fp
        and cached["entity_table"] == "connectors"
    ):
        if conn.execute(
            "SELECT 1 FROM connectors WHERE install_key = ? AND guid = ?",
            (install_key, cached["entity_key"]),
        ).fetchone():
            return
    try:
        connector = load_xcon(path)
    except Exception:
        return
    conn.execute(
        """INSERT OR REPLACE INTO connectors
           (install_key, guid, name, component_guid, site_guid, is_default, file_path)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            install_key,
            connector.guid,
            connector.name,
            connector.component_guid,
            connector.site_guid,
            int(connector.is_default),
            str(connector.file_path),
        ),
    )
    _upsert_indexed_source(
        conn,
        install_key=install_key,
        relative_path=rel,
        source_fingerprint=content_fp,
        entity_table="connectors",
        entity_key=connector.guid,
    )


def _index_site_referenced_connectors(
    conn,
    *,
    install: Path,
    install_key: str,
    sites_dir: Path,
    connectors_dir: Path,
    seen_paths: set[str],
    existing_sources: dict[str, dict[str, str]],
) -> None:
    """Index only connectors referenced by indexed site definitions."""
    connector_guids: set[str] = set()
    for path in sorted(sites_dir.glob("*.xsit"), key=lambda item: item.as_posix()):
        try:
            site = load_xsit(path)
        except Exception:
            continue
        connector_guids.update(site.connector_guids)

    for connector_guid in sorted(connector_guids):
        path = connectors_dir / f"{connector_guid}.xcon"
        if not path.exists():
            continue
        _index_connector_file(
            conn,
            install=install,
            install_key=install_key,
            path=path,
            seen_paths=seen_paths,
            existing_sources=existing_sources,
        )


def _index_all_connectors(
    conn,
    *,
    install: Path,
    install_key: str,
    connectors_dir: Path,
    seen_paths: set[str],
    existing_sources: dict[str, dict[str, str]],
) -> None:
    """Walk every ``.xcon`` under the install connectors directory."""
    for path in sorted(connectors_dir.glob("*.xcon"), key=lambda item: item.as_posix()):
        _index_connector_file(
            conn,
            install=install,
            install_key=install_key,
            path=path,
            seen_paths=seen_paths,
            existing_sources=existing_sources,
        )


def _component_grid(comp) -> tuple[Optional[int], Optional[int]]:
    """Pick the most useful (rows, cols) grid for a component.

    For pipettable labware (plates, tip boxes), use the well grid.
    For carriers without a well grid, use the arrangement site grid.
    """
    if comp.pipettable is not None:
        return comp.pipettable.x_wells, comp.pipettable.y_wells
    if comp.arrangement is not None:
        return comp.arrangement.sites_in_x, comp.arrangement.sites_in_y
    return None, None


def _relative_install_path(path: Path, install: Path) -> str:
    return path.relative_to(install).as_posix()


def _source_content_fingerprint(path: Path, install: Path) -> str:
    """Content hash for one catalog source file (path + size + bytes)."""
    digest = hashlib.sha256()
    digest.update(b"fluentcoder.catalog.content.v1")
    digest.update(_relative_install_path(path, install).encode("utf-8"))
    digest.update(str(path.stat().st_size).encode("utf-8"))
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _install_stat_fingerprint(
    install: Path,
    *,
    include_all_connectors: bool = False,
) -> str:
    """Cheap stat-only fingerprint for the whole install tree.

    Uses relative path + size (not mtime) so re-extracting an unchanged ZEIA
    still matches the indexed install.
    """
    digest = hashlib.sha256()
    digest.update(b"fluentcoder.install.stat.v1")
    digest.update(
        b"connectors:all" if include_all_connectors else b"connectors:site"
    )
    for rel_path in _catalog_source_paths(
        install,
        include_all_connectors=include_all_connectors,
    ):
        path = install / rel_path
        try:
            digest.update(rel_path.encode("utf-8"))
            digest.update(str(path.stat().st_size).encode("utf-8"))
        except OSError:
            continue
    return digest.hexdigest()


def _catalog_source_paths(
    install: Path,
    *,
    include_all_connectors: bool = False,
) -> list[str]:
    paths: list[str] = []
    for subdir, pattern in (
        ("SystemSpecific/Worktable/Components", "*.xcmp"),
        ("SystemSpecific/Worktable/Workspaces", "*.xwsp"),
        ("SystemSpecific/Worktable/Sites", "*.xsit"),
        ("SystemSpecific/LiquidClasses", "*.xlqc"),
    ):
        directory = install / subdir.replace("/", os.sep)
        if not directory.exists():
            continue
        for path in sorted(directory.glob(pattern), key=lambda item: item.as_posix()):
            paths.append(path.relative_to(install).as_posix())
    if include_all_connectors:
        connectors_dir = install / "SystemSpecific" / "Worktable" / "Connectors"
        if connectors_dir.exists():
            for path in sorted(connectors_dir.glob("*.xcon"), key=lambda item: item.as_posix()):
                paths.append(path.relative_to(install).as_posix())
    return paths


def _load_indexed_sources(conn, install_key: str) -> dict[str, dict[str, str]]:
    try:
        rows = conn.execute(
            "SELECT relative_path, source_fingerprint, entity_table, entity_key "
            "FROM indexed_sources WHERE install_key = ?",
            (install_key,),
        ).fetchall()
    except Exception:
        return {}
    return {
        str(row["relative_path"]): {
            "source_fingerprint": str(row["source_fingerprint"]),
            "entity_table": str(row["entity_table"]),
            "entity_key": str(row["entity_key"]),
        }
        for row in rows
    }


def _upsert_indexed_source(
    conn,
    *,
    install_key: str,
    relative_path: str,
    source_fingerprint: str,
    entity_table: str,
    entity_key: str,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO indexed_sources
           (install_key, relative_path, source_fingerprint, entity_table, entity_key)
           VALUES (?, ?, ?, ?, ?)""",
        (install_key, relative_path, source_fingerprint, entity_table, entity_key),
    )


def _purge_stale_sources(conn, seen_paths: set[str], install_key: str) -> None:
    rows = conn.execute(
        "SELECT relative_path, entity_table, entity_key FROM indexed_sources "
        "WHERE install_key = ?",
        (install_key,),
    ).fetchall()
    for row in rows:
        rel = str(row["relative_path"])
        if rel in seen_paths:
            continue
        table = str(row["entity_table"])
        key = str(row["entity_key"])
        conn.execute(
            f"DELETE FROM {table} WHERE install_key = ? AND guid = ?",
            (install_key, key),
        )
        conn.execute(
            "DELETE FROM indexed_sources WHERE install_key = ? AND relative_path = ?",
            (install_key, rel),
        )


def _read_counts(conn, install_key: str) -> dict[str, int]:
    counts: dict[str, int] = {
        "components": int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM components WHERE install_key = ?",
                (install_key,),
            ).fetchone()["n"]
        ),
        "workspaces": int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM workspaces WHERE install_key = ?",
                (install_key,),
            ).fetchone()["n"]
        ),
        "sites": int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM sites WHERE install_key = ?",
                (install_key,),
            ).fetchone()["n"]
        ),
        "liquid_classes": int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM liquid_classes WHERE install_key = ?",
                (install_key,),
            ).fetchone()["n"]
        ),
        "connectors": int(
            conn.execute(
                "SELECT COUNT(*) AS n FROM connectors WHERE install_key = ?",
                (install_key,),
            ).fetchone()["n"]
        ),
    }
    for row in conn.execute(
        "SELECT category, COUNT(*) AS n FROM components "
        "WHERE install_key = ? GROUP BY category",
        (install_key,),
    ).fetchall():
        counts[str(row["category"])] = int(row["n"])
    return counts


def _install_fingerprint(install: Path) -> str:
    """Backward-compatible alias for the install stat fingerprint."""
    return _install_stat_fingerprint(install)


def fingerprint_matches(
    install_path: Optional[Path | str] = None,
    db_path: Optional[Path | str] = None,
    *,
    include_all_connectors: Optional[bool] = None,
) -> bool:
    """True iff the on-disk install fingerprint matches the indexed one."""
    from .catalog import install_info  # local import to avoid cycles

    install = Path(install_path) if install_path else install_path_default()
    info = install_info(db_path=db_path)
    if not info:
        return False
    if info["install_path"] != str(install):
        return False
    try:
        return info["fingerprint"] == _install_stat_fingerprint(
            install,
            include_all_connectors=_index_all_connectors_enabled(include_all_connectors),
        )
    except FileNotFoundError:
        return False
