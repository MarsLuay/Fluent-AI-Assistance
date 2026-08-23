"""SQLite project index for many Tecan `.zeia` archives."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
import dataclasses
import hashlib
import json
import sqlite3

from .archive import inspect_archive
from .common import to_jsonable

SCHEMA_VERSION = "1"
# Repo root: .../source/01-project-reader/tecan_reader/project_index.py → parents[3]
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INDEX_PATH = (
    _REPO_ROOT
    / "ready-to-import"
    / "_shared"
    / "temp_files"
    / "build"
    / "tecan_project_index.sqlite"
)

ENTITY_KINDS = {
    "script",
    "worktable",
    "labware",
    "liquid_class",
    "carrier",
    "device_alias",
    "variable",
    "worklist",
    "subroutine",
    "hardware_pin",
    "custom_asset",
    "barcode",
    "dependency",
    "reference",
    "catalog_object",
}


@dataclasses.dataclass
class IndexContext:
    """Context for indexing operations."""

    conn: sqlite3.Connection
    zeia_id: int
    script_id: int
    source_path: str


def discover_zeia_paths(paths: Iterable[str | Path]) -> list[Path]:
    """Return unique `.zeia` files from explicit files or recursive directories."""
    discovered: list[Path] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_dir():
            discovered.extend(
                sorted(item for item in path.rglob("*.zeia") if item.is_file())
            )
            continue
        if path.is_file() and path.suffix.lower() == ".zeia":
            discovered.append(path)
            continue
        raise FileNotFoundError(f"No .zeia file found at {path}")

    unique: dict[str, Path] = {}
    for path in discovered:
        resolved = path.resolve()
        unique[str(resolved)] = resolved
    if not unique:
        raise FileNotFoundError("No .zeia files found")
    return [unique[key] for key in sorted(unique)]


def build_project_index(
    paths: Iterable[str | Path],
    db_path: str | Path = DEFAULT_INDEX_PATH,
    *,
    force: bool = False,
    script_limit: int | None = None,
    object_limit: int | None = None,
) -> dict[str, Any]:
    """Build or refresh a searchable SQLite index for one or more ZEIA files."""
    zeia_paths = discover_zeia_paths(paths)
    database = Path(db_path)
    if force and database.exists():
        database.unlink()
    database.parent.mkdir(parents=True, exist_ok=True)

    conn = _connect(database)
    try:
        _initialize_database(conn)
        indexed_files = []
        for zeia_path in zeia_paths:
            report = inspect_archive(
                zeia_path,
                script_limit=script_limit,
                object_limit=object_limit,
            )
            _index_archive(conn, zeia_path, report)
            indexed_files.append(str(zeia_path))
        conn.commit()
        summary = summarize_project_index(conn)
    finally:
        conn.close()

    return {
        **summary,
        "kind": "project_index_build",
        "database": str(database),
        "indexed_files": indexed_files,
    }


def summarize_project_index(
    db_path_or_conn: str | Path | sqlite3.Connection,
) -> dict[str, Any]:
    """Return project-level counts and file summaries from an index."""
    conn, should_close, database = _connection_arg(db_path_or_conn)
    try:
        entity_counts = {
            row["kind"]: row["count"]
            for row in conn.execute(
                "SELECT kind, COUNT(*) AS count FROM entities GROUP BY kind ORDER BY kind"
            )
        }
        command_family_counts = {row["family"]: row["count"] for row in conn.execute("""
                SELECT family, COUNT(*) AS count
                FROM commands
                GROUP BY family
                ORDER BY count DESC, family
                """)}
        files = [dict(row) for row in conn.execute("""
                SELECT path, file_name, sha256, indexed_at, entry_count,
                       script_count_total, script_count_summarized,
                       object_count_summarized, gwl_count_summarized
                FROM zeia_files
                ORDER BY file_name, path
                """)]
        return {
            "kind": "project_index_summary",
            "database": database,
            "schema_version": _metadata_value(conn, "schema_version"),
            "zeia_file_count": _count(conn, "zeia_files"),
            "script_count": _count(conn, "scripts"),
            "command_count": _count(conn, "commands"),
            "catalog_object_count": _count(conn, "catalog_objects"),
            "worklist_count": _count(conn, "worklists"),
            "command_sequence_count": _count(conn, "command_sequences"),
            "entity_counts": entity_counts,
            "command_family_counts": command_family_counts,
            "files": files,
        }
    finally:
        if should_close:
            conn.close()


def search_project_index(
    db_path: str | Path,
    query: str,
    *,
    kind: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search indexed ZEIA files, scripts, entities, commands, and sequences."""
    database = Path(db_path)
    conn = _connect(database)
    try:
        normalized_kind = (
            kind.strip().lower().replace("-", "_").replace(" ", "_") if kind else None
        )
        rows = _search_rows(conn, query=query, kind=normalized_kind, limit=limit)
        return {
            "kind": "project_index_search",
            "database": str(database),
            "query": query,
            "kind_filter": normalized_kind or "",
            "result_count": len(rows),
            "results": rows,
        }
    finally:
        conn.close()


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _connection_arg(
    value: str | Path | sqlite3.Connection,
) -> tuple[sqlite3.Connection, bool, str]:
    if isinstance(value, sqlite3.Connection):
        value.row_factory = sqlite3.Row
        return value, False, ""
    path = Path(value)
    return _connect(path), True, str(path)


def _initialize_database(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS zeia_files (
            id INTEGER PRIMARY KEY,
            path TEXT NOT NULL UNIQUE,
            file_name TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            indexed_at TEXT NOT NULL,
            entry_count INTEGER NOT NULL DEFAULT 0,
            script_count_total INTEGER NOT NULL DEFAULT 0,
            script_count_summarized INTEGER NOT NULL DEFAULT 0,
            object_count_summarized INTEGER NOT NULL DEFAULT 0,
            gwl_count_summarized INTEGER NOT NULL DEFAULT 0,
            extension_counts_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS scripts (
            id INTEGER PRIMARY KEY,
            zeia_file_id INTEGER NOT NULL REFERENCES zeia_files(id) ON DELETE CASCADE,
            entry_path TEXT NOT NULL,
            object_name TEXT NOT NULL DEFAULT '',
            script_version TEXT NOT NULL DEFAULT '',
            checksum TEXT NOT NULL DEFAULT '',
            command_count INTEGER NOT NULL DEFAULT 0,
            family_counts_json TEXT NOT NULL DEFAULT '{}',
            command_counts_json TEXT NOT NULL DEFAULT '{}',
            warnings_json TEXT NOT NULL DEFAULT '[]',
            dependencies_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS commands (
            id INTEGER PRIMARY KEY,
            zeia_file_id INTEGER NOT NULL REFERENCES zeia_files(id) ON DELETE CASCADE,
            script_id INTEGER NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
            command_index INTEGER NOT NULL,
            command_type TEXT NOT NULL DEFAULT '',
            raw_type TEXT NOT NULL DEFAULT '',
            family TEXT NOT NULL DEFAULT '',
            line TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            fields_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS command_sequences (
            id INTEGER PRIMARY KEY,
            zeia_file_id INTEGER NOT NULL REFERENCES zeia_files(id) ON DELETE CASCADE,
            script_id INTEGER NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
            start_index INTEGER NOT NULL,
            length INTEGER NOT NULL,
            command_names TEXT NOT NULL,
            command_families TEXT NOT NULL,
            source_path TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS catalog_objects (
            id INTEGER PRIMARY KEY,
            zeia_file_id INTEGER NOT NULL REFERENCES zeia_files(id) ON DELETE CASCADE,
            entry_path TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT '',
            object_name TEXT NOT NULL DEFAULT '',
            type_id TEXT NOT NULL DEFAULT '',
            functional_group TEXT NOT NULL DEFAULT '',
            footprint TEXT NOT NULL DEFAULT '',
            renderer TEXT NOT NULL DEFAULT '',
            names_json TEXT NOT NULL DEFAULT '[]',
            guids_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS worklists (
            id INTEGER PRIMARY KEY,
            zeia_file_id INTEGER NOT NULL REFERENCES zeia_files(id) ON DELETE CASCADE,
            entry_path TEXT NOT NULL,
            line_count INTEGER NOT NULL DEFAULT 0,
            transfer_pairs_estimate INTEGER NOT NULL DEFAULT 0,
            record_counts_json TEXT NOT NULL DEFAULT '{}',
            pipette_examples_json TEXT NOT NULL DEFAULT '[]'
        );

        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY,
            zeia_file_id INTEGER NOT NULL REFERENCES zeia_files(id) ON DELETE CASCADE,
            script_id INTEGER REFERENCES scripts(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            name TEXT NOT NULL DEFAULT '',
            value TEXT NOT NULL DEFAULT '',
            source_path TEXT NOT NULL DEFAULT '',
            command_index INTEGER,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_zeia_path ON zeia_files(path);
        CREATE INDEX IF NOT EXISTS idx_scripts_name ON scripts(object_name);
        CREATE INDEX IF NOT EXISTS idx_commands_type ON commands(command_type);
        CREATE INDEX IF NOT EXISTS idx_commands_family ON commands(family);
        CREATE INDEX IF NOT EXISTS idx_sequences_names ON command_sequences(command_names);
        CREATE INDEX IF NOT EXISTS idx_entities_kind_name ON entities(kind, name);
        CREATE INDEX IF NOT EXISTS idx_entities_value ON entities(value);
        """)
    conn.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
        (SCHEMA_VERSION,),
    )


def _index_archive(
    conn: sqlite3.Connection, zeia_path: Path, report: dict[str, Any]
) -> None:
    archive_path = str(zeia_path.resolve())
    existing = conn.execute(
        "SELECT id FROM zeia_files WHERE path = ?", (archive_path,)
    ).fetchone()
    if existing:
        conn.execute("DELETE FROM zeia_files WHERE id = ?", (existing["id"],))

    cursor = conn.execute(
        """
        INSERT INTO zeia_files(
            path, file_name, sha256, indexed_at, entry_count,
            script_count_total, script_count_summarized,
            object_count_summarized, gwl_count_summarized,
            extension_counts_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            archive_path,
            zeia_path.name,
            _sha256(zeia_path),
            datetime.now(timezone.utc).isoformat(),
            int(report.get("entry_count") or 0),
            int(report.get("script_count_total") or 0),
            int(report.get("script_count_summarized") or 0),
            int(report.get("object_count_summarized") or 0),
            int(report.get("gwl_count_summarized") or 0),
            _dump(report.get("extension_counts", {})),
        ),
    )
    zeia_id = int(cursor.lastrowid)

    for script in report.get("scripts", []):
        _index_script(conn, zeia_id, script)
    for obj in report.get("objects", []):
        _index_object(conn, zeia_id, obj)
    for gwl in report.get("gwls", []):
        _index_worklist(conn, zeia_id, gwl)


def _index_script(
    conn: sqlite3.Connection, zeia_id: int, script: dict[str, Any]
) -> None:
    source_path = script.get("source") or ""
    cursor = conn.execute(
        """
        INSERT INTO scripts(
            zeia_file_id, entry_path, object_name, script_version, checksum,
            command_count, family_counts_json, command_counts_json,
            warnings_json, dependencies_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            zeia_id,
            source_path,
            script.get("object_name") or "",
            script.get("script_version") or "",
            script.get("checksum") or "",
            int(script.get("command_count") or 0),
            _dump(script.get("family_counts", {})),
            _dump(script.get("command_counts", {})),
            _dump(script.get("warnings", [])),
            _dump(script.get("dependencies", {})),
        ),
    )
    script_id = int(cursor.lastrowid)
    script_name = script.get("object_name") or source_path
    _insert_entity(
        conn,
        zeia_id,
        script_id,
        "script",
        script_name,
        script.get("script_version") or "",
        source_path,
        {"checksum": script.get("checksum") or ""},
    )
    _index_references(conn, zeia_id, script_id, script)
    _index_variables(conn, zeia_id, script_id, script)
    _index_dependencies(conn, zeia_id, script_id, script)
    _index_commands(conn, zeia_id, script_id, script)
    _index_command_sequences(conn, zeia_id, script_id, script)


def _index_references(
    conn: sqlite3.Connection,
    zeia_id: int,
    script_id: int,
    script: dict[str, Any],
) -> None:
    source_path = script.get("source") or ""
    for ref in script.get("references", []):
        type_id = ref.get("type_id") or ""
        object_name = ref.get("object_name") or ref.get("guid") or ""
        metadata = {"type_id": type_id, "guid": ref.get("guid") or ""}
        kind = "worktable" if "worktable" in type_id.lower() else "reference"
        _insert_entity(
            conn, zeia_id, script_id, kind, object_name, type_id, source_path, metadata
        )
        if kind != "reference":
            _insert_entity(
                conn,
                zeia_id,
                script_id,
                "reference",
                object_name,
                type_id,
                source_path,
                metadata,
            )


def _index_variables(
    conn: sqlite3.Connection,
    zeia_id: int,
    script_id: int,
    script: dict[str, Any],
) -> None:
    source_path = script.get("source") or ""
    for variable in script.get("variables", []):
        _insert_entity(
            conn,
            zeia_id,
            script_id,
            "variable",
            variable.get("name") or "",
            variable.get("type") or "",
            source_path,
            {
                "scope": variable.get("scope") or "",
                "query_on_startup": variable.get("query_on_startup") or "",
                "read_only": variable.get("read_only") or "",
            },
        )
    for prompt in script.get("query_prompts", []):
        _insert_entity(
            conn,
            zeia_id,
            script_id,
            "variable",
            prompt.get("name") or "",
            prompt.get("prompt") or "",
            source_path,
            {
                "role": "query_prompt",
                "minimum": prompt.get("minimum") or "",
                "maximum": prompt.get("maximum") or "",
                "line": prompt.get("line") or "",
            },
        )
    for set_variable in script.get("set_variables", []):
        _insert_entity(
            conn,
            zeia_id,
            script_id,
            "variable",
            set_variable.get("name") or "",
            set_variable.get("value") or "",
            source_path,
            {"role": "set_variable", "line": set_variable.get("line") or ""},
        )


def _index_dependencies(
    conn: sqlite3.Connection,
    zeia_id: int,
    script_id: int,
    script: dict[str, Any],
) -> None:
    source_path = script.get("source") or ""
    dependencies = script.get("dependencies", {})
    dependency_kind_map = {
        "labware_names": "labware",
        "rack_labels": "labware",
        "rack_types": "carrier",
        "liquid_classes": "liquid_class",
        "device_aliases": "device_alias",
        "external_or_worklist_refs": "worklist",
        "workspace_guids": "worktable",
        "subroutine_refs": "subroutine",
        "pin_refs": "hardware_pin",
        "worktable_pin_locations": "hardware_pin",
        "custom_asset_refs": "custom_asset",
        "barcode_refs": "barcode",
    }
    for dep_key, values in dependencies.items():
        for value in values:
            _insert_entity(
                conn,
                zeia_id,
                script_id,
                dependency_kind_map.get(dep_key, "dependency"),
                value,
                dep_key,
                source_path,
                {"dependency_key": dep_key},
            )
            _insert_entity(
                conn,
                zeia_id,
                script_id,
                "dependency",
                value,
                dep_key,
                source_path,
                {"dependency_key": dep_key},
            )


def _index_commands(
    conn: sqlite3.Connection,
    zeia_id: int,
    script_id: int,
    script: dict[str, Any],
) -> None:
    source_path = script.get("source") or ""
    ctx = IndexContext(conn, zeia_id, script_id, source_path)
    for command in script.get("commands", []):
        command_index = int(command.get("index") or 0)
        fields = command.get("fields", {})
        conn.execute(
            """
            INSERT INTO commands(
                zeia_file_id, script_id, command_index, command_type, raw_type,
                family, line, name, fields_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                zeia_id,
                script_id,
                command_index,
                command.get("type") or "",
                command.get("raw_type") or "",
                command.get("family") or "",
                command.get("line") or "",
                command.get("name") or "",
                _dump(fields),
            ),
        )
        _index_command_field_entities(
            ctx,
            command_index,
            fields,
            command.get("family") or "",
        )


def _index_command_field_entities(
    ctx: IndexContext,
    command_index: int,
    fields: dict[str, Any],
    family: str,
) -> None:
    field_kind_map = {
        "LabwareName": "labware",
        "RackLabel": "labware",
        "RackType": "carrier",
        "LiquidClassName": "liquid_class",
        "LiquidClassNameBySelection": "liquid_class",
        "DeviceAlias": "device_alias",
        "WorklistName": "worklist",
    }
    for field_name, kind in field_kind_map.items():
        value = str(fields.get(field_name) or "").strip()
        if not value:
            continue
        _insert_entity(
            ctx.conn,
            ctx.zeia_id,
            ctx.script_id,
            kind,
            value,
            field_name,
            ctx.source_path,
            {"field": field_name, "family": family},
            command_index=command_index,
        )
    for field_name in ("FileName", "Path"):
        value = str(fields.get(field_name) or "").strip()
        if not value:
            continue
        kind = (
            "worklist"
            if family == "Worklist" or value.lower().endswith(".gwl")
            else "dependency"
        )
        _insert_entity(
            ctx.conn,
            ctx.zeia_id,
            ctx.script_id,
            kind,
            value,
            field_name,
            ctx.source_path,
            {"field": field_name, "family": family},
            command_index=command_index,
        )


def _index_command_sequences(
    conn: sqlite3.Connection,
    zeia_id: int,
    script_id: int,
    script: dict[str, Any],
) -> None:
    commands = script.get("commands", [])
    source_path = script.get("source") or ""
    for window_size in (2, 3, 4, 5):
        if len(commands) < window_size:
            continue
        for start in range(0, len(commands) - window_size + 1):
            window = commands[start : start + window_size]
            command_names = " > ".join(command.get("type") or "" for command in window)
            command_families = " > ".join(
                command.get("family") or "" for command in window
            )
            conn.execute(
                """
                INSERT INTO command_sequences(
                    zeia_file_id, script_id, start_index, length,
                    command_names, command_families, source_path, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    zeia_id,
                    script_id,
                    int(window[0].get("index") or start + 1),
                    window_size,
                    command_names,
                    command_families,
                    source_path,
                    _dump(
                        {
                            "lines": [command.get("line") or "" for command in window],
                            "raw_types": [
                                command.get("raw_type") or "" for command in window
                            ],
                        }
                    ),
                ),
            )


def _index_object(conn: sqlite3.Connection, zeia_id: int, obj: dict[str, Any]) -> None:
    source_path = obj.get("source") or ""
    conn.execute(
        """
        INSERT INTO catalog_objects(
            zeia_file_id, entry_path, kind, object_name, type_id,
            functional_group, footprint, renderer, names_json, guids_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            zeia_id,
            source_path,
            obj.get("kind") or "",
            obj.get("object_name") or "",
            obj.get("type_id") or "",
            obj.get("functional_group") or "",
            obj.get("footprint") or "",
            obj.get("renderer") or "",
            _dump(obj.get("names", [])),
            _dump(obj.get("guids", [])),
        ),
    )
    object_name = obj.get("object_name") or source_path
    object_kind = _entity_kind_for_object(obj)
    metadata = {
        "object_kind": obj.get("kind") or "",
        "type_id": obj.get("type_id") or "",
        "functional_group": obj.get("functional_group") or "",
        "footprint": obj.get("footprint") or "",
        "renderer": obj.get("renderer") or "",
        "guids": obj.get("guids", []),
    }
    _insert_entity(
        conn,
        zeia_id,
        None,
        object_kind,
        object_name,
        obj.get("kind") or "",
        source_path,
        metadata,
    )
    if object_kind != "catalog_object":
        _insert_entity(
            conn,
            zeia_id,
            None,
            "catalog_object",
            object_name,
            obj.get("kind") or "",
            source_path,
            metadata,
        )
    for name in obj.get("names", []):
        _insert_entity(
            conn, zeia_id, None, object_kind, name, "object_name", source_path, metadata
        )


def _index_worklist(
    conn: sqlite3.Connection, zeia_id: int, gwl: dict[str, Any]
) -> None:
    source_path = gwl.get("source") or ""
    conn.execute(
        """
        INSERT INTO worklists(
            zeia_file_id, entry_path, line_count, transfer_pairs_estimate,
            record_counts_json, pipette_examples_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            zeia_id,
            source_path,
            int(gwl.get("line_count") or 0),
            int(gwl.get("transfer_pairs_estimate") or 0),
            _dump(gwl.get("record_counts", {})),
            _dump(gwl.get("pipette_examples", [])),
        ),
    )
    _insert_entity(
        conn,
        zeia_id,
        None,
        "worklist",
        Path(source_path).name or source_path,
        source_path,
        source_path,
        {
            "line_count": gwl.get("line_count") or 0,
            "transfer_pairs_estimate": gwl.get("transfer_pairs_estimate") or 0,
            "record_counts": gwl.get("record_counts", {}),
        },
    )
    for example in gwl.get("pipette_examples", []):
        if example.get("rack_label"):
            _insert_entity(
                conn,
                zeia_id,
                None,
                "labware",
                example["rack_label"],
                "gwl_rack_label",
                source_path,
                {
                    "line": example.get("line") or "",
                    "operation": example.get("operation") or "",
                },
            )
        if example.get("rack_type"):
            _insert_entity(
                conn,
                zeia_id,
                None,
                "carrier",
                example["rack_type"],
                "gwl_rack_type",
                source_path,
                {
                    "line": example.get("line") or "",
                    "operation": example.get("operation") or "",
                },
            )
        if example.get("liquid_class"):
            _insert_entity(
                conn,
                zeia_id,
                None,
                "liquid_class",
                example["liquid_class"],
                "gwl_liquid_class",
                source_path,
                {
                    "line": example.get("line") or "",
                    "operation": example.get("operation") or "",
                },
            )


def _insert_entity(
    conn: sqlite3.Connection,
    zeia_id: int,
    script_id: int | None,
    kind: str,
    name: str,
    value: str,
    source_path: str,
    metadata: dict[str, Any],
    *,
    command_index: int | None = None,
) -> None:
    name = str(name or "").strip()
    value = str(value or "").strip()
    if not name and not value:
        return
    conn.execute(
        """
        INSERT INTO entities(
            zeia_file_id, script_id, kind, name, value, source_path,
            command_index, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            zeia_id,
            script_id,
            kind,
            name,
            value,
            source_path,
            command_index,
            _dump(metadata),
        ),
    )


def _entity_kind_for_object(obj: dict[str, Any]) -> str:
    object_kind = str(obj.get("kind") or "").lower()
    haystack = " ".join(
        str(value or "")
        for value in (
            obj.get("object_name"),
            obj.get("type_id"),
            obj.get("functional_group"),
            obj.get("footprint"),
            obj.get("renderer"),
            obj.get("source"),
            " ".join(obj.get("names", [])),
        )
    ).lower()
    if object_kind == "workspace" or "worktable" in haystack:
        return "worktable"
    if object_kind == "liquid_class":
        return "liquid_class"
    if "carrier" in haystack or object_kind == "site":
        return "carrier"
    if object_kind == "component":
        return "labware"
    if object_kind == "asset":
        return "custom_asset"
    if object_kind == "connector" and obj.get("pin_refs"):
        return "hardware_pin"
    return "catalog_object"


def _search_rows(
    conn: sqlite3.Connection,
    *,
    query: str,
    kind: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    pattern = f"%{query.lower()}%"
    candidates: list[dict[str, Any]] = []
    if kind not in {"command", "command_sequence"}:
        candidates.extend(_search_entities(conn, pattern, kind=kind, limit=limit))
    if kind in (None, "script"):
        candidates.extend(_search_scripts(conn, pattern, limit=limit))
    if kind in (None, "command"):
        candidates.extend(_search_commands(conn, pattern, limit=limit))
    if kind in (None, "command_sequence"):
        candidates.extend(_search_sequences(conn, pattern, limit=limit))
    return _dedupe_results(candidates)[:limit]


def _search_entities(
    conn: sqlite3.Connection,
    pattern: str,
    *,
    kind: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    params: list[Any] = [pattern, pattern, pattern, pattern, pattern]
    kind_clause = ""
    if kind:
        kind_clause = "AND e.kind = ?"
        params.append(kind)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT e.kind, e.name, e.value, e.source_path, e.command_index,
               e.metadata_json, z.path AS zeia_file, s.object_name AS script_name
        FROM entities e
        JOIN zeia_files z ON z.id = e.zeia_file_id
        LEFT JOIN scripts s ON s.id = e.script_id
        WHERE (
            lower(e.kind) LIKE ?
            OR lower(e.name) LIKE ?
            OR lower(e.value) LIKE ?
            OR lower(e.source_path) LIKE ?
            OR lower(e.metadata_json) LIKE ?
        )
        {kind_clause}
        ORDER BY e.kind, e.name, z.file_name, e.source_path
        LIMIT ?
        """,
        params,
    )
    return [
        {
            "kind": row["kind"],
            "match_type": "entity",
            "name": row["name"],
            "value": row["value"],
            "zeia_file": row["zeia_file"],
            "script": row["script_name"] or "",
            "source_path": row["source_path"],
            "command_index": row["command_index"],
            "metadata": _loads(row["metadata_json"]),
        }
        for row in rows
    ]


def _search_scripts(
    conn: sqlite3.Connection, pattern: str, *, limit: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT s.object_name, s.entry_path, s.script_version, s.checksum,
               s.command_count, s.dependencies_json, z.path AS zeia_file
        FROM scripts s
        JOIN zeia_files z ON z.id = s.zeia_file_id
        WHERE (
            lower(s.object_name) LIKE ?
            OR lower(s.entry_path) LIKE ?
            OR lower(s.dependencies_json) LIKE ?
            OR lower(s.family_counts_json) LIKE ?
            OR lower(s.command_counts_json) LIKE ?
        )
        ORDER BY s.object_name, z.file_name
        LIMIT ?
        """,
        (pattern, pattern, pattern, pattern, pattern, limit),
    )
    return [
        {
            "kind": "script",
            "match_type": "script",
            "name": row["object_name"],
            "value": f"{row['command_count']} commands",
            "zeia_file": row["zeia_file"],
            "script": row["object_name"],
            "source_path": row["entry_path"],
            "command_index": None,
            "metadata": {
                "script_version": row["script_version"],
                "checksum": row["checksum"],
                "dependencies": _loads(row["dependencies_json"]),
            },
        }
        for row in rows
    ]


def _search_commands(
    conn: sqlite3.Connection, pattern: str, *, limit: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.command_index, c.command_type, c.raw_type, c.family, c.line,
               c.name, c.fields_json, s.object_name AS script_name,
               s.entry_path, z.path AS zeia_file
        FROM commands c
        JOIN scripts s ON s.id = c.script_id
        JOIN zeia_files z ON z.id = c.zeia_file_id
        WHERE (
            lower(c.command_type) LIKE ?
            OR lower(c.raw_type) LIKE ?
            OR lower(c.family) LIKE ?
            OR lower(c.name) LIKE ?
            OR lower(c.fields_json) LIKE ?
        )
        ORDER BY z.file_name, s.object_name, c.command_index
        LIMIT ?
        """,
        (pattern, pattern, pattern, pattern, pattern, limit),
    )
    return [
        {
            "kind": "command",
            "match_type": "command",
            "name": row["command_type"],
            "value": row["name"] or row["family"],
            "zeia_file": row["zeia_file"],
            "script": row["script_name"],
            "source_path": row["entry_path"],
            "command_index": row["command_index"],
            "metadata": {
                "raw_type": row["raw_type"],
                "family": row["family"],
                "line": row["line"],
                "fields": _loads(row["fields_json"]),
            },
        }
        for row in rows
    ]


def _search_sequences(
    conn: sqlite3.Connection, pattern: str, *, limit: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT q.start_index, q.length, q.command_names, q.command_families,
               q.source_path, q.metadata_json, s.object_name AS script_name,
               z.path AS zeia_file
        FROM command_sequences q
        JOIN scripts s ON s.id = q.script_id
        JOIN zeia_files z ON z.id = q.zeia_file_id
        WHERE lower(q.command_names) LIKE ? OR lower(q.command_families) LIKE ?
        ORDER BY z.file_name, s.object_name, q.start_index, q.length
        LIMIT ?
        """,
        (pattern, pattern, limit),
    )
    return [
        {
            "kind": "command_sequence",
            "match_type": "command_sequence",
            "name": row["command_names"],
            "value": row["command_families"],
            "zeia_file": row["zeia_file"],
            "script": row["script_name"],
            "source_path": row["source_path"],
            "command_index": row["start_index"],
            "metadata": {"length": row["length"], **_loads(row["metadata_json"])},
        }
        for row in rows
    ]


def _dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for result in results:
        key = (
            result.get("kind"),
            result.get("match_type"),
            result.get("name"),
            result.get("zeia_file"),
            result.get("script"),
            result.get("source_path"),
            result.get("command_index"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(result)
    return out


def _metadata_value(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def _count(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"] or 0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, sort_keys=True)


def _loads(value: str) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
