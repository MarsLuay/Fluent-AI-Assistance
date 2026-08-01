"""Subroutine identity audit for api-v2-040 (subroutine_load_review / compile fixup parity).

Maps ``call_subroutine`` IR steps to typed ``Subroutine.ToXML()``, diffs compiled
``SubRoutineStatement`` payloads, and checks injected ``Reference`` GUID blocks
against the source manifest (same model as ``validate_compiled_subroutine_references``).
"""

from __future__ import annotations

from .. import xml_compat as ET
from pathlib import Path
from typing import Any, Mapping

from ..subroutine_dependencies import (
    clean_subroutine_reference,
    norm_subroutine_key,
    resolve_subroutine_dependencies,
    validate_compiled_subroutine_references,
)
from ..subroutine_variable_mappings import (
    build_script_lookup_from_manifest,
    mapping_pairs,
    subroutine_mappings_match_for_parity,
    valid_mapping_targets_for_subroutine,
)
from .commands import Subroutine, command_from_xscr_object, subroutine_from_ir_step
from .commands import _execution_mode_from_statement, _variable_mappings_from_statement
from .xml_compare import compare_command_xml, normalize_command_xml


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(element: ET.Element | None, name: str, default: str = "") -> str:
    if element is None:
        return default
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return default


def _find_descendant(element: ET.Element, name: str) -> ET.Element | None:
    for node in element.iter():
        if _local_name(node.tag) == name:
            return node
    return None


def _subroutine_statement_objects(root: ET.Element) -> list[ET.Element]:
    objects: list[ET.Element] = []
    for element in root.iter():
        if _local_name(element.tag) != "Object":
            continue
        object_type = str(element.attrib.get("Type") or "")
        if "SubRoutineStatement" in object_type or _find_descendant(element, "SubRoutineStatement") is not None:
            objects.append(element)
    return objects


def _ir_subroutine_steps(ir: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(ir, dict):
        return []
    return [
        step
        for step in ir.get("steps") or []
        if isinstance(step, dict) and step.get("operation") == "call_subroutine"
    ]


def _match_compiled_statement(
    statements: list[ET.Element],
    *,
    path: str,
    used: set[int],
) -> tuple[int, ET.Element] | tuple[None, None]:
    key = norm_subroutine_key(path)
    bare = norm_subroutine_key(path.rsplit("\\", 1)[-1])
    for index, element in enumerate(statements):
        if index in used:
            continue
        node = _find_descendant(element, "SubRoutineStatement")
        compiled_path = clean_subroutine_reference(_child_text(node, "SubRoutine"))
        compiled_key = norm_subroutine_key(compiled_path)
        compiled_bare = norm_subroutine_key(compiled_path.rsplit("\\", 1)[-1])
        if compiled_key in {key, bare} or compiled_bare in {key, bare}:
            return index, element
    return None, None


def compare_subroutine_step_to_compiled(
    step: Mapping[str, Any],
    compiled_object: ET.Element,
    *,
    source_manifest: Mapping[str, Any] | None = None,
    context_root: Path | str | None = None,
) -> dict[str, Any]:
    """Diff IR-derived ``Subroutine.ToXML()`` against one compiled statement."""
    ir_command = subroutine_from_ir_step(step)
    compiled_command = command_from_xscr_object(compiled_object, command_id="SubRoutineStatement")
    if not isinstance(compiled_command, Subroutine):
        return {
            "status": "error",
            "reason": "compiled_not_subroutine",
            "path": ir_command.path,
        }

    compiled_xml = ET.tostring(compiled_object, encoding="unicode")
    xml_result = compare_command_xml(compiled_xml, ir_command.to_xml())
    path_mismatch = norm_subroutine_key(ir_command.path) != norm_subroutine_key(compiled_command.path)
    mode_mismatch = (
        str(ir_command.execution_mode or "JoinSubroutine").casefold()
        != str(compiled_command.execution_mode or "JoinSubroutine").casefold()
    )
    root_path = Path(str(context_root)).expanduser() if context_root else None
    lookup = build_script_lookup_from_manifest(source_manifest, context_root=root_path)
    valid_targets = valid_mapping_targets_for_subroutine(
        ir_command.path,
        lookup,
        context_root=root_path,
    )
    mapping_mismatch = not (
        subroutine_mappings_match_for_parity(
            mapping_pairs(ir_command.variable_mappings_start),
            mapping_pairs(compiled_command.variable_mappings_start),
            valid_targets=valid_targets or None,
        )
        and subroutine_mappings_match_for_parity(
            mapping_pairs(getattr(ir_command, "variable_mappings_end", []) or []),
            mapping_pairs(compiled_command.variable_mappings_end),
            valid_targets=valid_targets or None,
        )
    )

    if not (mapping_mismatch or mode_mismatch or path_mismatch):
        return {
            "status": "matched",
            "path": ir_command.path,
            "execution_mode": ir_command.execution_mode,
            "xml_parity": xml_result.equal,
        }

    node = _find_descendant(compiled_object, "SubRoutineStatement")
    return {
        "status": "mismatch",
        "path": ir_command.path,
        "execution_mode": ir_command.execution_mode,
        "diff_hint": xml_result.diff_hint or "subroutine field mismatch",
        "path_mismatch": path_mismatch,
        "mode_mismatch": mode_mismatch,
        "mapping_mismatch": mapping_mismatch,
        "compiled_execution_mode": _execution_mode_from_statement(node),
        "compiled_mappings_start": _variable_mappings_from_statement(node, "VariableMappingsStart"),
        "ir_mappings_start": [
            {"target": item.target, "source": item.source}
            for item in ir_command.variable_mappings_start
        ],
    }


def audit_subroutine_identity(
    ir: Mapping[str, Any] | None,
    compiled_xscr: Path | None,
    source_manifest: Mapping[str, Any] | None = None,
    *,
    context_root: Path | str | None = None,
) -> dict[str, Any]:
    """Run subroutine statement + Script ``Reference`` identity checks."""
    steps = _ir_subroutine_steps(ir)
    if not steps:
        return {
            "status": "skipped",
            "summary": "Protocol calls no subroutines.",
            "issue": "api-v2-040",
            "trivial": True,
            "statement_findings": [],
            "reference_findings": [],
        }

    if compiled_xscr is None or not compiled_xscr.exists():
        return {
            "status": "needs_review",
            "summary": f"{len(steps)} subroutine call(s) present but no compiled XSCR to compare.",
            "issue": "api-v2-040",
            "statement_findings": [],
            "reference_findings": [],
            "unverified_count": len(steps),
        }

    try:
        root = ET.parse(compiled_xscr).getroot()
    except ET.ParseError as exc:
        return {
            "status": "error",
            "summary": f"Compiled XSCR parse error: {exc}",
            "issue": "api-v2-040",
            "statement_findings": [],
            "reference_findings": [],
        }

    statements = _subroutine_statement_objects(root)
    used: set[int] = set()
    statement_findings: list[dict[str, Any]] = []

    for step in steps:
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        path = clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine"))
        index, compiled_object = _match_compiled_statement(statements, path=path, used=used)
        if compiled_object is None:
            statement_findings.append(
                {
                    "status": "missing",
                    "path": path,
                    "step_index": step.get("index"),
                    "reason": "no_matching_compiled_subroutine_statement",
                }
            )
            continue
        used.add(index)
        statement_findings.append(
            compare_subroutine_step_to_compiled(
                step,
                compiled_object,
                source_manifest=source_manifest,
                context_root=context_root,
            )
        )

    resolution = resolve_subroutine_dependencies(dict(ir or {}), dict(source_manifest or {}))
    reference_findings = validate_compiled_subroutine_references(
        compiled_xscr,
        resolution.get("resolved") or [],
    )

    mismatches = [item for item in statement_findings if item.get("status") in {"mismatch", "missing", "error"}]
    if mismatches or reference_findings:
        status = "needs_review" if not reference_findings else "failed"
        if reference_findings:
            status = "failed"
        elif mismatches:
            status = "needs_review"
        summary_parts = []
        if mismatches:
            summary_parts.append(f"{len(mismatches)} subroutine statement mismatch(es)")
        if reference_findings:
            summary_parts.append(f"{len(reference_findings)} Script reference GUID issue(s)")
        summary = "; ".join(summary_parts) + "."
    else:
        status = "passed"
        summary = (
            f"Subroutine identity verified for {len(steps)} call(s): "
            f"{len([item for item in statement_findings if item.get('status') == 'matched'])} "
            "statement match(es), Script references OK."
        )

    return {
        "status": status,
        "summary": summary,
        "issue": "api-v2-040",
        "call_count": len(steps),
        "statement_findings": statement_findings,
        "reference_findings": reference_findings,
        "resolved_count": len(resolution.get("resolved") or []),
        "normalized_sample": normalize_command_xml(subroutine_from_ir_step(steps[0]).to_xml())[:120]
        if steps
        else "",
    }


def subroutine_identity_summary(audit: Mapping[str, Any]) -> dict[str, Any]:
    """Compact rollup suitable for subroutine_load_review ``details``."""
    findings = list(audit.get("statement_findings") or [])
    refs = list(audit.get("reference_findings") or [])
    matched = [item for item in findings if item.get("status") == "matched"]
    mismatches = [item for item in findings if item.get("status") == "mismatch"]
    missing = [item for item in findings if item.get("status") == "missing"]
    return {
        "status": audit.get("status"),
        "summary": audit.get("summary"),
        "issue": "api-v2-040",
        "call_count": audit.get("call_count", 0),
        "matched_count": len(matched),
        "mismatch_count": len(mismatches),
        "missing_count": len(missing),
        "reference_issue_count": len(refs),
        "findings": (findings + [{"kind": "reference", **item} for item in refs])[:20],
    }
