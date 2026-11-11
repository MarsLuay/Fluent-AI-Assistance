"""Source-preserving Scheduler process/task entity extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tecan_common import xml_compat as ET
from tecan_common.native_acceleration import (
    ascii_case_insensitive_substring_match_groups as _native_substring_match_groups,
    casefold_alias_values as _native_casefold_alias_values,
    merge_unique_string_groups as _native_merge_unique_string_groups,
    xml_direct_leaf_name_texts as _native_xml_direct_leaf_name_texts,
    xml_indexed_elements_by_folded_name as _native_indexed_elements_by_folded_name,
)
from tecan_common.xml_helpers import local_name

from .common import parse_xml_text, read_text

_SCHEDULER_NAMES = {"scheduler", "process", "task", "scheduledprocess", "scheduledtask"}
_KNOWN_FIELDS = {
    "id", "guid", "name", "description", "process", "processid", "task", "taskid",
    "script", "scriptname", "enabled", "status", "schedule", "priority", "order",
    "input", "output", "timeout", "workingdirectory", "arguments",
}


def is_scheduler_artifact(entry: str, text: str) -> bool:
    native_path_match = _native_substring_match_groups(
        [entry], [("scheduler", "scheduled", "processes", "tasks")]
    )
    if native_path_match is not None:
        if native_path_match[0]:
            return True
    else:
        path_hint = entry.casefold()
        if any(
            token in path_hint
            for token in ("scheduler", "scheduled", "processes", "tasks")
        ):
            return True

    prefix = text[:256 * 1024]
    markers = tuple(
        (f"<{name}", f"/>{name}") for name in _SCHEDULER_NAMES
    )
    native_text_match = _native_substring_match_groups([prefix], markers)
    if native_text_match is not None:
        return any(native_text_match)
    folded_prefix = prefix.casefold()
    return any(
        f"<{name}" in folded_prefix or f"/>{name}" in folded_prefix
        for name in _SCHEDULER_NAMES
    )


def inspect_scheduler_text(text: str, *, source_name: str) -> dict[str, Any]:
    root = parse_xml_text(text)
    processes: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    native_indexed = _native_indexed_elements_by_folded_name(
        root, _SCHEDULER_NAMES, ET.Element
    )
    candidates = native_indexed if native_indexed is not None else enumerate(root.iter())
    for index, element in candidates:
        if not isinstance(element.tag, str):
            continue
        kind_name = local_name(element.tag).casefold()
        if kind_name not in _SCHEDULER_NAMES:
            continue
        if kind_name in {"process", "scheduledprocess", "scheduler"}:
            kind = "scheduler_process"
            target = processes
        elif kind_name in {"task", "scheduledtask"}:
            kind = "scheduler_task"
            target = tasks
        else:
            continue
        record = _record(element, index=index, kind=kind, source_name=source_name)
        if record:
            target.append(record)
    records = [*processes, *tasks]
    unknown_groups = [
        record.get("source_metadata", {}).get("unknown_fields") or {}
        for record in records
    ]
    unknown = _native_merge_unique_string_groups(unknown_groups, 20)
    if unknown is None:
        unknown = {}
        for record in records:
            for name, values in (
                record.get("source_metadata", {}).get("unknown_fields") or {}
            ).items():
                bucket = unknown.setdefault(name, [])
                for value in values:
                    if value not in bucket and len(bucket) < 20:
                        bucket.append(value)
    return {
        "kind": "scheduler",
        "source": source_name,
        "entry": source_name,
        "object_name": Path(source_name).stem,
        "scheduler_processes": processes,
        "scheduler_tasks": tasks,
        "source_metadata": {"unknown_fields": {name: sorted(values) for name, values in sorted(unknown.items())}},
    }


def inspect_scheduler(path: str | Path, *, source_name: str | None = None) -> dict[str, Any]:
    source = source_name or str(path)
    return inspect_scheduler_text(read_text(path), source_name=source)


def _record(element: Any, *, index: int, kind: str, source_name: str) -> dict[str, Any]:
    fields: dict[str, str] = {}
    unknown: dict[str, list[str]] = {}
    for name, value in element.attrib.items():
        _store_field(fields, unknown, local_name(name), str(value))
    native_child_fields = _native_xml_direct_leaf_name_texts(element, ET.Element)
    if native_child_fields is None:
        for child in list(element):
            if not isinstance(child.tag, str):
                continue
            name = local_name(child.tag)
            value = " ".join((child.text or "").split())
            if value and not list(child):
                _store_field(fields, unknown, name, value)
    else:
        for name, value in native_child_fields:
            _store_field(fields, unknown, name, value)
    if not fields and not unknown:
        return {}
    field_aliases = {
        "process_id": ("processid", "id"),
        "process_name": ("processname", "name"),
        "task_id": ("taskid", "id"),
        "task_name": ("taskname", "name"),
        "script_name": ("scriptname", "script"),
        "schedule": ("schedule",),
        "due_time": ("duetime", "duedatetime"),
        "iteration_count": ("iterationcount", "count"),
    }
    native_values = _native_casefold_alias_values(
        fields, tuple(field_aliases.values())
    )
    if native_values is not None:
        normalized = dict(zip(field_aliases, native_values))
    else:
        folded = {name.casefold(): value for name, value in fields.items()}

        def first(*names: str) -> Any:
            for name in names:
                if folded.get(name):
                    return folded[name]
            return None

        normalized = {
            key: first(*aliases) for key, aliases in field_aliases.items()
        }
    return {
        "kind": kind,
        "source": source_name,
        "entry": source_name,
        "object_name": fields.get("Name") or fields.get("TaskName") or fields.get("ProcessName") or "",
        "record_index": index,
        "source_line": index + 1,
        "fields": dict(sorted(fields.items())),
        **{key: value for key, value in normalized.items() if value not in (None, "")},
        "source_metadata": {"unknown_fields": {name: sorted(values) for name, values in sorted(unknown.items())}},
    }


def _store_field(fields: dict[str, str], unknown: dict[str, list[str]], name: str, value: str) -> None:
    if name.casefold() in _KNOWN_FIELDS:
        fields[name] = value
    else:
        bucket = unknown.setdefault(name, [])
        if value not in bucket and len(bucket) < 20:
            bucket.append(value)
