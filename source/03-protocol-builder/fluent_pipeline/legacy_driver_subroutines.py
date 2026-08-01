"""Legacy driver macro detection and IR annotation for subroutine calls.

When a ``call_subroutine`` step resolves to a script tree that contains
``LegacyDriverMacro`` commands, subroutine_load_review flags the dependency for review.
This module keeps those subroutine calls in the IR and injects an operator-facing
``comment`` step immediately before each affected call so authors do not need to
hand-build device-specific fallback variants (for example NoBCR prompt-only paths).
"""

from __future__ import annotations

from . import xml_compat as ET
from pathlib import Path
from typing import Any

from .api_v2.command_summary import enrich_subroutine_load_review_record
from .subroutine_dependencies import (
    clean_subroutine_reference,
    find_subroutine_record,
    norm_subroutine_key,
    resolve_subroutine_dependencies,
)

LEGACY_DRIVER_COMMENT_REASON = "legacy_driver_dependency_notice"
LEGACY_DRIVER_SAFETY_FLAG = "legacy_driver_dependency_notice"
LEGACY_DRIVER_ASSUMPTION_ID = "legacy_driver_subroutine_dependencies"


def legacy_driver_macros_in_subroutines(
    resolved_dependencies: list[dict[str, Any]],
    ir: dict[str, Any] | None,
    source_manifest: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Walk resolved subroutine trees and collect ``LegacyDriverMacro`` findings."""
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    scripts = [script for script in (source_manifest or {}).get("scripts") or [] if isinstance(script, dict)]
    for dependency in resolved_dependencies:
        root_ref = clean_subroutine_reference(dependency.get("ref") or dependency.get("object_name"))
        queue: list[tuple[dict[str, Any], list[str]]] = [(dependency, [root_ref] if root_ref else [])]
        visited_nodes: set[str] = set()
        while queue:
            current, chain = queue.pop(0)
            path_text = str(current.get("path") or "")
            if not path_text:
                continue
            path = Path(path_text)
            node_key = str(path.resolve() if path.exists() else path)
            if node_key in visited_nodes:
                continue
            visited_nodes.add(node_key)
            if not path.exists():
                continue
            try:
                root = ET.parse(path).getroot()
            except Exception:
                continue
            actual_ref = clean_subroutine_reference(current.get("ref") or current.get("object_name"))
            for macro in root.iter():
                if _local_name(macro.tag) != "LegacyDriverMacro":
                    continue
                command_name = str(macro.attrib.get("Name") or "").strip()
                module_name = str(macro.attrib.get("ModuleName") or "").strip()
                line_number = str(macro.attrib.get("LineNumber") or "").strip()
                key = (
                    root_ref,
                    actual_ref,
                    command_name,
                    module_name,
                    line_number,
                )
                if key in seen:
                    continue
                seen.add(key)
                record = {
                    "subroutine": root_ref or actual_ref,
                    "object_name": current.get("object_name") or "",
                    "nested_subroutine": actual_ref,
                    "dependency_chain": [item for item in chain if item],
                    "path": path_text,
                    "command_name": command_name,
                    "module_name": module_name,
                    "line_number": line_number,
                    "reason": "called_subroutine_legacy_driver_macro",
                    "detail": (
                        "This local/non-instrument computer may not have the corresponding legacy "
                        "driver installed or configured. The routine may still load on the host "
                        "instrument PC if that driver is installed there; test on the instrument PC "
                        "before removing the automated path. If the host PC also reports the command "
                        "as unknown, install/configure the legacy driver there."
                    ),
                }
                findings.append(enrich_subroutine_load_review_record(record, ir))

            for nested in _script_references_from_xscr(root):
                nested_ref = clean_subroutine_reference(nested.get("object_name") or nested.get("guid"))
                if not nested_ref:
                    continue
                match, _alternatives = find_subroutine_record(source_manifest or {}, scripts, nested_ref, current)
                if match is None:
                    continue
                nested_record = _subroutine_dependency_record_for_validation(nested_ref, match, source_manifest or {})
                next_ref = clean_subroutine_reference(nested_record.get("ref") or nested_record.get("object_name"))
                queue.append((nested_record, [*chain, next_ref] if next_ref else chain))
    return findings


def annotate_legacy_driver_subroutine_comments(
    ir: dict[str, Any],
    source_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Insert dependency comments before subroutine calls that use legacy driver macros."""
    if not isinstance(ir, dict) or not isinstance(source_manifest, dict):
        return ir
    if not (source_manifest.get("scripts") or []):
        return ir

    resolution = resolve_subroutine_dependencies(ir, source_manifest)
    resolved = resolution.get("resolved") or []
    if not resolved:
        return ir

    findings = legacy_driver_macros_in_subroutines(resolved, ir, source_manifest)
    if not findings:
        return ir

    grouped: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        root_ref = clean_subroutine_reference(finding.get("subroutine") or finding.get("object_name"))
        if not root_ref:
            continue
        grouped.setdefault(norm_subroutine_key(root_ref), []).append({**finding, "subroutine": root_ref})

    if not grouped:
        return ir

    updated_steps: list[dict[str, Any]] = []
    annotated: list[dict[str, Any]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            updated_steps.append(step)
            continue
        if str(step.get("operation") or "") != "call_subroutine":
            updated_steps.append(step)
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        subroutine_ref = clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine"))
        macros = grouped.get(norm_subroutine_key(subroutine_ref)) if subroutine_ref else None
        if not macros:
            updated_steps.append(step)
            continue
        if updated_steps and _is_legacy_driver_notice_step(updated_steps[-1], subroutine_ref):
            updated_steps.append(step)
            continue
        comment_step = _legacy_driver_comment_step(step, subroutine_ref, macros)
        updated_steps.append(comment_step)
        updated_steps.append(step)
        annotated.append(
            {
                "subroutine": subroutine_ref,
                "step_id": comment_step.get("id"),
                "macro_count": len(macros),
                "macros": _macro_summaries(macros),
            }
        )

    if not annotated:
        return ir

    ir["steps"] = updated_steps
    _renumber_ir_steps(ir)
    source = ir.setdefault("source", {})
    source["legacy_driver_annotations"] = {
        "subroutines": annotated,
        "macro_count": len(findings),
    }
    assumptions = ir.setdefault("safety_assumptions", [])
    if not any(isinstance(item, dict) and item.get("id") == LEGACY_DRIVER_ASSUMPTION_ID for item in assumptions):
        assumptions.append(
            {
                "id": LEGACY_DRIVER_ASSUMPTION_ID,
                "text": (
                    "One or more subroutine calls depend on legacy driver macros inside the called "
                    "script tree. Dependency comment steps were injected immediately before those "
                    "calls; confirm the drivers are installed on the target instrument PC before "
                    "simulation or hardware run."
                ),
            }
        )
    return ir


def legacy_driver_macros_from_validation_report(validation_report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(validation_report, dict):
        return []
    for gate in validation_report.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        if gate.get("gate") != "subroutine_calls_resolve" and gate.get("id") != "subroutine_calls_resolve":
            continue
        details = gate.get("details") if isinstance(gate.get("details"), dict) else {}
        macros = details.get("legacy_driver_macros")
        if isinstance(macros, list):
            return [item for item in macros if isinstance(item, dict)]
    return []


def validation_diff_check_for_legacy_driver_subroutines(
    protocol_ir: dict[str, Any],
    validation_report: dict[str, Any] | None,
) -> dict[str, Any]:
    """Surface legacy driver dependencies in ``validation_diff.md``."""
    annotations = (protocol_ir.get("source") or {}).get("legacy_driver_annotations")
    gate_macros = legacy_driver_macros_from_validation_report(validation_report)
    injected = (annotations or {}).get("subroutines") if isinstance(annotations, dict) else None
    injected = injected if isinstance(injected, list) else []

    if not gate_macros and not injected:
        return {
            "id": "legacy_driver_subroutine_dependencies",
            "status": "passed",
            "summary": "No legacy driver macros were found in called subroutine trees.",
            "details": {},
        }

    subroutine_count = len({item.get("subroutine") for item in (gate_macros or injected) if item.get("subroutine")})
    macro_names = sorted(
        {
            str(item.get("command_name") or "").strip()
            for item in gate_macros
            if str(item.get("command_name") or "").strip()
        }
    )
    summary = (
        f"Legacy driver macro(s) found in {subroutine_count or len(injected)} subroutine tree(s). "
        "Subroutine calls were kept; dependency comment step(s) were injected before each affected call."
    )
    return {
        "id": "legacy_driver_subroutine_dependencies",
        "status": "passed",
        "summary": summary,
        "details": {
            "needs_review": True,
            "legacy_driver_macros": gate_macros,
            "injected_comments": injected,
            "macro_names": macro_names,
        },
    }


def _legacy_driver_comment_step(
    call_step: dict[str, Any],
    subroutine_ref: str,
    macros: list[dict[str, Any]],
) -> dict[str, Any]:
    call_id = str(call_step.get("id") or "subroutine_call")
    return {
        "command_id": "CommentStatement",
        "group": str(call_step.get("group") or "Verification"),
        "id": f"{call_id}_legacy_driver_notice",
        "index": call_step.get("index"),
        "name": "Device driver check",
        "operation": "comment",
        "parameters": {
            "comment": _legacy_driver_comment_text(subroutine_ref, macros),
            "reason": LEGACY_DRIVER_COMMENT_REASON,
            "subroutine": subroutine_ref,
        },
        "safety_flags": [LEGACY_DRIVER_SAFETY_FLAG],
    }


def _legacy_driver_comment_text(subroutine_ref: str, macros: list[dict[str, Any]]) -> str:
    return "Check device driver on instrument PC before running."


def _macro_summaries(macros: list[dict[str, Any]]) -> list[str]:
    bits: list[str] = []
    seen: set[tuple[str, str]] = set()
    for macro in macros:
        command_name = str(macro.get("command_name") or "").strip()
        module_name = str(macro.get("module_name") or "").strip()
        if not command_name:
            continue
        key = (command_name, module_name)
        if key in seen:
            continue
        seen.add(key)
        bits.append(f"{command_name} ({module_name})" if module_name else command_name)
    return bits


def _is_legacy_driver_notice_step(step: dict[str, Any], subroutine_ref: str) -> bool:
    if str(step.get("operation") or "") != "comment":
        return False
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    if params.get("reason") == LEGACY_DRIVER_COMMENT_REASON:
        return norm_subroutine_key(params.get("subroutine")) == norm_subroutine_key(subroutine_ref)
    flags = step.get("safety_flags") or []
    if LEGACY_DRIVER_SAFETY_FLAG not in flags:
        return False
    return norm_subroutine_key(params.get("subroutine")) == norm_subroutine_key(subroutine_ref)


def _renumber_ir_steps(ir: dict[str, Any]) -> None:
    for index, step in enumerate(ir.get("steps") or [], start=1):
        if isinstance(step, dict):
            step["index"] = index


def _script_references_from_xscr(root: ET.Element) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for ref in root.iter():
        if _local_name(ref.tag) != "Reference":
            continue
        if _first_xml_text(ref, "TypeId") != "Script":
            continue
        refs.append(
            {
                "object_name": clean_subroutine_reference(_first_xml_text(ref, "ObjectName")),
                "guid": _first_xml_text(ref, "Guid") or _first_xml_text(ref, "ObjectGUID"),
            }
        )
    return [ref for ref in refs if ref.get("object_name") or ref.get("guid")]


def _subroutine_dependency_record_for_validation(
    ref: str,
    script: dict[str, Any],
    source_manifest: dict[str, Any],
) -> dict[str, Any]:
    raw_path = script.get("resolved_path") or script.get("extracted_path") or script.get("entry") or ""
    path = Path(str(raw_path)).expanduser() if raw_path else None
    if path is not None and not path.is_absolute():
        root = Path(str(script.get("context_root") or source_manifest.get("root") or ""))
        path = root / path if str(root) else path
    return {
        "ref": ref,
        "object_name": str(script.get("object_name") or ref.rsplit("\\", 1)[-1]),
        "guid": str(script.get("guid") or script.get("script_guid") or ""),
        "entry": str(script.get("entry") or ""),
        "source_context": str(script.get("source_context") or ""),
        "path": str(path.resolve() if path is not None and path.exists() else path or ""),
    }


def _first_xml_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return (element.text or "").strip()
    return ""


def _local_name(tag: Any) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1]
