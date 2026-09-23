"""Bridge mined tecan-reader pattern windows into protocol-builder IR."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence
import hashlib
import json
import re
import sqlite3

from .runner import PipelineError


PATTERN_SELECTION_SCHEMA_VERSION = "tecan.generation_pattern_selection.v1"
DEFAULT_PATTERN_CONFIDENCE = 0.5


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


def rank_pattern_windows(
    windows: Sequence[Mapping[str, Any]],
    task_facets: Mapping[str, Any] | None = None,
    *,
    explicit_pattern_ids: Sequence[int | str] = (),
    explicit_pattern_queries: Sequence[str] = (),
    explicit_source_scripts: Sequence[str] = (),
    target_evidence: Mapping[str, Any] | None = None,
    max_selected: int = 1,
    min_confidence: float = DEFAULT_PATTERN_CONFIDENCE,
) -> dict[str, Any]:
    """Rank compact mined windows without selecting unsupported evidence silently."""
    if max_selected < 1:
        raise ValueError("max_selected must be at least 1")
    if not 0 <= min_confidence <= 1:
        raise ValueError("min_confidence must be between 0 and 1")

    facets = _selection_facets(task_facets or {})
    target = dict(target_evidence or {})
    ids = {str(value) for value in explicit_pattern_ids}
    queries = {str(value).strip().casefold() for value in explicit_pattern_queries if str(value).strip()}
    scripts = {str(value).strip().casefold() for value in explicit_source_scripts if str(value).strip()}
    explicit_requested = bool(ids or queries or scripts)

    candidates: list[dict[str, Any]] = []
    omissions: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    matched_queries: set[str] = set()
    matched_scripts: set[str] = set()
    for raw_window in windows:
        source_window = dict(raw_window)
        window = _compact_pattern_window(source_window)
        candidate_id = f"pattern:{window.get('id')}"
        retrieval = window.get("retrieval") if isinstance(window.get("retrieval"), Mapping) else {}
        source_script = str(window.get("source_script") or "")
        pattern_id = str(window.get("id") or "")
        retrieval_query = str(retrieval.get("query") or "").strip().casefold()
        is_explicit = (
            pattern_id in ids
            or (retrieval_query and retrieval_query in queries)
            or source_script.casefold() in scripts
        )
        if pattern_id in ids:
            matched_ids.add(pattern_id)
        if retrieval_query in queries:
            matched_queries.add(retrieval_query)
        if source_script.casefold() in scripts:
            matched_scripts.add(source_script.casefold())
        score, breakdown, matches = _pattern_score(source_window, facets, target)
        confidence = _confidence(source_window)
        incompatible, incompatibility_reason = _target_incompatibility(source_window, target)
        low_confidence = confidence < min_confidence
        eligible = not incompatible and not low_confidence and bool(matches or is_explicit)
        reason = _selection_reason(
            is_explicit=is_explicit,
            incompatible=incompatible,
            low_confidence=low_confidence,
            matches=matches,
        )
        provenance = {
            "source_script": source_script,
            "source_path": str(window.get("source_path") or ""),
            "zeia_file": str(window.get("zeia_file") or ""),
            "command_range": dict(window.get("command_range") or {}),
            "pattern_id": window.get("id"),
        }
        candidate = {
            "candidate_id": candidate_id,
            "pattern": window,
            "score": 100000 if is_explicit and eligible else score,
            "score_breakdown": breakdown,
            "matched_facets": sorted(matches),
            "confidence": confidence,
            "eligible": eligible,
            "selection_mode": "explicit" if is_explicit else "automatic_ranked",
            "selection_reason": reason,
            "provenance": provenance,
            "source_fingerprint": _fingerprint({"pattern": window, "provenance": provenance}),
        }
        candidates.append(candidate)
        if not eligible:
            omissions.append(
                {
                    "candidate_id": candidate_id,
                    "category": "source_pattern",
                    "reason": incompatibility_reason
                    or ("low_confidence" if low_confidence else "no_relevant_facet_match"),
                    "provenance": provenance,
                }
            )

    for pattern_id in sorted(ids - matched_ids):
        omissions.append(
            {
                "candidate_id": f"pattern:{pattern_id}",
                "category": "source_pattern",
                "reason": "explicit_pattern_not_found",
                "provenance": {"pattern_id": pattern_id},
            }
        )
    for query in sorted(queries - matched_queries):
        omissions.append(
            {
                "candidate_id": f"pattern_query:{query}",
                "category": "source_pattern",
                "reason": "explicit_pattern_query_not_found",
                "provenance": {"query": query},
            }
        )
    for source_script in sorted(scripts - matched_scripts):
        omissions.append(
            {
                "candidate_id": f"source_script:{source_script}",
                "category": "source_pattern",
                "reason": "explicit_source_script_not_found",
                "provenance": {"source_script": source_script},
            }
        )

    candidates.sort(key=_candidate_sort_key)
    eligible = [item for item in candidates if item["eligible"]]
    review_reasons: list[str] = []
    selected: list[dict[str, Any]] = []
    if explicit_requested:
        selected = eligible[:max_selected]
        if not selected:
            review_reasons.append("explicit_selection_has_no_eligible_candidate")
    elif eligible:
        top_score = eligible[0]["score"]
        tied = [item for item in eligible if item["score"] == top_score]
        if len(tied) > 1:
            review_reasons.append("ambiguous_top_candidates")
        else:
            selected = eligible[:max_selected]
    else:
        review_reasons.append("no_eligible_candidate")

    selected_ids = {item["candidate_id"] for item in selected}
    alternatives = [item for item in candidates if item["candidate_id"] not in selected_ids]
    omissions.sort(key=lambda item: (str(item["category"]), str(item["candidate_id"])))
    result: dict[str, Any] = {
        "schema_version": PATTERN_SELECTION_SCHEMA_VERSION,
        "status": "needs_review" if review_reasons else "ready",
        "selection_mode": "explicit" if explicit_requested else "automatic_ranked",
        "selected": selected,
        "alternatives": alternatives,
        "candidates": candidates,
        "omissions": omissions,
        "review_reasons": review_reasons,
        "max_selected": max_selected,
        "min_confidence": min_confidence,
    }
    result["fingerprint"] = _fingerprint(result)
    return result


_FACET_KEYS = (
    "operation_families",
    "device_families",
    "labware_names",
    "labware_types",
    "liquid_roles",
    "target_deck_regions",
)
_PATTERN_OPERATION_ALIASES = {
    "move_labware": "move_plate",
    "worklist": "read_worklist",
    "prompt": "prompt_user",
    "loop": "loop_over_wells",
    "load_labware": "load_labware",
}


def _selection_facets(value: Mapping[str, Any]) -> dict[str, set[str]]:
    nested = value.get("task_facets") if isinstance(value.get("task_facets"), Mapping) else value
    return {
        key: {str(item).strip().casefold() for item in _facet_strings(nested.get(key)) if str(item).strip()}
        for key in _FACET_KEYS
    }


def _facet_strings(value: Any) -> list[str]:
    if value is None or isinstance(value, Mapping):
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if item is not None and not isinstance(item, Mapping)]
    return [str(value)]


def _pattern_score(
    window: Mapping[str, Any],
    facets: Mapping[str, set[str]],
    target: Mapping[str, Any],
) -> tuple[int, dict[str, int], set[str]]:
    pattern_type = str(window.get("pattern_type") or "").casefold()
    normalized_operations = {
        _PATTERN_OPERATION_ALIASES.get(value, value)
        for value in facets.get("operation_families", set())
    }
    matches: set[str] = set()
    breakdown = {
        "operation_family": 0,
        "device_family": 0,
        "labware": 0,
        "liquid_class": 0,
        "target_compatibility": 0,
        "confidence": 0,
        "source_provenance": 0,
    }
    if pattern_type in normalized_operations:
        breakdown["operation_family"] = 100
        matches.add("operation_family")

    specifications = window.get("specifications") if isinstance(window.get("specifications"), Mapping) else {}
    steps = window.get("steps") if isinstance(window.get("steps"), list) else []
    step_text = " ".join(
        " ".join(
            str(item.get(key) or "")
            for key in ("command_name", "command_family", "summary", "line")
        )
        for item in steps
        if isinstance(item, Mapping)
    ).casefold()
    spec_text = " ".join(
        str(item)
        for values in specifications.values()
        for item in (values if isinstance(values, list) else [values])
    ).casefold()
    all_text = " ".join(
        str(window.get(key) or "")
        for key in ("name", "source_script", "command_signature")
    ).casefold() + " " + step_text + " " + spec_text

    for facet_name, score_key, label in (
        ("device_families", "device_family", "device_family"),
        ("labware_names", "labware", "labware"),
        ("labware_types", "labware", "labware"),
        ("liquid_roles", "liquid_class", "liquid_class"),
        ("target_deck_regions", "target_compatibility", "target_deck_region"),
    ):
        values = facets.get(facet_name, set())
        if values and any(_token_match(value, all_text) for value in values):
            breakdown[score_key] = max(breakdown[score_key], 20 if score_key != "target_compatibility" else 15)
            matches.add(label)

    if _target_compatible(window, target):
        breakdown["target_compatibility"] = max(breakdown["target_compatibility"], 20)
        matches.add("target_compatibility")
    confidence = _confidence(window)
    breakdown["confidence"] = round(confidence * 10)
    if window.get("source_path") or window.get("zeia_file"):
        breakdown["source_provenance"] = 5
    return sum(breakdown.values()), breakdown, matches


def _target_compatible(window: Mapping[str, Any], target: Mapping[str, Any]) -> bool:
    compatible_ids = {str(value) for value in _facet_strings(target.get("compatible_pattern_ids"))}
    if compatible_ids and str(window.get("id")) in compatible_ids:
        return True
    known_good_ids = {str(value) for value in _facet_strings(target.get("known_good_pattern_ids"))}
    return bool(known_good_ids and str(window.get("id")) in known_good_ids)


def _target_incompatibility(window: Mapping[str, Any], target: Mapping[str, Any]) -> tuple[bool, str]:
    pattern_id = str(window.get("id") or "")
    incompatible_ids = {str(value) for value in _facet_strings(target.get("incompatible_pattern_ids"))}
    if pattern_id in incompatible_ids:
        return True, "incompatible_target_evidence"
    scripts = {str(value).casefold() for value in _facet_strings(target.get("incompatible_source_scripts"))}
    if str(window.get("source_script") or "").casefold() in scripts:
        return True, "incompatible_source_script"
    metadata = window.get("metadata") if isinstance(window.get("metadata"), Mapping) else {}
    target_version = str(target.get("target_fluentcontrol_version") or "").strip().casefold()
    supported_versions = {str(value).casefold() for value in _facet_strings(metadata.get("supported_target_versions"))}
    if target_version and supported_versions and target_version not in supported_versions:
        return True, "incompatible_target_version"
    return False, ""


def _confidence(window: Mapping[str, Any]) -> float:
    try:
        return max(0.0, min(1.0, float(window.get("confidence", 0.0))))
    except (TypeError, ValueError):
        return 0.0


def _token_match(value: str, haystack: str) -> bool:
    normalized = str(value).strip().casefold()
    if not normalized:
        return False
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", haystack)) or normalized in haystack


def _selection_reason(
    *,
    is_explicit: bool,
    incompatible: bool,
    low_confidence: bool,
    matches: set[str],
) -> str:
    if is_explicit:
        return "explicit source/pattern selection"
    if incompatible:
        return "candidate conflicts with target compatibility evidence"
    if low_confidence:
        return "candidate confidence is below the selection threshold"
    if matches:
        return "ranked by " + ", ".join(sorted(matches))
    return "no relevant request facet matched"


def _candidate_sort_key(item: Mapping[str, Any]) -> tuple[Any, ...]:
    pattern = item.get("pattern") if isinstance(item.get("pattern"), Mapping) else {}
    return (
        -int(item.get("score") or 0),
        -float(item.get("confidence") or 0.0),
        str(pattern.get("pattern_type") or "").casefold(),
        str(pattern.get("source_script") or "").casefold(),
        str(pattern.get("id") or ""),
    )


def _fingerprint(value: Any) -> str:
    serialized = json.dumps(_canonical(value), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, set):
        return sorted((_canonical(item) for item in value), key=lambda item: json.dumps(item, sort_keys=True, default=str))
    return value


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
