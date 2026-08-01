"""Bridge mined tecan-reader pattern windows into protocol-builder IR."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sqlite3

from .runner import PipelineError


def load_pattern_windows(
    db_path: str | Path | None,
    *,
    pattern_ids: list[int | str] | None = None,
    pattern_queries: list[str] | None = None,
    source_script_rank: int = 1,
) -> list[dict[str, Any]]:
    """Load exact mined command windows from a tecan-reader SQLite index."""
    try:
        ids = [int(value) for value in pattern_ids or []]
    except (TypeError, ValueError) as exc:
        raise PipelineError("--pattern-id must be an integer") from exc
    queries = [str(value) for value in pattern_queries or []]
    if not ids and not queries:
        return []
    if db_path is None:
        raise PipelineError("--index-db is required when using --pattern-id or --pattern-query")

    database = Path(db_path).expanduser()
    if not database.exists():
        raise PipelineError(f"Pattern index database does not exist: {database}")
    if source_script_rank < 1:
        raise PipelineError("--source-script-rank must be 1 or greater")

    conn = sqlite3.connect(database)
    conn.row_factory = sqlite3.Row
    try:
        _require_pattern_tables(conn, database)
        windows: list[dict[str, Any]] = []
        seen: set[int] = set()
        for pattern_id in ids:
            row = _pattern_row_by_id(conn, pattern_id)
            if row is None:
                raise PipelineError(f"No mined pattern exists with id {pattern_id} in {database}")
            window = _pattern_result(conn, row)
            window["retrieval"] = {"method": "pattern_id", "pattern_id": pattern_id}
            if int(window["id"]) not in seen:
                windows.append(window)
                seen.add(int(window["id"]))

        for query in queries:
            row = _pattern_row_for_query(conn, query, source_script_rank=source_script_rank)
            if row is None:
                raise PipelineError(
                    f"No mined pattern matched query {query!r} at source script rank {source_script_rank}"
                )
            window = _pattern_result(conn, row)
            window["retrieval"] = {
                "method": "pattern_query",
                "query": query,
                "source_script_rank": source_script_rank,
            }
            if int(window["id"]) not in seen:
                windows.append(window)
                seen.add(int(window["id"]))
        return windows
    except sqlite3.Error as exc:
        raise PipelineError(f"Could not read pattern index {database}: {exc}") from exc
    finally:
        conn.close()


def summarize_pattern_windows(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return compact, IR-friendly pattern windows with exact command steps."""
    return [_compact_pattern_window(window) for window in windows]


def pattern_window_dependencies(windows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return dependency records for selected mined source windows."""
    dependencies = []
    for window in windows:
        command_range = window.get("command_range") or {}
        dependencies.append(
            {
                "kind": "source_pattern",
                "name": window.get("name") or f"pattern:{window.get('id')}",
                "required": False,
                "source_path": window.get("source_path") or "",
                "pattern_id": window.get("id"),
                "pattern_type": window.get("pattern_type") or "",
                "source_script": window.get("source_script") or "",
                "zeia_file": window.get("zeia_file") or "",
                "start_command_index": window.get("start_command_index", command_range.get("start")),
                "end_command_index": window.get("end_command_index", command_range.get("end")),
                "command_signature": window.get("command_signature") or "",
            }
        )
    return dependencies


def pattern_window_refs(windows: list[dict[str, Any]]) -> list[str]:
    """Return stable human-readable references for selected mined patterns."""
    refs = []
    for window in windows:
        pattern_id = window.get("id")
        name = window.get("name") or window.get("pattern_type") or "pattern"
        source = window.get("source_script") or window.get("source_path") or "unknown source"
        refs.append(f"pattern:{pattern_id} {name} [{source}]")
    return refs


def _require_pattern_tables(conn: sqlite3.Connection, database: Path) -> None:
    rows = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?, ?)",
            ("script_patterns", "script_pattern_steps", "zeia_files"),
        )
    }
    missing = {"script_patterns", "script_pattern_steps", "zeia_files"} - rows
    if missing:
        raise PipelineError(
            f"{database} is not a mined tecan-reader pattern library; missing {', '.join(sorted(missing))}"
        )


def _pattern_row_by_id(conn: sqlite3.Connection, pattern_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT p.*, z.path AS zeia_file
        FROM script_patterns p
        JOIN zeia_files z ON z.id = p.zeia_file_id
        WHERE p.id = ?
        """,
        (pattern_id,),
    ).fetchone()


def _pattern_row_for_query(
    conn: sqlite3.Connection,
    query: str,
    *,
    source_script_rank: int,
) -> sqlite3.Row | None:
    rows = _query_pattern_rows(conn, query)
    if not rows:
        return None

    groups: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
    order: list[tuple[str, str, str]] = []
    for row in rows:
        key = (row["source_script"] or "", row["source_path"] or "", row["zeia_file"] or "")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    ranked = sorted(
        order,
        key=lambda key: (
            -len(groups[key]),
            min(int(row["start_command_index"]) for row in groups[key]),
            key[0].lower(),
            key[1].lower(),
            key[2].lower(),
        ),
    )
    if source_script_rank > len(ranked):
        return None
    selected_source = ranked[source_script_rank - 1]
    return _best_pattern_row(groups[selected_source], query)


def _query_pattern_rows(conn: sqlite3.Connection, query: str) -> list[sqlite3.Row]:
    pattern = f"%{query.lower()}%"
    params = [pattern] * 9
    rows = conn.execute(
        """
        SELECT p.*, z.path AS zeia_file
        FROM script_patterns p
        JOIN zeia_files z ON z.id = p.zeia_file_id
        WHERE
            lower(p.pattern_type) LIKE ?
            OR lower(p.name) LIKE ?
            OR lower(p.source_script) LIKE ?
            OR lower(p.source_path) LIKE ?
            OR lower(p.command_signature) LIKE ?
            OR lower(p.specifications_json) LIKE ?
            OR EXISTS (
                SELECT 1
                FROM script_pattern_steps st
                WHERE st.pattern_id = p.id
                  AND (
                      lower(st.command_name) LIKE ?
                      OR lower(st.summary) LIKE ?
                      OR lower(st.fields_json) LIKE ?
                  )
            )
        ORDER BY p.source_script, p.source_path, p.pattern_type, p.start_command_index, p.id
        """,
        params,
    )
    return list(rows)


def _best_pattern_row(rows: list[sqlite3.Row], query: str) -> sqlite3.Row:
    normalized_query = query.strip().lower()

    def rank(row: sqlite3.Row) -> tuple[int, float, int, int]:
        pattern_type = str(row["pattern_type"] or "").lower()
        name = str(row["name"] or "").lower()
        exact_type = 0 if normalized_query and pattern_type == normalized_query else 1
        name_match = 0 if normalized_query and normalized_query in name else 1
        confidence = float(row["confidence"] or 0.0)
        return (exact_type + name_match, -confidence, int(row["start_command_index"]), int(row["id"]))

    return sorted(rows, key=rank)[0]


def _pattern_result(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    steps = [
        {
            "step_number": int(step["step_number"]),
            "command_index": int(step["command_index"]),
            "command_name": step["command_name"] or "",
            "command_family": step["command_family"] or "",
            "line": step["line"] or "",
            "summary": step["summary"] or "",
            "fields": _loads(step["fields_json"]),
        }
        for step in conn.execute(
            """
            SELECT step_number, command_index, command_name, command_family,
                   line, summary, fields_json
            FROM script_pattern_steps
            WHERE pattern_id = ?
            ORDER BY step_number
            """,
            (row["id"],),
        )
    ]
    return {
        "id": int(row["id"]),
        "pattern_type": row["pattern_type"] or "",
        "name": row["name"] or "",
        "source_script": row["source_script"] or "",
        "source_path": row["source_path"] or "",
        "zeia_file": row["zeia_file"] or "",
        "start_command_index": int(row["start_command_index"]),
        "end_command_index": int(row["end_command_index"]),
        "step_count": int(row["step_count"]),
        "command_signature": row["command_signature"] or "",
        "confidence": float(row["confidence"] or 0.0),
        "specifications": _loads(row["specifications_json"]),
        "safety_notes": _loads(row["safety_notes_json"]),
        "metadata": _loads(row["metadata_json"]),
        "steps": steps,
    }


def _compact_pattern_window(window: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": window.get("id"),
        "pattern_type": window.get("pattern_type") or "",
        "name": window.get("name") or "",
        "source_script": window.get("source_script") or "",
        "source_path": window.get("source_path") or "",
        "zeia_file": window.get("zeia_file") or "",
        "command_range": {
            "start": window.get("start_command_index"),
            "end": window.get("end_command_index"),
        },
        "step_count": window.get("step_count"),
        "command_signature": window.get("command_signature") or "",
        "confidence": window.get("confidence"),
        "specifications": window.get("specifications") or {},
        "safety_notes": window.get("safety_notes") or [],
        "retrieval": window.get("retrieval") or {},
        "steps": [
            {
                "step_number": step.get("step_number"),
                "command_index": step.get("command_index"),
                "command_name": step.get("command_name") or "",
                "command_family": step.get("command_family") or "",
                "line": step.get("line") or "",
                "summary": step.get("summary") or "",
                "fields": step.get("fields") or {},
            }
            for step in window.get("steps") or []
        ],
    }


def _loads(value: str) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
