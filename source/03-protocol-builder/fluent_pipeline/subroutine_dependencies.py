"""Subroutine dependency resolution and validation helpers.

Subroutine calls are load-time Script dependencies in FluentControl: a generated
method can compile and import but still fail to open if the called subroutine is
missing, ambiguous, or referenced with the wrong Script GUID.  This module keeps
that dependency model shared by IR generation, packaging, and ready validation.
"""

from __future__ import annotations

import re
from . import xml_compat as ET
from pathlib import Path
from typing import Any


def clean_subroutine_reference(value: Any) -> str:
    return str(value or "").strip().strip('"').replace("/", "\\")


def norm_subroutine_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", clean_subroutine_reference(value).casefold())


def subroutine_calls_from_ir(ir: dict[str, Any] | None) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not isinstance(ir, dict):
        return calls
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        ref = clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine"))
        if not ref:
            continue
        calls.append(
            {
                "ref": ref,
                "step_id": step.get("id"),
                "step_index": step.get("index"),
                "compiled_path": step.get("compiled_path"),
                "source_path": step.get("source_path"),
            }
        )
    return calls


def upsert_ir_subroutine_dependencies(ir: dict[str, Any]) -> None:
    dependencies = ir.setdefault("dependencies", [])
    existing = {
        (
            str(item.get("kind") or "").casefold(),
            norm_subroutine_key(item.get("name")),
            str(item.get("guid") or "").casefold(),
        )
        for item in dependencies
        if isinstance(item, dict)
    }
    for call in subroutine_calls_from_ir(ir):
        ref = call["ref"]
        key = ("script", norm_subroutine_key(ref), "")
        if key in existing:
            continue
        dependencies.append(
            {
                "kind": "Script",
                "name": ref,
                "required": True,
                "source_path": call.get("compiled_path") or call.get("source_path") or "",
                "dependency_role": "subroutine",
            }
        )
        existing.add(key)


def resolve_subroutine_dependencies(
    ir: dict[str, Any] | None,
    source_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    calls = subroutine_calls_from_ir(ir)
    scripts = _source_scripts(source_manifest)
    resolved: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []

    seen: set[str] = set()
    parent_hint = _selected_source_parent_hint(ir)
    for call in calls:
        ref = call["ref"]
        key = norm_subroutine_key(ref)
        if key in seen:
            continue
        seen.add(key)
        match, alternatives = find_subroutine_record(
            source_manifest or {},
            scripts,
            ref,
            parent_hint,
        )
        if match is None:
            missing.append(call)
            continue
        record = _dependency_record(ref, match, source_manifest or {}, call=call)
        if alternatives:
            record["alternatives"] = alternatives
            ambiguous.append(record)
            continue
        resolved.append(record)

    if missing or ambiguous:
        status = "failed"
    elif calls:
        status = "passed"
    else:
        status = "passed"
    return {
        "status": status,
        "required": calls,
        "resolved": resolved,
        "missing": missing,
        "ambiguous": ambiguous,
        "trivial": not calls,
    }


def _selected_source_parent_hint(ir: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ir, dict):
        return {}
    source = ir.get("source")
    if not isinstance(source, dict):
        return {}
    selected = source.get("selected_source_scripts")
    if not isinstance(selected, list):
        return {}
    for item in selected:
        if not isinstance(item, dict):
            continue
        source_context = str(item.get("source_context") or "").strip()
        context_root = str(item.get("context_root") or "").strip()
        if source_context or context_root:
            return {
                "source_context": source_context,
                "context_root": context_root,
            }
    return {}


def validate_compiled_subroutine_references(
    xscr_path: Path | None,
    resolved_dependencies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if xscr_path is None or not xscr_path.exists() or not resolved_dependencies:
        return []
    try:
        root = ET.parse(xscr_path).getroot()
    except ET.ParseError as exc:
        return [{"reason": "compiled_xscr_parse_error", "message": str(exc)}]
    references = compiled_script_references(root)
    findings: list[dict[str, Any]] = []
    for dep in resolved_dependencies:
        ref = _matching_compiled_reference(dep, references)
        if ref is None:
            findings.append(
                {
                    "reason": "compiled_subroutine_reference_missing",
                    "message": "Compiled XSCR has no Script reference for a required subroutine call.",
                    "subroutine": dep.get("ref"),
                    "expected_object_name": dep.get("object_name"),
                    "expected_guid": dep.get("guid"),
                }
            )
            continue
        expected_guid = str(dep.get("guid") or "").strip()
        actual_guid = str(ref.get("guid") or "").strip()
        if expected_guid and actual_guid.casefold() != expected_guid.casefold():
            findings.append(
                {
                    "reason": "compiled_subroutine_guid_mismatch",
                    "message": "Compiled XSCR Script reference GUID does not match the resolved source subroutine.",
                    "subroutine": dep.get("ref"),
                    "expected_object_name": dep.get("object_name"),
                    "expected_guid": expected_guid,
                    "actual_guid": actual_guid,
                    "actual_object_name": ref.get("object_name"),
                }
            )
    return findings


def compiled_script_references(root: ET.Element) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for ref in _elements_by_local_name(root, "Reference"):
        type_id = _first_text(ref, "TypeId")
        if type_id != "Script":
            continue
        object_name = clean_subroutine_reference(_first_text(ref, "ObjectName"))
        guid = _first_text(ref, "Guid")
        if object_name or guid:
            refs.append({"type_id": "Script", "object_name": object_name, "guid": guid})
    return refs


def find_subroutine_record(
    source_manifest: dict[str, Any],
    scripts: list[dict[str, Any]],
    ref: str,
    parent: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    clean = clean_subroutine_reference(ref)
    if not clean:
        return None, []
    normalized_path = clean.replace("\\", "/")
    name = Path(normalized_path).name
    forms = {
        "clean": clean.casefold(),
        "normalized_path": normalized_path.casefold(),
        "name": name.casefold(),
        "stem": Path(name).stem.casefold(),
    }
    scored = [
        (strength, script)
        for script, strength in ((script, _match_strength(script, forms)) for script in scripts)
        if strength
    ]
    if not scored:
        return None, []
    best_strength = max(strength for strength, _ in scored)
    matches = [script for strength, script in scored if strength == best_strength]
    matches = _dedupe_equivalent_script_records(matches)

    parent = parent or {}
    parent_context = str(parent.get("source_context") or "")
    if parent_context and len(matches) > 1:
        same_context = [script for script in matches if str(script.get("source_context") or "") == parent_context]
        if same_context:
            matches = same_context
    if len(matches) > 1:
        parent_root = str(parent.get("context_root") or source_manifest.get("root") or "")
        same_root = [
            script
            for script in matches
            if str(script.get("context_root") or source_manifest.get("root") or "") == parent_root
        ]
        if same_root:
            matches = same_root
    if len(matches) == 1:
        return matches[0], []

    matches = sorted(
        matches,
        key=lambda script: (
            str(script.get("source_context") or ""),
            str(script.get("qualified_name") or script.get("object_name") or ""),
            str(script.get("entry") or script.get("extracted_path") or ""),
        ),
    )
    chosen = matches[0]
    alternatives = [_alternative_record(script) for script in matches[1:]]
    return chosen, alternatives


def _dedupe_equivalent_script_records(
    scripts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collapse duplicate context records for the same FluentControl Script identity."""
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for script in scripts:
        guid = str(script.get("guid") or script.get("script_guid") or "").strip().casefold()
        if guid:
            identity = ("guid", guid)
        else:
            entry = str(script.get("entry") or script.get("extracted_path") or "")
            identity = (
                "entry",
                entry.replace("\\", "/").casefold()
                + "|"
                + str(script.get("object_name") or "").casefold()
                + "|"
                + str(script.get("source_context") or script.get("context_root") or "").casefold(),
            )
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(script)
    return unique


def subroutine_dependency_records_from_artifacts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = []
    for item in items:
        if not isinstance(item, dict):
            continue
        records.append(
            {
                "ref": item.get("ref"),
                "object_name": item.get("object_name"),
                "folder": item.get("folder"),
                "guid": item.get("guid"),
                "entry": item.get("entry"),
                "version": item.get("version") or item.get("script_version"),
                "source_context": item.get("source_context"),
                "path": str(item.get("path") or ""),
                "ambiguous": bool(item.get("ambiguous")),
                "alternatives": item.get("alternatives") or [],
            }
        )
    return [record for record in records if record.get("ref") or record.get("object_name")]


def _source_scripts(source_manifest: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(source_manifest, dict):
        return []
    return [script for script in source_manifest.get("scripts") or [] if isinstance(script, dict)]


def _dependency_record(
    ref: str,
    script: dict[str, Any],
    source_manifest: dict[str, Any],
    *,
    call: dict[str, Any] | None = None,
) -> dict[str, Any]:
    entry = str(script.get("entry") or "")
    folder = str(script.get("folder") or script.get("object_subfolder_path") or "")
    if not folder and entry:
        parent = Path(entry.replace("\\", "/")).parent.as_posix()
        folder = "" if parent == "." else parent.replace("/", "\\")
    path = _manifest_script_path(source_manifest, script)
    record = {
        "ref": ref,
        "object_name": str(script.get("object_name") or Path(entry).stem or ref.rsplit("\\", 1)[-1]),
        "folder": folder,
        "guid": str(script.get("guid") or script.get("script_guid") or ""),
        "entry": entry,
        "version": str(script.get("script_version") or script.get("version") or ""),
        "source_context": str(script.get("source_context") or ""),
        "path": str(path) if path is not None else "",
    }
    if call:
        record.update({key: value for key, value in call.items() if key not in {"ref"} and value not in (None, "", [], {})})
    return {key: value for key, value in record.items() if value not in (None, "", [], {})}


def _manifest_script_path(source_manifest: dict[str, Any], script: dict[str, Any]) -> Path | None:
    raw = script.get("resolved_path") or script.get("extracted_path") or script.get("entry") or ""
    if not raw:
        return None
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path.resolve()
    root = Path(str(script.get("context_root") or source_manifest.get("root") or ""))
    return (root / path).resolve() if str(root) else path


def _match_strength(script: dict[str, Any], forms: dict[str, str]) -> int:
    object_name = str(script.get("object_name") or "").casefold()
    qualified = str(script.get("qualified_name") or "").replace("\\", "/").casefold()
    entry = str(script.get("entry") or "").replace("\\", "/").casefold()
    extracted = str(script.get("extracted_path") or "").replace("\\", "/").casefold()
    folder = str(script.get("folder") or script.get("object_subfolder_path") or "").replace("\\", "/").casefold()
    folder_object = f"{folder}/{object_name}".strip("/") if folder and object_name else ""
    entry_name = Path(entry).name if entry else ""
    extracted_name = Path(extracted).name if extracted else ""
    entry_stem = Path(entry).stem if entry else ""
    extracted_stem = Path(extracted).stem if extracted else ""

    clean = forms["clean"]
    normalized_path = forms["normalized_path"]
    name = forms["name"]
    stem = forms["stem"]

    if object_name and object_name in {clean, name}:
        return 5
    if folder_object and folder_object in {clean.replace("\\", "/"), normalized_path}:
        return 5
    if qualified and qualified in {clean, normalized_path}:
        return 5
    if normalized_path and normalized_path in {entry, extracted}:
        return 4
    if name and name in {entry_name, extracted_name}:
        return 3
    if stem and stem in {entry_stem, extracted_stem}:
        return 2
    return 0


def _alternative_record(script: dict[str, Any]) -> dict[str, str]:
    return {
        key: value
        for key, value in {
            "object_name": str(script.get("object_name") or ""),
            "entry": str(script.get("entry") or ""),
            "source_context": str(script.get("source_context") or ""),
            "guid": str(script.get("guid") or script.get("script_guid") or ""),
        }.items()
        if value
    }


def _matching_compiled_reference(dep: dict[str, Any], references: list[dict[str, str]]) -> dict[str, str] | None:
    names = {
        norm_subroutine_key(dep.get("ref")),
        norm_subroutine_key(dep.get("object_name")),
        norm_subroutine_key(str(dep.get("ref") or "").rsplit("\\", 1)[-1]),
    }
    names.discard("")
    for ref in references:
        object_name = str(ref.get("object_name") or "")
        if norm_subroutine_key(object_name) in names or norm_subroutine_key(object_name.rsplit("\\", 1)[-1]) in names:
            return ref
    return None


def _elements_by_local_name(root: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in root.iter() if _local_name(element.tag) == name]


def _first_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return (element.text or "").strip()
    return ""


def _local_name(tag: Any) -> str:
    text = str(tag)
    return text.rsplit("}", 1)[-1]
