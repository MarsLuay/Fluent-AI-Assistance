"""Statement-level diff guard for minimal FluentControl script edits."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import re
from . import xml_compat as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MINIMAL_EDIT_REPORT_VERSION = "tecan.minimal_edit_diff.v1"
_IGNORED_METADATA_TAGS = {
    "Checksum",
    "Created",
    "CreatedAt",
    "LastModified",
    "LineNumber",
    "GroupLineNumber",
    "Modified",
    "ModifiedAt",
    "Timestamp",
}
_SCRIPT_GROUP_TYPE_SUFFIX = ".ScriptGroupDataV1"


@dataclass(frozen=True)
class CommandRecord:
    index: int
    command_id: str
    object_type: str
    line_number: str
    group: str
    fingerprint: str
    normalized_xml: str
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "command_id": self.command_id,
            "object_type": self.object_type,
            "line_number": self.line_number,
            "group": self.group,
            "fingerprint": self.fingerprint,
            "summary": self.summary,
        }


def compare_xscr_minimal_edit(
    original: Path,
    edited: Path,
    *,
    allowed_command_indexes: set[int] | None = None,
    allowed_edited_indexes: set[int] | None = None,
    allowed_line_numbers: set[int] | None = None,
    allowed_command_ids: set[str] | None = None,
) -> dict[str, Any]:
    """Compare two XSCR scripts and flag unapproved statement-level drift."""

    original = Path(original)
    edited = Path(edited)
    allowed_command_indexes = set(allowed_command_indexes or set())
    allowed_edited_indexes = set(allowed_edited_indexes or set())
    allowed_line_numbers = set(allowed_line_numbers or set())
    allowed_command_ids = {str(value).casefold() for value in (allowed_command_ids or set()) if str(value).strip()}
    original_records = extract_xscr_command_records(original)
    edited_records = extract_xscr_command_records(edited)
    changes = _diff_command_records(
        original_records,
        edited_records,
        allowed_command_indexes=allowed_command_indexes,
        allowed_edited_indexes=allowed_edited_indexes,
        allowed_line_numbers=allowed_line_numbers,
        allowed_command_ids=allowed_command_ids,
    )
    unapproved = [item for item in changes if not item.get("approved")]
    status = "passed" if not unapproved else "failed"
    return {
        "schema_version": MINIMAL_EDIT_REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": status,
        "ready_for_minimal_edit_packaging": status == "passed",
        "original": {
            "path": str(original),
            "command_count": len(original_records),
        },
        "edited": {
            "path": str(edited),
            "command_count": len(edited_records),
        },
        "allowlist": {
            "original_command_indexes": sorted(allowed_command_indexes),
            "edited_command_indexes": sorted(allowed_edited_indexes),
            "line_numbers": sorted(allowed_line_numbers),
            "command_ids": sorted(allowed_command_ids),
        },
        "summary": {
            "change_count": len(changes),
            "approved_change_count": len(changes) - len(unapproved),
            "unapproved_change_count": len(unapproved),
            "changed_count": sum(1 for item in changes if item.get("kind") == "changed"),
            "added_count": sum(1 for item in changes if item.get("kind") == "added"),
            "removed_count": sum(1 for item in changes if item.get("kind") == "removed"),
        },
        "changes": changes,
    }


def extract_xscr_command_records(path: Path) -> list[CommandRecord]:
    if not Path(path).exists():
        raise FileNotFoundError(path)
    tree = ET.parse(path)
    root = tree.getroot()
    parent_map = {id(child): parent for parent in root.iter() for child in list(parent)}
    records: list[CommandRecord] = []
    for element in root.iter():
        if _local_name(element.tag) != "Object":
            continue
        object_type = str(element.attrib.get("Type") or "").strip()
        if not object_type or object_type.endswith(_SCRIPT_GROUP_TYPE_SUFFIX):
            continue
        if not _looks_like_statement_object(element, parent_map):
            continue
        index = len(records) + 1
        normalized = _canonical_xml(element)
        command_id = object_type.rsplit(".", 1)[-1]
        line_number = _first_descendant_text(element, "LineNumber")
        group = _nearest_group_name(element, parent_map)
        records.append(
            CommandRecord(
                index=index,
                command_id=command_id,
                object_type=object_type,
                line_number=line_number,
                group=group,
                fingerprint=hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                normalized_xml=normalized,
                summary=_statement_summary(element, command_id, group, line_number),
            )
        )
    return records


def render_minimal_edit_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Minimal Edit Diff",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Ready for minimal-edit packaging: `{bool(report.get('ready_for_minimal_edit_packaging'))}`",
        f"- Original: `{(report.get('original') or {}).get('path')}`",
        f"- Edited: `{(report.get('edited') or {}).get('path')}`",
        f"- Original command count: `{(report.get('original') or {}).get('command_count')}`",
        f"- Edited command count: `{(report.get('edited') or {}).get('command_count')}`",
        f"- Changes: `{summary.get('change_count', 0)}`",
        f"- Unapproved changes: `{summary.get('unapproved_change_count', 0)}`",
        "",
    ]
    allow = report.get("allowlist") or {}
    if any(allow.get(key) for key in ("original_command_indexes", "edited_command_indexes", "line_numbers", "command_ids")):
        lines.extend(["## Allowlist", ""])
        for label, key in (
            ("Original command indexes", "original_command_indexes"),
            ("Edited command indexes", "edited_command_indexes"),
            ("Line numbers", "line_numbers"),
            ("Command IDs", "command_ids"),
        ):
            values = allow.get(key) or []
            if values:
                lines.append(f"- {label}: `{', '.join(str(value) for value in values)}`")
        lines.append("")
    changes = report.get("changes") or []
    if not changes:
        lines.extend(["No command-level changes detected.", ""])
        return "\n".join(lines)
    lines.extend(["## Changes", ""])
    for item in changes:
        status = "approved" if item.get("approved") else "unapproved"
        lines.append(
            f"- `{item.get('kind')}` `{status}`: "
            f"original #{item.get('original_index') or '-'} -> edited #{item.get('edited_index') or '-'}"
        )
        if item.get("command_id"):
            lines.append(f"  - Command: `{item.get('command_id')}`")
        if item.get("line_number"):
            lines.append(f"  - Line: `{item.get('line_number')}`")
        if item.get("original_summary"):
            lines.append(f"  - Original: {item.get('original_summary')}")
        if item.get("edited_summary"):
            lines.append(f"  - Edited: {item.get('edited_summary')}")
        if item.get("approval_reason"):
            lines.append(f"  - Approval reason: {item.get('approval_reason')}")
        if not item.get("approved"):
            lines.append("  - Required action: approve this command explicitly or remove the unintended edit.")
    lines.append("")
    return "\n".join(lines)


def _diff_command_records(
    original: list[CommandRecord],
    edited: list[CommandRecord],
    *,
    allowed_command_indexes: set[int],
    allowed_edited_indexes: set[int],
    allowed_line_numbers: set[int],
    allowed_command_ids: set[str],
) -> list[dict[str, Any]]:
    matcher = difflib.SequenceMatcher(
        a=[item.fingerprint for item in original],
        b=[item.fingerprint for item in edited],
        autojunk=False,
    )
    changes: list[dict[str, Any]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                before = original[i1 + offset]
                after = edited[j1 + offset]
                changes.append(
                    _change_record(
                        "changed",
                        before,
                        after,
                        allowed_command_indexes=allowed_command_indexes,
                        allowed_edited_indexes=allowed_edited_indexes,
                        allowed_line_numbers=allowed_line_numbers,
                        allowed_command_ids=allowed_command_ids,
                    )
                )
            for before in original[i1 + paired : i2]:
                changes.append(
                    _change_record(
                        "removed",
                        before,
                        None,
                        allowed_command_indexes=allowed_command_indexes,
                        allowed_edited_indexes=allowed_edited_indexes,
                        allowed_line_numbers=allowed_line_numbers,
                        allowed_command_ids=allowed_command_ids,
                    )
                )
            for after in edited[j1 + paired : j2]:
                changes.append(
                    _change_record(
                        "added",
                        None,
                        after,
                        allowed_command_indexes=allowed_command_indexes,
                        allowed_edited_indexes=allowed_edited_indexes,
                        allowed_line_numbers=allowed_line_numbers,
                        allowed_command_ids=allowed_command_ids,
                    )
                )
            continue
        if tag == "delete":
            for before in original[i1:i2]:
                changes.append(
                    _change_record(
                        "removed",
                        before,
                        None,
                        allowed_command_indexes=allowed_command_indexes,
                        allowed_edited_indexes=allowed_edited_indexes,
                        allowed_line_numbers=allowed_line_numbers,
                        allowed_command_ids=allowed_command_ids,
                    )
                )
            continue
        if tag == "insert":
            for after in edited[j1:j2]:
                changes.append(
                    _change_record(
                        "added",
                        None,
                        after,
                        allowed_command_indexes=allowed_command_indexes,
                        allowed_edited_indexes=allowed_edited_indexes,
                        allowed_line_numbers=allowed_line_numbers,
                        allowed_command_ids=allowed_command_ids,
                    )
                )
    return changes


def _change_record(
    kind: str,
    before: CommandRecord | None,
    after: CommandRecord | None,
    *,
    allowed_command_indexes: set[int],
    allowed_edited_indexes: set[int],
    allowed_line_numbers: set[int],
    allowed_command_ids: set[str],
) -> dict[str, Any]:
    approved, reason = _is_approved_change(
        before,
        after,
        allowed_command_indexes=allowed_command_indexes,
        allowed_edited_indexes=allowed_edited_indexes,
        allowed_line_numbers=allowed_line_numbers,
        allowed_command_ids=allowed_command_ids,
    )
    command = before or after
    return {
        "kind": kind,
        "approved": approved,
        "approval_reason": reason,
        "original_index": before.index if before else None,
        "edited_index": after.index if after else None,
        "command_id": command.command_id if command else "",
        "object_type": command.object_type if command else "",
        "line_number": command.line_number if command else "",
        "group": command.group if command else "",
        "original_summary": before.summary if before else "",
        "edited_summary": after.summary if after else "",
    }


def _is_approved_change(
    before: CommandRecord | None,
    after: CommandRecord | None,
    *,
    allowed_command_indexes: set[int],
    allowed_edited_indexes: set[int],
    allowed_line_numbers: set[int],
    allowed_command_ids: set[str],
) -> tuple[bool, str]:
    if before and before.index in allowed_command_indexes:
        return True, f"original command index {before.index} was allowed"
    if after and after.index in allowed_edited_indexes:
        return True, f"edited command index {after.index} was allowed"
    for record in (before, after):
        if record is None:
            continue
        if record.line_number and record.line_number.isdigit() and int(record.line_number) in allowed_line_numbers:
            return True, f"line number {record.line_number} was allowed"
        if record.command_id.casefold() in allowed_command_ids:
            return True, f"command id {record.command_id} was allowed"
    return False, ""


def _looks_like_statement_object(element: ET.Element, parent_map: dict[int, ET.Element]) -> bool:
    parent = parent_map.get(id(element))
    parent_name = _local_name(parent.tag) if parent is not None else ""
    if parent_name in {"Statements", "Objects"}:
        return True
    object_type = str(element.attrib.get("Type") or "")
    return any(
        marker in object_type
        for marker in (
            ".Commands.",
            ".Scripting.Data.",
            ".ApplicationDriver.",
            ".LegacyDriver",
            ".UserPromptStatement",
            ".SubRoutineStatement",
            ".CommentStatement",
            ".AddLabwareDataV1",
            ".RUP",
        )
    )


def _canonical_xml(element: ET.Element) -> str:
    clone = copy.deepcopy(element)
    _strip_ignored_metadata(clone)
    return _canonical_element(clone)


def _strip_ignored_metadata(element: ET.Element) -> None:
    for child in list(element):
        if _local_name(child.tag) in _IGNORED_METADATA_TAGS:
            element.remove(child)
            continue
        _strip_ignored_metadata(child)


def _canonical_element(element: ET.Element) -> str:
    name = _local_name(element.tag)
    attrs = "".join(f' {key}="{_normalize_text(value)}"' for key, value in sorted(element.attrib.items()))
    text = _normalize_text(element.text or "")
    children = "".join(_canonical_element(child) for child in list(element))
    return f"<{name}{attrs}>{text}{children}</{name}>"


def _statement_summary(element: ET.Element, command_id: str, group: str, line_number: str) -> str:
    fields = []
    for tag in (
        "Prompt",
        "Comment",
        "SubRoutine",
        "LabwareLable",
        "LabwareLabel",
        "LabwareName",
        "LabwareType",
        "Location",
        "Position",
        "LiquidClassName",
        "DeviceAlias",
    ):
        value = _first_descendant_text(element, tag)
        if value:
            fields.append(f"{tag}={value}")
        if len(fields) >= 4:
            break
    prefix = command_id
    if group:
        prefix = f"{group}: {prefix}"
    if line_number:
        prefix = f"{prefix} line {line_number}"
    return f"{prefix} ({'; '.join(fields)})" if fields else prefix


def _nearest_group_name(element: ET.Element, parent_map: dict[int, ET.Element]) -> str:
    current = parent_map.get(id(element))
    while current is not None:
        if _local_name(current.tag) == "Object" and str(current.attrib.get("Type") or "").endswith(_SCRIPT_GROUP_TYPE_SUFFIX):
            name = _first_descendant_text(current, "Name")
            if name:
                return name
        current = parent_map.get(id(current))
    return ""


def _first_descendant_text(element: ET.Element, tag: str) -> str:
    for child in element.iter():
        if _local_name(child.tag) == tag:
            return _normalize_text(child.text or "")
    return ""


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def write_minimal_edit_reports(report: dict[str, Any], json_path: Path | None, markdown_path: Path | None) -> None:
    if json_path is not None:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    if markdown_path is not None:
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.write_text(render_minimal_edit_markdown(report), encoding="utf-8")
