"""Offline variable reconciliation preflight before XML/XSCR generation.

Runs before ``render_python_draft`` so conflicting or unreconciled variable
declarations never reach generated FluentControl XML. Complements later
``query_variable_audit`` / ``runtime_variable_audit`` reporting in
``validation_diff.md`` (those may still run for operator review).
"""

from __future__ import annotations

from . import xml_compat as ET
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
from typing import Any, Mapping

from fluentcoder.expressions import walk_expression_values

from .protocol_ir import (
    _PROSE_PARAMETER_KEYS,
    _add_explicit_variable_name,
    _bracket_variable_names,
    _expression_variable_names,
    _expression_variable_names_from_mapping,
    _referenced_variable_names,
    _valid_variable_name,
)
from .query_variable_audit import expected_query_names_from_ir
from .subroutine_variable_mappings import (
    _VARIABLE_DEFINITION_COMPARE_KEYS,
    _called_subroutine_variable_definitions,
    _compare_value,
    _copy_subroutine_definition,
    _variable_definition_conflicts,
    clean_subroutine_reference,
    reconcile_ir_subroutine_variable_definitions,
    valid_mapping_targets_for_subroutine,
)


VARIABLE_RECONCILIATION_VERSION = "tecan.variable_reconciliation.v1"
INFERRED_REFERENCE_VARIABLE_SOURCE = "inferred_referenced_variable"
_PROVENANCE_ID_RE = re.compile(r"^exprprov:[0-9a-fA-F]{64}$")


@dataclass(frozen=True)
class VariableReconciliationFailure:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": dict(self.details)}


def _ir_variable_definition(record: Mapping[str, Any]) -> dict[str, Any]:
    definition: dict[str, Any] = {"name": str(record.get("name") or "").strip()}
    for key in _VARIABLE_DEFINITION_COMPARE_KEYS:
        value = _ir_field(record, key)
        if value in (None, "", []):
            continue
        if key in {"query_at_startup", "read_only"}:
            definition[key] = _compare_value(bool(value) if isinstance(value, bool) else str(value).strip().casefold() in {"1", "true", "yes", "on"})
        else:
            definition[key] = _compare_value(value)
    return definition


def _index_ir_variable_declarations(ir: Mapping[str, Any]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Index every IR variable declaration by name, including startup_variables."""
    indexed: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for index, record in enumerate(ir.get("variables") or []):
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "").strip()
        if not name:
            continue
        indexed.setdefault(name, []).append((f"variables[{index}]", dict(record)))
    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    for script_index, script in enumerate(source.get("selected_source_scripts") or []):
        if not isinstance(script, dict):
            continue
        for var_index, record in enumerate(script.get("startup_variables") or []):
            if not isinstance(record, dict):
                continue
            name = str(record.get("name") or "").strip()
            if not name:
                continue
            indexed.setdefault(name, []).append(
                (
                    f"source.selected_source_scripts[{script_index}]"
                    f".startup_variables[{var_index}]",
                    dict(record),
                )
            )
    return indexed


def _remove_startup_variable_declarations(ir: dict[str, Any], name: str) -> None:
    source = ir.get("source")
    if not isinstance(source, dict):
        return
    for script in source.get("selected_source_scripts") or []:
        if not isinstance(script, dict):
            continue
        variables = script.get("startup_variables")
        if not isinstance(variables, list):
            continue
        script["startup_variables"] = [
            item
            for item in variables
            if not (isinstance(item, dict) and str(item.get("name") or "").strip() == name)
        ]


def _pick_preferred_ir_variable(records: list[dict[str, Any]]) -> dict[str, Any]:
    return max(
        records,
        key=lambda item: sum(1 for key in _VARIABLE_DEFINITION_COMPARE_KEYS if _ir_field(item, key)),
    )


def _ir_field(record: Mapping[str, Any], key: str) -> Any:
    value = record.get(key)
    if value not in (None, "", []):
        return value
    if key == "scope":
        return record.get("variable_scope") or record.get("kind")
    if key == "type":
        return record.get("type_name") or record.get("data_type")
    if key == "query_at_startup":
        return record.get("query_on_startup")
    if key == "query_prompt":
        return record.get("prompt")
    if key == "default_value":
        if record.get("value") not in (None, "", []):
            return record.get("value")
        if record.get("values") not in (None, "", []):
            return record.get("values")
        return record.get("default_values")
    return None


def _is_source_startup_path(path: str) -> bool:
    return path.startswith("source.selected_source_scripts[")


def _variable_entries_have_explicit_conflict(entries: list[tuple[str, dict[str, Any]]]) -> bool:
    for key in _VARIABLE_DEFINITION_COMPARE_KEYS:
        seen = ""
        for path, record in entries:
            value = _ir_field(record, key)
            if value in (None, "", []):
                continue
            normalized = _compare_value(value)
            if not normalized:
                continue
            if key == "default_value" and _is_source_startup_path(path):
                continue
            if seen and seen.casefold() != normalized.casefold():
                return True
            seen = normalized
    return False


def _set_ir_field(record: dict[str, Any], key: str, value: Any) -> None:
    if value in (None, "", []):
        return
    if key == "default_value":
        if all(record.get(alias) in (None, "", []) for alias in ("value", "default_value", "values", "default_values")):
            if isinstance(value, list):
                record["default_values"] = value
            else:
                record["value"] = value
        return
    record.setdefault(key, value)


def _explicit_generated_default(entries: list[tuple[str, dict[str, Any]]]) -> Any:
    selected: Any = None
    seen = ""
    for path, record in entries:
        if _is_source_startup_path(path):
            continue
        value = _ir_field(record, "default_value")
        normalized = _compare_value(value)
        if not normalized:
            continue
        if seen and seen.casefold() != normalized.casefold():
            return None
        seen = normalized
        selected = value
    return selected


def _merge_preferred_ir_variable(entries: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    records = [record for _, record in entries]
    merged = dict(_pick_preferred_ir_variable(records))
    merged["name"] = str(merged.get("name") or records[0].get("name") or "").strip()
    for key in _VARIABLE_DEFINITION_COMPARE_KEYS:
        if _ir_field(merged, key) not in (None, "", []):
            continue
        for record in records:
            value = _ir_field(record, key)
            if value not in (None, "", []):
                _set_ir_field(merged, key, value)
                break
    generated_default = _explicit_generated_default(entries)
    if generated_default not in (None, "", []):
        for alias in ("value", "default_value", "values", "default_values"):
            merged.pop(alias, None)
        if isinstance(generated_default, list):
            merged["default_values"] = generated_default
        else:
            merged["value"] = generated_default
    return merged


def _collapse_identical_ir_variables(ir: dict[str, Any]) -> list[dict[str, str]]:
    """Drop duplicate declarations with the same name and compatible fields across IR."""
    indexed = _index_ir_variable_declarations(ir)
    collapsed: list[dict[str, str]] = []
    normalized: list[dict[str, Any]] = []

    for name in sorted(indexed):
        entries = indexed[name]
        records = [record for _, record in entries]
        paths = [path for path, _ in entries]
        if len(records) == 1:
            normalized.append(records[0])
            _remove_startup_variable_declarations(ir, name)
            continue

        if _variable_entries_have_explicit_conflict(entries):
            normalized.extend(records)
            continue

        kept = _merge_preferred_ir_variable(entries)
        normalized.append(kept)
        collapsed.append(
            {
                "name": name,
                "removed_count": str(len(records) - 1),
                "sources": "; ".join(paths),
            }
        )
        _remove_startup_variable_declarations(ir, name)

    ir["variables"] = normalized
    return collapsed


def _conflicting_ir_variable_names(ir: Mapping[str, Any]) -> list[dict[str, str]]:
    indexed = _index_ir_variable_declarations(ir)
    conflicts: list[dict[str, str]] = []
    for name, entries in indexed.items():
        records = [record for _, record in entries]
        if len(records) < 2:
            continue
        paths = [path for path, _ in entries]
        first = _ir_variable_definition(records[0])
        for record in records[1:]:
            if _variable_definition_conflicts(first, _ir_variable_definition(record)):
                conflicts.append(
                    {
                        "name": name,
                        "first_scope": str(records[0].get("scope") or records[0].get("variable_scope") or ""),
                        "first_type": str(records[0].get("type") or records[0].get("type_name") or ""),
                        "second_scope": str(record.get("scope") or record.get("variable_scope") or ""),
                        "second_type": str(record.get("type") or record.get("type_name") or ""),
                        "sources": "; ".join(paths),
                    }
                )
                break
    return conflicts


def _declared_variable_names(ir: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for record in ir.get("variables") or []:
        if isinstance(record, dict):
            name = str(record.get("name") or "").strip()
            if _valid_variable_name(name):
                names.add(name)
    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    for script in source.get("selected_source_scripts") or []:
        if not isinstance(script, dict):
            continue
        for record in script.get("startup_variables") or []:
            if isinstance(record, dict):
                name = str(record.get("name") or "").strip()
                if _valid_variable_name(name):
                    names.add(name)
    return names


def find_undeclared_variable_references(ir: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return unresolved variable references without mutating protocol IR."""

    declared = _declared_variable_names(ir)
    expected_query = set(expected_query_names_from_ir(ir))
    missing: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for usage in _referenced_variable_usages(ir):
        name = str(usage.get("name") or "").strip()
        if not _valid_variable_name(name) or name in declared or name in expected_query:
            continue
        key = (name, str(usage.get("path") or ""), str(usage.get("step_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        missing.append(usage)
    return missing


def _opaque_expression_metadata_failures(
    ir: Mapping[str, Any],
) -> list[VariableReconciliationFailure]:
    failures: list[VariableReconciliationFailure] = []
    for record in walk_expression_values(ir):
        expression = record.expression
        kind = str(expression.get("kind") or "")
        if kind not in {"source_preserved_expression", "reviewed_raw_expression"}:
            continue
        path = str(record.path)
        referenced_variables = expression.get("referenced_variables")
        referenced_functions = expression.get("referenced_functions")
        if not isinstance(referenced_variables, list) or not isinstance(
            referenced_functions, list
        ):
            failures.append(
                VariableReconciliationFailure(
                    code="opaque_expression_reference_metadata_missing",
                    message=(
                        "Opaque expression reference metadata must explicitly list "
                        "referenced_variables and referenced_functions."
                    ),
                    details={"path": path, "kind": kind},
                )
            )
            continue
        if kind == "source_preserved_expression":
            provenance_id = str(expression.get("provenance_id") or "")
            if (
                expression.get("reference_metadata_origin") != "source_ingestion"
                or not str(expression.get("source_entry") or "")
                or not _PROVENANCE_ID_RE.fullmatch(provenance_id)
            ):
                failures.append(
                    VariableReconciliationFailure(
                        code="unverified_source_preserved_expression",
                        message=(
                            "Source-preserved expression metadata must be bound to "
                            "a trusted source-ingestion provenance record."
                        ),
                        details={"path": path, "kind": kind},
                    )
                )
        elif (
            expression.get("reference_metadata_origin") != "reviewer_approved"
            or not str(expression.get("approval_id") or "")
            or not str(expression.get("reviewer") or "")
        ):
            failures.append(
                VariableReconciliationFailure(
                    code="unapproved_reviewed_raw_expression",
                    message=(
                        "Reviewed raw expression reference metadata requires an "
                        "approval ID and named reviewer."
                    ),
                    details={"path": path, "kind": kind},
                )
            )
    return failures


def ensure_referenced_variables_declared(ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Legacy migration helper that materializes missing declarations.

    Normal generation must call ``find_undeclared_variable_references`` and fail
    instead of using this compatibility helper.
    """

    declared = _declared_variable_names(ir)
    expected_query = set(expected_query_names_from_ir(ir))
    missing = sorted((_referenced_variable_names(ir) - declared) - expected_query)
    variables = ir.setdefault("variables", [])
    if not isinstance(variables, list):
        variables = []
        ir["variables"] = variables

    additions: list[dict[str, Any]] = []
    for name in missing:
        if not _valid_variable_name(name):
            continue
        record = {
            "name": name,
            "value": 1,
            "scope": "Script",
            "source": INFERRED_REFERENCE_VARIABLE_SOURCE,
            "manual_review_required": True,
        }
        variables.append(record)
        additions.append(
            {
                "name": name,
                "value": 1,
                "scope": "Script",
                "source": INFERRED_REFERENCE_VARIABLE_SOURCE,
            }
        )

    if additions:
        source = ir.setdefault("source", {})
        existing = source.setdefault("inferred_referenced_variables", [])
        if isinstance(existing, list):
            existing.extend(additions)
        else:
            source["inferred_referenced_variables"] = additions
    return additions


def _referenced_variable_usages(ir: Mapping[str, Any]) -> list[dict[str, Any]]:
    usages: list[dict[str, Any]] = []

    for record in walk_expression_values(ir):
        path = str(record.path)
        step = _step_from_path(ir, path)
        for name in sorted(_expression_variable_names_from_mapping(record.expression)):
            usages.append(_reference_usage(name, path, step=step))

    category_conditions = ir.get("category_conditions")
    if isinstance(category_conditions, Mapping):
        for key, condition in category_conditions.items():
            if not isinstance(condition, Mapping):
                continue
            refs: set[str] = set()
            _add_explicit_variable_name(refs, condition.get("variable"))
            for name in sorted(refs):
                usages.append(
                    _reference_usage(
                        name,
                        f"{_join_path('$.category_conditions', key)}.variable",
                    )
                )

    for labware_index, item in enumerate(ir.get("labware") or []):
        if not isinstance(item, Mapping):
            continue
        for name in sorted(_bracket_variable_names(item.get("label"))):
            usages.append(_reference_usage(name, f"$.labware[{labware_index}].label"))

    for step_index, step in enumerate(ir.get("steps") or []):
        if not isinstance(step, Mapping):
            continue
        operation = str(step.get("operation") or "")
        step_path = f"$.steps[{step_index}]"
        for name in sorted(_bracket_variable_names(step.get("target_labware"))):
            usages.append(_reference_usage(name, f"{step_path}.target_labware", step=step))
        params = step.get("parameters")
        if not isinstance(params, Mapping):
            continue
        for key, value in params.items():
            key_text = str(key)
            if key_text in _PROSE_PARAMETER_KEYS:
                continue
            if key_text.endswith("_expression") or key_text.endswith("_expressions"):
                continue
            if isinstance(value, (str, int, float)):
                for name in sorted(_bracket_variable_names(value)):
                    usages.append(
                        _reference_usage(
                            name,
                            _join_path(f"{step_path}.parameters", key_text),
                            step=step,
                        )
                    )
        if operation in {"query_variable", "set_variable"}:
            refs: set[str] = set()
            _add_explicit_variable_name(refs, params.get("variable"))
            for name in sorted(refs):
                usages.append(_reference_usage(name, f"{step_path}.parameters.variable", step=step))
        elif operation == "execute_application":
            refs = set()
            _add_explicit_variable_name(refs, params.get("variable"))
            for name in sorted(refs):
                usages.append(_reference_usage(name, f"{step_path}.parameters.variable", step=step))
        elif operation == "runtime_variable_prompt":
            for item_index, item in enumerate(params.get("variables") or []):
                if not isinstance(item, Mapping):
                    continue
                refs = set()
                _add_explicit_variable_name(refs, item.get("name"))
                for name in sorted(refs):
                    usages.append(
                        _reference_usage(
                            name,
                            f"{step_path}.parameters.variables[{item_index}].name",
                            step=step,
                        )
                    )
        elif operation == "call_subroutine":
            for section in ("variable_mappings_start", "variable_mappings_end"):
                for mapping_index, mapping in enumerate(params.get(section) or []):
                    if not isinstance(mapping, Mapping) or "source_expression" in mapping:
                        continue
                    refs = set()
                    _add_explicit_variable_name(refs, mapping.get("source"))
                    for name in sorted(refs):
                        usages.append(
                            _reference_usage(
                                name,
                                f"{step_path}.parameters.{section}[{mapping_index}].source",
                                step=step,
                            )
                        )
        elif operation in {"conditional_branch", "default_branch"}:
            for name in sorted(_expression_variable_names(params.get("condition"))):
                usages.append(_reference_usage(name, f"{step_path}.parameters.condition", step=step))

    return usages


def _reference_usage(
    name: str,
    path: str,
    *,
    step: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    usage: dict[str, Any] = {"name": name, "path": path}
    if step is not None:
        step_id = str(step.get("id") or "").strip()
        operation = str(step.get("operation") or "").strip()
        if step_id:
            usage["step_id"] = step_id
        if operation:
            usage["operation"] = operation
    return usage


def _step_from_path(ir: Mapping[str, Any], path: str) -> Mapping[str, Any] | None:
    match = re.match(r"^\$\.steps\[(\d+)\]", path)
    if not match:
        return None
    try:
        step = list(ir.get("steps") or [])[int(match.group(1))]
    except (IndexError, TypeError, ValueError):
        return None
    return step if isinstance(step, Mapping) else None


def _join_path(parent: str, value: Any) -> str:
    text = str(value)
    return f"{parent}.{text}" if text.isidentifier() else f"{parent}[{text!r}]"


def _invalid_subroutine_variable_mappings(
    ir: Mapping[str, Any],
    lookup: Mapping[str, Mapping[str, Any]],
    *,
    context_root: Path | None,
) -> list[dict[str, str]]:
    invalid: list[dict[str, str]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        subroutine = clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine"))
        if not subroutine:
            continue
        valid_targets = valid_mapping_targets_for_subroutine(
            subroutine,
            lookup,
            context_root=context_root,
        )
        if not valid_targets:
            continue
        for section in ("variable_mappings_start", "variable_mappings_end"):
            mappings = params.get(section)
            if not isinstance(mappings, list):
                continue
            for item in mappings:
                if not isinstance(item, dict):
                    continue
                target = str(item.get("target") or "").strip()
                if target and target not in valid_targets:
                    invalid.append(
                        {
                            "step_id": str(step.get("id") or ""),
                            "subroutine": subroutine,
                            "section": section,
                            "target": target,
                            "source": str(item.get("source") or ""),
                        }
                    )
    return invalid


def preflight_variable_reconciliation(
    ir: dict[str, Any],
    *,
    request_spec: Mapping[str, Any] | None = None,
    lookup: Mapping[str, Mapping[str, Any]] | None = None,
    context_root: Path | None = None,
) -> dict[str, Any]:
    """Run offline variable reconciliation before Python/XML generation.

    Mutates ``ir`` to collapse exact duplicate declarations and re-run subroutine
    reconciliation. Returns a report dict with ``ok`` and ``failures``.
    """
    lookup = lookup or {}
    failures: list[VariableReconciliationFailure] = []
    collapsed = _collapse_identical_ir_variables(ir)

    if lookup:
        reconcile_ir_subroutine_variable_definitions(
            ir,
            lookup,
            context_root=context_root,
        )

    sub_defs, sub_sources, sub_conflicts = _called_subroutine_variable_definitions(
        ir,
        lookup,
        context_root=context_root,
    )
    for conflict in sub_conflicts:
        failures.append(
            VariableReconciliationFailure(
                code="subroutine_variable_definition_conflict",
                message=(
                    f"Called subroutines disagree on variable `{conflict.get('name')}` "
                    f"({conflict.get('first_subroutine')} vs {conflict.get('second_subroutine')})."
                ),
                details=dict(conflict),
            )
        )

    for conflict in _conflicting_ir_variable_names(ir):
        name = conflict["name"]
        if name in sub_defs:
            # Subroutine-authoritative: a called subroutine owns this variable name.
            definition = _copy_subroutine_definition(name, sub_defs[name])
            variables = [
                item
                for item in (ir.get("variables") or [])
                if not (isinstance(item, dict) and str(item.get("name") or "").strip() == name)
            ]
            variables.append(definition)
            ir["variables"] = variables
            _remove_startup_variable_declarations(ir, name)
            continue
        failures.append(
            VariableReconciliationFailure(
                code="conflicting_variable_declarations",
                message=(
                    f"Variable `{name}` is declared multiple times in protocol IR with "
                    "different fields and no subroutine-authoritative definition to match."
                ),
                details=dict(conflict),
            )
        )

    declared = _declared_variable_names(ir)
    expected_query = set(expected_query_names_from_ir(ir))
    failures.extend(_opaque_expression_metadata_failures(ir))
    missing_refs = find_undeclared_variable_references(ir)
    for reference in missing_refs:
        name = str(reference.get("name") or "")
        failures.append(
            VariableReconciliationFailure(
                code="undeclared_referenced_variable",
                message=f"Variable `{name}` is used but not declared in protocol IR.",
                details=dict(reference),
            )
        )

    for name in sorted(expected_query - declared):
        failures.append(
            VariableReconciliationFailure(
                code="query_variable_undeclared",
                message=f"Query-at-startup variable `{name}` is modeled but not declared in protocol IR.",
                details={"name": name},
            )
        )

    for item in _invalid_subroutine_variable_mappings(ir, lookup, context_root=context_root):
        failures.append(
            VariableReconciliationFailure(
                code="invalid_subroutine_variable_mapping",
                message=(
                    f"Subroutine `{item.get('subroutine')}` mapping target `{item.get('target')}` "
                    "is not declared in the called subroutine."
                ),
                details=dict(item),
            )
        )

    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    report = source.setdefault("variable_reconciliation", {})
    report.update(
        {
            "schema_version": VARIABLE_RECONCILIATION_VERSION,
            "ok": not failures,
            "failure_count": len(failures),
            "collapsed_duplicates": collapsed,
            "subroutine_definition_conflicts": sub_conflicts,
            "failures": [item.as_dict() for item in failures],
        }
    )

    return {
        "schema_version": VARIABLE_RECONCILIATION_VERSION,
        "ok": not failures,
        "failure_count": len(failures),
        "failures": [item.as_dict() for item in failures],
        "collapsed_duplicates": collapsed,
        "subroutine_definition_conflicts": sub_conflicts,
    }


def validate_xscr_variable_declarations(xscr_path: Path | None) -> list[VariableReconciliationFailure]:
    """Fail on duplicate declarations or defaults FluentControl cannot materialize."""
    if xscr_path is None or not xscr_path.exists():
        return []
    try:
        root = ET.parse(xscr_path).getroot()
    except (ET.ParseError, OSError, UnicodeDecodeError) as exc:
        return [
            VariableReconciliationFailure(
                code="xscr_parse_error",
                message=f"Could not parse compiled XSCR for variable declaration audit: {xscr_path}",
                details={"error": str(exc), "path": str(xscr_path)},
            )
        ]

    seen: dict[str, int] = {}
    duplicates: list[dict[str, str]] = []
    invalid_defaults: list[VariableReconciliationFailure] = []
    for element in root.iter():
        local = _local_xml_name(element.tag)
        if local == "anyType":
            type_hint = " ".join(str(value) for value in element.attrib.values())
            if "VariableDefinitionHelper" not in type_hint:
                continue
        elif local != "VariableDefinitionHelper":
            direct_names = {_local_xml_name(child.tag) for child in list(element)}
            if not {"Name", "TypeName", "QueryOnStartup"}.issubset(direct_names):
                continue
        name = _first_xml_text(element, "Name")
        if not _valid_variable_name(name):
            continue
        seen[name] = seen.get(name, 0) + 1
        if seen[name] == 2:
            duplicates.append({"name": name})
        type_name = _first_xml_text(element, "TypeName")
        if type_name not in {"Integer", "Floating Point"}:
            continue
        default_value = _first_xml_value_text(element, "Values")
        if not default_value:
            invalid_defaults.append(
                VariableReconciliationFailure(
                    code="missing_xscr_value_type_default",
                    message=(
                        f"Compiled XSCR declares value-type variable `{name}` as `{type_name}` "
                        "without a startup default; FluentControl will fail in VariableContainer.Declare."
                    ),
                    details={"name": name, "type": type_name, "value": ""},
                )
            )
            continue
        try:
            number = Decimal(default_value)
        except InvalidOperation:
            number = None
        valid = number is not None and number.is_finite()
        if type_name == "Integer":
            valid = valid and number == number.to_integral_value() and "." not in default_value
        if not valid:
            invalid_defaults.append(
                VariableReconciliationFailure(
                    code="invalid_xscr_value_type_default",
                    message=(
                        f"Compiled XSCR declares `{name}` as `{type_name}` with incompatible "
                        f"startup default `{default_value}`; FluentControl may resolve it to null."
                    ),
                    details={"name": name, "type": type_name, "value": default_value},
                )
            )

    duplicate_failures = [
        VariableReconciliationFailure(
            code="duplicate_xscr_variable_declaration",
            message=(
                f"Compiled XSCR declares variable `{item['name']}` more than once "
                "(duplicate VariableDefinitionHelper/Name)."
            ),
            details=dict(item),
        )
        for item in duplicates
    ]
    return duplicate_failures + invalid_defaults


def failures_to_dicts(failures: list[VariableReconciliationFailure]) -> list[dict[str, Any]]:
    return [item.as_dict() for item in failures]


def render_variable_reconciliation_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# Variable Reconciliation Preflight",
        "",
        f"- Status: `{'passed' if report.get('ok') else 'failed'}`",
        f"- Failures: `{report.get('failure_count', 0)}`",
        "",
    ]
    collapsed = report.get("collapsed_duplicates") or []
    if collapsed:
        lines.append("## Collapsed duplicate declarations")
        lines.append("")
        for item in collapsed:
            lines.append(f"- `{item.get('name')}` removed `{item.get('removed_count')}` duplicate(s)")
        lines.append("")
    failures = report.get("failures") or []
    if failures:
        lines.append("## Failures")
        lines.append("")
        for item in failures:
            lines.append(f"- `{item.get('code')}`: {item.get('message')}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _local_xml_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _first_xml_text(element: ET.Element, local_name: str) -> str:
    for child in element.iter():
        if _local_xml_name(child.tag) == local_name and child.text:
            text = child.text.strip()
            if text:
                return text
    return ""


def _first_xml_value_text(element: ET.Element, local_name: str) -> str:
    for child in element.iter():
        if _local_xml_name(child.tag) != local_name:
            continue
        for value in child.iter():
            if value is child or not value.text:
                continue
            text = value.text.strip()
            if text:
                return text
    return ""
