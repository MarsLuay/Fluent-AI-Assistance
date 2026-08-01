"""Shared post-compile XSCR finalization and verification."""

from __future__ import annotations

from dataclasses import dataclass, field
import html
import json
from pathlib import Path
import re
from typing import Any, Mapping

from . import xml_compat as ET
from .api_v2.command_validate import validate_compiled_xscr_commands
from .checksums import entry_checksum_state, recompute_checksum_bytes
from .prompt_media import ensure_compiled_prompt_media_references
from .protocol_ir import protocol_ir_from_path, protocol_ir_from_xscr
from .protocol_ir_schema import migrate_protocol_ir
from .subroutine_dependencies import (
    clean_subroutine_reference,
    compiled_script_references,
    norm_subroutine_key,
    resolve_subroutine_dependencies,
    validate_compiled_subroutine_references,
)
from .validation import (
    _compiled_xsi_type_namespace_findings,
    _inspect_compiled_xscr,
    _setup_expression_signatures,
    _step_signatures,
)
from .variable_namespaces import localize_variable_declaration_namespaces
from .variable_reconciliation import failures_to_dicts, validate_xscr_variable_declarations
from .zeia_filesystem import ensure_script_file_references


FINALIZATION_REPORT_VERSION = "tecan.compiled_xscr_finalization.v1"
_VX_WORKSPACE_DATA_RE = re.compile(
    r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?VxWorkspaceData)\b[^>]*>.*?</(?P=tag)>",
    re.DOTALL,
)
_WORKTABLE_WORKSPACE_REFERENCE_RE = re.compile(
    r"<Reference\b[^>]*>\s*<Guid>(?P<guid>[^<]+)</Guid>\s*<TypeId>WorktableWorkspace</TypeId>",
    re.DOTALL,
)
_BASE_WORKSPACE_NAME_RE = re.compile(
    r"(<(?P<tag>(?:[A-Za-z_][\w.-]*:)?BaseWorkspaceName)\b[^>]*>)(?P<value>.*?)(</(?P=tag)>)",
    re.DOTALL,
)
_WORKSPACE_DELTA_IDENTIFIER_RE = re.compile(r"<Identifier>\s*(?P<value>[^<\s][^<]*)</Identifier>")


@dataclass
class FinalizationReport:
    schema_version: str = FINALIZATION_REPORT_VERSION
    ok: bool = False
    xscr_path: str = ""
    source_ir_origin: str = ""
    modified: bool = False
    modification_count: int = 0
    checksum_before: str = "absent"
    checksum_after: str = "absent"
    roundtrip: dict[str, Any] = field(default_factory=dict)
    inventory_summary: dict[str, Any] = field(default_factory=dict)
    command_validation: dict[str, Any] = field(default_factory=dict)
    generic_command_validation: dict[str, Any] = field(default_factory=dict)
    namespace_findings: list[dict[str, Any]] = field(default_factory=list)
    variable_declaration_failures: list[dict[str, Any]] = field(default_factory=list)
    script_reference_failures: list[dict[str, Any]] = field(default_factory=list)
    file_reference_failures: list[dict[str, Any]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def record_change(self, change_id: str, summary: str, **details: Any) -> None:
        count = int(details.get("count", 1) or 1)
        self.changes.append({"id": change_id, "summary": summary, **details})
        self.modified = True
        self.modification_count += max(count, 1)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ok": self.ok,
            "xscr_path": self.xscr_path,
            "source_ir_origin": self.source_ir_origin,
            "modified": self.modified,
            "modification_count": self.modification_count,
            "checksum_before": self.checksum_before,
            "checksum_after": self.checksum_after,
            "roundtrip": dict(self.roundtrip),
            "inventory_summary": dict(self.inventory_summary),
            "command_validation": dict(self.command_validation),
            "generic_command_validation": dict(self.generic_command_validation),
            "namespace_findings": list(self.namespace_findings),
            "variable_declaration_failures": list(self.variable_declaration_failures),
            "script_reference_failures": list(self.script_reference_failures),
            "file_reference_failures": list(self.file_reference_failures),
            "changes": list(self.changes),
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }


def derive_protocol_ir_for_finalization(
    source: Path | str | Mapping[str, Any] | None,
    *,
    compiled_xscr: Path | str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Resolve the best available source IR for compiled XSCR finalization."""
    if isinstance(source, Mapping):
        return dict(source), "provided"
    if source:
        try:
            return protocol_ir_from_path(Path(str(source))), Path(str(source)).suffix.lstrip(".") or "path"
        except Exception:
            pass
    if compiled_xscr:
        try:
            return protocol_ir_from_xscr(Path(str(compiled_xscr))), "compiled_xscr_fallback"
        except Exception:
            pass
    return None, "unavailable"


def finalize_compiled_xscr(
    xscr_path: Path | str,
    protocol_ir: Mapping[str, Any] | Path | str | None,
    source_manifest: Mapping[str, Any] | None,
    source_scripts: list[Path | str] | None,
    options: Mapping[str, Any] | None = None,
) -> FinalizationReport:
    """Finalize one compiled XSCR in place and verify the result."""
    path = Path(str(xscr_path))
    report = FinalizationReport(
        xscr_path=str(path),
        source_ir_origin=str((options or {}).get("source_ir_origin") or ""),
    )
    if not path.is_file():
        report.errors.append(f"Compiled XSCR does not exist: {path}")
        return report

    original_bytes = _safe_read_bytes(path)
    report.checksum_before = entry_checksum_state(original_bytes)
    source_ir, source_ir_origin = derive_protocol_ir_for_finalization(protocol_ir, compiled_xscr=path)
    report.source_ir_origin = report.source_ir_origin or source_ir_origin
    if source_ir is None:
        report.errors.append("Source protocol IR was not available for compiled XSCR finalization.")

    resolved_source_scripts = _resolved_source_scripts(source_scripts, source_ir)

    parse_error = _safe_parse_error(path)
    if parse_error:
        report.errors.append(f"Compiled XSCR could not be parsed before finalization: {parse_error}")
        return report

    if source_ir is not None:
        try:
            reference_fixups = _ensure_compiled_subroutine_references(path, source_ir, source_manifest)
            if reference_fixups:
                report.record_change(
                    "subroutine_reference_injection",
                    "Injected or repaired compiled Script references for called subroutines.",
                    count=len(reference_fixups),
                    entries=reference_fixups,
                )
        except Exception as exc:
            report.errors.append(f"Subroutine reference finalization failed: {exc}")

    if source_ir is not None:
        try:
            prompt_media_fixups = ensure_compiled_prompt_media_references(
                path,
                source_ir,
                recompute_checksum=False,
            )
            if prompt_media_fixups:
                report.record_change(
                    "prompt_media_reference_correction",
                    "Updated compiled prompt media references from the source IR.",
                    count=len(prompt_media_fixups),
                    entries=prompt_media_fixups,
                )
        except Exception as exc:
            report.errors.append(f"Prompt media finalization failed: {exc}")

    try:
        workspace_fixup = _copy_source_workspace_data(path, resolved_source_scripts)
        if workspace_fixup:
            report.record_change(
                "worktable_workspace_metadata_transplant",
                "Transplanted source-backed VxWorkspaceData into the compiled XSCR.",
                count=1,
                **workspace_fixup,
            )
    except Exception as exc:
        report.errors.append(f"Workspace metadata transplant failed: {exc}")

    try:
        workspace_base_fixup = _align_workspace_base_name_to_reference(path)
        if workspace_base_fixup:
            report.record_change(
                "worktable_workspace_base_reference_alignment",
                "Aligned VxWorkspaceData BaseWorkspaceName to the packaged WorktableWorkspace reference.",
                count=1,
                **workspace_base_fixup,
            )
    except Exception as exc:
        report.errors.append(f"Workspace base reference alignment failed: {exc}")

    try:
        source_metadata = source_ir.get("source") if isinstance(source_ir, dict) else {}
        recipe_authored = bool(
            isinstance(source_metadata, dict)
            and source_metadata.get("verification_recipe")
        )
        inherited_file_references = (
            [] if recipe_authored else _source_file_reference_paths(resolved_source_scripts)
        )
        inherited_file_references.extend(_compiled_external_command_paths(path))
        inherited_file_references = _dedupe_casefolded_strings(inherited_file_references)
        file_reference_fixups = ensure_script_file_references(path, inherited_file_references)
        if file_reference_fixups:
            report.record_change(
                "referenced_file_inheritance",
                "Injected missing FileReference blocks for inherited source/external paths.",
                count=len(file_reference_fixups),
                paths=file_reference_fixups,
            )
    except Exception as exc:
        report.errors.append(f"Referenced-file inheritance failed: {exc}")
        inherited_file_references = []
    else:
        inherited_file_references = inherited_file_references if "inherited_file_references" in locals() else []

    try:
        namespace_fixups = _normalize_compiled_variable_declaration_namespaces(path)
        if namespace_fixups:
            report.record_change(
                "variable_namespace_normalization",
                "Localized VariableDefinitionHelper namespaces for FluentControl InnerXml deserialization.",
                count=len(namespace_fixups),
                entries=namespace_fixups,
            )
    except Exception as exc:
        report.errors.append(f"Variable declaration namespace finalization failed: {exc}")

    try:
        text_before_checksum = _safe_read_text(path)
        text_with_checksum = _ensure_blank_checksum_element(text_before_checksum)
        if text_with_checksum != text_before_checksum:
            path.write_text(text_with_checksum, encoding="utf-8")
            report.record_change(
                "checksum_element_insertion",
                "Inserted a blank Checksum element before recomputing the compiled XSCR checksum.",
                count=1,
            )
        bytes_before_checksum = _safe_read_bytes(path)
        rewritten = recompute_checksum_bytes(bytes_before_checksum)
        if rewritten is None:
            report.errors.append("Checksum recomputation did not produce rewritten XSCR bytes.")
        else:
            checksum_before_rewrite = entry_checksum_state(bytes_before_checksum)
            if rewritten != bytes_before_checksum:
                path.write_bytes(rewritten)
                report.record_change(
                    "checksum_recompute",
                    "Recomputed the compiled XSCR checksum after finalization edits.",
                    count=1,
                    before=checksum_before_rewrite,
                    after=entry_checksum_state(rewritten),
                )
            report.checksum_after = entry_checksum_state(_safe_read_bytes(path))
            if report.checksum_after != "valid":
                report.errors.append(
                    f"Compiled XSCR checksum is not valid after recomputation: {report.checksum_after}"
                )
    except Exception as exc:
        report.errors.append(f"Checksum finalization failed: {exc}")

    text_after = _safe_read_text(path)
    report.namespace_findings = _compiled_xsi_type_namespace_findings(text_after)
    if report.namespace_findings:
        report.errors.append(
            f"Compiled XSCR still has {len(report.namespace_findings)} variable-namespace finding(s)."
        )

    report.variable_declaration_failures = failures_to_dicts(validate_xscr_variable_declarations(path))
    if report.variable_declaration_failures:
        report.errors.append(
            f"Compiled XSCR still has {len(report.variable_declaration_failures)} variable declaration failure(s)."
        )

    compiled_ir, compiled_error, inventory = _inspect_compiled_xscr(path)
    report.command_validation, inherited_command_failures = _exclude_source_inherited_command_failures(
        _trim_validation_report(inventory.get("command_validation") or {}),
        resolved_source_scripts,
    )
    if inherited_command_failures:
        report.warnings.append(
            "Ignored "
            f"{len(inherited_command_failures)} unchanged offline command-validation finding(s) "
            "already present in the source XSCR."
        )
    report.generic_command_validation = _trim_validation_report(
        inventory.get("generic_command_validation") or {}
    )
    report.inventory_summary = {
        "command_ids": len(inventory.get("command_ids") or []),
        "unsupported_commands": len(inventory.get("unsupported_commands") or []),
        "fluentcontrol_findings": len(inventory.get("fluentcontrol_findings") or []),
        "error": inventory.get("error"),
    }
    if compiled_error:
        report.errors.append(f"Compiled XSCR could not be converted back to canonical IR: {compiled_error}")
    if int(report.command_validation.get("failure_count", 0) or 0):
        report.errors.append(
            f"Compiled command validation reported {report.command_validation['failure_count']} failure(s)."
        )
    if int(report.generic_command_validation.get("failure_count", 0) or 0):
        report.errors.append(
            f"Compiled generic command validation reported {report.generic_command_validation['failure_count']} failure(s)."
        )

    report.script_reference_failures = _compiled_script_reference_failures(
        path,
        source_ir,
        source_manifest,
    )
    if report.script_reference_failures:
        report.errors.append(
            f"Compiled XSCR still has {len(report.script_reference_failures)} Script reference failure(s)."
        )

    report.file_reference_failures = _compiled_file_reference_failures(path, inherited_file_references)
    if report.file_reference_failures:
        report.errors.append(
            f"Compiled XSCR still has {len(report.file_reference_failures)} FileReference failure(s)."
        )

    report.roundtrip = _roundtrip_report(source_ir, compiled_ir)
    if not report.roundtrip.get("matched", False):
        report.errors.append("Compiled XSCR canonical IR does not match the source protocol IR.")

    if report.source_ir_origin == "compiled_xscr_fallback":
        report.warnings.append(
            "Source IR could not be derived from the original input; roundtrip used the compiled XSCR fallback."
        )
    if not resolved_source_scripts:
        report.warnings.append(
            "No source XSCR paths were available, so workspace-metadata transplant and source FileReference inheritance were best-effort only."
        )
    if not report.checksum_after:
        report.checksum_after = entry_checksum_state(_safe_read_bytes(path))
    report.ok = not report.errors
    return report


def render_compiled_xscr_finalization_markdown(report: FinalizationReport) -> str:
    """Render a compact Markdown section for compile/finalization reports."""
    lines = [
        "## Compiled XSCR Finalization",
        "",
        f"- Status: `{'passed' if report.ok else 'failed'}`",
        f"- Modified: `{'yes' if report.modified else 'no'}`",
        f"- Modifications applied: `{report.modification_count}`",
        f"- Checksum: `{report.checksum_before}` -> `{report.checksum_after}`",
        f"- Source IR origin: `{report.source_ir_origin or 'unknown'}`",
        f"- Roundtrip match: `{bool(report.roundtrip.get('matched'))}`",
        f"- Command validation failures: `{report.command_validation.get('failure_count', 0)}`",
        f"- Source-inherited command findings: `{report.command_validation.get('inherited_failure_count', 0)}`",
        f"- Generic command validation failures: `{report.generic_command_validation.get('failure_count', 0)}`",
        "",
    ]
    if report.changes:
        lines.extend(["### Applied Changes", ""])
        for item in report.changes:
            lines.append(f"- `{item.get('id')}`: {item.get('summary')}")
        lines.append("")
    if report.warnings:
        lines.extend(["### Warnings", ""])
        for warning in report.warnings:
            lines.append(f"- {warning}")
        lines.append("")
    if report.errors:
        lines.extend(["### Failures", ""])
        for error in report.errors:
            lines.append(f"- {error}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _resolved_source_scripts(
    source_scripts: list[Path | str] | None,
    protocol_ir: Mapping[str, Any] | None,
) -> list[Path]:
    from .project_context import filter_generation_source_script_records, resolve_recorded_script_path

    resolved: list[Path] = []
    for raw in source_scripts or []:
        candidate = Path(str(raw))
        if candidate.is_file():
            resolved.append(candidate.resolve())
    source = protocol_ir.get("source") if isinstance(protocol_ir, dict) else {}
    for record in filter_generation_source_script_records(
        source.get("selected_source_scripts") or [] if isinstance(source, dict) else [],
        protocol_ir,
    ):
        if not isinstance(record, dict):
            continue
        candidate = resolve_recorded_script_path(record)
        if candidate is not None:
            resolved.append(candidate)
    seen: set[str] = set()
    deduped: list[Path] = []
    for item in resolved:
        key = str(item).casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _trim_validation_report(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "provider": report.get("provider"),
        "command_count": report.get("command_count", 0),
        "failure_count": report.get("failure_count", 0),
        "summary": report.get("summary", ""),
        "failures": list(report.get("failures") or []),
    }


def _roundtrip_report(
    source_ir: Mapping[str, Any] | None,
    compiled_ir: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(source_ir, dict):
        return {"matched": False, "reason": "source_ir_missing"}
    if not isinstance(compiled_ir, dict):
        return {"matched": False, "reason": "compiled_ir_missing"}
    canonical_source = migrate_protocol_ir(dict(source_ir))
    canonical_compiled = migrate_protocol_ir(dict(compiled_ir))
    expected = _step_signatures(canonical_source)
    actual = _step_signatures(canonical_compiled)
    expected_setup_expressions = _setup_expression_signatures(canonical_source)
    actual_setup_expressions = _setup_expression_signatures(canonical_compiled)
    setup_expressions_match = not expected_setup_expressions or all(
        actual_setup_expressions.get(label) == expressions
        for label, expressions in expected_setup_expressions.items()
    )
    matched = expected == actual and setup_expressions_match
    report = {
        "matched": matched,
        "expected_step_count": len(expected),
        "actual_step_count": len(actual),
    }
    if not matched:
        report["expected"] = expected[:20]
        report["actual"] = actual[:20]
        report["expected_setup_expressions"] = expected_setup_expressions
        report["actual_setup_expressions"] = actual_setup_expressions
    return report


def _compiled_script_reference_failures(
    xscr_path: Path,
    protocol_ir: Mapping[str, Any] | None,
    source_manifest: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(protocol_ir, dict):
        return []
    resolved = (
        resolve_subroutine_dependencies(dict(protocol_ir), dict(source_manifest))
        if isinstance(source_manifest, dict)
        else {"resolved": []}
    )
    findings = list(validate_compiled_subroutine_references(xscr_path, resolved.get("resolved") or []))
    parse_error = _safe_parse_error(xscr_path)
    if parse_error:
        return findings
    root = ET.parse(xscr_path).getroot()
    referenced = {item.get("object_name", "") for item in compiled_script_references(root)}
    seen_missing = {
        (str(item.get("reason") or ""), clean_subroutine_reference(item.get("subroutine")))
        for item in findings
        if item.get("reason")
    }
    for subroutine in _required_subroutine_references(protocol_ir):
        clean = clean_subroutine_reference(subroutine)
        key = ("compiled_subroutine_reference_missing", clean)
        if key in seen_missing:
            continue
        if _subroutine_reference_present(clean, referenced):
            continue
        findings.append(
            {
                "reason": "compiled_subroutine_reference_missing",
                "message": "Compiled XSCR has no Script reference for a required subroutine call.",
                "subroutine": clean,
            }
        )
    return findings


def _compiled_file_reference_failures(xscr_path: Path, expected_paths: list[str]) -> list[dict[str, Any]]:
    if not expected_paths or not xscr_path.is_file():
        return []
    actual = {item.casefold(): item for item in _script_file_references(xscr_path)}
    failures: list[dict[str, Any]] = []
    for raw in expected_paths:
        clean = _clean_windows_literal(raw)
        if not clean or clean.casefold() in actual:
            continue
        failures.append(
            {
                "reason": "compiled_file_reference_missing",
                "message": "Compiled XSCR is missing a required FileReference entry.",
                "path": clean,
            }
        )
    return failures


def _ensure_compiled_subroutine_references(
    xscr_path: Path,
    protocol_ir: Mapping[str, Any],
    source_manifest: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    root = ET.parse(xscr_path).getroot()
    payload = _first_xml_child(root, "Payload")
    if payload is None:
        return []

    existing: dict[str, Any] = {}
    for ref in list(payload):
        if _local_xml_name(ref.tag) != "Reference" or _first_xml_text(ref, "TypeId") != "Script":
            continue
        object_name = clean_subroutine_reference(_first_xml_text(ref, "ObjectName"))
        for alias in (object_name, object_name.rsplit("\\", 1)[-1]):
            key = norm_subroutine_key(alias)
            if key:
                existing[key] = ref

    resolved_dependencies: dict[str, dict[str, Any]] = {}
    if isinstance(source_manifest, dict):
        for item in resolve_subroutine_dependencies(dict(protocol_ir), dict(source_manifest)).get("resolved", []):
            if not isinstance(item, dict):
                continue
            aliases = (
                item.get("ref"),
                item.get("object_name"),
                str(item.get("ref") or "").rsplit("\\", 1)[-1],
                str(item.get("object_name") or "").rsplit("\\", 1)[-1],
            )
            for alias in aliases:
                key = norm_subroutine_key(alias)
                if key:
                    resolved_dependencies[key] = item

    added: list[dict[str, str]] = []
    for subroutine in _required_subroutine_references(protocol_ir):
        clean = clean_subroutine_reference(subroutine)
        if not clean:
            continue
        bare = clean.rsplit("\\", 1)[-1]
        metadata = resolved_dependencies.get(norm_subroutine_key(clean)) or resolved_dependencies.get(
            norm_subroutine_key(bare)
        ) or {}
        guid = str(metadata.get("guid") or _ir_script_dependency_guid(protocol_ir, bare) or "")
        object_name = str(metadata.get("object_name") or bare)
        if not metadata and not guid:
            continue
        existing_ref = existing.get(norm_subroutine_key(clean)) or existing.get(norm_subroutine_key(bare))
        if existing_ref is not None:
            changed = False
            guid_node = _first_xml_child(existing_ref, "Guid")
            if guid_node is None:
                guid_node = ET.Element("Guid")
                existing_ref.insert(0, guid_node)
                changed = True
            if guid and str(guid_node.text or "").strip().casefold() != guid.casefold():
                guid_node.text = guid
                changed = True
            object_name_node = _first_xml_child(existing_ref, "ObjectName")
            if object_name_node is None:
                object_name_node = ET.SubElement(existing_ref, "ObjectName")
                changed = True
            if object_name and str(object_name_node.text or "").strip() != object_name:
                object_name_node.text = object_name
                changed = True
            if changed:
                added.append(
                    {
                        "subroutine": clean,
                        "object_name": object_name,
                        "guid": guid,
                        "action": "repaired",
                    }
                )
            continue
        ref = ET.Element("Reference")
        ET.SubElement(ref, "Guid").text = guid
        ET.SubElement(ref, "TypeId").text = "Script"
        ET.SubElement(ref, "ObjectName").text = object_name
        payload.insert(_first_payload_data_index(payload), ref)
        for alias in (object_name, object_name.rsplit("\\", 1)[-1]):
            key = norm_subroutine_key(alias)
            if key:
                existing[key] = ref
        added.append(
            {
                "subroutine": clean,
                "object_name": object_name,
                "guid": guid,
                "action": "added",
            }
        )

    if added:
        _register_root_namespace(root)
        ET.ElementTree(root).write(xscr_path, encoding="utf-8", xml_declaration=True)
    return added


def _normalize_compiled_variable_declaration_namespaces(xscr_path: Path) -> list[dict[str, str]]:
    original = _safe_read_text(xscr_path)
    if "VariableDefinitionHelper" not in original:
        return []
    text, fixups = localize_variable_declaration_namespaces(original)
    if text != original:
        xscr_path.write_text(text, encoding="utf-8")
    return fixups


def _copy_source_workspace_data(
    xscr_path: Path,
    source_scripts: list[Path],
) -> dict[str, str]:
    target_text = _safe_read_text(xscr_path)
    if "RUPWorktableStatement" not in target_text:
        return {}
    target_match = _VX_WORKSPACE_DATA_RE.search(target_text)
    if target_match is None:
        return {}
    expected_base = _worktable_reference_guid(target_text)
    candidates: list[dict[str, str]] = []
    for source_script in source_scripts:
        try:
            source_text = source_script.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for source_match in _VX_WORKSPACE_DATA_RE.finditer(source_text):
            source_block = source_match.group(0)
            candidates.append(
                {
                    "block": source_block,
                    "source_script": str(source_script),
                    "base_workspace": _workspace_block_base(source_block),
                    "delta_identifier": _workspace_block_delta_identifier(source_block),
                }
            )
    if not candidates:
        return {}
    source = _select_workspace_data_candidate(candidates, expected_base)
    source_block = source["block"]
    source_base = source.get("base_workspace") or ""
    delta_identifier = source.get("delta_identifier") or ""
    target_block = target_match.group(0)
    if re.sub(r"\s+", " ", target_block).strip() == re.sub(r"\s+", " ", source_block).strip():
        return {}
    updated = target_text[: target_match.start()] + source_block + target_text[target_match.end() :]
    xscr_path.write_text(updated, encoding="utf-8")
    return {
        "source_script": source["source_script"],
        "status": "replaced",
        "base_workspace": source_base,
        "delta_identifier": delta_identifier,
        "matched_worktable_reference": str(bool(expected_base and source_base.casefold() == expected_base.casefold())),
    }


def _exclude_source_inherited_command_failures(
    compiled_report: Mapping[str, Any],
    source_scripts: list[Path],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Keep source-equivalent offline findings visible without failing output.

    This is a roundtrip gate, not a source repair tool. A generated XSCR must
    not introduce new command-validation failures, while an unchanged finding
    already present in the original XSCR remains a documented source warning.
    """
    baseline_failures: list[dict[str, Any]] = []
    for source_path in source_scripts:
        if source_path.suffix.casefold() != ".xscr":
            continue
        try:
            baseline_failures.extend(validate_compiled_xscr_commands(source_path).as_dict().get("failures") or [])
        except Exception:
            continue
    return _subtract_inherited_validation_failures(compiled_report, baseline_failures)


def _subtract_inherited_validation_failures(
    compiled_report: Mapping[str, Any],
    baseline_failures: list[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return residual validation failures plus matching source findings."""
    available: dict[str, int] = {}
    for failure in baseline_failures:
        key = _validation_failure_fingerprint(failure)
        available[key] = available.get(key, 0) + 1

    inherited: list[dict[str, Any]] = []
    residual: list[dict[str, Any]] = []
    for raw_failure in compiled_report.get("failures") or []:
        failure = dict(raw_failure)
        key = _validation_failure_fingerprint(failure)
        if available.get(key, 0):
            available[key] -= 1
            inherited.append(failure)
        else:
            residual.append(failure)

    result = dict(compiled_report)
    result["source_failure_count"] = len(baseline_failures)
    result["inherited_failure_count"] = len(inherited)
    result["inherited_failures"] = inherited
    result["failure_count"] = len(residual)
    result["failures"] = residual
    return result, inherited


def _validation_failure_fingerprint(failure: Mapping[str, Any]) -> str:
    """Compare stable command semantics, not generated command positions/text."""
    stable = {
        str(key): value
        for key, value in failure.items()
        if key not in {"command_index", "message"}
    }
    return json.dumps(stable, sort_keys=True, default=str)


def _align_workspace_base_name_to_reference(xscr_path: Path) -> dict[str, str]:
    text = _safe_read_text(xscr_path)
    if "RUPWorktableStatement" not in text or "VxWorkspaceData" not in text:
        return {}
    reference = _WORKTABLE_WORKSPACE_REFERENCE_RE.search(text)
    if reference is None:
        return {}
    expected = reference.group("guid").strip()
    if not expected:
        return {}
    workspace = _VX_WORKSPACE_DATA_RE.search(text)
    if workspace is None:
        return {}
    workspace_block = workspace.group(0)
    base = _BASE_WORKSPACE_NAME_RE.search(workspace_block)
    if base is None:
        return {}
    current = base.group("value").strip()
    if current.casefold() == expected.casefold():
        return {}
    updated_block = (
        workspace_block[: base.start("value")]
        + expected
        + workspace_block[base.end("value") :]
    )
    updated = text[: workspace.start()] + updated_block + text[workspace.end() :]
    xscr_path.write_text(updated, encoding="utf-8")
    return {"status": "replaced", "from": current, "to": expected}


def _worktable_reference_guid(text: str) -> str:
    reference = _WORKTABLE_WORKSPACE_REFERENCE_RE.search(text)
    return reference.group("guid").strip() if reference else ""


def _workspace_block_base(block: str) -> str:
    match = _BASE_WORKSPACE_NAME_RE.search(block)
    return match.group("value").strip() if match else ""


def _workspace_block_delta_identifier(block: str) -> str:
    match = _WORKSPACE_DELTA_IDENTIFIER_RE.search(html.unescape(block))
    return match.group("value").strip() if match else ""


def _select_workspace_data_candidate(
    candidates: list[dict[str, str]],
    expected_base: str,
) -> dict[str, str]:
    def score(candidate: dict[str, str]) -> tuple[int, int, int]:
        base = candidate.get("base_workspace") or ""
        delta = candidate.get("delta_identifier") or ""
        return (
            1 if expected_base and base.casefold() == expected_base.casefold() else 0,
            1 if delta else 0,
            1 if base else 0,
        )

    return max(candidates, key=score)


def _required_subroutine_references(protocol_ir: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for step in protocol_ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        subroutine = clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine"))
        if subroutine and subroutine not in refs:
            refs.append(subroutine)
    return refs


def _ir_script_dependency_guid(protocol_ir: Mapping[str, Any], object_name: str) -> str:
    target = norm_subroutine_key(object_name)
    for dep in protocol_ir.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        if str(dep.get("kind") or "").casefold() not in {"script", "subroutine"}:
            continue
        dep_name = str(dep.get("name") or "")
        if target in {
            norm_subroutine_key(dep_name),
            norm_subroutine_key(dep_name.rsplit("\\", 1)[-1]),
        }:
            return str(dep.get("guid") or "")
    return ""


def _first_payload_data_index(payload: Any) -> int:
    for index, child in enumerate(list(payload)):
        if _local_xml_name(child.tag) == "PayloadData":
            return index
    return len(list(payload))


def _first_xml_child(root: Any, name: str) -> Any | None:
    for child in list(root):
        if _local_xml_name(child.tag) == name:
            return child
    return None


def _first_xml_text(root: Any, name: str) -> str:
    for child in root.iter():
        if _local_xml_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _local_xml_name(tag: Any) -> str:
    text = str(tag)
    if "}" in text:
        return text.rsplit("}", 1)[-1]
    return text


def _register_root_namespace(root: Any) -> None:
    tag = str(root.tag)
    if tag.startswith("{") and "}" in tag:
        ET.register_namespace("sd", tag[1:].split("}", 1)[0])


def _source_file_reference_paths(paths: list[Path]) -> list[str]:
    references: list[str] = []
    pattern = re.compile(
        r"<FileReference>.*?<File>(.*?)</File>.*?</FileReference>",
        re.DOTALL | re.IGNORECASE,
    )
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        references.extend(_clean_windows_literal(match) for match in pattern.findall(text))
    return [value for value in references if re.match(r"^[A-Za-z]:\\", value)]


def _compiled_external_command_paths(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = _safe_read_text(path)
    values: list[str] = []
    for tag in ("Application", "VbScript"):
        for raw in re.findall(
            rf"<{tag}\b[^>]*>(.*?)</{tag}>",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            value = _clean_windows_literal(raw)
            if re.match(r"^[A-Za-z]:\\", value):
                values.append(value)
    return values


def _dedupe_casefolded_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def _script_file_references(path: Path) -> list[str]:
    if not path.exists():
        return []
    text = _safe_read_text(path)
    files: list[str] = []
    for block in re.findall(r"<FileReference>.*?</FileReference>", text, flags=re.DOTALL):
        value = _first_xml_text_from_text(block, "File")
        if value and value not in files:
            files.append(value)
    return files


def _first_xml_text_from_text(text: str, name: str) -> str:
    match = re.search(rf"<{re.escape(name)}>(.*?)</{re.escape(name)}>", text, flags=re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _safe_read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _safe_read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _safe_parse_error(path: Path) -> str:
    try:
        ET.parse(path)
    except Exception as exc:
        return str(exc)
    return ""


def _ensure_blank_checksum_element(text: str) -> str:
    if re.search(r"<Checksum>.*?</Checksum>", text, flags=re.DOTALL):
        return text
    if re.search(r"<Checksum\s*/>", text):
        return re.sub(r"<Checksum\s*/>", "<Checksum></Checksum>", text, count=1)
    return re.sub(
        r'(\s*</(?:[A-Za-z_][\w.-]*:)?VxData>\s*)$',
        "\n  <Checksum></Checksum>\\1",
        text,
        count=1,
    )


def _subroutine_reference_present(subroutine: str, references: set[str]) -> bool:
    target = norm_subroutine_key(subroutine)
    bare = norm_subroutine_key(subroutine.rsplit("\\", 1)[-1])
    return any(
        norm_subroutine_key(ref) in {target, bare}
        or norm_subroutine_key(ref.rsplit("\\", 1)[-1]) == bare
        for ref in references
    )


def _clean_windows_literal(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'").replace("/", "\\")
