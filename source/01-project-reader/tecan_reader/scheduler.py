"""Source-preserving Scheduler process/task entity extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tecan_common.xml_helpers import local_name

from .common import parse_xml_text, read_text

_SCHEDULER_NAMES = {"scheduler", "process", "task", "scheduledprocess", "scheduledtask"}
_KNOWN_FIELDS = {
    "id", "guid", "name", "description", "process", "processid", "task", "taskid",
    "script", "scriptname", "enabled", "status", "schedule", "priority", "order",
    "input", "output", "timeout", "workingdirectory", "arguments",
}


def is_scheduler_artifact(entry: str, text: str) -> bool:
    path_hint = entry.casefold()
    if any(token in path_hint for token in ("scheduler", "scheduled", "processes", "tasks")):
        return True
    prefix = text[:256 * 1024].casefold()
    return any(f"<{name}" in prefix or f"/>{name}" in prefix for name in _SCHEDULER_NAMES)


def inspect_scheduler_text(text: str, *, source_name: str) -> dict[str, Any]:
    root = parse_xml_text(text)
    processes: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    for index, element in enumerate(root.iter()):
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
    unknown: dict[str, list[str]] = {}
    for record in [*processes, *tasks]:
        for name, values in (record.get("source_metadata", {}).get("unknown_fields") or {}).items():
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
    for child in list(element):
        if not isinstance(child.tag, str):
            continue
        name = local_name(child.tag)
        value = " ".join((child.text or "").split())
        if value and not list(child):
            _store_field(fields, unknown, name, value)
    if not fields and not unknown:
        return {}
    folded = {name.casefold(): value for name, value in fields.items()}
    normalized = {
        "process_id": folded.get("processid") or folded.get("id"),
        "process_name": folded.get("processname") or folded.get("name"),
        "task_id": folded.get("taskid") or folded.get("id"),
        "task_name": folded.get("taskname") or folded.get("name"),
        "script_name": folded.get("scriptname") or folded.get("script"),
        "schedule": folded.get("schedule"),
        "due_time": folded.get("duetime") or folded.get("duedatetime"),
        "iteration_count": folded.get("iterationcount") or folded.get("count"),
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
