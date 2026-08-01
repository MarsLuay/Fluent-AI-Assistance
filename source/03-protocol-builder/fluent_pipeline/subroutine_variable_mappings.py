"""Normalize subroutine variable mappings against called subroutine declarations."""

from __future__ import annotations

import re
from . import xml_compat as ET
from pathlib import Path
from typing import Any, Mapping

from .subroutine_dependencies import clean_subroutine_reference, norm_subroutine_key


def _local_xml_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _first_xml_text(parent: Any, name: str) -> str:
    if parent is None:
        return ""
    for child in parent:
        if _local_xml_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _xml_leaf_text(parent: Any, name: str) -> str:
    if parent is None:
        return ""
    for child in parent:
        if _local_xml_name(child.tag) != name:
            continue
        leaves = [
            str(descendant.text or "").strip()
            for descendant in child.iter()
            if descendant is not child and str(descendant.text or "").strip()
        ]
        if leaves:
            return ";".join(leaves)
        return str(child.text or "").strip()
    return ""


def _variable_definition_elements(root: Any) -> list[Any]:
    elements: list[Any] = []
    for el in root.iter():
        if _local_xml_name(el.tag) != "anyType":
            continue
        if "VariableDefinitionHelper" not in " ".join(str(value) for value in el.attrib.values()):
            continue
        elements.append(el)
    return elements


def variable_definitions_from_xscr(xscr_path: Path | None) -> dict[str, dict[str, str]]:
    if xscr_path is None or not xscr_path.exists():
        return {}
    try:
        root = ET.parse(xscr_path).getroot()
    except (ET.ParseError, OSError):
        return {}
    definitions: dict[str, dict[str, str]] = {}
    for el in _variable_definition_elements(root):
        name = _first_xml_text(el, "Name")
        if not name:
            continue
        definitions[name] = _variable_definition_from_element(el)
    return definitions


_VARIABLE_XML_FIELD_MAP = (
    ("Scope", "scope"),
    ("TypeName", "type"),
    ("QueryOnStartup", "query_at_startup"),
    ("QueryOnStartupString", "query_prompt"),
    ("ReadOnly", "read_only"),
    ("Values", "default_value"),
    ("AllowedValues", "allowed_values"),
    ("MinimumText", "minimum"),
    ("MaximumText", "maximum"),
)
_VARIABLE_DEFINITION_COMPARE_KEYS = tuple(target for _, target in _VARIABLE_XML_FIELD_MAP)
_BOOLEAN_VARIABLE_FIELDS = {"query_at_startup", "read_only"}


def _variable_definition_from_element(el: Any) -> dict[str, Any]:
    definition: dict[str, Any] = {"name": _first_xml_text(el, "Name")}
    for xml_name, target_key in _VARIABLE_XML_FIELD_MAP:
        value = _xml_leaf_text(el, xml_name)
        if value == "":
            continue
        if target_key in _BOOLEAN_VARIABLE_FIELDS:
            definition[target_key] = value.strip().casefold() in {"1", "true", "yes", "on"}
        else:
            definition[target_key] = value
    return definition


def _compare_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ";".join(str(item).strip() for item in value)
    return str(value or "").strip()


def _variable_definition_conflicts(first: Mapping[str, Any], second: Mapping[str, Any]) -> bool:
    for key in _VARIABLE_DEFINITION_COMPARE_KEYS:
        left = _compare_value(first.get(key))
        right = _compare_value(second.get(key))
        if (left or right) and left.casefold() != right.casefold():
            return True
    return False


def _variable_definition_needs_subroutine_match(main: Mapping[str, Any], subroutine: Mapping[str, Any]) -> bool:
    if _variable_definition_conflicts(main, subroutine):
        return True
    for key in _VARIABLE_DEFINITION_COMPARE_KEYS:
        sub_value = str(subroutine.get(key) or "").strip()
        main_value = str(
            main.get(key)
            or (main.get("variable_scope") if key == "scope" else "")
            or (main.get("kind") if key == "scope" else "")
            or (main.get("type_name") if key == "type" else "")
            or (main.get("data_type") if key == "type" else "")
            or ""
        ).strip()
        if sub_value and not main_value:
            return True
    return False


def _copy_subroutine_definition(name: str, definition: Mapping[str, Any]) -> dict[str, Any]:
    out = {"name": name}
    for key in _VARIABLE_DEFINITION_COMPARE_KEYS:
        value = definition.get(key)
        if value not in (None, "", []):
            out[key] = value
    for source_key, target_key in (("type_name", "type"), ("variable_scope", "scope"), ("kind", "scope")):
        value = str(definition.get(source_key) or "").strip()
        if value and target_key not in out:
            out[target_key] = value
    return out


def _unique_variable_name(base: str, existing: set[str]) -> str:
    candidate = f"{base}_Main"
    if candidate not in existing:
        return candidate
    suffix = 2
    while f"{candidate}{suffix}" in existing:
        suffix += 1
    return f"{candidate}{suffix}"


def _same_token_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])")


def _string_references_variable(value: str, name: str) -> bool:
    return _same_token_pattern(name).search(value) is not None


def _value_references_variable(value: Any, name: str, *, key: str = "") -> bool:
    ignored_text_keys = {
        "comment",
        "description",
        "display_text",
        "group",
        "image_path",
        "media",
        "name",
        "path",
        "prompt",
        "reason",
        "screen_title",
        "source_path",
        "subroutine",
        "title",
    }
    if isinstance(value, str):
        return key not in ignored_text_keys and _string_references_variable(value, name)
    if isinstance(value, Mapping):
        return any(_value_references_variable(child, name, key=str(child_key)) for child_key, child in value.items())
    if isinstance(value, list):
        return any(_value_references_variable(child, name, key=key) for child in value)
    return False


def _has_local_main_references(ir: Mapping[str, Any], name: str) -> bool:
    """Return true when a conflicting variable is used outside call-boundary plumbing.

    Direct query/set commands are still compatible with the subroutine variable:
    they prepare the shared variable. Expressions, labware labels, and other
    command fields are treated as local main-script usage and get renamed.
    """

    for item in ir.get("labware") or []:
        if isinstance(item, Mapping):
            for key, value in item.items():
                if key == "label" and isinstance(value, str) and f"[{name}]" in value:
                    return True
    for step in ir.get("steps") or []:
        if not isinstance(step, Mapping):
            continue
        operation = str(step.get("operation") or "")
        params = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}
        if operation == "call_subroutine":
            continue
        if operation in {"set_variable", "query_variable", "set_remaining_runtime"}:
            params_without_variable = {key: value for key, value in params.items() if key != "variable"}
            if _value_references_variable(params_without_variable, name):
                return True
            continue
        if operation == "runtime_variable_prompt":
            scrubbed = []
            for item in params.get("variables") or []:
                if not isinstance(item, Mapping):
                    scrubbed.append(item)
                    continue
                scrubbed.append({key: value for key, value in item.items() if key != "name"})
            params_without_names = {**params, "variables": scrubbed}
            if _value_references_variable(params_without_names, name):
                return True
            continue
        if _value_references_variable(params, name) or _value_references_variable(step.get("target_labware"), name):
            return True
    return False


def _replace_variable_token(value: Any, old: str, new: str, *, key: str = "") -> Any:
    ignored_text_keys = {
        "comment",
        "description",
        "display_text",
        "group",
        "image_path",
        "media",
        "name",
        "path",
        "prompt",
        "reason",
        "screen_title",
        "source_path",
        "subroutine",
        "title",
    }
    if isinstance(value, str):
        if key in ignored_text_keys:
            return value
        return _same_token_pattern(old).sub(new, value)
    if isinstance(value, list):
        return [_replace_variable_token(item, old, new, key=key) for item in value]
    if isinstance(value, dict):
        return {item_key: _replace_variable_token(item_value, old, new, key=str(item_key)) for item_key, item_value in value.items()}
    return value


def _rename_local_main_references(ir: dict[str, Any], old: str, new: str) -> None:
    for item in ir.get("labware") or []:
        if isinstance(item, dict):
            for key, value in list(item.items()):
                item[key] = _replace_variable_token(value, old, new, key=str(key))
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation = str(step.get("operation") or "")
        if operation == "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        if operation in {"set_variable", "query_variable", "set_remaining_runtime"}:
            for key, value in list(params.items()):
                if key == "variable":
                    continue
                params[key] = _replace_variable_token(value, old, new, key=str(key))
            continue
        if operation == "runtime_variable_prompt":
            for item in params.get("variables") or []:
                if not isinstance(item, dict):
                    continue
                for key, value in list(item.items()):
                    if key == "name":
                        continue
                    item[key] = _replace_variable_token(value, old, new, key=str(key))
            continue
        for key, value in list(params.items()):
            params[key] = _replace_variable_token(value, old, new, key=str(key))
        if "target_labware" in step:
            step["target_labware"] = _replace_variable_token(step.get("target_labware"), old, new)


def _align_operator_variable_prompts(
    ir: dict[str, Any],
    name: str,
    sub_def: Mapping[str, Any],
) -> list[dict[str, str]]:
    query_prompt = str(sub_def.get("query_prompt") or "").strip()
    if not query_prompt:
        return []
    changes: list[dict[str, str]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation = str(step.get("operation") or "")
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        label = str(step.get("id") or step.get("index") or "")
        if operation == "query_variable" and str(params.get("variable") or "").strip() == name:
            old = str(params.get("prompt") or params.get("query_prompt") or "").strip()
            if old != query_prompt:
                params["prompt"] = query_prompt
                step["parameters"] = params
                changes.append({"step": label, "field": "prompt", "from": old, "to": query_prompt})
            continue
        if operation != "runtime_variable_prompt":
            continue
        for item in params.get("variables") or []:
            if not isinstance(item, dict) or str(item.get("name") or "").strip() != name:
                continue
            old = str(item.get("display_text") or item.get("query_prompt") or "").strip()
            if old != query_prompt:
                item["display_text"] = query_prompt
                changes.append({"step": label, "field": "display_text", "from": old, "to": query_prompt})
    return changes


def _called_subroutine_variable_definitions(
    ir: Mapping[str, Any],
    lookup: Mapping[str, Mapping[str, Any]],
    *,
    context_root: Path | None = None,
) -> tuple[dict[str, dict[str, str]], dict[str, str], list[dict[str, str]]]:
    definitions: dict[str, dict[str, str]] = {}
    sources: dict[str, str] = {}
    conflicts: list[dict[str, str]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, Mapping) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}
        subroutine = clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine"))
        if not subroutine:
            continue
        key = norm_subroutine_key(subroutine)
        bare = norm_subroutine_key(subroutine.rsplit("\\", 1)[-1])
        record = lookup.get(key) or lookup.get(bare)
        path = script_record_path(record, context_root=context_root)
        for name, definition in variable_definitions_from_xscr(path).items():
            existing = definitions.get(name)
            if existing and _variable_definition_conflicts(existing, definition):
                conflicts.append(
                    {
                        "name": name,
                        "first_subroutine": sources.get(name, ""),
                        "second_subroutine": subroutine,
                        "first_scope": str(existing.get("scope") or ""),
                        "first_type": str(existing.get("type") or ""),
                        "first_query_prompt": str(existing.get("query_prompt") or ""),
                        "second_scope": str(definition.get("scope") or ""),
                        "second_type": str(definition.get("type") or ""),
                        "second_query_prompt": str(definition.get("query_prompt") or ""),
                    }
                )
                continue
            definitions.setdefault(name, definition)
            sources.setdefault(name, subroutine)
    return definitions, sources, conflicts


def reconcile_ir_subroutine_variable_definitions(
    ir: dict[str, Any],
    lookup: Mapping[str, Mapping[str, Any]],
    *,
    context_root: Path | None = None,
) -> list[dict[str, str]]:
    """Make main-script variable declarations agree with called subroutines.

    FluentControl rejects generated scripts when the main script and a called
    subroutine both declare the same variable with different declaration fields.
    A called subroutine is authoritative for that variable name. If the generated
    main script also needs the old definition for local expressions, those local
    uses are renamed and the original name is kept for the subroutine boundary.
    """

    if not isinstance(ir, dict):
        return []
    sub_defs, sources, sub_conflicts = _called_subroutine_variable_definitions(
        ir,
        lookup,
        context_root=context_root,
    )
    if not sub_defs:
        return []

    variables = ir.setdefault("variables", [])
    existing_names = {str(item.get("name") or "").strip() for item in variables if isinstance(item, dict)}
    fixups: list[dict[str, str]] = []
    for variable in list(variables):
        if not isinstance(variable, dict):
            continue
        name = str(variable.get("name") or "").strip()
        if not name or name not in sub_defs:
            continue
        sub_def = sub_defs[name]
        if not _variable_definition_needs_subroutine_match(variable, sub_def):
            continue
        old_scope = str(variable.get("scope") or variable.get("variable_scope") or variable.get("kind") or "")
        old_type = str(variable.get("type") or variable.get("type_name") or variable.get("data_type") or "")
        if _has_local_main_references(ir, name):
            new_name = _unique_variable_name(name, existing_names)
            existing_names.add(new_name)
            local_variable = dict(variable)
            local_variable["name"] = new_name
            variables.append(local_variable)
            _rename_local_main_references(ir, name, new_name)
            variable.clear()
            variable.update(_copy_subroutine_definition(name, sub_def))
            prompt_changes = _align_operator_variable_prompts(ir, name, sub_def)
            fixups.append(
                {
                    "name": name,
                    "local_name": new_name,
                    "subroutine": sources.get(name, ""),
                    "main_scope": old_scope,
                    "main_type": old_type,
                    "sub_scope": str(sub_def.get("scope") or ""),
                    "sub_type": str(sub_def.get("type") or ""),
                    "sub_query_prompt": str(sub_def.get("query_prompt") or ""),
                    "prompt_changes": str(len(prompt_changes)),
                    "action": "renamed_local_variable_and_matched_subroutine",
                }
            )
            continue
        variable.clear()
        variable.update(_copy_subroutine_definition(name, sub_def))
        prompt_changes = _align_operator_variable_prompts(ir, name, sub_def)
        fixups.append(
            {
                "name": name,
                "subroutine": sources.get(name, ""),
                "main_scope": old_scope,
                "main_type": old_type,
                "sub_scope": str(sub_def.get("scope") or ""),
                "sub_type": str(sub_def.get("type") or ""),
                "sub_query_prompt": str(sub_def.get("query_prompt") or ""),
                "prompt_changes": str(len(prompt_changes)),
                "action": "matched_main_variable_to_subroutine",
            }
        )

    report = ir.setdefault("source", {}).setdefault("subroutine_variable_definitions", {})
    if fixups:
        report["fixups"] = fixups
        report["fixup_count"] = len(fixups)
    if sub_conflicts:
        report["subroutine_definition_conflicts"] = sub_conflicts
        report["subroutine_definition_conflict_count"] = len(sub_conflicts)
    return fixups


def script_record_path(record: Mapping[str, Any] | None, *, context_root: Path | None = None) -> Path | None:
    if not isinstance(record, Mapping):
        return None
    for key in ("resolved_path", "path", "extracted_path"):
        raw = str(record.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if not path.is_absolute() and context_root is not None:
            path = (context_root / path).resolve()
        elif not path.is_absolute():
            continue
        else:
            path = path.resolve()
        if path.exists():
            return path
    return None


def valid_mapping_targets_for_subroutine(
    subroutine: str,
    lookup: Mapping[str, Mapping[str, Any]],
    *,
    context_root: Path | None = None,
) -> set[str]:
    key = norm_subroutine_key(clean_subroutine_reference(subroutine))
    bare = norm_subroutine_key(clean_subroutine_reference(subroutine).rsplit("\\", 1)[-1])
    record = lookup.get(key) or lookup.get(bare)
    if not isinstance(record, Mapping):
        return set()
    path = script_record_path(record, context_root=context_root)
    return set(variable_definitions_from_xscr(path))


def filter_variable_mappings(
    mappings: list[dict[str, Any]] | None,
    valid_targets: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    if not isinstance(mappings, list):
        return [], []
    kept: list[dict[str, Any]] = []
    removed: list[dict[str, str]] = []
    for item in mappings:
        if not isinstance(item, dict):
            continue
        target = str(item.get("target") or "").strip()
        if target and target not in valid_targets:
            removed.append(
                {
                    "target": target,
                    "source": str(item.get("source") or ""),
                }
            )
            continue
        kept.append(dict(item))
    return kept, removed


def normalize_ir_subroutine_variable_mappings(
    ir: dict[str, Any],
    lookup: Mapping[str, Mapping[str, Any]],
    *,
    context_root: Path | None = None,
) -> list[dict[str, str]]:
    """Drop IR subroutine mappings whose target is absent from the called subroutine."""
    fixups: list[dict[str, str]] = []
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
            updated, removed = filter_variable_mappings(mappings, valid_targets)
            if removed:
                params[section] = updated
                step["parameters"] = params
                for item in removed:
                    fixups.append(
                        {
                            "step_id": str(step.get("id") or ""),
                            "subroutine": subroutine,
                            "section": section,
                            "target": item["target"],
                            "source": item["source"],
                        }
                    )
    if fixups:
        report = ir.setdefault("source", {}).setdefault("subroutine_variable_mappings", {})
        report["ir_fixups"] = fixups
        report["ir_fixup_count"] = len(fixups)
    return fixups


def mapping_pairs(mappings: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not isinstance(mappings, list):
        return pairs
    for item in mappings:
        if isinstance(item, dict):
            target = str(item.get("target") or "").strip()
            source = str(item.get("source") or "").strip()
        else:
            target = str(getattr(item, "target", "") or "").strip()
            source = str(getattr(item, "source", "") or "").strip()
        if target:
            pairs.append((target, source))
    return pairs


def subroutine_mappings_match_for_parity(
    ir_mappings: list[tuple[str, str]],
    compiled_mappings: list[tuple[str, str]],
    *,
    valid_targets: set[str] | None = None,
) -> bool:
    """Compare IR vs compiled mappings, ignoring stale IR-only invalid targets."""
    compiled_set = set(compiled_mappings)
    if not compiled_set and not ir_mappings:
        return True
    compiled_targets = {target for target, _ in compiled_set}
    if valid_targets:
        ir_relevant = {(target, source) for target, source in ir_mappings if target in valid_targets}
    else:
        ir_relevant = {(target, source) for target, source in ir_mappings if target in compiled_targets}
    return ir_relevant == compiled_set


def build_script_lookup_from_manifest(
    manifest: Mapping[str, Any] | None,
    *,
    context_root: Path | None = None,
) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    if not isinstance(manifest, Mapping):
        return lookup
    for script in manifest.get("scripts") or []:
        if not isinstance(script, dict):
            continue
        object_name = str(script.get("object_name") or script.get("name") or "").strip()
        if not object_name:
            continue
        record = dict(script)
        if context_root is not None:
            path = script_record_path(record, context_root=context_root)
            if path is not None:
                record["resolved_path"] = str(path)
        for key in {
            norm_subroutine_key(object_name),
            norm_subroutine_key(object_name.rsplit("\\", 1)[-1]),
        }:
            if key:
                lookup[key] = record
    return lookup
