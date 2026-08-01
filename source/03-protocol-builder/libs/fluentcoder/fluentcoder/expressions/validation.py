"""Expression inventory and validation for shipped XSCR files."""

from __future__ import annotations

import zipfile
import re
import hashlib
from pathlib import Path
from typing import Any, Iterable, Mapping

from .. import xml_compat as ET
from .ast import expression_kind
from .fields import (
    canonical_expression_command_id,
    expression_fields_for_command,
)
from .parser import ExpressionParseError, parse_expression
from .semantics import (
    SemanticIssue,
    check_expression_semantics,
    normalize_fluent_type_name,
    semantic_context_from_variables,
)

# Add only documented FluentControl/host-provided variables here. Keeping this
# explicit prevents an empty script declaration table from disabling validation.
FLUENTCONTROL_SYSTEM_VARIABLES: dict[str, str] = {}

# MoveAxisCommand serializes ChargeCondition as an expression-shaped field, but
# FluentControl also accepts these unquoted vendor enum literals.  Model them as
# field-local Boolean constants so normal expression validation still catches
# malformed syntax and genuinely undeclared variables.
_CHARGE_CONDITION_ENUM_LITERALS: dict[str, str] = {
    "Maximum": "Boolean",
    "Standard": "Boolean",
}


def expression_inventory_from_xscr_text(
    text: str,
    *,
    script: str = "",
    entry: str = "",
    permitted_variables: Mapping[str, str] | None = None,
    source_preserved_allowlist: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    try:
        root = _parse_xscr_text(text)
    except Exception as exc:
        failure = {
            "script": script,
            "entry": entry,
            "valid": False,
            "reason": "xml_parse_error",
            "message": str(exc),
        }
        return {"valid": False, "record_count": 0, "records": [], "failures": [failure]}

    script_name = script or _first_text(root, "ObjectName") or Path(entry).stem
    variables = _variable_declarations(root)
    semantic_variables = {
        **FLUENTCONTROL_SYSTEM_VARIABLES,
        **dict(permitted_variables or {}),
        **variables,
    }
    semantic_context = semantic_context_from_variables(
        semantic_variables,
        enforce_declared_variables=True,
    )
    charge_condition_semantic_context = semantic_context_from_variables(
        {
            **semantic_variables,
            **_CHARGE_CONDITION_ENUM_LITERALS,
        },
        enforce_declared_variables=True,
    )
    source_preserved_records = tuple(source_preserved_allowlist or ())
    for command_index, obj in enumerate(_elements_by_local_name(root, "Object"), start=1):
        raw_command = _compiled_command_id(obj)
        command = canonical_expression_command_id(raw_command)
        field_paths = expression_fields_for_command(command)
        if not field_paths:
            continue
        line = _first_text(obj, "LineNumber")
        variable = (
            _first_text(obj, "VariableName") or _first_text(obj, "Name")
            if command == "SetVariableStatement"
            else None
        )
        for field_path in field_paths:
            for raw_expression in _expression_field_values(obj, field_path):
                expected_type: Any = _expected_expression_type(
                    command,
                    field_path,
                    variable=variable,
                    variables=semantic_variables,
                )
                issues: list[SemanticIssue] = []
                if (
                    command == "SetVariableStatement"
                    and variable
                    and variable not in semantic_variables
                ):
                    issues.append(
                        SemanticIssue(
                            code="undefined_assignment_target",
                            message=f"SetVariable target {variable!r} is not declared.",
                        )
                    )
                record = {
                    "script": script_name,
                    "entry": entry,
                    "line": _int_or_text(line),
                    "command_index": command_index,
                    "command": command,
                    "source_command": raw_command,
                    "field": field_path,
                    "variable": variable,
                    "raw_expression": raw_expression,
                }
                _validate_expression_record(
                    records=records,
                    failures=failures,
                    record=record,
                    semantic_context=(
                        charge_condition_semantic_context
                        if field_path == "ChargeCondition"
                        else semantic_context
                    ),
                    expected_type=expected_type,
                    assignment_target=variable if command == "SetVariableStatement" else None,
                    seed_issues=issues,
                    source_preserved_allowlist=source_preserved_records,
                )
    return {
        "valid": not failures,
        "record_count": len(records),
        "failure_count": len(failures),
        "declaration_count": len(variables),
        "records": records,
        "failures": failures,
    }


def _validate_expression_record(
    *,
    records: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    record: dict[str, Any],
    semantic_context: Any,
    expected_type: Any = None,
    assignment_target: str | None = None,
    seed_issues: list[SemanticIssue] | None = None,
    source_preserved_allowlist: Iterable[Mapping[str, Any]] = (),
) -> None:
    raw_expression = str(record.get("raw_expression") or "")
    try:
        parsed = parse_expression(raw_expression)
    except ExpressionParseError as exc:
        allowance = _matching_source_preserved_allowance(
            record,
            raw_expression,
            source_preserved_allowlist,
        )
        if allowance is not None:
            seed = tuple(seed_issues or ())
            seed_errors = [issue for issue in seed if issue.severity == "error"]
            allowed = {
                **record,
                "parsed_kind": "source_preserved_expression",
                "semantic_type": None,
                "semantic_issue_count": len(seed),
                "valid": not seed_errors,
                "source_preserved": True,
                "source_hash": _expression_source_hash(raw_expression),
                "source_entry": allowance.get("source_entry") or allowance.get("accepted_source_entry"),
                "provenance_policy": allowance.get("provenance_policy") or "source_preservation_allowed",
            }
            if seed:
                allowed["semantic_issues"] = [issue.to_dict() for issue in seed]
            records.append(allowed)
            if seed_errors:
                failures.append(
                    {
                        **allowed,
                        "reason": seed_errors[0].code,
                    }
                )
            return
        failed = {
            **record,
            "valid": False,
            "reason": exc.reason,
            "offset": exc.offset,
        }
        records.append(failed)
        failures.append(failed)
        return

    semantic_result = check_expression_semantics(
        parsed,
        semantic_context,
        expected_type=expected_type,
        assignment_target=assignment_target,
    )
    semantic_issues = tuple(seed_issues or ()) + semantic_result.issues
    semantic_errors = [issue for issue in semantic_issues if issue.severity == "error"]
    valid_record = {
        **record,
        "parsed_kind": expression_kind(parsed),
        "semantic_type": semantic_result.type_name,
        "semantic_issue_count": len(semantic_issues),
        "valid": not semantic_errors,
    }
    if semantic_issues:
        valid_record["semantic_issues"] = [issue.to_dict() for issue in semantic_issues]
    records.append(valid_record)
    if semantic_errors:
        failure = {
            **valid_record,
            "reason": semantic_errors[0].code,
        }
        failures.append(failure)


def _matching_source_preserved_allowance(
    record: Mapping[str, Any],
    raw_expression: str,
    allowlist: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any] | None:
    actual_hash = _expression_source_hash(raw_expression)
    for allowance in allowlist:
        if allowance.get("_consumed"):
            continue
        if not _source_preservation_policy_permits(allowance):
            continue
        if str(allowance.get("source") or allowance.get("raw_expression") or "") != raw_expression:
            continue
        if str(allowance.get("source_hash") or "") != actual_hash:
            continue
        if not _same_identity(allowance, record, "command"):
            continue
        if not _same_identity(allowance, record, "field"):
            continue
        if "entry" in allowance:
            allowed_entry = allowance.get("entry")
            if allowed_entry and str(allowed_entry) != str(record.get("entry") or ""):
                continue
        else:
            allowed_entry = (
                allowance.get("archive_entry")
                or allowance.get("script_entry")
                or allowance.get("source_entry")
            )
            if not allowed_entry or str(allowed_entry) != str(record.get("entry") or ""):
                continue
        allowed_script = allowance.get("script")
        if allowed_script and str(allowed_script) != str(record.get("script") or ""):
            continue
        if not _optional_identity_matches(allowance, record, "line"):
            continue
        if not _optional_identity_matches(allowance, record, "command_index"):
            continue
        source_entry = allowance.get("source_entry") or allowance.get("accepted_source_entry")
        if not source_entry:
            continue
        if isinstance(allowance, dict):
            allowance["_consumed"] = True
        return allowance
    return None


def _source_preservation_policy_permits(allowance: Mapping[str, Any]) -> bool:
    if allowance.get("source_preservation_allowed") is True:
        return True
    if allowance.get("allow_source_preserved") is True:
        return True
    return str(allowance.get("provenance_policy") or "") in {
        "source_preservation_allowed",
        "accepted_source_xscr",
    }


def _same_identity(allowance: Mapping[str, Any], record: Mapping[str, Any], key: str) -> bool:
    value = allowance.get(key)
    if not value:
        return False
    return str(value) == str(record.get(key) or "")


def _optional_identity_matches(
    allowance: Mapping[str, Any],
    record: Mapping[str, Any],
    key: str,
) -> bool:
    value = allowance.get(key)
    if value in (None, ""):
        return True
    return str(value) == str(record.get(key) or "")


def _expression_source_hash(source: str) -> str:
    return "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()


def _expected_expression_type(
    command: str,
    field_path: str,
    *,
    variable: str | None,
    variables: dict[str, str],
) -> Any:
    if command == "SetVariableStatement":
        return normalize_fluent_type_name(variables.get(variable)) if variable in variables else None
    if field_path in {"Condition", "ChargeCondition"}:
        return "boolean"
    if field_path == "Source":
        return None
    if field_path in {"Position", "Site"}:
        return ("number", "string")
    return "number"


def _expression_field_values(root: ET.Element, field_path: str) -> list[str]:
    parts = tuple(part for part in field_path.split("/") if part)
    if not parts:
        return []
    if len(parts) == 1:
        return [
            (element.text or "").strip()
            for element in root.iter()
            if _local_name(element.tag) == parts[0] and not list(element)
        ]

    values: list[str] = []
    for container in root.iter():
        if _local_name(container.tag) != parts[0]:
            continue
        for element in container.iter():
            if element is container:
                continue
            if _local_name(element.tag) == parts[-1]:
                values.append((element.text or "").strip())
    return values


def expression_inventory_from_zeia(
    archive_path: Path,
    *,
    permitted_variables: Mapping[str, str] | None = None,
    source_preserved_allowlist: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    inventories: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    if not archive_path.exists() or not zipfile.is_zipfile(archive_path):
        return {
            "valid": False,
            "archive": str(archive_path),
            "script_count": 0,
            "record_count": 0,
            "failure_count": 1,
            "scripts": [],
            "failures": [{"valid": False, "reason": "not_a_readable_zeia", "archive": str(archive_path)}],
        }
    with zipfile.ZipFile(archive_path, "r") as zf:
        for name in zf.namelist():
            if not name.replace("\\", "/").casefold().endswith(".xscr"):
                continue
            data = zf.read(name)
            text = _decode_xml_bytes(data)
            inventory = expression_inventory_from_xscr_text(
                text,
                entry=name,
                permitted_variables=permitted_variables,
                source_preserved_allowlist=source_preserved_allowlist,
            )
            inventories.append(inventory)
            failures.extend(inventory.get("failures") or [])
    return {
        "valid": not failures,
        "archive": str(archive_path),
        "script_count": len(inventories),
        "record_count": sum(int(item.get("record_count") or 0) for item in inventories),
        "failure_count": len(failures),
        "scripts": inventories,
        "failures": failures,
    }


def expression_inventory_ok(inventory: dict[str, Any]) -> bool:
    return bool(inventory.get("valid")) and not inventory.get("failures")


def _variable_declarations(root: ET.Element) -> dict[str, str]:
    variables: dict[str, str] = {}
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "anyType":
            type_hint = " ".join(str(value) for value in element.attrib.values())
            if "VariableDefinitionHelper" not in type_hint:
                continue
        elif local != "VariableDefinitionHelper":
            continue
        name = _first_text(element, "Name")
        if not name:
            continue
        variables[name] = _first_text(element, "TypeName") or "unknown"
    return variables


def _compiled_command_id(element: ET.Element) -> str:
    for child in list(element):
        return _local_name(child.tag)
    return str(element.attrib.get("Type") or "").rsplit(".", 1)[-1]


def _elements_by_local_name(root: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in root.iter() if _local_name(element.tag) == name]


def _first_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return (element.text or "").strip()
    return ""


def _local_name(tag: Any) -> str:
    text = str(tag)
    if "}" in text:
        return text.rsplit("}", 1)[-1]
    return text


def _int_or_text(value: str) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _decode_xml_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _parse_xscr_text(text: str) -> ET.Element:
    try:
        return ET.fromstring(text)
    except Exception:
        # Some legacy/unit-test archive payloads use sd: element prefixes without
        # carrying the namespace declaration. The expression audit only needs
        # local element names, so strip unbound element prefixes and try once more.
        normalized = re.sub(r"<(/?)([A-Za-z_][\w.-]*):", r"<\1", text)
        if normalized == text:
            raise
        return ET.fromstring(normalized)
