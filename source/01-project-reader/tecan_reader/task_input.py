"""Bounded parser for Fluent Scheduler Task Input (``.twl``) artifacts.

TWL files are kept separate from GWL worklists.  FluentControl releases have
used both XML and delimited task-input exports, so this reader normalizes the
small record contract while retaining additive fields and source order.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any

from tecan_common.xml_helpers import local_name

from .common import parse_xml_text, read_text

TASK_INPUT_SCHEMA_VERSION = "tecan.task_input.v1"
_KNOWN_FIELDS = {
    "id", "name", "task", "taskid", "taskname", "process", "processid",
    "processname", "script", "scriptname", "value", "type", "datatype",
    "source", "target", "status", "enabled", "order", "sequence", "parameter",
}


def inspect_twl(path: str | Path, *, source_name: str | None = None) -> dict[str, Any]:
    source = source_name or str(path)
    return inspect_twl_text(read_text(path), source_name=source)


def inspect_twl_text(text: str, *, source_name: str) -> dict[str, Any]:
    stripped = text.lstrip("\ufeff \t\r\n")
    if stripped.startswith("<"):
        return _inspect_xml(stripped, source_name=source_name)
    return _inspect_delimited(text, source_name=source_name)


def _inspect_xml(text: str, *, source_name: str) -> dict[str, Any]:
    root = parse_xml_text(text)
    records: list[dict[str, Any]] = []
    unknown_fields: dict[str, list[str]] = {}
    record_names = {"task", "taskrecord", "input", "item", "record"}
    record_nodes = [
        element for element in root.iter()
        if isinstance(element.tag, str)
        and local_name(element.tag).casefold() in record_names
        and list(element)
    ]
    if not record_nodes:
        record_nodes = [root]
    for index, element in enumerate(record_nodes):
        record = _element_record(element, index=index)
        if record:
            records.append(record)
            for name, values in (record.get("unknown_fields") or {}).items():
                bucket = unknown_fields.setdefault(name, [])
                for value in values:
                    if value not in bucket and len(bucket) < 20:
                        bucket.append(value)
    return {
        "kind": "task_input",
        "schema_version": TASK_INPUT_SCHEMA_VERSION,
        "format": "xml",
        "source": source_name,
        "entry": source_name,
        "object_name": Path(source_name).stem,
        "records": records,
        "record_count": len(records),
        "unknown_fields": {name: unknown_fields[name] for name in sorted(unknown_fields)},
        "source_metadata": {
            "format": "xml",
            "unknown_fields": {name: unknown_fields[name] for name in sorted(unknown_fields)},
        },
    }


def _element_record(element: Any, *, index: int) -> dict[str, Any]:
    values: dict[str, str] = {}
    unknown: dict[str, list[str]] = {}
    for child in list(element):
        if not isinstance(child.tag, str):
            continue
        name = local_name(child.tag)
        text = " ".join((child.text or "").split())
        if list(child):
            nested = _element_record(child, index=index)
            if nested:
                values[name] = nested
            continue
        if not text:
            continue
        key = name.casefold()
        if key in _KNOWN_FIELDS:
            values[name] = text
        else:
            unknown.setdefault(name, []).append(text)
    for name, value in element.attrib.items():
        key = local_name(name).casefold()
        if key in _KNOWN_FIELDS:
            values[local_name(name)] = str(value)
        else:
            unknown.setdefault(local_name(name), []).append(str(value))
    if not values and not unknown:
        return {}
    record = {
        "index": index,
        "source_line": index + 1,
        "fields": dict(sorted(values.items())),
        **_normalized_task_fields(values),
    }
    if unknown:
        record["unknown_fields"] = {
            name: sorted(dict.fromkeys(items)) for name, items in sorted(unknown.items())
        }
    return record


def _normalized_task_fields(values: dict[str, Any]) -> dict[str, Any]:
    folded = {str(key).casefold(): value for key, value in values.items()}

    def first(*names: str) -> Any:
        for name in names:
            if folded.get(name) not in (None, ""):
                return folded[name]
        return None

    normalized = {
        "task_id": first("taskid", "task_id", "id"),
        "task_name": first("taskname", "task_name", "task", "name"),
        "process_id": first("processid", "process_id"),
        "process_name": first("processname", "process_name", "process"),
        "schedule": first("schedule", "scheduledtime", "starttime"),
        "due_time": first("duetime", "due_time", "duedatetime", "due"),
        "iteration_count": first("iterationcount", "iteration_count", "iterations", "count"),
        "parameter": first("parameter", "parameters", "value"),
        "labware_group": first("labwaregroup", "labware_group", "labwaregroupref"),
        "comment": first("comment", "comments", "description"),
    }
    return {key: value for key, value in normalized.items() if value not in (None, "")}


def _inspect_delimited(text: str, *, source_name: str) -> dict[str, Any]:
    rows = list(csv.reader(io.StringIO(text), delimiter="\t" if "\t" in text else ","))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return {
            "kind": "task_input", "schema_version": TASK_INPUT_SCHEMA_VERSION,
            "format": "delimited", "source": source_name, "entry": source_name,
            "object_name": Path(source_name).stem, "records": [], "record_count": 0,
            "unknown_fields": {}, "source_metadata": {"format": "delimited", "unknown_fields": {}},
        }
    header = [cell.strip() for cell in rows[0]]
    has_header = any(cell.casefold() in _KNOWN_FIELDS for cell in header)
    data_rows = rows[1:] if has_header else rows
    records: list[dict[str, Any]] = []
    for index, row in enumerate(data_rows):
        fields = {
            (header[column] if has_header and column < len(header) else f"column_{column + 1}"): value.strip()
            for column, value in enumerate(row)
            if value.strip()
        }
        if fields:
            records.append({
                "index": index,
                "source_line": index + (2 if has_header else 1),
                "fields": dict(sorted(fields.items())),
                **_normalized_task_fields(fields),
            })
    return {
        "kind": "task_input", "schema_version": TASK_INPUT_SCHEMA_VERSION,
        "format": "delimited", "source": source_name, "entry": source_name,
        "object_name": Path(source_name).stem, "header": header if has_header else [],
        "records": records, "record_count": len(records), "unknown_fields": {},
        "source_metadata": {"format": "delimited", "unknown_fields": {}},
    }
