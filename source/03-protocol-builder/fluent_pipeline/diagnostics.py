"""Static diagnostics for real ZEIA/XSCR/GWL troubleshooting."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .aliases import load_alias_maps, resolve_alias
from .command_registry import (
    registry_command_approved_passthrough,
    registry_command_operation,
    registry_command_support_status,
    registry_command_supported,
)
from .config import READY_TO_IMPORT_DIR, TEMP_FILES_DIRNAME, resolve_user_path
from .fluent_log_parser import diagnostics_to_findings, diagnose_fluent_log_text
from .project_context import (
    ProjectContext,
    ProjectLike,
    import_project,
    is_context_archive,
    resolve_context_path,
    resolve_context_script,
)
from .protocol_ir import (
    CANONICAL_IR_VERSION,
    is_ir_bundle,
    protocol_ir_from_path,
    validate_protocol_ir,
)
from .runner import PipelineError, ensure_parent, write_json
from .worktable_diff import diff_worktable_requirements, render_worktable_changes_markdown


DIAGNOSTIC_REPORT_VERSION = "tecan.diagnostic_report.v1"
SEVERITIES = ("blocking", "high", "medium", "low", "info")


@dataclass(frozen=True)
class DiagnosticBundle:
    report: dict[str, Any]
    protocol_ir: dict[str, Any] | None = None
    worktable_diff: dict[str, Any] | None = None
    report_path: Path | None = None
    json_path: Path | None = None


def diagnose_input(
    input_path: Path,
    *,
    context: ProjectLike | None = None,
    script: str | None = None,
    project_name: str | None = None,
    force_import: bool = False,
    snapshot_archives: list[Path] | None = None,
    error_text: str | None = None,
    out_dir: Path | None = None,
) -> DiagnosticBundle:
    """Diagnose a ZEIA archive or a single script/worklist input."""
    source = resolve_user_path(input_path)
    findings: list[dict[str, Any]] = []
    imported = False
    ctx = context
    selected_path: Path | None = None
    selected_record: dict[str, Any] | None = None
    protocol_ir: dict[str, Any] | None = None
    worktable_diff: dict[str, Any] | None = None
    parse_error = ""

    snapshots = [resolve_user_path(path) for path in (snapshot_archives or [])]

    if source.suffix.lower() == ".zeia" or is_context_archive(source):
        ctx = import_project(source, name=project_name, force=force_import, snapshot_archives=snapshots)
        imported = True
        findings.extend(_manifest_findings(ctx))
        findings.extend(_snapshot_evidence_findings(ctx))
        selected_record, selection_findings = _select_script_record(ctx, script)
        findings.extend(selection_findings)
        if selected_record is not None:
            selected_path = (ctx.root / selected_record["extracted_path"]).resolve()
    else:
        selected_path = _resolve_input_path(ctx, source)
        if ctx is not None:
            selected_record = _script_record_for_path(ctx, selected_path)

    if selected_path is not None:
        try:
            payload = protocol_ir_from_path(selected_path)
            if is_ir_bundle(payload):
                findings.append(
                    _finding(
                        "input.ir_bundle",
                        "blocking",
                        "script_selection",
                        "Input exported multiple protocols; choose one script",
                        evidence=[f"Input: {selected_path}", f"Protocols: {payload.get('protocol_count', 0)}"],
                        next_steps=["Run diagnose on a specific extracted .xscr or pass --script with the ZEIA archive."],
                    )
                )
            else:
                protocol_ir = payload
        except Exception as exc:  # pragma: no cover - exact XML/parser failures vary
            parse_error = str(exc)
            findings.append(
                _finding(
                    "input.parse_failed",
                    "blocking",
                    "parse",
                    "Selected input could not be parsed into protocol IR",
                    evidence=[f"Input: {selected_path}", parse_error],
                    next_steps=[
                        "Inspect the file with project-reader to confirm it is a readable XSCR/GWL/Python/IR input.",
                        "If this came from ZEIA, verify the archive extracts cleanly and choose another script if needed.",
                    ],
                )
            )

    if protocol_ir is not None:
        findings.extend(_protocol_ir_findings(protocol_ir))
        if ctx is not None:
            worktable_diff = diff_worktable_requirements(protocol_ir, source_manifest=ctx.manifest)
            findings.extend(_worktable_findings(worktable_diff))

    if ctx is not None and selected_record is not None:
        findings.extend(_script_dependency_findings(ctx, selected_record))
        findings.extend(_script_custom_part_findings(ctx, selected_record))
        findings.extend(_unsupported_command_findings(selected_record, protocol_ir))
        findings.extend(_approved_passthrough_command_findings(selected_record))
        findings.extend(_alias_candidate_findings(ctx))
    elif ctx is not None and not imported:
        findings.extend(_snapshot_evidence_findings(ctx))

    if error_text:
        findings.extend(_error_text_findings(error_text, worktable_diff=worktable_diff))
    else:
        findings.append(
            _finding(
                "error_text.missing",
                "info",
                "error_text",
                "No FluentControl error text was provided",
                evidence=["Diagnosis is based on static ZEIA/XSCR/GWL inspection only."],
                next_steps=["Re-run with --error-text or --error-file to correlate this report with the actual failure message."],
            )
        )

    report = _report(
        input_path=source,
        context=ctx,
        imported=imported,
        selected_path=selected_path,
        selected_record=selected_record,
        protocol_ir=protocol_ir,
        worktable_diff=worktable_diff,
        findings=findings,
        parse_error=parse_error,
    )

    output_dir = out_dir or _default_output_dir(ctx, selected_path or source)
    report, report_path, json_path = _write_diagnostic_artifacts(
        report,
        output_dir,
        protocol_ir=protocol_ir,
        worktable_diff=worktable_diff,
    )
    return DiagnosticBundle(
        report=report,
        protocol_ir=protocol_ir,
        worktable_diff=worktable_diff,
        report_path=report_path,
        json_path=json_path,
    )


def render_diagnostic_markdown(report: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    summary = report.get("summary") or {}
    lines = [
        "# Tecan Script Diagnosis",
        "",
        f"- Generated at: `{now}`",
        f"- Result: `{summary.get('status', 'unknown')}`",
        f"- Findings: `{summary.get('finding_count', 0)}`",
        f"- Input: `{(report.get('input') or {}).get('path', '')}`",
    ]
    context = report.get("context") or {}
    if context:
        lines.append(f"- Context: `{context.get('name')}`")
    script = report.get("selected_script") or {}
    if script:
        lines.append(f"- Script: `{script.get('object_name') or script.get('path')}`")
    lines.append("")

    top_causes = summary.get("top_likely_causes") or []
    lines.extend(["## Top Likely Causes", ""])
    if top_causes:
        for item in top_causes:
            lines.append(f"- `{item.get('severity')}` {item.get('title')}")
    else:
        lines.append("- No high-confidence static cause was found from available metadata.")
    lines.append("")

    artifacts = report.get("artifacts") or {}
    if artifacts:
        lines.extend(["## Artifacts", ""])
        for label, path in artifacts.items():
            lines.append(f"- {label}: `{path}`")
        lines.append("")

    worktable = report.get("worktable_summary") or {}
    if worktable:
        lines.extend(
            [
                "## Worktable Summary",
                "",
                f"- Missing labware: `{worktable.get('missing_labware_count', 0)}`",
                f"- Changed deck positions: `{worktable.get('changed_position_count', 0)}`",
                f"- Missing or unverified liquid classes: `{worktable.get('liquid_class_issue_count', 0)}`",
                f"- Missing or unverified device aliases: `{worktable.get('device_alias_issue_count', 0)}`",
                f"- Missing or unverified worklists: `{worktable.get('worklist_issue_count', 0)}`",
                "",
            ]
        )

    lines.extend(["## Findings", ""])
    for finding in report.get("findings") or []:
        lines.extend(
            [
                f"### {finding.get('title')}",
                "",
                f"- Severity: `{finding.get('severity')}`",
                f"- Category: `{finding.get('category')}`",
            ]
        )
        evidence = finding.get("evidence") or []
        if evidence:
            lines.append("- Evidence:")
            for item in evidence:
                lines.append(f"  - {item}")
        next_steps = finding.get("next_steps") or []
        if next_steps:
            lines.append("- Next checks:")
            for item in next_steps:
                lines.append(f"  - {item}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _write_diagnostic_artifacts(
    report: dict[str, Any],
    out_dir: Path,
    *,
    protocol_ir: dict[str, Any] | None,
    worktable_diff: dict[str, Any] | None,
) -> tuple[dict[str, Any], Path, Path]:
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts = dict(report.get("artifacts") or {})
    if protocol_ir is not None:
        ir_path = out_dir / "diagnostic.protocol-ir.json"
        write_json(ir_path, protocol_ir)
        artifacts["protocol_ir"] = str(ir_path)
    if worktable_diff is not None:
        diff_json = out_dir / "worktable_diff.json"
        diff_report = out_dir / "worktable_changes.md"
        write_json(diff_json, worktable_diff)
        diff_report.write_text(render_worktable_changes_markdown(worktable_diff), encoding="utf-8")
        artifacts["worktable_diff_json"] = str(diff_json)
        artifacts["worktable_changes"] = str(diff_report)

    report_path = out_dir / "diagnosis.md"
    json_path = out_dir / "diagnosis.json"
    report["artifacts"] = artifacts
    report["artifacts"]["diagnosis_markdown"] = str(report_path)
    report["artifacts"]["diagnosis_json"] = str(json_path)
    report_path.write_text(render_diagnostic_markdown(report), encoding="utf-8")
    write_json(json_path, report)
    return report, report_path, json_path


def _report(
    *,
    input_path: Path,
    context: ProjectLike | None,
    imported: bool,
    selected_path: Path | None,
    selected_record: dict[str, Any] | None,
    protocol_ir: dict[str, Any] | None,
    worktable_diff: dict[str, Any] | None,
    findings: list[dict[str, Any]],
    parse_error: str,
) -> dict[str, Any]:
    clean_findings = sorted(findings, key=lambda item: (_severity_rank(item.get("severity")), item.get("id", "")))
    severity_counts = {severity: 0 for severity in SEVERITIES}
    for finding in clean_findings:
        severity = finding.get("severity")
        if severity in severity_counts:
            severity_counts[severity] += 1
    if severity_counts["blocking"]:
        status = "blocked"
    elif severity_counts["high"]:
        status = "likely_issue"
    elif severity_counts["medium"]:
        status = "needs_review"
    else:
        status = "no_clear_static_fault"

    return {
        "diagnostic_version": DIAGNOSTIC_REPORT_VERSION,
        "input": {
            "path": str(input_path),
            "suffix": input_path.suffix.lower(),
        },
        "context": _context_summary(context, imported),
        "selected_script": _selected_script_summary(selected_path, selected_record),
        "protocol_summary": _protocol_summary(protocol_ir),
        "worktable_summary": _worktable_summary(worktable_diff),
        "parse_error": parse_error,
        "summary": {
            "status": status,
            "finding_count": len(clean_findings),
            "severity_counts": severity_counts,
            "top_likely_causes": [
                {
                    "id": item.get("id"),
                    "severity": item.get("severity"),
                    "title": item.get("title"),
                }
                for item in clean_findings
                if item.get("severity") in {"blocking", "high", "medium"}
            ][:5],
        },
        "findings": clean_findings,
        "artifacts": {},
    }


def _manifest_findings(ctx: ProjectContext) -> list[dict[str, Any]]:
    errors = ctx.manifest.get("errors") or []
    if not errors:
        return []
    return [
        _finding(
            "manifest.import_errors",
            "blocking",
            "import",
            "ZEIA import found unreadable entries",
            evidence=[json.dumps(item, sort_keys=True) for item in errors[:5]],
            next_steps=[
                "Open the ZEIA import manifest and inspect the listed entries.",
                "If these entries are required by the failing script, repair or re-export the ZEIA before deeper diagnosis.",
            ],
            details={"error_count": len(errors)},
        )
    ]


def _snapshot_evidence_findings(ctx: ProjectLike) -> list[dict[str, Any]]:
    summary = ctx.manifest.get("snapshot_summary") or {}
    if not summary.get("evidence_count"):
        return []
    findings: list[dict[str, Any]] = []
    role_specs = [
        (
            "instrument_configuration",
            "snapshot.instrument_configuration",
            "snapshot",
            "Snapshot includes instrument configuration evidence",
            summary.get("instrument_configuration_files") or [],
            [
                "Compare the Snapshot instrument configuration against the FluentControl configuration selected for simulation.",
                "Use serial number, arm order, tip configuration, and device evidence as a manual compatibility checklist.",
            ],
        ),
        (
            "simulation_setup",
            "snapshot.simulation_setup",
            "snapshot",
            "Snapshot includes simulation setup evidence",
            summary.get("simulation_setup_files") or [],
            [
                "If `system.config` is present, copy/rename it only through the FluentControl-supported simulation configuration workflow.",
                "Treat this as setup evidence, not proof that the local simulator selected the same configuration.",
            ],
        ),
        (
            "hardware_details",
            "snapshot.hardware_details",
            "hardware",
            "Snapshot includes hardware or firmware detail evidence",
            summary.get("hardware_detail_files") or [],
            [
                "Check firmware, driver, arm, tip, and integrated-device details before editing hardware-sensitive commands.",
                "Do not auto-reconfigure physical hardware from imported Snapshot metadata.",
            ],
        ),
        (
            "troubleshooting_context",
            "snapshot.troubleshooting_context",
            "troubleshooting",
            "Snapshot includes troubleshooting context",
            summary.get("troubleshooting_context_files") or [],
            [
                "Correlate logs, screenshots, user description, audit trail, and sample-tracking files with the failure timestamp.",
                "Re-run diagnosis with --error-text or --error-file for tighter static matching.",
            ],
        ),
    ]
    role_counts = summary.get("role_counts") or {}
    for role, finding_id, category, title, files, next_steps in role_specs:
        if not role_counts.get(role):
            continue
        evidence = [str(path) for path in files[:8]]
        if role == "instrument_configuration":
            evidence.extend(f"serial: {value}" for value in (summary.get("instrument_serial_numbers") or [])[:4])
        if role == "hardware_details":
            evidence.extend(f"firmware: {value}" for value in (summary.get("firmware_versions") or [])[:4])
            evidence.extend(f"driver: {value}" for value in (summary.get("driver_versions") or [])[:4])
        findings.append(
            _finding(
                finding_id,
                "info",
                category,
                title,
                evidence=evidence[:12],
                next_steps=next_steps,
                details={
                    "count": role_counts.get(role, 0),
                    "system_config_paths": summary.get("system_config_paths") or [],
                },
            )
        )
    return findings


def _select_script_record(
    ctx: ProjectLike,
    script: str | None,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    scripts = ctx.manifest.get("scripts") or []
    if script:
        path = resolve_context_script(ctx, script)
        record = _script_record_for_path(ctx, path)
        if record is not None:
            return record, []
        return None, [
            _finding(
                "script.selection_not_found",
                "blocking",
                "script_selection",
                "Requested script was not found in the project context",
                evidence=[f"Requested: {script}", f"Resolved path: {path}"],
                next_steps=["Run project-info or project-find to choose an exact script object name or extracted path."],
            )
        ]
    if len(scripts) == 1:
        return scripts[0], []
    if not scripts and (ctx.manifest.get("snapshot_summary") or {}).get("evidence_count"):
        return None, [
            _finding(
                "snapshot.no_script",
                "info",
                "script_selection",
                "Snapshot context contains no XSCR scripts",
                evidence=[f"Context: {ctx.name}", f"Snapshot files: {(ctx.manifest.get('snapshot_summary') or {}).get('evidence_count', 0)}"],
                next_steps=["Use this Snapshot alongside a ZEIA/script diagnosis when you need method-specific findings."],
            )
        ]
    if not scripts:
        return None, [
            _finding(
                "script.none_found",
                "blocking",
                "script_selection",
                "No XSCR scripts were found in the ZEIA archive",
                evidence=[f"Context: {ctx.name}"],
                next_steps=["Confirm this ZEIA contains FluentControl script entries, not only support objects."],
            )
        ]
    return None, [
        _finding(
            "script.multiple_without_selection",
            "blocking",
            "script_selection",
            "Multiple scripts were found; choose one to diagnose",
            evidence=[_script_label(item) for item in scripts[:10]],
            next_steps=["Re-run diagnose with --script using the object name or extracted path for the failing script."],
            details={"script_count": len(scripts)},
        )
    ]


def _resolve_input_path(ctx: ProjectLike | None, source: Path) -> Path:
    if ctx is not None and source.suffix.lower() == ".xscr":
        return resolve_context_script(ctx, source)
    if ctx is not None:
        return resolve_context_path(ctx, source)
    return source.resolve()


def _script_record_for_path(ctx: ProjectLike, path: Path) -> dict[str, Any] | None:
    resolved = path.resolve()
    for script in ctx.manifest.get("scripts") or []:
        extracted = (ctx.root / script.get("extracted_path", "")).resolve()
        if extracted == resolved:
            return script
    return None


def _protocol_ir_findings(protocol_ir: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    issues = validate_protocol_ir(protocol_ir)
    if issues:
        findings.append(
            _finding(
                "protocol_ir.schema_issues",
                "high",
                "protocol_ir",
                "Protocol IR has schema issues",
                evidence=[f"{issue.path}: {issue.message}" for issue in issues[:8]],
                next_steps=["Inspect diagnostic.protocol-ir.json and repair malformed or incomplete protocol fields."],
                details={"issue_count": len(issues)},
            )
        )
    steps = protocol_ir.get("steps") or []
    if not steps:
        findings.append(
            _finding(
                "protocol_ir.no_steps",
                "high",
                "parse",
                "No supported protocol steps were parsed from the selected input",
                evidence=["The parser produced an IR with zero steps."],
                next_steps=[
                    "Check whether the script uses command types missing from tecan_common/data/command_registry.json.",
                    "Inspect the raw XSCR with project-reader to identify unsupported command IDs.",
                ],
            )
        )
    return findings


def _worktable_findings(diff: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    missing_labware = [item for item in diff.get("missing_labware") or [] if item.get("status") == "missing"]
    unverified_labware = [item for item in diff.get("missing_labware") or [] if item.get("status") == "unverified"]
    changed_positions = diff.get("changed_deck_positions") or []
    liquid_issues = _status_issues(diff.get("required_liquid_classes") or [])
    carrier_issues = _status_issues(diff.get("required_carriers") or [])
    device_issues = _status_issues(diff.get("device_aliases") or [])
    worklist_issues = _status_issues(diff.get("worklist_paths") or [])
    tip_issues = [item for item in diff.get("required_tip_boxes") or [] if item.get("status") != "available"]

    if missing_labware:
        findings.append(
            _finding(
                "worktable.missing_labware",
                "high",
                "worktable",
                "Required labware is missing from the source context",
                evidence=[_labware_evidence(item) for item in missing_labware[:8]],
                next_steps=["Add or rename the missing labware in FluentControl, or add an alias if the name differs by import suffix."],
                details={"count": len(missing_labware)},
            )
        )
    if unverified_labware:
        findings.append(
            _finding(
                "worktable.unverified_labware",
                "medium",
                "worktable",
                "Labware could not be verified from extracted source metadata",
                evidence=[_labware_evidence(item) for item in unverified_labware[:8]],
                next_steps=["Run the optional FluentControl import/load diagnostic and inspect the source worktable manually."],
                details={"count": len(unverified_labware)},
            )
        )
    if changed_positions:
        findings.append(
            _finding(
                "worktable.changed_positions",
                "medium",
                "worktable",
                "Required deck positions differ from source metadata",
                evidence=[
                    f"{item.get('label')}: {item.get('source_deck_location')} -> {item.get('required_deck_location')}"
                    for item in changed_positions[:8]
                ],
                next_steps=["Confirm the physical deck layout and update the worktable or script placement before running."],
                details={"count": len(changed_positions)},
            )
        )
    if liquid_issues:
        findings.append(_status_finding("liquid_class", "Liquid classes are missing or unverified", liquid_issues))
    if carrier_issues:
        findings.append(_status_finding("carrier", "Carriers are missing or unverified", carrier_issues))
    if device_issues:
        findings.append(_status_finding("device_alias", "Device aliases are missing or unverified", device_issues))
    if worklist_issues:
        findings.append(_status_finding("worklist", "Worklist paths are missing or unverified", worklist_issues))
    if tip_issues:
        findings.append(
            _finding(
                "worktable.tip_box_issues",
                "high",
                "tips",
                "Required tip boxes are missing or unverified",
                evidence=[_labware_evidence(item) for item in tip_issues[:8]],
                next_steps=["Load the required tip box, correct the tip labware name, or add an alias for the imported name."],
                details={"count": len(tip_issues)},
            )
        )
    for index, warning in enumerate(diff.get("warnings") or [], start=1):
        findings.append(
            _finding(
                f"worktable.warning.{index}",
                "low",
                "worktable",
                "Worktable comparison has a safety note",
                evidence=[warning],
                next_steps=["Treat the worktable report as incomplete until the optional FluentControl import/load diagnostic or a manual Script Editor review confirms the deck."],
            )
        )
    return findings


def _script_dependency_findings(ctx: ProjectLike, script: dict[str, Any]) -> list[dict[str, Any]]:
    deps = script.get("dependencies") or {}
    findings = []
    missing_refs = [
        ref
        for ref in deps.get("external_or_worklist_refs") or []
        if _looks_like_worklist(ref) and not _worklist_ref_exists(ctx, ref)
    ]
    if missing_refs:
        findings.append(
            _finding(
                "script.missing_worklist_refs",
                "high",
                "worklist",
                "Script references worklists that were not found in the ZEIA archive",
                evidence=[str(ref) for ref in missing_refs[:8]],
                next_steps=[
                    "Confirm the worklist file is packaged with the method or reachable at runtime.",
                    "Update the script path or copy the required GWL into the expected location.",
                ],
                details={"count": len(missing_refs)},
            )
        )
    subroutine_refs = [str(ref) for ref in deps.get("subroutine_refs") or [] if str(ref or "").strip()]
    if subroutine_refs:
        resolved = []
        unresolved = []
        for ref in subroutine_refs:
            match = _subroutine_ref_match(ctx, ref)
            if match:
                resolved.append(f"{ref} -> {_script_label(match)}")
            else:
                unresolved.append(ref)
        if unresolved:
            findings.append(
                _finding(
                    "script.unresolved_subroutine_refs",
                    "high",
                    "subroutines",
                    "Script calls subroutines that were not found in the imported context",
                    evidence=unresolved[:8],
                    next_steps=[
                        "Import the ZEIA that contains the missing subroutine script, or add it to a project collection.",
                        "Preserve the SubRoutineStatement raw XML until the called script is available and validated.",
                    ],
                    details={"count": len(unresolved)},
                )
            )
        if resolved:
            findings.append(
                _finding(
                    "script.subroutine_refs",
                    "info",
                    "subroutines",
                    "Script calls subroutines available in the imported context",
                    evidence=resolved[:10],
                    next_steps=[
                        "Validate each subroutine alongside the parent script before editing.",
                        "Prefer preserving SubRoutineStatement links unless the generated script intentionally inlines the called logic.",
                    ],
                    details={"count": len(resolved)},
                )
            )
    return findings

def _script_custom_part_findings(ctx: ProjectLike, script: dict[str, Any]) -> list[dict[str, Any]]:
    deps = script.get("dependencies") or {}
    findings = []
    pin_refs = sorted(
        set(str(value) for value in deps.get("pin_refs") or [] if str(value or "").strip())
        | set(str(value) for value in deps.get("worktable_pin_locations") or [] if str(value or "").strip())
    )
    custom_asset_refs = sorted(str(value) for value in deps.get("custom_asset_refs") or [] if str(value or "").strip())
    barcode_refs = sorted(str(value) for value in deps.get("barcode_refs") or [] if str(value or "").strip())
    summary = ctx.manifest.get("custom_part_summary") or {}

    if pin_refs:
        findings.append(
            _finding(
                "hardware.pin_refs",
                "medium",
                "hardware_pins",
                "Script references pin-controlled or pin-located hardware",
                evidence=pin_refs[:12],
                next_steps=[
                    "Verify these pins/sites in FluentControl's worktable/hardware configuration before import.",
                    "Do not auto-reconfigure physical pins from generated artifacts; use the report as a checklist.",
                ],
                details={"count": len(pin_refs)},
            )
        )
    if custom_asset_refs:
        findings.append(
            _finding(
                "custom_parts.asset_refs",
                "medium",
                "custom_parts",
                "Script references custom visual/detail assets",
                evidence=custom_asset_refs[:12],
                next_steps=[
                    "Confirm each image/detail asset exists on the FluentControl runtime machine or is packaged with the ZEIA.",
                    "Keep TouchTools/RUP raw XML preserved if these custom assets drive operator prompts.",
                ],
                details={"count": len(custom_asset_refs)},
            )
        )
    if barcode_refs:
        findings.append(
            _finding(
                "custom_parts.barcode_refs",
                "info",
                "barcode",
                "Script reads or writes barcode metadata",
                evidence=barcode_refs[:12],
                next_steps=[
                    "Validate barcode variable mappings in FluentControl simulation/logs before real runs.",
                ],
                details={"count": len(barcode_refs)},
            )
        )
    if summary.get("pin_connector_count") and pin_refs:
        findings.append(
            _finding(
                "custom_parts.pin_connectors",
                "info",
                "custom_parts",
                "Imported context contains worktable connector definitions for pin-mounted parts",
                evidence=[
                    f"pin connector objects: {summary.get('pin_connector_count')}",
                    *[f"pin: {value}" for value in (summary.get("pin_refs") or [])[:8]],
                ],
                next_steps=[
                    "Use these connector definitions as evidence for compatibility checks, not as automatic hardware edits.",
                ],
                details={
                    "pin_connector_count": summary.get("pin_connector_count", 0),
                    "asset_count": summary.get("asset_count", 0),
                },
            )
        )
    return findings


def _unsupported_command_findings(
    script: dict[str, Any],
    protocol_ir: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    command_counts = script.get("command_counts") or {}
    unsupported = {
        command: count
        for command, count in command_counts.items()
        if count and not _supported_command_name(command)
    }
    unsupported.pop("ScriptGroup", None)
    if not unsupported:
        return []

    severity = "high" if not (protocol_ir and protocol_ir.get("steps")) else "medium"
    return [
        _finding(
            "commands.unsupported",
            severity,
            "commands",
            "Some FluentControl commands are not mapped to protocol IR operations",
            evidence=[f"{name}: {count}" for name, count in sorted(unsupported.items())[:10]],
            next_steps=[
                "Inspect these command IDs in the raw XSCR.",
                "Add mappings to tecan_common/data/command_registry.json before trusting automated diagnosis or simulation for this script.",
            ],
            details={"unsupported_command_counts": unsupported},
        )
    ]


def _approved_passthrough_command_findings(script: dict[str, Any]) -> list[dict[str, Any]]:
    command_counts = script.get("command_counts") or {}
    approved = {
        command: count
        for command, count in command_counts.items()
        if count and registry_command_approved_passthrough(command)
    }
    if not approved:
        return []
    statuses = {
        command: registry_command_support_status(command) or ""
        for command in approved
    }
    return [
        _finding(
            "commands.approved_passthrough",
            "info",
            "commands",
            "Known FluentControl scaffolding/runtime commands are explicitly approved outside protocol IR",
            evidence=[
                f"{name}: {count} ({statuses.get(name)})"
                for name, count in sorted(approved.items())[:12]
            ],
            next_steps=[
                "Keep these commands preserved in source XSCR or FluentControl-generated output.",
                "Add a real protocol IR operation only if the command must be edited semantically.",
            ],
            details={
                "approved_command_counts": approved,
                "support_statuses": statuses,
            },
        )
    ]


def _alias_candidate_findings(ctx: ProjectLike) -> list[dict[str, Any]]:
    alias_maps = load_alias_maps()
    missing = []
    for item in ctx.manifest.get("catalog_alias_candidates") or []:
        project_name = str(item.get("project_name") or "")
        base_name = str(item.get("base_name") or "")
        if project_name and base_name and resolve_alias(project_name, "catalog", alias_maps) == project_name:
            missing.append(f"{project_name} -> {base_name}")
    if not missing:
        return []
    return [
        _finding(
            "aliases.new_catalog_candidates",
            "medium",
            "aliases",
            "ZEIA import exposed catalog alias candidates not in the configured maps",
            evidence=missing[:10],
            next_steps=[
                "Verify each candidate in FluentControl/catalog-info.",
                "Add confirmed mappings to config/aliases/catalog_aliases.yaml and rerun diagnosis.",
            ],
            details={"count": len(missing)},
        )
    ]


def _error_text_findings(error_text: str, *, worktable_diff: dict[str, Any] | None) -> list[dict[str, Any]]:
    findings = []
    text = error_text.strip()
    if not text:
        return findings
    fluent_log_findings = diagnostics_to_findings(diagnose_fluent_log_text(text))
    classified_log_findings = [item for item in fluent_log_findings if item.get("id") != "fluent_log.unclassified"]
    excerpts = _error_excerpts(text)
    patterns = [
        (
            "error.labware",
            "high",
            "worktable",
            r"\b(labware|rack|carrier|plate|trough)\b.*\b(not found|missing|unknown|invalid|could not)",
            "FluentControl error text points at labware/deck setup",
            ["Compare the failing labware name against worktable_changes.md and aliases/labware_aliases.yaml."],
        ),
        (
            "error.liquid_class",
            "high",
            "liquid_class",
            r"\b(liquid class|liquidclass)\b.*\b(not found|missing|unknown|invalid|could not)",
            "FluentControl error text points at a liquid-class mismatch",
            ["Check required liquid classes and add a liquid-class alias only after confirming the real catalog name."],
        ),
        (
            "error.device_alias",
            "high",
            "device_alias",
            r"\b(device alias|devicealias|instrument=.*device=)\b.*\b(not found|missing|unknown|invalid|could not|resolve)",
            "FluentControl error text points at a device alias mismatch",
            ["Confirm the instrument device alias in FluentControl and config/aliases/device_aliases.yaml."],
        ),
        (
            "error.scanner_instance",
            "blocking",
            "device_alias",
            r"(USB:TECAN|CGA|BCR|barcode|scanner).{0,160}not associated with a scanner instance|not associated with a scanner instance.{0,160}(USB:TECAN|CGA|BCR|barcode|scanner)",
            "FluentControl error text points at a scanner/device binding mismatch",
            [
                "First confirm the relevant instrument/scanner is powered on, connected, and initialized on the instrument PC.",
                "Only if the error persists with powered/initialized hardware should the workflow consider a script change, such as replacing a generated hard-coded USB/CGA binding with source-proven logic or an operator prompt.",
            ],
        ),
        (
            "error.unknown_driver_command",
            "blocking",
            "driver_command",
            r"Command\s+[\"“][^\"”]*(RGA|CGA|BCR|TransferLabware|ExecuteSingleVector)[^\"”]*[\"”]\s+is unknown|corresponding driver is (available|installed|configured)",
            "FluentControl does not know a hardware driver command in this script",
            [
                "First confirm the relevant instrument hardware is powered on, connected, and initialized on the instrument PC.",
                "If it was opened off-instrument or while hardware was off, mark it as an environment/device-readiness issue and re-check before editing the script.",
                "Only if the command remains unknown with initialized hardware should the workflow use a source-mined native command or an explicit operator/manual verification prompt.",
            ],
        ),
        (
            "error.xml_checksum",
            "blocking",
            "import",
            r"XML checksum error|InvalidChecksumException|unauthorized modification of Script|unauthorized modification of .*?(?:VxData|Script|Worktable)",
            "FluentControl import/load rejected a datastore XML checksum",
            [
                "Rebuild the ZEIA through the exporter/checksum path so every edited datastore entry gets a fresh FluentControl checksum.",
                "Do not hand-edit the final XSCR/ZEIA after checksum stamping; if edits are needed, edit the source and regenerate.",
                "If this came from a generated harness script, check for placeholder, blank, stale, or non-hex <Checksum> values before import.",
                "For hand-built XSCR XML, make sure every generated text value is XML-escaped before checksum stamping; raw `>` text such as `A -> B` can load as valid XML offline but fail FluentControl checksum validation.",
            ],
        ),
        (
            "error.worklist",
            "high",
            "worklist",
            r"\b(worklist|\.gwl|file|path)\b.*\b(not found|missing|cannot open|could not|invalid)",
            "FluentControl error text points at a missing file or worklist",
            ["Confirm GWL/worklist paths are packaged in the ZEIA or reachable on the runtime machine."],
        ),
        (
            "error.tip",
            "medium",
            "tips",
            r"\b(tip|diti)\b.*\b(empty|missing|not found|invalid|capacity|insufficient)",
            "FluentControl error text points at tip setup or capacity",
            ["Check tip box names, deck position, and volume/tip capacity assumptions."],
        ),
        (
            "error.volume",
            "medium",
            "volume",
            r"\b(volume|ul|µl|microliter)\b.*\b(invalid|too high|too low|capacity|insufficient|negative)",
            "FluentControl error text points at volume bounds or liquid state",
            ["Inspect protocol IR volumes and run liquid-state validation before using the method."],
        ),
    ]
    for finding_id, severity, category, pattern, title, next_steps in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL):
            evidence = excerpts
            if worktable_diff is not None and category in {"worktable", "liquid_class", "device_alias", "worklist", "tips"}:
                evidence.extend(_matching_worktable_evidence(category, worktable_diff))
            findings.append(_finding(finding_id, severity, category, title, evidence=evidence[:8], next_steps=next_steps))
    # Classic patterns win when they match. Fluent-log rules fill gaps only;
    # otherwise broad rules like fluent_log.missing_referenced_files escalate
    # worklist/file misses from high -> blocked and double-count the same cause.
    if findings:
        return findings
    if classified_log_findings:
        return classified_log_findings
    if fluent_log_findings:
        return fluent_log_findings
    findings.append(
        _finding(
            "error_text.unclassified",
            "medium",
            "error_text",
            "FluentControl error text was provided but did not match a known diagnostic pattern",
            evidence=excerpts,
            next_steps=["Keep the error text with the diagnosis report and add a fixture once the root cause is known."],
        )
    )
    return findings


def _matching_worktable_evidence(category: str, diff: dict[str, Any]) -> list[str]:
    if category == "worktable":
        return [_labware_evidence(item) for item in (diff.get("missing_labware") or [])[:5]]
    if category == "liquid_class":
        return [f"{item.get('name')}: {item.get('status')}" for item in _status_issues(diff.get("required_liquid_classes") or [])[:5]]
    if category == "device_alias":
        return [f"{item.get('name')}: {item.get('status')}" for item in _status_issues(diff.get("device_aliases") or [])[:5]]
    if category == "worklist":
        return [f"{item.get('name')}: {item.get('status')}" for item in _status_issues(diff.get("worklist_paths") or [])[:5]]
    if category == "tips":
        return [_labware_evidence(item) for item in (diff.get("required_tip_boxes") or [])[:5]]
    return []


def _status_finding(category: str, title: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    severity = "high" if any(item.get("status") == "missing" for item in items) else "medium"
    return _finding(
        f"worktable.{category}_issues",
        severity,
        category,
        title,
        evidence=[f"{item.get('name')}: {item.get('status')}" for item in items[:8]],
        next_steps=[f"Confirm each {category.replace('_', ' ')} in FluentControl or add a verified alias map entry."],
        details={"count": len(items)},
    )


def _status_issues(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if item.get("status") != "available"]


def _context_summary(ctx: ProjectLike | None, imported: bool) -> dict[str, Any]:
    if ctx is None:
        return {}
    return {
        "name": ctx.name,
        "root": str(ctx.root),
        "manifest": str(ctx.root / "manifest.json"),
        "imported_during_diagnosis": imported,
        "script_count": len(ctx.manifest.get("scripts") or []),
        "workspace_count": len(ctx.manifest.get("workspaces") or []),
        "object_count": len(ctx.manifest.get("objects") or []),
        "snapshot_evidence_count": len(ctx.manifest.get("snapshot_evidence") or []),
        "snapshot_summary": ctx.manifest.get("snapshot_summary") or {},
        "errors": ctx.manifest.get("errors") or [],
    }


def _selected_script_summary(path: Path | None, record: dict[str, Any] | None) -> dict[str, Any]:
    if path is None and record is None:
        return {}
    return {
        "path": str(path) if path else "",
        "object_name": (record or {}).get("object_name") or "",
        "entry": (record or {}).get("entry") or "",
        "command_count": (record or {}).get("command_count"),
        "command_counts": (record or {}).get("command_counts") or {},
        "dependencies": (record or {}).get("dependencies") or {},
    }


def _protocol_summary(protocol_ir: dict[str, Any] | None) -> dict[str, Any]:
    if protocol_ir is None:
        return {}
    return {
        "ir_version": protocol_ir.get("ir_version") or CANONICAL_IR_VERSION,
        "name": (protocol_ir.get("protocol") or {}).get("name") or protocol_ir.get("id") or "",
        "source": protocol_ir.get("source") or {},
        "step_count": len(protocol_ir.get("steps") or []),
        "labware_count": len(protocol_ir.get("labware") or []),
        "liquid_class_count": len(protocol_ir.get("liquid_classes") or []),
        "worklist_count": len(protocol_ir.get("worklists") or []),
    }


def _worktable_summary(diff: dict[str, Any] | None) -> dict[str, Any]:
    if diff is None:
        return {}
    return {
        "missing_labware_count": len([item for item in diff.get("missing_labware") or [] if item.get("status") == "missing"]),
        "unverified_labware_count": len([item for item in diff.get("missing_labware") or [] if item.get("status") == "unverified"]),
        "changed_position_count": len(diff.get("changed_deck_positions") or []),
        "liquid_class_issue_count": len(_status_issues(diff.get("required_liquid_classes") or [])),
        "carrier_issue_count": len(_status_issues(diff.get("required_carriers") or [])),
        "device_alias_issue_count": len(_status_issues(diff.get("device_aliases") or [])),
        "worklist_issue_count": len(_status_issues(diff.get("worklist_paths") or [])),
        "warning_count": len(diff.get("warnings") or []),
    }


def _default_output_dir(ctx: ProjectLike | None, source: Path) -> Path:
    label = _slug(source.stem or "diagnosis")
    if ctx is not None:
        return (ctx.reports_dir / f"{label}_diagnosis").resolve()
    return (READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "reports" / "diagnostics" / label).resolve()


def _worklist_ref_exists(ctx: ProjectLike, ref: Any) -> bool:
    text = str(ref or "").strip()
    if not text:
        return True
    path = Path(text)
    candidates = [
        ctx.extracted_dir / path,
        ctx.root / path,
    ]
    if path.is_absolute():
        candidates.append(path)
    if any(candidate.exists() for candidate in candidates):
        return True
    name = path.name.casefold()
    if not name:
        return False
    return any(candidate.name.casefold() == name for candidate in ctx.extracted_dir.rglob("*") if candidate.is_file())


def _subroutine_ref_match(ctx: ProjectLike, ref: str) -> dict[str, Any] | None:
    text = str(ref or "").strip().strip('"')
    if not text:
        return None
    name = Path(text.replace("\\", "/")).name
    candidates = {text.casefold(), name.casefold(), Path(name).stem.casefold()}
    for script in ctx.manifest.get("scripts") or []:
        script_names = {
            str(script.get("object_name") or "").casefold(),
            Path(str(script.get("entry") or "")).stem.casefold(),
            Path(str(script.get("extracted_path") or "")).stem.casefold(),
        }
        if candidates & {value for value in script_names if value}:
            return script
    return None


def _operation_from_command_name(command_id: str) -> str | None:
    operation = registry_command_operation(command_id)
    if operation:
        return operation
    lowered = command_id.lower()
    fallbacks = {
        "addlabware": "add_labware",
        "getheadadapter": "get_head_adapter",
        "dropheadadapter": "drop_head_adapter",
        "pickuptips": "pick_up_tips",
        "settipsback": "set_tips_back",
        "aspirate": "aspirate",
        "dispense": "dispense",
        "mix": "mix",
        "droptips": "drop_tips",
        "gettips": "get_tips",
        "wash": "wash",
    }
    for token, value in fallbacks.items():
        if token in lowered:
            return value
    return None


def _supported_command_name(command_id: str) -> bool:
    if registry_command_supported(command_id):
        return True
    return _operation_from_command_name(command_id) is not None


def _finding(
    finding_id: str,
    severity: str,
    category: str,
    title: str,
    *,
    evidence: list[str] | None = None,
    next_steps: list[str] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "severity": severity if severity in SEVERITIES else "medium",
        "category": category,
        "title": title,
        "evidence": [str(item) for item in (evidence or []) if str(item).strip()],
        "next_steps": [str(item) for item in (next_steps or []) if str(item).strip()],
        "details": details or {},
    }


def _severity_rank(value: Any) -> int:
    try:
        return SEVERITIES.index(str(value))
    except ValueError:
        return len(SEVERITIES)


def _labware_evidence(item: dict[str, Any]) -> str:
    pieces = [str(item.get("label") or item.get("name") or "labware")]
    if item.get("catalog"):
        pieces.append(f"type={item['catalog']}")
    if item.get("deck_location"):
        pieces.append(f"deck={item['deck_location']}")
    if item.get("status"):
        pieces.append(f"status={item['status']}")
    return "; ".join(pieces)


def _script_label(script: dict[str, Any]) -> str:
    return str(script.get("qualified_name") or script.get("object_name") or script.get("entry") or "script")


def _looks_like_worklist(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return bool(text and (text.endswith(".gwl") or "worklist" in text))


def _error_excerpts(text: str) -> list[str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    return lines[:6]


def _slug(value: Any) -> str:
    chars = []
    previous_dash = False
    for char in str(value or "").casefold():
        if char.isalnum():
            chars.append(char)
            previous_dash = False
        elif not previous_dash:
            chars.append("-")
            previous_dash = True
    return "".join(chars).strip("-") or "diagnosis"
