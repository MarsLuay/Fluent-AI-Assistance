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

from tecan_common import xml_compat as ET
from tecan_common.native_acceleration import (
    any_ascii_casefold_in,
    delimited_row_fields,
    delimited_text_rows,
    casefold_alias_values,
    collapse_ascii_whitespace,
    merge_unique_string_groups,
    sort_string_keys,
    sorted_unique_strings,
    xml_attribute_name_values as _native_xml_attribute_name_values,
    xml_elements_by_folded_name_with_children,
)
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
    unknown_groups: list[dict[str, list[str]]] = []
    record_names = {"task", "taskrecord", "input", "item", "record"}
    record_nodes = xml_elements_by_folded_name_with_children(
        root, record_names, ET.Element
    )
    if record_nodes is None:
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
            unknown_groups.append(record.get("unknown_fields") or {})
    unknown_fields = merge_unique_string_groups(unknown_groups, limit=20)
    if unknown_fields is None:
        unknown_fields = {}
        for group in unknown_groups:
            for name, values in group.items():
                bucket = unknown_fields.setdefault(name, [])
                for value in values:
                    if value not in bucket and len(bucket) < 20:
                        bucket.append(value)
    sorted_unknown_fields = sort_string_keys(unknown_fields)
    if sorted_unknown_fields is None:
        sorted_unknown_fields = {
            name: unknown_fields[name] for name in sorted(unknown_fields)
        }
    return {
        "kind": "task_input",
        "schema_version": TASK_INPUT_SCHEMA_VERSION,
        "format": "xml",
        "source": source_name,
        "entry": source_name,
        "object_name": Path(source_name).stem,
        "records": records,
        "record_count": len(records),
        "unknown_fields": sorted_unknown_fields,
        "source_metadata": {
            "format": "xml",
            "unknown_fields": sorted_unknown_fields.copy(),
        },
    }


def _row_fields(
    row: list[str], header: list[str], has_header: bool
) -> dict[str, str]:
    native = delimited_row_fields(row, header, has_header)
    if native is not None:
        return native
    return {
        (header[column] if has_header and column < len(header) else f"column_{column + 1}"): value.strip()
        for column, value in enumerate(row)
        if value.strip()
    }


def _sorted_fields(values: dict[str, Any]) -> dict[str, Any]:
    native = sort_string_keys(values)
    if native is not None:
        return native
    return dict(sorted(values.items()))


def _element_record(element: Any, *, index: int) -> dict[str, Any]:
    values: dict[str, str] = {}
    unknown: dict[str, list[str]] = {}
    for child in list(element):
        if not isinstance(child.tag, str):
            continue
        name = local_name(child.tag)
        native_text = collapse_ascii_whitespace(child.text)
        text = (
            native_text
            if native_text is not None
            else " ".join((child.text or "").split())
        )
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
    native_attributes = _native_xml_attribute_name_values(element, ET.Element)
    if native_attributes is None:
        for name, value in element.attrib.items():
            local = local_name(name)
            key = local.casefold()
            if key in _KNOWN_FIELDS:
                values[local] = str(value)
            else:
                unknown.setdefault(local, []).append(str(value))
    else:
        for name, value in native_attributes:
            if name.casefold() in _KNOWN_FIELDS:
                values[name] = value
            else:
                unknown.setdefault(name, []).append(value)
    if not values and not unknown:
        return {}
    record = {
        "index": index,
        "source_line": index + 1,
        "fields": _sorted_fields(values),
        **_normalized_task_fields(values),
    }
    if unknown:
        ordered_unknown = sort_string_keys(unknown)
        if ordered_unknown is None:
            ordered_unknown = dict(sorted(unknown.items()))
        record["unknown_fields"] = {}
        for name, items in ordered_unknown.items():
            native_values = sorted_unique_strings([items])
            record["unknown_fields"][name] = (
                native_values
                if native_values is not None
                else sorted(dict.fromkeys(items))
            )
    return record


def _normalized_task_fields(values: dict[str, Any]) -> dict[str, Any]:
    field_aliases = {
        "task_id": ("taskid", "task_id", "id"),
        "task_name": ("taskname", "task_name", "task", "name"),
        "process_id": ("processid", "process_id"),
        "process_name": ("processname", "process_name", "process"),
        "schedule": ("schedule", "scheduledtime", "starttime"),
        "due_time": ("duetime", "due_time", "duedatetime", "due"),
        "iteration_count": ("iterationcount", "iteration_count", "iterations", "count"),
        "parameter": ("parameter", "parameters", "value"),
        "labware_group": ("labwaregroup", "labware_group", "labwaregroupref"),
        "comment": ("comment", "comments", "description"),
    }
    native_values = casefold_alias_values(values, tuple(field_aliases.values()))
    if native_values is not None:
        normalized = dict(zip(field_aliases, native_values))
        return {
            key: value
            for key, value in normalized.items()
            if value not in (None, "")
        }

    folded = {str(key).casefold(): value for key, value in values.items()}

    def first(*names: str) -> Any:
        for name in names:
            if folded.get(name) not in (None, ""):
                return folded[name]
        return None

    normalized = {
        key: first(*names) for key, names in field_aliases.items()
    }
    return {key: value for key, value in normalized.items() if value not in (None, "")}


def _inspect_delimited(text: str, *, source_name: str) -> dict[str, Any]:
    delimiter = "\t" if "\t" in text else ","
    rows = delimited_text_rows(text, delimiter)
    if rows is None:
        rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
        rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return {
            "kind": "task_input", "schema_version": TASK_INPUT_SCHEMA_VERSION,
            "format": "delimited", "source": source_name, "entry": source_name,
            "object_name": Path(source_name).stem, "records": [], "record_count": 0,
            "unknown_fields": {}, "source_metadata": {"format": "delimited", "unknown_fields": {}},
        }
    header = [cell.strip() for cell in rows[0]]
    native_header_match = any_ascii_casefold_in(header, _KNOWN_FIELDS)
    has_header = (
        native_header_match
        if native_header_match is not None
        else any(cell.casefold() in _KNOWN_FIELDS for cell in header)
    )
    data_rows = rows[1:] if has_header else rows
    records: list[dict[str, Any]] = []
    for index, row in enumerate(data_rows):
        fields = _row_fields(row, header, has_header)
        if fields:
            records.append({
                "index": index,
                "source_line": index + (2 if has_header else 1),
                "fields": _sorted_fields(fields),
                **_normalized_task_fields(fields),
            })
    return {
        "kind": "task_input", "schema_version": TASK_INPUT_SCHEMA_VERSION,
        "format": "delimited", "source": source_name, "entry": source_name,
        "object_name": Path(source_name).stem, "header": header if has_header else [],
        "records": records, "record_count": len(records), "unknown_fields": {},
        "source_metadata": {"format": "delimited", "unknown_fields": {}},
    }
