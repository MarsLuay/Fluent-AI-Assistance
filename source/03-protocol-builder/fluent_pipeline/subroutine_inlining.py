"""Inline risky FluentControl subroutine calls into a generated main script."""

from __future__ import annotations

import copy
import re
from . import xml_compat as ET
from pathlib import Path
from typing import Any

from .legacy_driver_subroutines import legacy_driver_macros_in_subroutines
from .protocol_ir import protocol_ir_from_xscr
from .subroutine_dependencies import (
    clean_subroutine_reference,
    norm_subroutine_key,
    resolve_subroutine_dependencies,
)


DEFAULT_SUBROUTINE_ERROR_POLICY = "inline_local_on_error"
SUBROUTINE_INLINING_ASSUMPTION_ID = "problem_subroutines_inlined_locally"
INLINE_SAFETY_FLAG = "inlined_subroutine_body"
FALLBACK_PROMPT_SAFETY_FLAG = "subroutine_inline_fallback_prompt"

_INLINE_POLICIES = {
    "inline_local_on_error",
    "always_inline",
    "preserve",
    "none",
}


def normalize_subroutine_error_policy(value: Any) -> str:
    policy = str(value or DEFAULT_SUBROUTINE_ERROR_POLICY).strip().casefold()
    aliases = {
        "keep": "preserve",
        "keep_subroutines": "preserve",
        "off": "none",
        "never": "none",
        "inline": "always_inline",
        "inline_all": "always_inline",
        "inline_on_error": "inline_local_on_error",
        "local_on_error": "inline_local_on_error",
    }
    policy = aliases.get(policy, policy)
    return policy if policy in _INLINE_POLICIES else DEFAULT_SUBROUTINE_ERROR_POLICY


def inline_problem_subroutine_calls(
    ir: dict[str, Any],
    source_manifest: dict[str, Any] | None,
    *,
    policy: str | None = None,
    max_passes: int = 5,
) -> dict[str, Any]:
    """Replace risky ``call_subroutine`` steps with local steps or prompts.

    ``inline_local_on_error`` keeps healthy subroutine calls. It replaces calls
    that are missing, ambiguous, or contain legacy-driver macro dependencies,
    because those are the load/simulation cases that have repeatedly surfaced
    as "unable to load subroutine" or "command is unknown" in FluentControl.
    """

    chosen_policy = normalize_subroutine_error_policy(policy)
    report: dict[str, Any] = {
        "policy": chosen_policy,
        "inlined": [],
        "fallback_prompts": [],
        "preserved": [],
        "skipped": [],
    }
    if chosen_policy in {"preserve", "none"}:
        return _store_report(ir, report)
    if not isinstance(ir, dict) or not isinstance(source_manifest, dict):
        report["skipped"].append({"reason": "missing_source_manifest"})
        return _store_report(ir, report)

    for _pass in range(max(1, max_passes)):
        plan = _build_inline_plan(ir, source_manifest, policy=chosen_policy)
        if not plan["actions"]:
            report["preserved"] = plan["preserved"]
            break
        changed = _apply_inline_plan(ir, source_manifest, plan, report)
        if not changed:
            report["preserved"] = plan["preserved"]
            break
    else:
        report["skipped"].append({"reason": "max_inline_passes_reached", "max_passes": max_passes})

    _remove_inlined_subroutine_dependencies(ir, report)
    _renumber_steps(ir)
    if report["inlined"] or report["fallback_prompts"]:
        _add_inlining_assumption(ir)
    return _store_report(ir, report)


def _build_inline_plan(
    ir: dict[str, Any],
    source_manifest: dict[str, Any],
    *,
    policy: str,
) -> dict[str, Any]:
    resolution = resolve_subroutine_dependencies(ir, source_manifest)
    resolved_by_key = {
        norm_subroutine_key(record.get("ref")): record
        for record in resolution.get("resolved") or []
        if norm_subroutine_key(record.get("ref"))
    }
    action_by_key: dict[str, dict[str, Any]] = {}

    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        if not _call_step_requests_inline(step):
            continue
        ref = clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine"))
        key = norm_subroutine_key(ref)
        if not key:
            continue
        record = resolved_by_key.get(key)
        action_by_key[key] = (
            {
                "action": "inline",
                "reason": "step_requested_local_inline",
                "message": "the recipe step requested local subroutine inlining",
                "record": record,
            }
            if record
            else {
                "action": "prompt",
                "reason": "step_requested_local_inline_unresolved",
                "message": "the recipe step requested local inlining, but the subroutine did not resolve",
            }
        )

    for call in resolution.get("missing") or []:
        key = norm_subroutine_key(call.get("ref"))
        if key:
            action_by_key.setdefault(key, {
                "action": "prompt",
                "reason": "subroutine_missing",
                "message": "the referenced subroutine did not resolve in the source project",
                "call": call,
            })

    for record in resolution.get("ambiguous") or []:
        key = norm_subroutine_key(record.get("ref"))
        if key:
            action_by_key.setdefault(key, {
                "action": "prompt",
                "reason": "subroutine_ambiguous",
                "message": "the referenced subroutine matched more than one source script",
                "record": record,
            })

    if policy == "always_inline":
        for key, record in resolved_by_key.items():
            action_by_key.setdefault(
                key,
                {
                    "action": "inline",
                    "reason": "always_inline_policy",
                    "message": "generation policy requested local subroutine inlining",
                    "record": record,
                },
            )

    legacy_findings = legacy_driver_macros_in_subroutines(
        list(resolution.get("resolved") or []),
        ir,
        source_manifest,
    )
    for finding in legacy_findings:
        key = norm_subroutine_key(finding.get("subroutine"))
        if not key:
            continue
        action_by_key[key] = {
            "action": "inline",
            "reason": "legacy_driver_macro_dependency",
            "message": "the called subroutine tree contains legacy driver macro dependencies",
            "record": resolved_by_key.get(key),
            "legacy_driver_macros": [
                item for item in legacy_findings if norm_subroutine_key(item.get("subroutine")) == key
            ],
        }

    variable_conflicts = _variable_conflicts_by_subroutine(ir, resolved_by_key)
    for key, conflicts in variable_conflicts.items():
        if not conflicts:
            continue
        action_by_key.setdefault(
            key,
            {
                "action": "inline",
                "reason": "subroutine_variable_scope_conflict",
                "message": "the main script and called subroutine declare conflicting variables",
                "record": resolved_by_key.get(key),
                "variable_conflicts": conflicts,
            },
        )

    actions: list[dict[str, Any]] = []
    preserved: list[dict[str, Any]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        ref = clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine"))
        key = norm_subroutine_key(ref)
        action = action_by_key.get(key)
        if action:
            actions.append({"step": step, "ref": ref, "key": key, **action})
        elif ref:
            preserved.append({"subroutine": ref, "step_id": step.get("id"), "step_index": step.get("index")})
    return {"actions": actions, "preserved": preserved}


def _call_step_requests_inline(step: dict[str, Any]) -> bool:
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    return bool(
        params.get("inline")
        or params.get("inline_local")
        or params.get("force_inline")
        or step.get("inline")
        or step.get("inline_local")
        or step.get("force_inline")
    )


def _variable_conflicts_by_subroutine(
    ir: dict[str, Any],
    resolved_by_key: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, str]]]:
    main_defs = _ir_variable_definitions(ir)
    if not main_defs:
        return {}
    conflicts_by_key: dict[str, list[dict[str, str]]] = {}
    for key, record in resolved_by_key.items():
        path = _record_path(record)
        if path is None:
            continue
        sub_defs = variable_names_from_xscr(path)
        conflicts: list[dict[str, str]] = []
        for name, main_def in main_defs.items():
            sub_def = sub_defs.get(name)
            if not sub_def or not _variable_definition_conflicts(main_def, sub_def):
                continue
            conflicts.append(
                {
                    "name": name,
                    "main_scope": main_def.get("scope", ""),
                    "main_type": main_def.get("type", ""),
                    "main_query_prompt": main_def.get("query_prompt", ""),
                    "main_default_value": main_def.get("default_value", ""),
                    "sub_scope": sub_def.get("scope", ""),
                    "sub_type": sub_def.get("type", ""),
                    "sub_query_prompt": sub_def.get("query_prompt", ""),
                    "sub_default_value": sub_def.get("default_value", ""),
                }
            )
        if conflicts:
            conflicts_by_key[key] = conflicts
    return conflicts_by_key


def _ir_variable_definitions(ir: dict[str, Any]) -> dict[str, dict[str, str]]:
    definitions: dict[str, dict[str, str]] = {}
    for item in ir.get("variables") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        definitions[name] = {
            "name": name,
            "scope": str(item.get("scope") or item.get("variable_scope") or item.get("kind") or "").strip(),
            "type": str(item.get("type") or item.get("type_name") or item.get("data_type") or "").strip(),
            "query_prompt": str(item.get("query_prompt") or item.get("query_text") or "").strip(),
            "query_at_startup": str(item.get("query_at_startup") or "").strip(),
            "read_only": str(item.get("read_only") or "").strip(),
            "default_value": str(item.get("default_value") or item.get("value") or "").strip(),
            "allowed_values": str(item.get("allowed_values") or "").strip(),
        }
    return definitions


_VARIABLE_CONFLICT_KEYS = ("scope", "type", "query_prompt", "query_at_startup", "read_only", "default_value", "allowed_values")


def _variable_definition_conflicts(first: dict[str, str], second: dict[str, str]) -> bool:
    for key in _VARIABLE_CONFLICT_KEYS:
        left = str(first.get(key) or "").strip()
        right = str(second.get(key) or "").strip()
        if (left or right) and left.casefold() != right.casefold():
            return True
    return False


def _labware_label_from_step(step: dict[str, Any]) -> str:
    if step.get("operation") != "add_labware":
        return ""
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    return str(params.get("label") or step.get("target_labware") or "").strip()


def _placed_labware_labels(ir: dict[str, Any], *, steps: list[dict[str, Any]] | None = None) -> set[str]:
    """Labels already on the deck from the labware table and emitted add_labware steps."""
    labels = {
        str(item.get("label") or "").strip()
        for item in ir.get("labware") or []
        if isinstance(item, dict) and str(item.get("label") or "").strip()
    }
    for step in steps if steps is not None else (ir.get("steps") or []):
        if isinstance(step, dict):
            label = _labware_label_from_step(step)
            if label:
                labels.add(label)
    return labels


def _register_placed_labware_labels(steps: list[dict[str, Any]], placed: set[str]) -> None:
    for step in steps:
        if not isinstance(step, dict):
            continue
        label = _labware_label_from_step(step)
        if label:
            placed.add(label)


def _apply_inline_plan(
    ir: dict[str, Any],
    source_manifest: dict[str, Any],
    plan: dict[str, Any],
    report: dict[str, Any],
) -> bool:
    action_by_step_id = {id(action["step"]): action for action in plan.get("actions") or []}
    if not action_by_step_id:
        return False
    existing_labels = _placed_labware_labels(ir)
    updated_steps: list[dict[str, Any]] = []
    changed = False

    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or id(step) not in action_by_step_id:
            updated_steps.append(step)
            label = _labware_label_from_step(step)
            if label:
                existing_labels.add(label)
            continue
        action = action_by_step_id[id(step)]
        local_steps = _local_steps_for_action(
            ir,
            source_manifest,
            step,
            action,
            existing_labels=existing_labels,
        )
        if local_steps:
            updated_steps.extend(local_steps)
            _register_placed_labware_labels(local_steps, existing_labels)
            report["inlined"].append(_report_record(action, step, local_step_count=len(local_steps)))
        else:
            prompt = _fallback_prompt_step(step, action)
            updated_steps.append(prompt)
            report["fallback_prompts"].append(_report_record(action, step, local_step_count=1))
        changed = True

    if changed:
        ir["steps"] = updated_steps
    return changed


def _local_steps_for_action(
    ir: dict[str, Any],
    source_manifest: dict[str, Any],
    call_step: dict[str, Any],
    action: dict[str, Any],
    *,
    existing_labels: set[str],
) -> list[dict[str, Any]]:
    if action.get("action") != "inline":
        return []
    record = action.get("record") if isinstance(action.get("record"), dict) else {}
    path = _record_path(record)
    if path is None:
        return []
    try:
        source_ir = protocol_ir_from_xscr(path, source_name=f"inlined subroutine {action.get('ref') or record.get('ref')}")
    except Exception:
        return []
    _merge_ir_tables(ir, source_ir)

    _merge_ir_tables(ir, source_ir)
    existing_labels.update(_placed_labware_labels(ir))

    local_steps: list[dict[str, Any]] = [_inline_comment_step(call_step, action, source_path=path)]
    body_count = 0
    call_id = str(call_step.get("id") or "subroutine")
    for source_index, source_step in enumerate(source_ir.get("steps") or [], start=1):
        if not isinstance(source_step, dict):
            continue
        if _skip_duplicate_add_labware(source_step, existing_labels):
            continue
        body_count += 1
        copied = _copy_inlined_step(source_step, call_step, action, call_id=call_id, source_index=source_index)
        label = _labware_label_from_step(copied)
        if label:
            existing_labels.add(label)
        local_steps.append(copied)
    return local_steps if body_count else []


def _merge_ir_tables(ir: dict[str, Any], source_ir: dict[str, Any]) -> None:
    _merge_unique(ir.setdefault("variables", []), source_ir.get("variables") or [], keys=("name",))
    _merge_unique(ir.setdefault("labware", []), source_ir.get("labware") or [], keys=("label",))
    _merge_unique(ir.setdefault("reagents", []), source_ir.get("reagents") or [], keys=("name",))
    _merge_unique(ir.setdefault("liquid_classes", []), source_ir.get("liquid_classes") or [], keys=("name",))
    dependencies = [
        item
        for item in source_ir.get("dependencies") or []
        if not _is_script_dependency(item)
    ]
    _merge_unique(ir.setdefault("dependencies", []), dependencies, keys=("kind", "name", "guid"))


def _merge_unique(target: list[Any], incoming: list[Any], *, keys: tuple[str, ...]) -> None:
    existing = {
        tuple(str(item.get(key) or "").casefold() for key in keys)
        for item in target
        if isinstance(item, dict)
    }
    for item in incoming:
        if not isinstance(item, dict):
            continue
        key = tuple(str(item.get(name) or "").casefold() for name in keys)
        if key in existing:
            continue
        target.append(copy.deepcopy(item))
        existing.add(key)


def _skip_duplicate_add_labware(step: dict[str, Any], existing_labels: set[str]) -> bool:
    label = _labware_label_from_step(step)
    return bool(label and label in existing_labels)


def _copy_inlined_step(
    source_step: dict[str, Any],
    call_step: dict[str, Any],
    action: dict[str, Any],
    *,
    call_id: str,
    source_index: int,
) -> dict[str, Any]:
    step = copy.deepcopy(source_step)
    step["id"] = f"{call_id}_inline_{source_index:03d}"
    step["group"] = str(call_step.get("group") or step.get("group") or "Verification")
    step["index"] = call_step.get("index")
    step["inlined_from_subroutine"] = action.get("ref")
    flags = list(step.get("safety_flags") or [])
    if INLINE_SAFETY_FLAG not in flags:
        flags.append(INLINE_SAFETY_FLAG)
    step["safety_flags"] = flags
    source_path = str(step.get("compiled_path") or step.get("source_path") or "").strip()
    step["source_path"] = (
        f"Inlined from {action.get('ref')}: {source_path}"
        if source_path
        else f"Inlined from {action.get('ref')}"
    )
    return step


def _inline_comment_step(call_step: dict[str, Any], action: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    call_id = str(call_step.get("id") or "subroutine")
    return {
        "command_id": "CommentStatement",
        "group": str(call_step.get("group") or "Verification"),
        "id": f"{call_id}_inlined_notice",
        "index": call_step.get("index"),
        "name": "Inlined subroutine",
        "operation": "comment",
        "parameters": {
            "comment": (
                f"Inlined local copy of subroutine `{action.get('ref')}` because "
                f"{action.get('message')}. The generated script does not call the external "
                f"subroutine; source copy: {source_path}."
            ),
            "reason": action.get("reason"),
            "subroutine": action.get("ref"),
        },
        "safety_flags": [INLINE_SAFETY_FLAG],
    }


def _fallback_prompt_step(call_step: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
    call_id = str(call_step.get("id") or "subroutine")
    ref = str(action.get("ref") or "selected subroutine")
    return {
        "command_id": "UserPromptStatement",
        "group": str(call_step.get("group") or "Verification"),
        "id": f"{call_id}_subroutine_fallback_prompt",
        "index": call_step.get("index"),
        "name": "Verify subroutine manually",
        "operation": "prompt_user",
        "parameters": {
            "prompt": (
                f"Subroutine `{ref}` was not emitted because {action.get('message')}. "
                "Use the original source method or instrument behavior as reference, perform or "
                "visually verify the equivalent local action, then continue."
            ),
            "timeout": 0,
            "reason": action.get("reason"),
            "subroutine": ref,
        },
        "safety_flags": [FALLBACK_PROMPT_SAFETY_FLAG],
    }


def _report_record(action: dict[str, Any], step: dict[str, Any], *, local_step_count: int) -> dict[str, Any]:
    record = {
        "subroutine": action.get("ref"),
        "step_id": step.get("id"),
        "step_index": step.get("index"),
        "reason": action.get("reason"),
        "message": action.get("message"),
        "local_step_count": local_step_count,
    }
    macros = action.get("legacy_driver_macros")
    if macros:
        record["legacy_driver_macros"] = [
            {
                key: item.get(key)
                for key in ("command_name", "module_name", "nested_subroutine", "line_number")
                if item.get(key)
            }
            for item in macros
            if isinstance(item, dict)
        ]
    conflicts = action.get("variable_conflicts")
    if conflicts:
        record["variable_conflicts"] = [
            {
                key: item.get(key)
                for key in (
                    "name",
                    "main_scope",
                    "main_type",
                    "main_query_prompt",
                    "main_default_value",
                    "sub_scope",
                    "sub_type",
                    "sub_query_prompt",
                    "sub_default_value",
                )
                if item.get(key)
            }
            for item in conflicts
            if isinstance(item, dict)
        ]
    return {key: value for key, value in record.items() if value not in (None, "", [], {})}


def _record_path(record: dict[str, Any]) -> Path | None:
    for key in ("path", "resolved_path", "extracted_path"):
        raw = str(record.get(key) or "").strip()
        if not raw:
            continue
        path = Path(raw).expanduser()
        if path.exists():
            return path.resolve()
    return None


def _remove_inlined_subroutine_dependencies(ir: dict[str, Any], report: dict[str, Any]) -> None:
    inlined_refs = {
        norm_subroutine_key(item.get("subroutine"))
        for key in ("inlined", "fallback_prompts")
        for item in report.get(key) or []
        if isinstance(item, dict)
    }
    inlined_refs.update(
        norm_subroutine_key(str(item.get("subroutine") or "").rsplit("\\", 1)[-1])
        for key in ("inlined", "fallback_prompts")
        for item in report.get(key) or []
        if isinstance(item, dict)
    )
    inlined_refs.discard("")
    if not inlined_refs:
        return
    dependencies = []
    for item in ir.get("dependencies") or []:
        if isinstance(item, dict) and _is_script_dependency(item):
            names = {
                norm_subroutine_key(item.get("name")),
                norm_subroutine_key(str(item.get("name") or "").rsplit("\\", 1)[-1]),
            }
            if names.intersection(inlined_refs):
                continue
        dependencies.append(item)
    ir["dependencies"] = dependencies


def _is_script_dependency(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    kind = str(item.get("kind") or "").casefold()
    role = str(item.get("dependency_role") or "").casefold()
    return kind in {"script", "subroutine"} or role == "subroutine"


def _add_inlining_assumption(ir: dict[str, Any]) -> None:
    assumptions = ir.setdefault("safety_assumptions", [])
    if any(isinstance(item, dict) and item.get("id") == SUBROUTINE_INLINING_ASSUMPTION_ID for item in assumptions):
        return
    assumptions.append(
        {
            "id": SUBROUTINE_INLINING_ASSUMPTION_ID,
            "text": (
                "Problematic subroutine calls were not emitted as external Script dependencies. "
                "The workflow inlined parseable local commands or inserted explicit manual "
                "verification prompts when a subroutine body could not be safely represented."
            ),
        }
    )


def _store_report(ir: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    cleaned = _clean_report(report)
    if isinstance(ir, dict):
        source = ir.setdefault("source", {})
        source["subroutine_inlining"] = cleaned
    return cleaned


def _clean_report(report: dict[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(report)
    for key in ("inlined", "fallback_prompts", "preserved", "skipped"):
        cleaned[key] = [item for item in cleaned.get(key) or [] if item]
    cleaned["inlined_count"] = len(cleaned["inlined"])
    cleaned["fallback_prompt_count"] = len(cleaned["fallback_prompts"])
    cleaned["preserved_count"] = len(cleaned["preserved"])
    return cleaned


def _renumber_steps(ir: dict[str, Any]) -> None:
    for index, step in enumerate(ir.get("steps") or [], start=1):
        if isinstance(step, dict):
            step["index"] = index


def variable_names_from_xscr(path: Path) -> dict[str, dict[str, str]]:
    """Small public helper for future repair passes that need variable conflict checks."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}
    definitions: dict[str, dict[str, str]] = {}
    for el in root.iter():
        if _local_name(el.tag) != "anyType":
            continue
        if "VariableDefinitionHelper" not in " ".join(str(value) for value in el.attrib.values()):
            continue
        name = _first_text(el, "Name")
        if not name:
            continue
        definitions[name] = {
            "name": name,
            "scope": _first_text(el, "Scope"),
            "type": _first_text(el, "TypeName"),
            "query_prompt": _first_text(el, "QueryOnStartupString"),
            "query_at_startup": _first_text(el, "QueryOnStartup"),
            "read_only": _first_text(el, "ReadOnly"),
            "default_value": _leaf_text(el, "Values"),
            "allowed_values": _first_text(el, "AllowedValues"),
        }
    return definitions


def _leaf_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) != name:
            continue
        leaves = [
            re.sub(r"\s+", " ", descendant.text or "").strip()
            for descendant in element.iter()
            if descendant is not element and (descendant.text or "").strip()
        ]
        if leaves:
            return ";".join(leaves)
        return re.sub(r"\s+", " ", element.text or "").strip()
    return ""


def _first_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return re.sub(r"\s+", " ", element.text or "").strip()
    return ""


def _local_name(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]
