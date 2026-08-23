"""Reusable script-pattern mining for indexed Tecan projects."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import re
import sqlite3

from .command_registry import registry_manual_step, registry_pattern_type
from .common import to_jsonable
from .project_index import DEFAULT_INDEX_PATH


PATTERN_SCHEMA_VERSION = "1"

PATTERN_TYPES: dict[str, dict[str, str]] = {
    "pick_up_tips": {
        "label": "Pick up tips",
        "description": "Tip pickup, tip loading, or equivalent tip acquisition commands.",
    },
    "drop_tips": {
        "label": "Drop tips",
        "description": "Tip drop, eject, or disposal commands.",
    },
    "aspirate": {
        "label": "Aspirate",
        "description": "Liquid aspirate commands with labware, volume, and liquid-class context.",
    },
    "dispense": {
        "label": "Dispense",
        "description": "Liquid dispense commands with labware, volume, and liquid-class context.",
    },
    "mix": {
        "label": "Mix",
        "description": "Mixing commands and adjacent setup/cleanup context.",
    },
    "wash": {
        "label": "Wash",
        "description": "Wash, flush, clean, or decontamination commands.",
    },
    "prompt_user": {
        "label": "Prompt user",
        "description": "User prompts, variable queries, confirmations, or operator checkpoints.",
    },
    "loop_over_wells": {
        "label": "Loop over wells",
        "description": "Looping or repeated-well control-flow commands.",
    },
    "read_worklist": {
        "label": "Read worklist",
        "description": "Worklist load/read/execute commands and referenced GWL files.",
    },
    "move_plate": {
        "label": "Move plate",
        "description": "Plate, rack, or labware movement commands, often through gripper devices.",
    },
    "gripper": {
        "label": "Gripper",
        "description": "RGA/CGA finger pickup, drop, or gripper-specific hardware command windows.",
    },
    "load_labware": {
        "label": "Load labware",
        "description": "Labware load/add commands and worktable placement setup.",
    },
    "initialize_device": {
        "label": "Initialize device",
        "description": "Device initialize, home, reset, prime, or prepare commands.",
    },
}


def mine_script_patterns(
    db_path: str | Path = DEFAULT_INDEX_PATH,
    *,
    replace: bool = True,
    context_before: int = 1,
    context_after: int = 1,
) -> dict[str, Any]:
    """Mine reusable script patterns from commands in a project index."""
    database = Path(db_path)
    conn = _connect(database)
    try:
        _initialize_pattern_tables(conn)
        if replace:
            conn.execute("DELETE FROM script_patterns")

        mined = 0
        seen: set[tuple[int, str, int, int, str]] = set()
        for script in _load_indexed_scripts(conn):
            commands = script["commands"]
            for anchor_position, command in enumerate(commands):
                for pattern_type in classify_command_pattern(command):
                    start_pos = max(0, anchor_position - max(context_before, 0))
                    end_pos = min(
                        len(commands) - 1, anchor_position + max(context_after, 0)
                    )
                    window = commands[start_pos : end_pos + 1]
                    signature = " > ".join(step["command_type"] for step in window)
                    key = (
                        int(script["script_id"]),
                        pattern_type,
                        int(window[0]["command_index"]),
                        int(window[-1]["command_index"]),
                        signature,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    _insert_pattern(conn, script, pattern_type, window, signature)
                    mined += 1
        conn.commit()
        summary = summarize_script_patterns(conn)
        return {
            **summary,
            "kind": "script_pattern_mining",
            "database": str(database),
            "mined_pattern_count": mined,
            "replaced_existing": replace,
        }
    finally:
        conn.close()


def summarize_script_patterns(
    db_path_or_conn: str | Path | sqlite3.Connection,
) -> dict[str, Any]:
    """Summarize the reusable script-pattern library."""
    conn, should_close, database = _connection_arg(db_path_or_conn)
    try:
        _initialize_pattern_tables(conn)
        type_counts = {
            row["pattern_type"]: row["count"]
            for row in conn.execute(
                """
                SELECT pattern_type, COUNT(*) AS count
                FROM script_patterns
                GROUP BY pattern_type
                ORDER BY count DESC, pattern_type
                """
            )
        }
        top_sources = [
            dict(row)
            for row in conn.execute(
                """
                SELECT s.object_name AS source_script, z.path AS zeia_file,
                       COUNT(*) AS pattern_count
                FROM script_patterns p
                JOIN scripts s ON s.id = p.script_id
                JOIN zeia_files z ON z.id = p.zeia_file_id
                GROUP BY s.object_name, z.path
                ORDER BY pattern_count DESC, source_script
                LIMIT 25
                """
            )
        ]
        return {
            "kind": "script_pattern_summary",
            "database": database,
            "pattern_schema_version": _metadata_value(conn, "pattern_schema_version"),
            "pattern_count": _count(conn, "script_patterns"),
            "step_count": _count(conn, "script_pattern_steps"),
            "pattern_type_counts": type_counts,
            "pattern_types": [
                {"pattern_type": key, **value} for key, value in PATTERN_TYPES.items()
            ],
            "top_sources": top_sources,
        }
    finally:
        if should_close:
            conn.close()


def search_script_patterns(
    db_path: str | Path = DEFAULT_INDEX_PATH,
    query: str = "",
    *,
    pattern_type: str | None = None,
    source_script: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search mined script patterns by type, source script, command, or specification."""
    database = Path(db_path)
    conn = _connect(database)
    try:
        _initialize_pattern_tables(conn)
        normalized_type = normalize_pattern_type(pattern_type) if pattern_type else None
        rows = _search_pattern_rows(
            conn,
            query=query,
            pattern_type=normalized_type,
            source_script=source_script,
            limit=limit,
        )
        return {
            "kind": "script_pattern_search",
            "database": str(database),
            "query": query,
            "pattern_type_filter": normalized_type or "",
            "source_script_filter": source_script or "",
            "result_count": len(rows),
            "results": rows,
        }
    finally:
        conn.close()


def list_script_pattern_types() -> dict[str, Any]:
    """Return supported pattern types and their intended use."""
    return {
        "kind": "script_pattern_types",
        "pattern_types": [
            {"pattern_type": key, **value} for key, value in PATTERN_TYPES.items()
        ],
    }


def normalize_pattern_type(pattern_type: str) -> str:
    return pattern_type.strip().lower().replace("-", "_").replace(" ", "_")


def classify_command_pattern(command: dict[str, Any]) -> list[str]:
    """Classify one indexed command into reusable pattern types."""
    registry_match = (
        registry_pattern_type(command.get("command_type"))
        or registry_pattern_type(command.get("raw_type"))
        or registry_pattern_type(command.get("name"))
    )
    if registry_match and registry_match in PATTERN_TYPES:
        return [registry_match]

    haystack = _command_haystack(command)
    matches: list[str] = []
    if (
        "pickup" in haystack or "pick_up" in haystack or "pick up" in haystack
    ) and "tip" in haystack:
        matches.append("pick_up_tips")
    if (
        "drop" in haystack or "eject" in haystack or "discard" in haystack
    ) and "tip" in haystack:
        matches.append("drop_tips")
    if "aspirate" in haystack:
        matches.append("aspirate")
    if "dispense" in haystack:
        matches.append("dispense")
    if "mix" in haystack:
        matches.append("mix")
    if any(token in haystack for token in ("wash", "flush", "clean", "decontam")):
        matches.append("wash")
    if any(
        token in haystack
        for token in ("prompt", "queryvariable", "confirmation", "confirm")
    ):
        matches.append("prompt_user")
    if "loop" in haystack and (
        "well" in haystack or "control flow" in haystack or "numberofloops" in haystack
    ):
        matches.append("loop_over_wells")
    if "worklist" in haystack:
        matches.append("read_worklist")
    if (
        "move" in haystack
        and any(token in haystack for token in ("plate", "labware", "rack"))
    ) or "gripper" in haystack:
        matches.append("move_plate")
    if (
        "addlabware" in haystack
        or "loadlabware" in haystack
        or ("load" in haystack and "labware" in haystack)
    ):
        matches.append("load_labware")
    if any(
        token in haystack
        for token in ("initialize", "initialise", "home", "reset", "prime")
    ):
        matches.append("initialize_device")
    return matches


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


def _initialize_pattern_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS script_patterns (
            id INTEGER PRIMARY KEY,
            zeia_file_id INTEGER NOT NULL REFERENCES zeia_files(id) ON DELETE CASCADE,
            script_id INTEGER NOT NULL REFERENCES scripts(id) ON DELETE CASCADE,
            pattern_type TEXT NOT NULL,
            name TEXT NOT NULL,
            source_script TEXT NOT NULL DEFAULT '',
            source_path TEXT NOT NULL DEFAULT '',
            start_command_index INTEGER NOT NULL,
            end_command_index INTEGER NOT NULL,
            step_count INTEGER NOT NULL,
            command_signature TEXT NOT NULL,
            confidence REAL NOT NULL DEFAULT 1.0,
            specifications_json TEXT NOT NULL DEFAULT '{}',
            safety_notes_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS script_pattern_steps (
            id INTEGER PRIMARY KEY,
            pattern_id INTEGER NOT NULL REFERENCES script_patterns(id) ON DELETE CASCADE,
            step_number INTEGER NOT NULL,
            command_index INTEGER NOT NULL,
            command_name TEXT NOT NULL DEFAULT '',
            command_family TEXT NOT NULL DEFAULT '',
            line TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            fields_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_script_patterns_type ON script_patterns(pattern_type);
        CREATE INDEX IF NOT EXISTS idx_script_patterns_source ON script_patterns(source_script);
        CREATE INDEX IF NOT EXISTS idx_script_patterns_signature ON script_patterns(command_signature);
        CREATE INDEX IF NOT EXISTS idx_script_pattern_steps_name ON script_pattern_steps(command_name);
        """
    )
    conn.execute(
        "INSERT OR REPLACE INTO metadata(key, value) VALUES('pattern_schema_version', ?)",
        (PATTERN_SCHEMA_VERSION,),
    )


def _load_indexed_scripts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.id AS command_id, c.zeia_file_id, c.script_id, c.command_index,
               c.command_type, c.raw_type, c.family, c.line, c.name,
               c.fields_json, s.object_name AS source_script,
               s.entry_path AS source_path, z.path AS zeia_file
        FROM commands c
        JOIN scripts s ON s.id = c.script_id
        JOIN zeia_files z ON z.id = c.zeia_file_id
        ORDER BY z.path, s.object_name, c.script_id, c.command_index
        """
    )
    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        script_id = int(row["script_id"])
        script = grouped.setdefault(
            script_id,
            {
                "script_id": script_id,
                "zeia_file_id": int(row["zeia_file_id"]),
                "source_script": row["source_script"] or "",
                "source_path": row["source_path"] or "",
                "zeia_file": row["zeia_file"] or "",
                "commands": [],
            },
        )
        script["commands"].append(
            {
                "command_id": int(row["command_id"]),
                "command_index": int(row["command_index"]),
                "command_type": row["command_type"] or "",
                "raw_type": row["raw_type"] or "",
                "family": row["family"] or "",
                "line": row["line"] or "",
                "name": row["name"] or "",
                "fields": _loads(row["fields_json"]),
            }
        )
    return list(grouped.values())


def _insert_pattern(
    conn: sqlite3.Connection,
    script: dict[str, Any],
    pattern_type: str,
    steps: list[dict[str, Any]],
    signature: str,
) -> None:
    specifications = _collect_specifications(steps)
    cursor = conn.execute(
        """
        INSERT INTO script_patterns(
            zeia_file_id, script_id, pattern_type, name, source_script,
            source_path, start_command_index, end_command_index, step_count,
            command_signature, confidence, specifications_json,
            safety_notes_json, metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            script["zeia_file_id"],
            script["script_id"],
            pattern_type,
            _pattern_name(pattern_type, script, steps, specifications),
            script["source_script"],
            script["source_path"],
            steps[0]["command_index"],
            steps[-1]["command_index"],
            len(steps),
            signature,
            _confidence(pattern_type, steps),
            _dump(specifications),
            _dump(_safety_notes(pattern_type, steps, specifications)),
            _dump({"zeia_file": script["zeia_file"]}),
        ),
    )
    pattern_id = int(cursor.lastrowid)
    step_params = [
        (
            pattern_id,
            step_number,
            command["command_index"],
            command["command_type"],
            command["family"],
            command["line"],
            summarize_pattern_step(command),
            _dump(command["fields"]),
        )
        for step_number, command in enumerate(steps, start=1)
    ]
    conn.executemany(
        """
        INSERT INTO script_pattern_steps(
            pattern_id, step_number, command_index, command_name,
            command_family, line, summary, fields_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        step_params,
    )


def summarize_pattern_step(command: dict[str, Any]) -> str:
    """Create a readable, specific step summary for one command."""
    command_type = command.get("command_type") or "Command"
    fields = command.get("fields", {})
    registry_summary = registry_manual_step(
        command_type, fields
    ) or registry_manual_step(command.get("raw_type"), fields)
    if registry_summary:
        return registry_summary
    lower_type = command_type.lower()
    labware = (
        fields.get("LabwareName")
        or fields.get("LabwareLable")
        or fields.get("LabwareLabel")
        or fields.get("RackLabel")
        or fields.get("RackType")
        or ""
    )
    liquid_class = (
        fields.get("LiquidClassName") or fields.get("LiquidClassNameBySelection") or ""
    )
    volume = fields.get("Volume") or ""
    worklist = (
        fields.get("WorklistName") or fields.get("FileName") or fields.get("Path") or ""
    )
    prompt = (
        fields.get("QueryPrompt") or fields.get("Comment") or fields.get("Name") or ""
    )
    device = fields.get("DeviceAlias") or ""

    if "comment" in lower_type and prompt:
        return f"Comment: {prompt}"
    if "prompt" in lower_type or "queryvariable" in lower_type:
        return _join_summary(
            "Prompt user",
            prompt,
            f"variable {fields.get('Name')}" if fields.get("Name") else "",
        )
    if "pickup" in lower_type and "tip" in lower_type:
        return _join_summary("Pick up tips", f"from {labware}" if labware else "")
    if ("drop" in lower_type or "eject" in lower_type) and "tip" in lower_type:
        return _join_summary("Drop tips", f"at {labware}" if labware else "")
    if "aspirate" in lower_type:
        return _liquid_step("Aspirate", labware, volume, liquid_class)
    if "dispense" in lower_type:
        return _liquid_step("Dispense", labware, volume, liquid_class)
    if "mix" in lower_type:
        return _liquid_step("Mix", labware, volume, liquid_class)
    if "worklist" in lower_type:
        return _join_summary("Read or execute worklist", worklist)
    if "move" in lower_type:
        return _join_summary(
            "Move plate or labware", labware, f"with {device}" if device else ""
        )
    if "loadlabware" in lower_type or "addlabware" in lower_type:
        return _join_summary("Load labware", labware)
    if any(
        token in lower_type
        for token in ("initialize", "initialise", "home", "reset", "prime")
    ):
        return _join_summary("Initialize device", device)
    return _join_summary(command_type, labware, liquid_class, worklist)


def _pattern_name(
    pattern_type: str,
    script: dict[str, Any],
    steps: list[dict[str, Any]],
    specifications: dict[str, list[str]],
) -> str:
    label = PATTERN_TYPES[pattern_type]["label"]
    script_name = script["source_script"] or script["source_path"]
    focus = (
        _first(specifications, "labware")
        or _first(specifications, "liquid_classes")
        or _first(specifications, "worklists")
        or _first(specifications, "device_aliases")
    )
    suffix = f" ({focus})" if focus else ""
    return f"{label} from {script_name}{suffix}"


def _collect_specifications(steps: list[dict[str, Any]]) -> dict[str, list[str]]:
    specs: dict[str, list[str]] = {
        "labware": [],
        "rack_labels": [],
        "rack_types": [],
        "liquid_classes": [],
        "volumes": [],
        "device_aliases": [],
        "worklists": [],
        "variables": [],
        "conditions": [],
    }
    for step in steps:
        fields = step.get("fields", {})
        _append_unique(specs["labware"], fields.get("LabwareName"))
        _append_unique(specs["labware"], fields.get("LabwareLable"))
        _append_unique(specs["labware"], fields.get("LabwareLabel"))
        _append_unique(specs["labware"], fields.get("RackLabel"))
        _append_unique(specs["rack_labels"], fields.get("RackLabel"))
        _append_unique(specs["rack_types"], fields.get("RackType"))
        _append_unique(specs["liquid_classes"], fields.get("LiquidClassName"))
        _append_unique(
            specs["liquid_classes"], fields.get("LiquidClassNameBySelection")
        )
        _append_unique(specs["volumes"], fields.get("Volume"))
        _append_unique(specs["device_aliases"], fields.get("DeviceAlias"))
        _append_unique(specs["worklists"], fields.get("WorklistName"))
        value = fields.get("FileName") or fields.get("Path")
        if value and str(value).lower().endswith(".gwl"):
            _append_unique(specs["worklists"], value)
        _append_unique(specs["variables"], fields.get("Name"))
        _append_unique(specs["variables"], fields.get("LoopVariable"))
        _append_unique(specs["conditions"], fields.get("Condition"))
    return {key: values for key, values in specs.items() if values}


def _confidence(pattern_type: str, steps: list[dict[str, Any]]) -> float:
    anchor_hits = sum(
        1 for step in steps if pattern_type in classify_command_pattern(step)
    )
    return 1.0 if anchor_hits else 0.75


def _safety_notes(
    pattern_type: str,
    steps: list[dict[str, Any]],
    specifications: dict[str, list[str]],
) -> list[str]:
    notes = [
        "Reuse as FluentControl structure only; verify deck positions, labware definitions, and liquid classes before import."
    ]
    if pattern_type in {"aspirate", "dispense", "mix"} and not specifications.get(
        "liquid_classes"
    ):
        notes.append("No liquid class was detected in this pattern window.")
    if pattern_type in {"pick_up_tips", "drop_tips"} and not specifications.get(
        "labware"
    ):
        notes.append(
            "No tip labware or rack label was detected in this pattern window."
        )
    if pattern_type in {"move_plate", "gripper"}:
        notes.append(
            "Physical RGA/CGA motion pattern; reuse only after verifying worktable positions and finger compatibility."
        )
    if any(step["family"] == "Other" for step in steps):
        notes.append(
            "Pattern includes commands outside the current command family classifier."
        )
    return notes


def _search_pattern_rows(
    conn: sqlite3.Connection,
    *,
    query: str,
    pattern_type: str | None,
    source_script: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    pattern = f"%{query.lower()}%"
    params: list[Any] = [pattern, pattern, pattern, pattern, pattern, pattern, pattern]
    clauses = [
        """
        (
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
        )
        """
    ]
    params.extend([pattern, pattern])
    if pattern_type:
        clauses.append("p.pattern_type = ?")
        params.append(pattern_type)
    if source_script:
        clauses.append("lower(p.source_script) LIKE ?")
        params.append(f"%{source_script.lower()}%")
    params.append(limit)

    rows = conn.execute(
        f"""
        SELECT p.*, z.path AS zeia_file
        FROM script_patterns p
        JOIN zeia_files z ON z.id = p.zeia_file_id
        WHERE {" AND ".join(clauses)}
        ORDER BY p.pattern_type, p.source_script, p.start_command_index
        LIMIT ?
        """,
        params,
    )
    return [_pattern_result(conn, row) for row in rows]


def _pattern_result(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    steps = [
        {
            "step_number": step["step_number"],
            "command_index": step["command_index"],
            "command_name": step["command_name"],
            "command_family": step["command_family"],
            "line": step["line"],
            "summary": step["summary"],
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
        "id": row["id"],
        "pattern_type": row["pattern_type"],
        "label": PATTERN_TYPES.get(row["pattern_type"], {}).get(
            "label", row["pattern_type"]
        ),
        "name": row["name"],
        "source_script": row["source_script"],
        "source_path": row["source_path"],
        "zeia_file": row["zeia_file"],
        "start_command_index": row["start_command_index"],
        "end_command_index": row["end_command_index"],
        "step_count": row["step_count"],
        "command_signature": row["command_signature"],
        "confidence": row["confidence"],
        "specifications": _loads(row["specifications_json"]),
        "safety_notes": _loads(row["safety_notes_json"]),
        "metadata": _loads(row["metadata_json"]),
        "steps": steps,
    }


def _command_haystack(command: dict[str, Any]) -> str:
    return " ".join(
        str(value)
        for value in (
            command.get("command_type", ""),
            command.get("raw_type", ""),
            command.get("family", ""),
            command.get("name", ""),
            json.dumps(command.get("fields", {}), sort_keys=True),
        )
    ).lower()


def _join_summary(*parts: str) -> str:
    values = [str(part).strip() for part in parts if str(part or "").strip()]
    return " ".join(values) if values else "Command"


def _liquid_step(action: str, labware: str, volume: str, liquid_class: str) -> str:
    pieces = [action]
    if volume:
        pieces.append(str(volume))
    if labware:
        pieces.append(f"at {labware}")
    if liquid_class:
        pieces.append(f"using {liquid_class}")
    return " ".join(pieces)


def _first(specifications: dict[str, list[str]], key: str) -> str:
    values = specifications.get(key, [])
    return values[0] if values else ""


def _append_unique(values: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in values:
        values.append(text)


def _metadata_value(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def _count(conn: sqlite3.Connection, table: str) -> int:
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", table):
        raise ValueError(f"Invalid table name: {table}")
    escaped_table = '"' + table.replace('"', '""') + '"'
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {escaped_table}").fetchone()
    return int(row["count"] or 0)


def _dump(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False, sort_keys=True)


def _loads(value: str) -> Any:
    if not value:
        return {}
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value
