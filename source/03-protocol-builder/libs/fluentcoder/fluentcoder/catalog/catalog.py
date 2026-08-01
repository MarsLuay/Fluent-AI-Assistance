"""SQL queries over the catalog index.

The index lives at `fluentcoder/catalog/install_index.db` (inside the package).
It's built by `indexer.build_index()` and queried by everything else in
fluentcoder that needs to resolve a catalog name to a file path.
"""

from __future__ import annotations

import sqlite3
import json
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterator, Optional


DEFAULT_INDEX_PATH = Path(__file__).resolve().parent / "install_index.db"


# ── Public types ───────────────────────────────────────────────────


@dataclass(frozen=True)
class CatalogEntry:
    """One row from the `components` table."""

    guid: str
    name: str
    category: str
    file_path: Path
    grid_x: Optional[int] = None
    grid_y: Optional[int] = None
    dim_x_mm: Optional[float] = None
    dim_y_mm: Optional[float] = None
    dim_z_mm: Optional[float] = None
    site_count: Optional[int] = None
    functional_group: Optional[str] = None
    component_kind: str = "unknown"
    component_subtype: Optional[str] = None


@dataclass(frozen=True)
class WorkspaceEntry:
    """One row from the `workspaces` table."""

    guid: str
    name: str
    file_path: Path


@dataclass(frozen=True)
class LiquidClassEntry:
    """One row from the `liquid_classes` table."""

    guid: str
    name: str
    head: Optional[str]
    file_path: Path
    supported_heads: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConnectorEntry:
    """One row from the lightweight `connectors` table."""

    guid: str
    name: str
    component_guid: str
    site_guid: str
    file_path: Path
    is_default: bool = False


# ── Schema ─────────────────────────────────────────────────────────


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS install (
    install_path  TEXT PRIMARY KEY,
    install_key   TEXT NOT NULL,
    fingerprint   TEXT NOT NULL,
    built_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS components (
    install_key   TEXT NOT NULL,
    guid          TEXT NOT NULL,
    name          TEXT NOT NULL,
    category      TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    grid_x        INTEGER,
    grid_y        INTEGER,
    dim_x_mm      REAL,
    dim_y_mm      REAL,
    dim_z_mm      REAL,
    site_count    INTEGER,
    functional_group TEXT,
    component_kind TEXT NOT NULL DEFAULT 'unknown',
    component_subtype TEXT,
    PRIMARY KEY (install_key, guid)
);

CREATE TABLE IF NOT EXISTS workspaces (
    install_key   TEXT NOT NULL,
    guid          TEXT NOT NULL,
    name          TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    PRIMARY KEY (install_key, guid)
);

CREATE TABLE IF NOT EXISTS sites (
    install_key   TEXT NOT NULL,
    guid          TEXT NOT NULL,
    file_path     TEXT NOT NULL,
    PRIMARY KEY (install_key, guid)
);

CREATE TABLE IF NOT EXISTS liquid_classes (
    install_key   TEXT NOT NULL,
    guid          TEXT NOT NULL,
    name          TEXT NOT NULL,
    head          TEXT,
    supported_heads TEXT,
    file_path     TEXT NOT NULL,
    PRIMARY KEY (install_key, guid)
);

CREATE TABLE IF NOT EXISTS connectors (
    install_key     TEXT NOT NULL,
    guid            TEXT NOT NULL,
    name            TEXT NOT NULL,
    component_guid  TEXT NOT NULL,
    site_guid       TEXT NOT NULL,
    is_default      INTEGER NOT NULL DEFAULT 0,
    file_path       TEXT NOT NULL,
    PRIMARY KEY (install_key, guid)
);

CREATE TABLE IF NOT EXISTS indexed_sources (
    install_key         TEXT NOT NULL,
    relative_path       TEXT NOT NULL,
    source_fingerprint  TEXT NOT NULL,
    entity_table        TEXT NOT NULL,
    entity_key          TEXT NOT NULL,
    PRIMARY KEY (install_key, relative_path)
);
"""

SCHEMA_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS components_by_name     ON components(install_key, name);
CREATE INDEX IF NOT EXISTS components_by_category ON components(install_key, category);
CREATE INDEX IF NOT EXISTS workspaces_by_name ON workspaces(install_key, name);
CREATE INDEX IF NOT EXISTS liquid_classes_by_name ON liquid_classes(install_key, name);
CREATE INDEX IF NOT EXISTS connectors_by_name ON connectors(install_key, name);
CREATE INDEX IF NOT EXISTS connectors_by_site ON connectors(install_key, site_guid);
CREATE INDEX IF NOT EXISTS connectors_by_component ON connectors(install_key, component_guid);
CREATE INDEX IF NOT EXISTS components_by_kind ON components(component_kind);
CREATE INDEX IF NOT EXISTS components_by_subtype ON components(component_subtype);
"""


# ── Connection management ──────────────────────────────────────────


def _resolved_db_path(db_path: Path | str | None = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    from .paths import index_db_path_default

    return index_db_path_default()


@contextmanager
def open_index(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Context manager returning a connection to the catalog index."""
    path = _resolved_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA_SQL)
        _migrate_schema(conn)
        conn.executescript(SCHEMA_INDEX_SQL)
        yield conn
    finally:
        conn.close()


def index_exists(db_path: Path | str | None = None) -> bool:
    """True iff the index DB has at least one component row for the active install."""
    path = _resolved_db_path(db_path)
    if not path.exists():
        return False
    try:
        with open_index(path) as conn:
            install_key = _resolve_install_key(conn)
            if not install_key:
                return False
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM components WHERE install_key = ?",
                (install_key,),
            ).fetchone()
            return bool(row and row["n"] > 0)
    except sqlite3.DatabaseError:
        return False


# ── Queries ────────────────────────────────────────────────────────


def resolve_by_name(name: str, *, db_path: Path | str | None = None) -> Optional[CatalogEntry]:
    """Return the unique component with this exact name for the active install, or None."""
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return None
        row = conn.execute(
            "SELECT * FROM components WHERE install_key = ? AND name = ? LIMIT 1",
            (install_key, name),
        ).fetchone()
    if not row:
        return None
    entry = _entry_from_row(row)
    from .category_overrides import get_category_override

    override = get_category_override(name)
    if override and override != entry.category:
        return replace(entry, category=override)
    return entry


def find_components(pattern: str, *, db_path: Path | str | None = None) -> list[CatalogEntry]:
    """Substring search on component name. Case-insensitive."""
    like = f"%{pattern}%"
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return []
        rows = conn.execute(
            "SELECT * FROM components WHERE install_key = ? AND name LIKE ? COLLATE NOCASE ORDER BY name",
            (install_key, like),
        ).fetchall()
    return [_entry_from_row(r) for r in rows]


def find_components_by_metadata(
    pattern: str,
    *,
    component_kind: str | None = None,
    component_subtype: str | None = None,
    db_path: Path | str | None = None,
) -> list[CatalogEntry]:
    """Substring search on component name with optional component taxonomy filters."""
    like = f"%{pattern}%"
    clauses = ["install_key = ?", "name LIKE ? COLLATE NOCASE"]
    params: list[str] = []
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return []
        params = [install_key, like]
        if component_kind:
            clauses.append("component_kind = ?")
            params.append(component_kind)
        if component_subtype:
            clauses.append("component_subtype = ?")
            params.append(component_subtype)
        sql = "SELECT * FROM components WHERE " + " AND ".join(clauses) + " ORDER BY name"
        rows = conn.execute(sql, params).fetchall()
    return [_entry_from_row(r) for r in rows]


def list_by_category(category: str, *, db_path: Path | str | None = None) -> list[CatalogEntry]:
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return []
        rows = conn.execute(
            "SELECT * FROM components WHERE install_key = ? AND category = ? ORDER BY name",
            (install_key, category),
        ).fetchall()
    return [_entry_from_row(r) for r in rows]


def category_counts(*, db_path: Path | str | None = None) -> dict[str, int]:
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return {}
        rows = conn.execute(
            "SELECT category, COUNT(*) AS n FROM components "
            "WHERE install_key = ? GROUP BY category ORDER BY n DESC",
            (install_key,),
        ).fetchall()
    return {r["category"]: r["n"] for r in rows}


def resolve_workspace_by_name(name: str, *, db_path: Path | str | None = None) -> Optional[WorkspaceEntry]:
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return None
        row = conn.execute(
            "SELECT * FROM workspaces WHERE install_key = ? AND name = ? LIMIT 1",
            (install_key, name),
        ).fetchone()
    if not row:
        return None
    return WorkspaceEntry(guid=row["guid"], name=row["name"], file_path=Path(row["file_path"]))


def resolve_workspace_by_guid(guid: str, *, db_path: Path | str | None = None) -> Optional[WorkspaceEntry]:
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return None
        row = conn.execute(
            "SELECT * FROM workspaces WHERE install_key = ? AND guid = ? LIMIT 1",
            (install_key, guid),
        ).fetchone()
    if not row:
        return None
    return WorkspaceEntry(guid=row["guid"], name=row["name"], file_path=Path(row["file_path"]))


def resolve_connector_by_guid(
    guid: str, *, db_path: Path | str | None = None
) -> Optional[ConnectorEntry]:
    """Return the connectors row matching ``guid`` exactly, or None."""
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return None
        row = conn.execute(
            "SELECT * FROM connectors WHERE install_key = ? AND guid = ? LIMIT 1",
            (install_key, guid),
        ).fetchone()
    if not row:
        return None
    return _connector_entry_from_row(row)


def resolve_connector_by_name(
    name: str, *, db_path: Path | str | None = None
) -> Optional[ConnectorEntry]:
    """Return the unique connector with this exact name for the active install, or None."""
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return None
        row = conn.execute(
            "SELECT * FROM connectors WHERE install_key = ? AND name = ? LIMIT 1",
            (install_key, name),
        ).fetchone()
    if not row:
        return None
    return _connector_entry_from_row(row)


def resolve_liquid_class_by_name(
    name: str, *, db_path: Path | str | None = None
) -> Optional[LiquidClassEntry]:
    """Return the liquid_classes row matching ``name`` exactly, or None."""
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return None
        row = conn.execute(
            "SELECT * FROM liquid_classes WHERE install_key = ? AND name = ? LIMIT 1",
            (install_key, name),
        ).fetchone()
    if not row:
        return None
    supported_heads = _json_tuple(row["supported_heads"]) if "supported_heads" in row.keys() else ()
    if not supported_heads:
        try:
            from .xlqc import load_xlqc

            supported_heads = load_xlqc(Path(row["file_path"])).supported_heads
        except Exception:
            supported_heads = ()
    return LiquidClassEntry(
        guid=row["guid"],
        name=row["name"],
        head=row["head"],
        file_path=Path(row["file_path"]),
        supported_heads=supported_heads,
    )


def install_info(*, db_path: Path | str | None = None) -> Optional[dict[str, str]]:
    """Return the install path / fingerprint / built_at row for the active install, or None."""
    with open_index(db_path) as conn:
        install_key = _resolve_install_key(conn)
        if not install_key:
            return None
        row = conn.execute(
            "SELECT * FROM install WHERE install_key = ? LIMIT 1",
            (install_key,),
        ).fetchone()
        if row is None:
            row = conn.execute("SELECT * FROM install LIMIT 1").fetchone()
    if not row:
        return None
    return dict(row)


def resolve_install_key(conn: sqlite3.Connection) -> Optional[str]:
    """Public alias for the active install key within an open index connection."""
    return _resolve_install_key(conn)


# ── Helpers ────────────────────────────────────────────────────────


def _connector_entry_from_row(row: sqlite3.Row) -> ConnectorEntry:
    return ConnectorEntry(
        guid=row["guid"],
        name=row["name"],
        component_guid=row["component_guid"],
        site_guid=row["site_guid"],
        file_path=Path(row["file_path"]),
        is_default=bool(row["is_default"]),
    )


def _entry_from_row(row: sqlite3.Row) -> CatalogEntry:
    return CatalogEntry(
        guid=row["guid"],
        name=row["name"],
        category=row["category"],
        file_path=Path(row["file_path"]),
        grid_x=row["grid_x"],
        grid_y=row["grid_y"],
        dim_x_mm=row["dim_x_mm"],
        dim_y_mm=row["dim_y_mm"],
        dim_z_mm=row["dim_z_mm"],
        site_count=row["site_count"],
        functional_group=row["functional_group"] if "functional_group" in row.keys() else None,
        component_kind=row["component_kind"] if "component_kind" in row.keys() and row["component_kind"] else "unknown",
        component_subtype=row["component_subtype"] if "component_subtype" in row.keys() else None,
    )


def _migrate_schema(conn: sqlite3.Connection) -> None:
    component_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(components)").fetchall()
    }
    if "functional_group" not in component_columns:
        conn.execute("ALTER TABLE components ADD COLUMN functional_group TEXT")
    if "component_kind" not in component_columns:
        conn.execute("ALTER TABLE components ADD COLUMN component_kind TEXT NOT NULL DEFAULT 'unknown'")
    if "component_subtype" not in component_columns:
        conn.execute("ALTER TABLE components ADD COLUMN component_subtype TEXT")

    liquid_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(liquid_classes)").fetchall()
    }
    if "supported_heads" not in liquid_columns:
        conn.execute("ALTER TABLE liquid_classes ADD COLUMN supported_heads TEXT")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS connectors (
            install_key     TEXT NOT NULL,
            guid            TEXT NOT NULL,
            name            TEXT NOT NULL,
            component_guid  TEXT NOT NULL,
            site_guid       TEXT NOT NULL,
            is_default      INTEGER NOT NULL DEFAULT 0,
            file_path       TEXT NOT NULL,
            PRIMARY KEY (install_key, guid)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS indexed_sources (
            relative_path TEXT PRIMARY KEY,
            source_fingerprint TEXT NOT NULL,
            entity_table TEXT NOT NULL,
            entity_key TEXT NOT NULL
        )
        """
    )

    _migrate_install_key_schema(conn)


def _migrate_install_key_schema(conn: sqlite3.Connection) -> None:
    component_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(components)").fetchall()
    }
    if "install_key" in component_columns:
        return

    from .paths import install_path_key

    install_rows = conn.execute("SELECT install_path FROM install").fetchall()
    if install_rows:
        legacy_key = install_path_key(install_rows[0]["install_path"])
    else:
        legacy_key = "legacy"

    install_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(install)").fetchall()
    }
    if "install_key" not in install_columns:
        conn.execute("ALTER TABLE install ADD COLUMN install_key TEXT NOT NULL DEFAULT ''")
    conn.execute(
        "UPDATE install SET install_key = ? WHERE install_key = '' OR install_key IS NULL",
        (legacy_key,),
    )

    for table, columns_sql in (
        (
            "components",
            """
            CREATE TABLE components_new (
                install_key TEXT NOT NULL,
                guid TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                file_path TEXT NOT NULL,
                grid_x INTEGER,
                grid_y INTEGER,
                dim_x_mm REAL,
                dim_y_mm REAL,
                dim_z_mm REAL,
                site_count INTEGER,
                functional_group TEXT,
                component_kind TEXT NOT NULL DEFAULT 'unknown',
                component_subtype TEXT,
                PRIMARY KEY (install_key, guid)
            )
            """,
        ),
        (
            "workspaces",
            """
            CREATE TABLE workspaces_new (
                install_key TEXT NOT NULL,
                guid TEXT NOT NULL,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                PRIMARY KEY (install_key, guid)
            )
            """,
        ),
        (
            "sites",
            """
            CREATE TABLE sites_new (
                install_key TEXT NOT NULL,
                guid TEXT NOT NULL,
                file_path TEXT NOT NULL,
                PRIMARY KEY (install_key, guid)
            )
            """,
        ),
        (
            "liquid_classes",
            """
            CREATE TABLE liquid_classes_new (
                install_key TEXT NOT NULL,
                guid TEXT NOT NULL,
                name TEXT NOT NULL,
                head TEXT,
                supported_heads TEXT,
                file_path TEXT NOT NULL,
                PRIMARY KEY (install_key, guid)
            )
            """,
        ),
    ):
        conn.execute(columns_sql)
        old_cols = {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        select_cols = [col for col in old_cols if col != "install_key"]
        conn.execute(
            f"INSERT INTO {table}_new (install_key, {', '.join(select_cols)}) "
            f"SELECT ?, {', '.join(select_cols)} FROM {table}",
            (legacy_key,),
        )
        conn.execute(f"DROP TABLE {table}")
        conn.execute(f"ALTER TABLE {table}_new RENAME TO {table}")

    indexed_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(indexed_sources)").fetchall()
    }
    if "install_key" not in indexed_columns:
        conn.execute(
            """
            CREATE TABLE indexed_sources_new (
                install_key TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                entity_table TEXT NOT NULL,
                entity_key TEXT NOT NULL,
                PRIMARY KEY (install_key, relative_path)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO indexed_sources_new
                (install_key, relative_path, source_fingerprint, entity_table, entity_key)
            SELECT ?, relative_path, source_fingerprint, entity_table, entity_key
            FROM indexed_sources
            """,
            (legacy_key,),
        )
        conn.execute("DROP TABLE indexed_sources")
        conn.execute("ALTER TABLE indexed_sources_new RENAME TO indexed_sources")

    conn.executescript(SCHEMA_INDEX_SQL)


def _resolve_install_key(conn: sqlite3.Connection) -> Optional[str]:
    from .indexer import install_path_default
    from .paths import install_path_key

    install = install_path_default()
    install_str = str(install)
    install_key = install_path_key(install)

    if conn.execute(
        "SELECT 1 FROM install WHERE install_path = ?",
        (install_str,),
    ).fetchone():
        return install_key

    install_rows = conn.execute("SELECT install_path FROM install").fetchall()
    if len(install_rows) == 1:
        return install_path_key(install_rows[0]["install_path"])

    if len(install_rows) > 1:
        for row in install_rows:
            if row["install_path"] == install_str:
                return install_path_key(install_str)
        return None

    rows = conn.execute(
        "SELECT DISTINCT install_key FROM components WHERE install_key != ''"
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0]["install_key"])
    if len(rows) == 0:
        return install_key
    return None


def _json_tuple(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed if item)
