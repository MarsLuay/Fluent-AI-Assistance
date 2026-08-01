"""Validation gates for ready-to-import Tecan protocol bundles."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping
from . import xml_compat as ET
from fluentcoder.expressions import (
    canonical_expression_key,
    expression_fields_for_command,
    expression_from_mapping,
    expression_inventory_from_xscr_text,
    parse_or_preserve_source_expression,
    render_expression,
    walk_expression_values,
)

from .api_v2.add_labware_golden import enrich_compiled_inventory_with_golden_compare
from .api_v2.command_validate import validate_compiled_xscr_commands
from .api_v2.generic_command_validate import validate_passthrough_commands_from_xscr
from .api_v2.xml_compare import NON_EXECUTABLE_OBJECT_TYPES
from .aliases import alias_candidates, load_alias_maps, resolve_alias
from .checksums import checksum_bridge_available, entry_checksum_state
from .command_registry import registry_command_operation, registry_command_support_status
from .expression_provenance import (
    load_expression_provenance_ledger,
    source_preserved_expression_allowlist_from_verified_ledger,
    verify_expression_provenance_ledger,
    verify_protocol_ir_expression_provenance,
)
from .gates import ValidationContext, readiness_evaluator
from .gates.ir import a200_adapter_catalog_issues
from .labware_contracts import (
    LABEL_CATALOG_MISMATCH_CODE,
    label_catalog_issue,
    preferred_label_catalogs_from_manifest,
    resolve_preferred_label_catalogs,
)
from .liquid_state import validate_liquid_state
from .protocol_ir import (
    load_protocol_ir,
    prompt_step_media_path,
    prompt_step_worktable_media_path,
    protocol_ir_from_python,
    protocol_ir_from_xscr,
    render_recreate_markdown,
)
from .protocol_ir_schema import (
    LIQUID_CLASS_OPERATIONS,
    VOLUME_OPERATIONS,
    migrate_protocol_ir,
)
from .readiness import (
    REVIEWABLE_READY,
    STRICT_READY,
    ReadinessGateStatus,
    coerce_readiness_gate_status,
    gate_status_in_policy,
    normalize_readiness_gate_status,
    normalize_readiness_gate_policy,
    readiness_policy_name,
    readiness_policy_statuses,
)
from .readiness_gates import (
    active_validation_gate_tuples,
    readiness_gate,
    readiness_gates,
    registered_readiness_gate_evaluators,
    required_offline_gate_count,
)
from .subroutine_dependencies import (
    resolve_subroutine_dependencies,
    validate_compiled_subroutine_references,
)
from .traceability import annotate_findings_with_trace, annotate_runtime_report_with_trace
from .variable_namespaces import (
    VARIABLE_HANDLING_NAMESPACE,
    variable_declaration_fragment_error,
)
from .worktable_diff import diff_worktable_requirements


READY_VALIDATION_VERSION = "tecan.ready_validation.v2"

GATE_DEFINITIONS = list(active_validation_gate_tuples())
FLUENT_CONTEXT_GATE = readiness_gate("fluent_context_check")
FLUENT_CONTEXT_GATE_LABEL = FLUENT_CONTEXT_GATE.gate_label
OPTIONAL_DIAGNOSTIC_GATE_IDS = {
    gate.id for gate in readiness_gates() if gate.is_optional_diagnostic
}
REQUIRED_OFFLINE_GATE_IDS = {
    gate.id for gate in readiness_gates() if gate.is_required_offline_gate
}


def validate_ready_to_import(
    *,
    compiled_xscr: Path,
    draft_path: Path | None = None,
    protocol_ir: Path | None = None,
    expression_provenance: Path | None = None,
    worklist: Path | None = None,
    source_projects: list[Path] | None = None,
    source_scripts: list[Path] | None = None,
    source_xscr: Path | None = None,
    provenance_source_artifacts: list[Path] | None = None,
    source_manifest: dict[str, Any] | None = None,
    recreate_guide: Path | None = None,
    validation_context: dict[str, Any] | None = None,
    ready_policy: Iterable[Any] = STRICT_READY,
) -> dict[str, Any]:
    """Run the required gates before a bundle can be copied to ready-to-import."""
    registered_readiness_gate_evaluators()
    context = validation_context or {}
    source_irs = _source_irs(source_xscr=source_xscr, source_scripts=source_scripts or [])
    ir, ir_error = _load_or_derive_ir(protocol_ir, draft_path)
    provenance_sources = provenance_source_artifacts or [
        *(source_projects or []),
        *(source_scripts or []),
        *([source_xscr] if source_xscr is not None else []),
    ]
    ledger_verification = verify_expression_provenance_ledger(
        load_expression_provenance_ledger(expression_provenance)
        if expression_provenance is not None
        else None,
        provenance_sources,
    )
    provenance_verification = verify_protocol_ir_expression_provenance(
        ir,
        ledger_verification,
    )
    source_preserved_allowlist = source_preserved_expression_allowlist_from_verified_ledger(
        ir,
        provenance_verification,
        validation_entry=compiled_xscr,
    )
    compiled_ir, compiled_error, compiled_inventory = _inspect_compiled_xscr(
        compiled_xscr,
        source_preserved_allowlist=source_preserved_allowlist,
        source_manifest=source_manifest,
    )
    compiled_inventory = {
        **compiled_inventory,
        "expression_provenance": provenance_verification,
    }
    traceability = context.get("traceability") if isinstance(context.get("traceability"), dict) else None
    if traceability:
        compiled_inventory = {
            **compiled_inventory,
            "fluentcontrol_findings": annotate_findings_with_trace(
                compiled_inventory.get("fluentcontrol_findings") or [],
                traceability,
            ),
        }
        if isinstance(context.get("fluent_context_check"), dict):
            context = {
                **context,
                "fluent_context_check": annotate_runtime_report_with_trace(
                    context["fluent_context_check"],
                    traceability,
                ),
            }
    domain_ir = ir or compiled_ir
    diff = (
        diff_worktable_requirements(ir, source_manifest=source_manifest, source_irs=source_irs)
        if ir is not None
        else None
    )
    evaluator_context = ValidationContext(
        make_gate=_gate,
        compiled_xscr=compiled_xscr,
        draft_path=draft_path,
        protocol_ir_path=protocol_ir,
        protocol_ir=ir,
        protocol_ir_error=ir_error,
        compiled_ir=compiled_ir,
        compiled_ir_error=compiled_error,
        compiled_inventory=compiled_inventory,
        worklist=worklist,
        source_projects=tuple(source_projects or ()),
        source_scripts=tuple(source_scripts or ()),
        source_xscr=source_xscr,
        source_irs=tuple(source_irs),
        source_manifest=source_manifest,
        recreate_guide=recreate_guide,
        worktable_diff=diff,
        validation_options=context,
        domain_ir=domain_ir,
    )

    gates = [
        _evaluate_registered_gate("zeia_parsed", evaluator_context),
        _evaluate_registered_gate("protocol_ir_schema", evaluator_context),
        _gate_labware(diff),
        _gate_liquid_classes(diff),
        _gate_worklists(diff, worklist),
        _gate_python_draft(draft_path),
        _gate_simulation(context),
        _gate_repair_plan(context),
        _gate_xscr(compiled_xscr, context),
        _gate_recreate(ir, recreate_guide),
        _gate_post_compile_xscr(compiled_ir, compiled_error, compiled_inventory),
        _gate_xscr_ir_roundtrip(ir, compiled_ir),
        _gate_volume_bounds(domain_ir, context),
        _gate_well_ranges(domain_ir),
        _gate_tip_capacity(domain_ir),
        _gate_liquid_class_compatibility(domain_ir, source_manifest),
        _gate_no_unapproved_raw_xml(
            draft_path,
            compiled_inventory,
            context,
        ),
        _gate_liquid_state(domain_ir, context),
        _evaluate_registered_gate("tip_boxes_resolve", evaluator_context),
        _evaluate_registered_gate("carriers_resolve", evaluator_context),
        _evaluate_registered_gate("device_aliases_resolve", evaluator_context),
        _evaluate_registered_gate("deck_layout_consistent", evaluator_context),
        _gate_checksums(compiled_xscr, source_projects or [], context),
        _gate_generated_zeia(source_projects or [], context),
        _gate_command_inventory(compiled_inventory, source_manifest, context),
        _gate_subroutine_dependencies(domain_ir, compiled_xscr, source_manifest, source_projects or [], context),
    ]
    fluent_gate = _gate_fluent_context_check(context)
    if fluent_gate is not None:
        gates.append(fluent_gate)
    host_config = context.get("host_instrument_configuration") or {}
    host_config_blocking = _host_config_blocks_readiness(host_config)
    required_gates = [gate for gate in gates if _is_required_offline_gate(gate)]
    optional_gates = [gate for gate in gates if _is_optional_diagnostic_gate(gate)]
    ready_policy = normalize_readiness_gate_policy(ready_policy)
    ready_policy_name = readiness_policy_name(ready_policy)
    ready_policy_statuses = readiness_policy_statuses(ready_policy)
    review_policy_name = readiness_policy_name(REVIEWABLE_READY)
    review_policy_statuses = readiness_policy_statuses(REVIEWABLE_READY)
    required_blocking = [gate for gate in required_gates if not gate_status_in_policy(gate.get("status"), ready_policy)]
    reviewable_blocking = [
        gate for gate in required_gates if not gate_status_in_policy(gate.get("status"), REVIEWABLE_READY)
    ]
    overall_blocking = [gate for gate in gates if not gate_status_in_policy(gate.get("status"), ready_policy)]
    optional_blocking = [gate for gate in optional_gates if not gate_status_in_policy(gate.get("status"), ready_policy)]
    review_gates = [gate for gate in required_gates if _gate_requires_review(gate)]
    publication_ready = not required_blocking and not host_config_blocking
    reviewable_ready = not reviewable_blocking and not host_config_blocking
    offline_status = "ready_to_import" if publication_ready else "validated_not_ready"
    if publication_ready:
        offline_summary = f"All required offline readiness gates passed under {ready_policy_name}."
    elif host_config_blocking:
        offline_summary = "Required host instrument configuration blocks publication."
    elif all(_gate_status(gate) == ReadinessGateStatus.NEEDS_REVIEW for gate in required_blocking):
        offline_summary = (
            f"Required offline readiness gates are reviewable but not publication-ready under {ready_policy_name}."
        )
    else:
        offline_summary = f"One or more required offline readiness gates are outside {ready_policy_name}."

    if not reviewable_ready:
        review_status = "validated_not_ready"
        review_summary = "Required offline validation did not pass; resolve blocking gates before review handoff."
    elif review_gates:
        review_status = "import_ready_needs_review"
        review_summary = (
            f"{len(review_gates)} required offline gate(s) passed with needs-review details. "
            "Complete the named review steps before treating the bundle as fully import-ready."
        )
    elif required_blocking:
        review_status = "import_ready_needs_review"
        review_summary = (
            f"{len(required_blocking)} required offline gate(s) are outside {ready_policy_name} but still reviewable. "
            "Complete the named review steps before treating the bundle as fully import-ready."
        )
    else:
        review_status = "hardware_review_required"
        review_summary = (
            "Required offline validation passed without outstanding offline review findings. "
            "A target-system operator still must review the bundle before hardware use."
        )
    if fluent_gate is None:
        load_status = "not_run"
        load_summary = (
            "Optional FluentControl import/load diagnostic was not run. Offline validation does not "
            "prove Script Editor can open the generated artifact."
        )
    elif fluent_gate.get("status") == "passed":
        load_status = "load_clean"
        load_summary = fluent_gate.get("summary") or "Optional FluentControl import/load diagnostic passed."
    else:
        load_status = "load_failed"
        load_summary = fluent_gate.get("summary") or "Optional FluentControl import/load diagnostic failed."
    trivial_pass_gates = [
        gate["id"]
        for gate in gates
        if gate["status"] == "passed" and (gate.get("details") or {}).get("trivial")
    ]
    return {
        "validation_version": READY_VALIDATION_VERSION,
        "ready_policy": ready_policy_name,
        "ready_policy_statuses": list(ready_policy_statuses),
        "review_policy": review_policy_name,
        "review_policy_statuses": list(review_policy_statuses),
        "ready": publication_ready,
        "gate_count": len(gates),
        "passed_count": sum(1 for gate in gates if _gate_status(gate) == ReadinessGateStatus.PASSED),
        "failed_count": len(overall_blocking),
        "required_gate_count": len(required_gates),
        "required_passed_count": sum(1 for gate in required_gates if _gate_status(gate) == ReadinessGateStatus.PASSED),
        "required_failed_count": len(required_blocking),
        "optional_gate_count": len(optional_gates),
        "optional_passed_count": sum(1 for gate in optional_gates if _gate_status(gate) == ReadinessGateStatus.PASSED),
        "optional_failed_count": len(optional_blocking),
        "blocking_count": len(required_blocking),
        "blocking_gates": [gate["id"] for gate in required_blocking],
        "reviewable_blocking_count": len(reviewable_blocking),
        "reviewable_blocking_gates": [gate["id"] for gate in reviewable_blocking],
        "needs_review_count": len(review_gates),
        "trivial_pass_count": len(trivial_pass_gates),
        "trivial_pass_gates": trivial_pass_gates,
        "offline_validation": {
            "status": offline_status,
            "summary": offline_summary,
            "policy": ready_policy_name,
            "policy_statuses": list(ready_policy_statuses),
            "required_gate_count": len(required_gates),
            "passed_count": sum(1 for gate in required_gates if _gate_status(gate) == ReadinessGateStatus.PASSED),
            "failed_count": len(required_blocking),
            "blocking_count": len(required_blocking),
            "blocking_gates": [gate["id"] for gate in required_blocking],
            "failing_gates": [gate["id"] for gate in required_blocking],
            "host_instrument_config_blocking": host_config_blocking,
        },
        "review_state": {
            "status": review_status,
            "summary": review_summary,
            "policy": review_policy_name,
            "policy_statuses": list(review_policy_statuses),
            "needs_review_count": len(review_gates),
            "gates": [gate["id"] for gate in review_gates],
        },
        "fluentcontrol_load_diagnostic": {
            "status": load_status,
            "summary": load_summary,
            "requested": bool(context.get("fluent_context_check_required")),
            "gate": FLUENT_CONTEXT_GATE_LABEL,
            "gate_present": fluent_gate is not None,
        },
        "full_zeia_export": context.get("full_zeia_export"),
        "partial_zeia_export_approved": bool(context.get("partial_zeia_export_approved")),
        "host_instrument_configuration": host_config or None,
        "host_instrument_config_blocking": host_config_blocking,
        "gates": gates,
    }


REQUIRED_GATE_COUNT = required_offline_gate_count()


def scaffold_validation_report(reason: str) -> dict[str, Any]:
    """Report emitted when ready validation cannot run (scaffold / no compiled XSCR).

    A scaffold is explicitly NOT validated and NOT ready to import. This keeps a
    stable, loud artifact in the build folder so an agent never mistakes an
    unvalidated scaffold for a passing bundle just because gates did not run.
    """
    return {
        "validation_version": READY_VALIDATION_VERSION,
        "ready_policy": "STRICT_READY",
        "ready_policy_statuses": list(readiness_policy_statuses(STRICT_READY)),
        "review_policy": "REVIEWABLE_READY",
        "review_policy_statuses": list(readiness_policy_statuses(REVIEWABLE_READY)),
        "ready": False,
        "scaffold": True,
        "reason": reason,
        "gate_count": REQUIRED_GATE_COUNT,
        "passed_count": 0,
        "failed_count": 0,
        "required_gate_count": REQUIRED_GATE_COUNT,
        "required_passed_count": 0,
        "required_failed_count": 0,
        "optional_gate_count": 0,
        "optional_passed_count": 0,
        "optional_failed_count": 0,
        "blocking_count": 0,
        "blocking_gates": [],
        "reviewable_blocking_count": 0,
        "reviewable_blocking_gates": [],
        "needs_review_count": 0,
        "trivial_pass_count": 0,
        "trivial_pass_gates": [],
        "offline_validation": {
            "status": "not_validated",
            "summary": "Required offline validation did not run for this scaffold.",
            "policy": "STRICT_READY",
            "policy_statuses": list(readiness_policy_statuses(STRICT_READY)),
            "required_gate_count": REQUIRED_GATE_COUNT,
            "passed_count": 0,
            "failed_count": 0,
            "blocking_count": 0,
            "blocking_gates": [],
            "failing_gates": [],
            "host_instrument_config_blocking": False,
        },
        "review_state": {
            "status": "not_validated",
            "summary": "Review state is unavailable because ready validation did not run.",
            "policy": "REVIEWABLE_READY",
            "policy_statuses": list(readiness_policy_statuses(REVIEWABLE_READY)),
            "needs_review_count": 0,
            "gates": [],
        },
        "fluentcontrol_load_diagnostic": {
            "status": "not_run",
            "summary": "Optional FluentControl import/load diagnostic did not run for this scaffold.",
            "requested": False,
            "gate": FLUENT_CONTEXT_GATE_LABEL,
            "gate_present": False,
        },
        "host_instrument_configuration": None,
        "host_instrument_config_blocking": False,
        "gates": [],
    }


def render_validation_markdown(report: dict[str, Any]) -> str:
    title = "Ready Validation"
    if report.get("scaffold"):
        lines = [
            f"# {title}",
            "",
            "- Result: `not validated`",
            "- Status: `scaffold only`",
            f"- Gates run: `0/{report.get('gate_count', REQUIRED_GATE_COUNT)}`",
            f"- Reason: {report.get('reason') or 'Ready validation did not run.'}",
            "",
            "## Not Ready To Import",
            "",
            "This is an unvalidated scaffold. None of the ready gates have run, so "
            "this bundle MUST NOT be copied into `ready-to-import` or treated as a "
            "validated artifact. Run the final generation pass with compile enabled "
            "(omit `--no-compile`) to produce a real `ready_validation.md` with all "
            f"{report.get('gate_count', REQUIRED_GATE_COUNT)} required gates.",
            "",
        ]
        host_config = report.get("host_instrument_configuration") or {}
        if host_config:
            lines.extend(_host_config_markdown_lines(host_config))
        return "\n".join(lines).rstrip() + "\n"
    readiness = report.get("readiness") if isinstance(report.get("readiness"), dict) else {}
    offline = readiness.get("offline_validation") or report.get("offline_validation") or {}
    review = readiness.get("review_state") or report.get("review_state") or {}
    load_diag = readiness.get("fluentcontrol_load_diagnostic") or report.get("fluentcontrol_load_diagnostic") or {}
    generated_import = readiness.get("generated_zeia_import") or {}
    script_editor_load = readiness.get("script_editor_load") or {}
    simulation = readiness.get("simulation") or {}
    hardware_run = readiness.get("hardware_run") or {}
    ready_policy_value = report.get("ready_policy") or offline.get("policy") or STRICT_READY
    ready_policy = str(report.get("ready_policy") or offline.get("policy") or "STRICT_READY")
    ready_policy_statuses = list(
        report.get("ready_policy_statuses")
        or offline.get("policy_statuses")
        or readiness_policy_statuses(ready_policy_value)
    )
    review_policy_value = report.get("review_policy") or review.get("policy") or REVIEWABLE_READY
    review_policy = str(report.get("review_policy") or review.get("policy") or "REVIEWABLE_READY")
    review_policy_statuses = list(
        report.get("review_policy_statuses")
        or review.get("policy_statuses")
        or readiness_policy_statuses(review_policy_value)
    )
    lines = [
        f"# {title}",
        "",
        f"- Result: `{'passed' if report.get('ready') else 'failed'}`",
        f"- Publication policy: `{ready_policy}` ({', '.join(ready_policy_statuses)})",
        f"- Review policy: `{review_policy}` ({', '.join(review_policy_statuses)})",
        f"- Readiness status: `{report.get('readiness_status') or ('ready_to_import' if report.get('ready') else 'validated_not_ready')}`",
        f"- Offline validation: `{offline.get('status') or ('ready_to_import' if report.get('ready') else 'validated_not_ready')}`",
        f"- Review state: `{review.get('status') or ('hardware_review_required' if report.get('ready') else 'validated_not_ready')}`",
        f"- FluentControl load diagnostic: `{load_diag.get('status') or 'not_run'}`",
        f"- Required gates within policy: `{report.get('required_gate_count', report.get('gate_count', 0)) - report.get('required_failed_count', 0)}/{report.get('required_gate_count', report.get('gate_count', 0))}`",
    ]
    optional_gate_count = int(report.get("optional_gate_count", 0) or 0)
    if optional_gate_count:
        lines.append(
            f"- Optional diagnostics passed: `{report.get('optional_passed_count', 0)}/{optional_gate_count}`"
        )
    trivial_gates = report.get("trivial_pass_gates") or []
    if trivial_gates:
        lines.append(
            f"- Trivial passes: `{len(trivial_gates)}` "
            f"({', '.join(trivial_gates)}) — these gates had nothing to check; "
            "confirm an empty result matches the intended protocol."
        )
    full_zeia = report.get("full_zeia_export") or {}
    if full_zeia:
        lines.append(f"- Full ZEIA export: `{full_zeia.get('status') or 'not_checked'}`")
        if report.get("partial_zeia_export_approved"):
            lines.append(
                "- Partial ZEIA approval: `true` — user explicitly approved continuing without a confirmed full export."
            )
    host_config = report.get("host_instrument_configuration") or {}
    if host_config:
        lines.append(f"- Host instrument configuration: `{host_config.get('status') or 'not_checked'}`")
        if report.get("host_instrument_config_blocking"):
            lines.append("- Host configuration blocker: `true` — an explicitly required host configuration was not found.")
    lines.extend(
        [
            "",
            "## Canonical Readiness",
            "",
            f"- Offline validation summary: {offline.get('summary') or ''}",
            f"- Review summary: {review.get('summary') or ''}",
            f"- FluentControl load summary: {load_diag.get('summary') or ''}",
            f"- Generated ZEIA import: `{generated_import.get('status') or 'unknown'}`",
            f"- Script Editor load: `{script_editor_load.get('status') or 'unknown'}`",
            f"- Simulation: `{simulation.get('status') or 'unknown'}`",
            f"- Hardware run: `{hardware_run.get('status') or 'unknown'}`",
        ]
    )
    lines.extend(
        [
            "",
            "## Readiness Boundaries",
            "",
            f"- Publication policy is `{ready_policy}`; anything outside `{ready_policy}` blocks publication.",
            f"- `{review_policy}` is the review handoff policy; `{', '.join(review_policy_statuses)}` are reviewable, while anything outside that set blocks review handoff.",
            "- `offline_validation.status: ready_to_import` means the required offline gates passed under the selected publication policy.",
            "- `review_state.status: import_ready_needs_review` means the bundle is not publication-ready yet or still carries review findings, but the issue is reviewable rather than a hard offline failure.",
            "- `review_state.status: hardware_review_required` is the default post-validation handoff state: the artifact is offline-valid, but hardware use still requires operator review on the target system.",
            f"- `fluentcontrol_load_diagnostic.status: load_clean` requires the optional {FLUENT_CONTEXT_GATE_LABEL} FluentControl import/load diagnostic or an equivalent manual FluentControl Script Editor open/load check against the generated artifact.",
            "- `fluentcontrol_load_diagnostic.status: load_failed` means the optional load diagnostic found a Script Editor load problem; it does not retroactively invalidate required offline gates.",
            "",
            "## Gates",
            "",
        ]
    )
    if host_config:
        lines.extend(_host_config_markdown_lines(host_config))
    for gate in report.get("gates") or []:
        lines.append(f"{gate.get('gate')}. {gate.get('name')}")
        lines.append(f"   - Status: `{gate.get('status')}`")
        lines.append(f"   - Summary: {gate.get('summary')}")
        details = gate.get("details") or {}
        for key, value in details.items():
            if value in (None, "", [], {}):
                continue
            rendered = json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
            lines.append(f"   - {key}: `{rendered}`")
    lines.append("")
    if (report.get("offline_validation") or {}).get("status") != "ready_to_import":
        lines.extend(
            [
                "## Blocking Rule",
                "",
                f"This draft must not be copied into `ready-to-import` until every required offline gate is within `{ready_policy}`.",
                "Optional FluentControl load diagnostics do not make the offline artifact structurally invalid, "
                "but they do affect whether the bundle can be called load-clean.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def validation_failure_message(report: dict[str, Any]) -> str:
    ready_policy_value = (
        report.get("ready_policy_statuses")
        or (report.get("offline_validation") or {}).get("policy_statuses")
        or report.get("ready_policy")
        or (report.get("offline_validation") or {}).get("policy")
        or STRICT_READY
    )
    ready_policy_statuses = ready_policy_value
    ready_policy = normalize_readiness_gate_policy(ready_policy_statuses)
    policy_name = str(
        report.get("ready_policy")
        or (report.get("offline_validation") or {}).get("policy")
        or readiness_policy_name(ready_policy)
    )
    failed = [
        gate
        for gate in report.get("gates") or []
        if _is_required_offline_gate(gate) and not _gate_allowed(gate, ready_policy)
    ]
    if report.get("host_instrument_config_blocking"):
        host_config = report.get("host_instrument_configuration") or {}
        return f"ready validation failed: required host instrument configuration not matched: {host_config.get('summary') or 'review host configuration report'}"
    if not failed:
        return "ready validation failed"
    summaries = "; ".join(
        f"{gate.get('gate')} {gate.get('name')} [status={_gate_status_text(gate)}]: {gate.get('summary')}"
        for gate in failed
    )
    return f"ready validation failed under {policy_name}: {summaries}"


def _is_optional_diagnostic_gate(gate: dict[str, Any]) -> bool:
    return str(gate.get("id") or "") in OPTIONAL_DIAGNOSTIC_GATE_IDS


def _is_required_offline_gate(gate: dict[str, Any]) -> bool:
    return str(gate.get("id") or "") in REQUIRED_OFFLINE_GATE_IDS


def _gate_needs_review(gate: dict[str, Any]) -> bool:
    details = gate.get("details") or {}
    return bool(details.get("needs_review") or details.get("needs_review_count"))


def _gate_status(gate: dict[str, Any]) -> ReadinessGateStatus | None:
    return normalize_readiness_gate_status(gate.get("status"))


def _gate_status_text(gate: dict[str, Any]) -> str:
    return str(coerce_readiness_gate_status(gate.get("status")))


def _gate_allowed(gate: dict[str, Any], policy: Iterable[Any]) -> bool:
    return gate_status_in_policy(gate.get("status"), policy)


def _gate_requires_review(gate: dict[str, Any]) -> bool:
    return _gate_status(gate) == ReadinessGateStatus.NEEDS_REVIEW or _gate_needs_review(gate)


def _host_config_markdown_lines(host_config: dict[str, Any]) -> list[str]:
    lines = ["## Host Instrument Configuration", ""]
    lines.append(f"- Summary: {host_config.get('summary') or ''}")
    expected = host_config.get("expected") or {}
    if expected.get("exact_names"):
        lines.append(f"- Expected exact names: `{', '.join(expected['exact_names'])}`")
    if expected.get("patterns"):
        lines.append(f"- Expected name patterns: `{', '.join(expected['patterns'])}`")
    if host_config.get("installed_configs"):
        lines.append(f"- Installed configs detected: `{', '.join(host_config['installed_configs'])}`")
    if host_config.get("matches"):
        lines.append(f"- Matching configs: `{', '.join(host_config['matches'])}`")
    instruction = host_config.get("user_instruction")
    if instruction:
        lines.append(f"- User action: {instruction}")
    lines.append("")
    return lines


def _host_config_blocks_readiness(host_config: dict[str, Any]) -> bool:
    expected = host_config.get("expected") if isinstance(host_config, dict) else {}
    return (
        isinstance(expected, dict)
        and bool(expected.get("required"))
        and str(host_config.get("status") or "") == "failed"
    )


def _gate(gate_id: str, status: str, summary: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    labels = {item[0]: item[1:] for item in GATE_DEFINITIONS}
    labels.setdefault("subroutine_additions_safe", ("Gate 26a", "added subroutine datastore metadata safe"))
    gate_label, name = labels[gate_id]
    return {
        "id": gate_id,
        "gate": gate_label,
        "name": name,
        "status": coerce_readiness_gate_status(status),
        "summary": summary,
        "details": details or {},
    }


def _gate_zeia(source_manifest: dict[str, Any] | None, source_projects: list[Path]) -> dict[str, Any]:
    """Compatibility facade for the registered source-archive evaluator."""
    return readiness_evaluator("zeia_parsed").evaluate(
        ValidationContext(
            make_gate=_gate,
            source_manifest=source_manifest,
            source_projects=tuple(source_projects),
        )
    )


def _gate_ir_schema(
    ir: dict[str, Any] | None,
    error: str,
    *,
    source_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compatibility facade for the registered protocol-IR evaluator."""
    return readiness_evaluator("protocol_ir_schema").evaluate(
        ValidationContext(
            make_gate=_gate,
            protocol_ir=ir,
            protocol_ir_error=error,
            source_manifest=source_manifest,
        )
    )


def _a200_adapter_catalog_issues(
    ir: dict[str, Any],
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> list[dict[str, str]]:
    """Compatibility facade for ZEIA preferred label/catalog checks."""
    return a200_adapter_catalog_issues(ir, preferred_label_catalogs)


# Older test helpers imported the Script2-named alias.
_script2_a200_catalog_issues = _a200_adapter_catalog_issues


def _gate_labware(diff: dict[str, Any] | None) -> dict[str, Any]:
    if diff is None:
        return _gate("labware_resolves", "failed", "Labware could not be checked without valid protocol IR.")
    missing = [item for item in diff.get("missing_labware") or [] if item.get("status") != "available"]
    if missing:
        return _gate("labware_resolves", "failed", "Some required labware names are not resolved.", {"labware": missing})
    return _gate("labware_resolves", "passed", "All required labware names resolve in the source context.")


def _gate_liquid_classes(diff: dict[str, Any] | None) -> dict[str, Any]:
    if diff is None:
        return _gate("liquid_classes_resolve", "failed", "Liquid classes could not be checked without valid protocol IR.")
    missing = [item for item in diff.get("required_liquid_classes") or [] if item.get("status") != "available"]
    if missing:
        return _gate("liquid_classes_resolve", "failed", "Some required liquid classes are missing or unverified.", {"liquid_classes": missing})
    return _gate("liquid_classes_resolve", "passed", "All required liquid classes resolve.")


def _gate_worklists(diff: dict[str, Any] | None, worklist: Path | None) -> dict[str, Any]:
    if diff is None:
        return _gate("worklist_paths_valid", "failed", "Worklist paths could not be checked without valid protocol IR.")
    missing = [item for item in diff.get("worklist_paths") or [] if item.get("status") != "available"]
    if missing and not (worklist and worklist.exists()):
        return _gate("worklist_paths_valid", "failed", "Some required worklist paths are missing or unverified.", {"worklists": missing})
    return _gate("worklist_paths_valid", "passed", "All required worklist paths are available or no worklists are required.")


def _gate_worktable_resource(
    gate_id: str,
    label_plural: str,
    detail_key: str,
    items: list[dict[str, Any]] | None,
    diff_present: bool,
) -> dict[str, Any]:
    """Legacy helper retained for direct test and monkeypatch compatibility."""
    if not diff_present:
        return _gate(gate_id, "failed", f"{label_plural.capitalize()} could not be checked without valid protocol IR.")
    items = items or []
    missing = [item for item in items if _norm_status(item) == "missing"]
    if missing:
        return _gate(
            gate_id,
            "failed",
            f"Some required {label_plural} are missing from the source context.",
            {detail_key: missing},
        )
    unverified = [item for item in items if _norm_status(item) == "unverified"]
    if unverified:
        return _gate(
            gate_id,
            "passed",
            f"Required {label_plural} could not be verified against the source context; confirm before import.",
            {detail_key: unverified, "needs_review": True},
        )
    if not items:
        return _gate(
            gate_id,
            "passed",
            f"No {label_plural} are required by the protocol IR.",
            {"trivial": True},
        )
    return _gate(
        gate_id,
        "passed",
        f"All {len(items)} required {label_plural} resolve in the source context.",
    )


def _norm_status(item: dict[str, Any]) -> str:
    return str(item.get("status") or "").strip().casefold()


def _gate_tip_boxes(diff: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility facade for the registered tip-box evaluator."""
    return readiness_evaluator("tip_boxes_resolve").evaluate(
        ValidationContext(make_gate=_gate, worktable_diff=diff)
    )


def _gate_carriers(diff: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility facade for the registered carrier evaluator."""
    return readiness_evaluator("carriers_resolve").evaluate(
        ValidationContext(make_gate=_gate, worktable_diff=diff)
    )


def _gate_device_aliases(diff: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility facade for the registered device-alias evaluator."""
    return readiness_evaluator("device_aliases_resolve").evaluate(
        ValidationContext(make_gate=_gate, worktable_diff=diff)
    )


def _gate_deck_layout(diff: dict[str, Any] | None, context: dict[str, Any]) -> dict[str, Any]:
    """Compatibility facade for the registered deck-layout evaluator."""
    return readiness_evaluator("deck_layout_consistent").evaluate(
        ValidationContext(
            make_gate=_gate,
            worktable_diff=diff,
            validation_options=context,
        )
    )


_LEGACY_REGISTERED_GATE_FACADES = {
    "zeia_parsed": _gate_zeia,
    "protocol_ir_schema": _gate_ir_schema,
    "tip_boxes_resolve": _gate_tip_boxes,
    "carriers_resolve": _gate_carriers,
    "device_aliases_resolve": _gate_device_aliases,
    "deck_layout_consistent": _gate_deck_layout,
}


def _evaluate_registered_gate(gate_id: str, context: ValidationContext) -> dict[str, Any]:
    """Run a registered evaluator, preserving test monkeypatch facades.

    Production evaluation uses the typed, static registry.  The legacy facade
    is consulted only when a test replaces it, so established tests can still
    inject a gate result without depending on registry internals.
    """
    original_facade = _LEGACY_REGISTERED_GATE_FACADES.get(gate_id)
    if original_facade is not None:
        current_facade = globals()[original_facade.__name__]
        if current_facade is not original_facade:
            return _invoke_legacy_registered_gate(gate_id, current_facade, context)
    return readiness_evaluator(gate_id).evaluate(context)


def _invoke_legacy_registered_gate(
    gate_id: str,
    facade: Any,
    context: ValidationContext,
) -> dict[str, Any]:
    if gate_id == "zeia_parsed":
        return facade(context.source_manifest, list(context.source_projects))
    if gate_id == "protocol_ir_schema":
        return facade(context.protocol_ir, context.protocol_ir_error)
    if gate_id == "tip_boxes_resolve":
        return facade(context.worktable_diff)
    if gate_id == "carriers_resolve":
        return facade(context.worktable_diff)
    if gate_id == "device_aliases_resolve":
        return facade(context.worktable_diff)
    if gate_id == "deck_layout_consistent":
        return facade(context.worktable_diff, dict(context.validation_options))
    raise KeyError(f"No legacy facade adapter for readiness gate {gate_id!r}.")


def _gate_checksums(
    compiled_xscr: Path,
    source_projects: list[Path],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Verify the generated ZEIA will carry valid FluentControl checksums.

    FluentControl validates ``<Checksum>`` on every datastore object at load, so
    any edited entry shipped with a blank checksum makes the bundle reject or
    prompt on import. This gate fails when blank checksums will ship, unless the
    operator waives it (acknowledging they will recompute on a FluentControl
    machine or accept the in-app recalculation prompt).

    When packaging has already run, the precise per-entry audit is passed in via
    ``context['project_checksum_audit']``. Otherwise the gate predicts the
    outcome from checksum-bridge availability and the compiled script's own
    checksum state, both known before packaging.
    """
    produces_archive = any(
        path.suffix.lower() == ".zeia" and path.exists() for path in source_projects
    )
    if not produces_archive:
        return _gate(
            "checksums_valid",
            "passed",
            "No project ZEIA is packaged, so no datastore checksums are produced.",
            {"trivial": True},
        )

    waived = bool(
        context.get("checksums_recompute_waived")
        or context.get("checksum_recompute_waived")
    )

    audit = context.get("project_checksum_audit")
    if isinstance(audit, dict):
        blank_entries = audit.get("blank_entries") or []
        absent_entries = audit.get("absent_entries") or []
        invalid_entries = audit.get("invalid_entries") or []
        problem_entries = [*blank_entries, *absent_entries, *invalid_entries]
        if not problem_entries:
            return _gate(
                "checksums_valid",
                "passed",
                "All edited generated-ZEIA entries carry valid checksums; "
                "preserved base entries keep their originals.",
            )
        details = {
            "blank_entries": blank_entries,
            "blank_count": len(blank_entries),
            "absent_entries": absent_entries,
            "absent_count": len(absent_entries),
            "invalid_entries": invalid_entries,
            "invalid_count": len(invalid_entries),
            "bridge_available": bool(audit.get("bridge_available")),
            "approval_keys": ["checksums_recompute_waived"],
        }
        if invalid_entries:
            return _gate(
                "checksums_valid",
                "failed",
                f"{len(invalid_entries)} edited generated-ZEIA entr(y/ies) have invalid checksums; "
                "FluentControl will reject them on import/load. Duplicate, malformed, or stale "
                "checksums must be repaired before packaging and cannot be waived.",
                details,
            )
        if waived:
            return _gate(
                "checksums_valid",
                "passed",
                f"{len(problem_entries)} edited entr(y/ies) ship without valid checksums; waived. "
                "Recompute on a FluentControl machine or accept the recalculation prompt before import.",
                {**details, "waived": True, "needs_review": True},
            )
        return _gate(
            "checksums_valid",
            "failed",
            f"{len(problem_entries)} edited generated-ZEIA entr(y/ies) lack valid checksums; "
            "FluentControl will reject them on import/load. Recompute on a FluentControl machine "
            "or set checksums_recompute_waived only after accepting the in-app recalculation path.",
            details,
        )

    # No packaged audit available yet: predict from bridge + compiled script.
    if checksum_bridge_available():
        return _gate(
            "checksums_valid",
            "passed",
            "FluentControl checksum bridge is available; edited entries will be re-checksummed "
            "during packaging.",
        )
    compiled_state = "absent"
    try:
        compiled_state = entry_checksum_state(compiled_xscr.read_bytes())
    except OSError:
        compiled_state = "absent"
    if compiled_state == "valid":
        return _gate(
            "checksums_valid",
            "passed",
            "Compiled script carries a valid checksum and no checksum-invalidating edits are "
            "expected.",
            {"needs_review": True},
        )
    details = {
        "bridge_available": False,
        "compiled_checksum_state": compiled_state,
        "approval_keys": ["checksums_recompute_waived"],
    }
    if waived:
        return _gate(
            "checksums_valid",
            "passed",
            "Checksum bridge is unavailable so edited entries will ship with blank checksums; "
            "waived. Recompute on a FluentControl machine or accept the in-app prompt before import.",
            {**details, "waived": True, "needs_review": True},
        )
    return _gate(
        "checksums_valid",
        "failed",
        "Checksum bridge (fluentcontrol_core) is unavailable, so the generated ZEIA will ship "
        "edited entries with blank checksums that FluentControl rejects or prompts to recalculate "
        "on import. Recompute on a FluentControl machine or set checksums_recompute_waived.",
        details,
    )


def _gate_generated_zeia(
    source_projects: list[Path],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Validate the packaged generated ZEIA as a one-file import artifact.

    The other gates check the IR and the standalone ``.xscr``; this gate opens the
    packaged ``generated_project.zeia`` itself and reports whether it is a
    structurally importable artifact (zip integrity, every ``<Reference>`` GUID
    resolving inside the archive, and ``meta/content.xml`` entries matching real
    files).

    The audit can only run after packaging writes the archive, so the precise
    result is supplied via ``context['project_archive_audit']``. During the
    pre-flight validation (before packaging) the audit is absent and the gate
    defers with a trivial pass; ``export_ready_to_import`` re-runs validation with
    the audit attached to produce the authoritative report shipped in the bundle.
    """
    produces_archive = any(
        path.suffix.lower() == ".zeia" and path.exists() for path in source_projects
    )
    if not produces_archive:
        return _gate(
            "generated_zeia_valid",
            "passed",
            "No project ZEIA is packaged, so there is no generated archive to validate.",
            {"trivial": True},
        )

    audit = context.get("project_archive_audit")
    if not isinstance(audit, dict):
        return _gate(
            "generated_zeia_valid",
            "passed",
            "Generated ZEIA is audited after packaging writes it; no archive exists yet at "
            "this pre-flight stage.",
            {"trivial": True, "deferred": True},
        )

    blocking = list(audit.get("blocking") or [])
    needs_review = list(audit.get("needs_review") or [])
    archives = audit.get("archives") or []
    if not audit.get("zip_ok", True) and not any(
        item.get("kind") in {"not_a_zip", "corrupt_entry", "unreadable_zip", "missing_archive"}
        for item in blocking
    ):
        blocking.append({"kind": "zip_integrity", "detail": "archive failed its zip integrity check"})

    if blocking:
        return _gate(
            "generated_zeia_valid",
            "failed",
            f"The packaged generated ZEIA has {len(blocking)} blocking problem(s) and will not "
            "load in FluentControl (unresolved references, datastore-metadata mismatch, or a "
            "corrupt archive). See project_import_report.md for the full list.",
            {
                "blocking": blocking,
                "needs_review": needs_review,
                "archives": archives,
            },
        )
    if needs_review:
        return _gate(
            "generated_zeia_valid",
            "passed",
            f"The packaged generated ZEIA opens and all used references resolve; "
            f"{len(needs_review)} item(s) need review (dependencies that must already exist in "
            "the target system).",
            {
                "needs_review": needs_review,
                "needs_review_count": len(needs_review),
                "archives": archives,
            },
        )
    return _gate(
        "generated_zeia_valid",
        "passed",
        "The packaged generated ZEIA opens cleanly; all references resolve inside the archive "
        "and datastore metadata matches the shipped files.",
        {"archives": archives},
    )


def _gate_subroutine_additions(
    source_projects: list[Path],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Validate the datastore metadata of subroutines ADDED to the generated ZEIA.

    Replacing an existing subroutine reuses its GUID/entry and is the safe path;
    adding a brand-new subroutine synthesizes datastore metadata (fresh GUID,
    incremented ``<V>``, ``<FileRef>``) and is inherently riskier. This gate:

    * passes trivially when no ZEIA is packaged or no subroutine was added,
    * fails when an added subroutine has a metadata defect that breaks datastore
      identity (missing entry, malformed GUID, or no nodedescription node), and
    * otherwise passes as needs-review, surfacing the additions and the
      recommendation to build the subroutine into the base ZEIA and re-run so it
      is replaced instead of added.

    The precise audit is produced after packaging and supplied via
    ``context['project_subroutine_audit']``; during pre-flight validation it is
    absent and the gate defers with a trivial pass.
    """
    produces_archive = any(
        path.suffix.lower() == ".zeia" and path.exists() for path in source_projects
    )
    if not produces_archive:
        return _gate(
            "subroutine_additions_safe",
            "passed",
            "No project ZEIA is packaged, so no subroutines are added.",
            {"trivial": True},
        )

    audit = context.get("project_subroutine_audit")
    if not isinstance(audit, dict):
        return _gate(
            "subroutine_additions_safe",
            "passed",
            "Added subroutines are audited after packaging writes the archive; nothing to "
            "check at this pre-flight stage.",
            {"trivial": True, "deferred": True},
        )

    added = audit.get("added") or []
    blocking = audit.get("blocking") or []
    needs_review = audit.get("needs_review") or []
    if blocking:
        return _gate(
            "subroutine_additions_safe",
            "failed",
            f"{len(blocking)} added subroutine(s) have datastore metadata defects (missing entry, "
            "malformed GUID, or no nodedescription node) that break the datastore object. See "
            "project_import_report.md.",
            {"blocking": blocking, "added_count": len(added), "needs_review": needs_review},
        )
    if not added:
        return _gate(
            "subroutine_additions_safe",
            "passed",
            "No subroutines were added; any packaged subroutines reuse existing base entries "
            "(the safe replace path).",
            {"trivial": True, "replaced_count": int(audit.get("replaced_count") or 0)},
        )
    return _gate(
        "subroutine_additions_safe",
        "passed",
        f"{len(added)} subroutine(s) were added to the base ZEIA with sound synthesized metadata. "
        "Prefer building them into the base so they are replaced rather than added; confirm before import.",
        {
            "added": added,
            "added_count": len(added),
            "needs_review": True,
            "metadata_review": needs_review,
            "approval_keys": ["subroutine_additions_acknowledged"],
        },
    )


def _gate_subroutine_dependencies(
    protocol_ir: dict[str, Any] | None,
    compiled_xscr: Path | None,
    source_manifest: dict[str, Any] | None,
    source_projects: list[Path],
    context: dict[str, Any],
) -> dict[str, Any]:
    resolution = resolve_subroutine_dependencies(protocol_ir, source_manifest)
    additions_gate = _gate_subroutine_additions(source_projects, context)
    calls = resolution.get("required") or []
    missing = resolution.get("missing") or []
    ambiguous = resolution.get("ambiguous") or []
    resolved = resolution.get("resolved") or []
    compiled_findings = validate_compiled_subroutine_references(compiled_xscr, resolved)
    package_audit = context.get("project_subroutine_audit") if isinstance(context.get("project_subroutine_audit"), dict) else None
    package_dependencies = (package_audit or {}).get("dependencies") or []
    package_findings = _subroutine_package_findings(resolved, package_dependencies, package_audit is not None)
    details = {
        "required": calls,
        "resolved": resolved,
        "missing": missing,
        "ambiguous": ambiguous,
        "compiled_reference_findings": compiled_findings,
        "package_findings": package_findings,
        "packaged_dependencies": package_dependencies,
        "addition_audit": additions_gate,
    }
    if not calls:
        return _gate(
            "subroutine_dependencies_valid",
            "passed",
            "No subroutine calls were present.",
            {"trivial": True, "addition_audit": additions_gate},
        )
    blockers = [*missing, *ambiguous, *compiled_findings, *package_findings]
    if additions_gate.get("status") == "failed":
        blockers.extend((additions_gate.get("details") or {}).get("blocking") or [])
    if blockers:
        return _gate(
            "subroutine_dependencies_valid",
            "failed",
            "One or more subroutine dependencies are missing, ambiguous, mismatched, or not packaged consistently.",
            details,
        )
    if additions_gate.get("status") != "passed":
        return _gate(
            "subroutine_dependencies_valid",
            additions_gate.get("status") or "needs_review",
            "Subroutine calls resolve, but added subroutine metadata still needs review.",
            details,
        )
    return _gate(
        "subroutine_dependencies_valid",
        "passed",
        f"Resolved and verified {len(resolved)} required subroutine dependency(ies).",
        details,
    )


def _gate_subroutine_calls_resolve(
    protocol_ir: dict[str, Any] | None,
    source_manifest: dict[str, Any] | None,
    compiled_xscr: Path | None,
    context: dict[str, Any],
    source_projects: list[Path] | None = None,
) -> dict[str, Any]:
    """Backward-compatible wrapper for the legacy gate helper name/signature."""
    return _gate_subroutine_dependencies(
        protocol_ir,
        compiled_xscr,
        source_manifest,
        source_projects or [],
        context,
    )


def _subroutine_package_findings(
    resolved: list[dict[str, Any]],
    package_dependencies: list[dict[str, Any]],
    package_audit_present: bool,
) -> list[dict[str, Any]]:
    if not package_audit_present:
        return []
    package_keys = {
        _norm_subroutine_dependency_key(item.get("object_name") or item.get("ref"))
        for item in package_dependencies
        if isinstance(item, dict)
    }
    findings: list[dict[str, Any]] = []
    for dep in resolved:
        key = _norm_subroutine_dependency_key(dep.get("object_name") or dep.get("ref"))
        if key and key in package_keys:
            continue
        findings.append(
            {
                "reason": "packaged_subroutine_dependency_missing",
                "message": "Generated ZEIA packaging did not record the resolved subroutine dependency.",
                "subroutine": dep.get("ref"),
                "object_name": dep.get("object_name"),
                "guid": dep.get("guid"),
                "entry": dep.get("entry"),
            }
        )
    return findings


def _norm_subroutine_dependency_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().strip('"').casefold())


def _gate_command_inventory(
    compiled_inventory: dict[str, Any],
    source_manifest: dict[str, Any] | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Validate the literal name strings the compiled XSCR command XML uses.

    The earlier gates resolve labware/liquid-class/device names from the IR, but
    a compiled ``.xscr`` can still embed a literal ``LabwareType``,
    ``LabwareName``/``LabwareLable``, ``DeviceAlias``, ``AvailableID``, or
    ``LiquidClassName`` string that FluentControl does not actually have. This
    post-compile inventory gate extracts those strings from the compiled command
    XML and diffs them against what the source context exposes
    (``source_manifest``) and the alias maps (``config/aliases/``).

    Classification mirrors the worktable-resource gates:

    * ``missing`` — the source context exposed an inventory for that category and
      the used name (after alias resolution) is not in it. FluentControl will not
      have the string, so this is blocking.
    * ``unverified`` — the source context exposed no inventory for that category,
      so the name cannot be confidently checked offline. The gate passes but
      surfaces it as ``needs_review`` instead of hiding the gap.

    Blocking findings can be acknowledged with ``command_inventory_approved`` (or
    ``command_names_approved``) once an operator confirms the target FluentControl
    system carries the names; the gate then passes as needs-review.
    """
    name_fields = (compiled_inventory or {}).get("name_fields") or {}
    categories = [
        ("unknown_labware_types", "labware_types", "catalog", "labware type"),
        ("unknown_labware_names", "labware_names", "labware", "labware name"),
        ("unknown_liquid_classes", "liquid_class_names", "liquid_class", "liquid class"),
        ("unknown_device_aliases", "device_aliases", "device_alias", "device alias"),
        ("unknown_available_ids", "available_ids", "device_alias", "available device ID"),
    ]
    total_used = sum(len(name_fields.get(field) or []) for _, field, _, _ in categories)
    if total_used == 0:
        return _gate(
            "command_inventory_resolves",
            "passed",
            "Compiled XSCR command XML exposes no labware/liquid-class/device name strings to validate.",
            {"trivial": True},
        )

    alias_maps = load_alias_maps()
    inventory = _manifest_name_inventory(source_manifest, alias_maps)
    blocking: dict[str, list[dict[str, Any]]] = {}
    needs_review: dict[str, list[dict[str, Any]]] = {}
    checked_counts: dict[str, int] = {}
    for detail_key, field, kind, label in categories:
        used = name_fields.get(field) or []
        if not used:
            continue
        checked_counts[field] = len(used)
        available = inventory[field]
        for name in used:
            status = _command_name_status(name, kind, available, alias_maps)
            if status == "available":
                continue
            record = _command_name_record(name, kind, status, label, alias_maps)
            if status == "missing":
                blocking.setdefault(detail_key, []).append(record)
            else:
                needs_review.setdefault(detail_key, []).append(record)

    approved = bool(
        context.get("command_inventory_approved")
        or context.get("command_names_approved")
    )
    if blocking:
        blocking_count = sum(len(items) for items in blocking.values())
        if approved:
            details = {
                **blocking,
                **needs_review,
                "needs_review": True,
                "approved": True,
                "checked_counts": checked_counts,
            }
            return _gate(
                "command_inventory_resolves",
                "passed",
                f"{blocking_count} compiled command name string(s) resolve nowhere in the source "
                "context, but were explicitly approved. Confirm the target FluentControl system "
                "carries them before import.",
                details,
            )
        details = {
            **blocking,
            **needs_review,
            "approval_keys": ["command_inventory_approved", "command_names_approved"],
            "checked_counts": checked_counts,
        }
        if needs_review:
            details["needs_review"] = True
        return _gate(
            "command_inventory_resolves",
            "failed",
            f"{blocking_count} compiled command name string(s) (catalog/labware/liquid-class/device) "
            "are absent from the source manifest and alias maps; FluentControl will not have them. "
            "Fix the names or approve via command_inventory_approved once confirmed in the target system.",
            details,
        )
    if needs_review:
        review_count = sum(len(items) for items in needs_review.values())
        return _gate(
            "command_inventory_resolves",
            "passed",
            f"All checkable compiled command name strings resolve; {review_count} name(s) could not "
            "be verified because the source manifest exposed no inventory for their category. "
            "Confirm before import.",
            {**needs_review, "needs_review": True, "checked_counts": checked_counts},
        )
    return _gate(
        "command_inventory_resolves",
        "passed",
        f"All {total_used} compiled command name string(s) resolve in the source context and alias maps.",
        {"checked_counts": checked_counts},
    )


def _manifest_name_inventory(
    source_manifest: dict[str, Any] | None,
    alias_maps: dict[str, dict[str, str]],
) -> dict[str, set[str]]:
    """Build the per-category set of names the source context actually provides."""
    inventory: dict[str, set[str]] = {
        "labware_types": set(),
        "labware_names": set(),
        "liquid_class_names": set(),
        "device_aliases": set(),
        "available_ids": set(),
    }
    if not isinstance(source_manifest, dict):
        return inventory

    def add(target: str, value: Any, kind: str) -> None:
        for candidate in alias_candidates(value, kind, alias_maps):
            inventory[target].add(_norm_name(candidate))

    for name in source_manifest.get("labware_names") or []:
        add("labware_names", name, "labware")
    for name in source_manifest.get("rack_types") or []:
        add("labware_types", name, "catalog")
    for name in source_manifest.get("labware_types") or []:
        add("labware_types", name, "catalog")
    for name in source_manifest.get("liquid_classes") or []:
        add("liquid_class_names", name, "liquid_class")
    for name in source_manifest.get("device_aliases") or []:
        add("device_aliases", name, "device_alias")
    for name in source_manifest.get("available_ids") or []:
        add("available_ids", name, "device_alias")

    for script in source_manifest.get("scripts") or []:
        deps = script.get("dependencies") or {} if isinstance(script, dict) else {}
        for name in deps.get("labware_names") or []:
            add("labware_names", name, "labware")
        for name in deps.get("rack_labels") or []:
            add("labware_names", name, "labware")
        for name in deps.get("rack_types") or []:
            add("labware_types", name, "catalog")
        for name in deps.get("liquid_classes") or []:
            add("liquid_class_names", name, "liquid_class")
        for name in deps.get("device_aliases") or []:
            add("device_aliases", name, "device_alias")
        for name in deps.get("available_ids") or []:
            add("available_ids", name, "device_alias")
    return inventory


def _command_name_status(
    name: str,
    kind: str,
    available: set[str],
    alias_maps: dict[str, dict[str, str]],
) -> str:
    if not available:
        return "unverified"
    candidates = alias_candidates(name, kind, alias_maps) or [name]
    if any(_norm_name(candidate) in available for candidate in candidates):
        return "available"
    return "missing"


def _command_name_record(
    name: str,
    kind: str,
    status: str,
    label: str,
    alias_maps: dict[str, dict[str, str]],
) -> dict[str, Any]:
    resolved = resolve_alias(name, kind, alias_maps)
    record = {"name": name, "status": status, "category": label}
    if resolved and resolved != name:
        record["resolved_name"] = resolved
    return record


def _norm_name(value: Any) -> str:
    return str(value or "").strip().casefold()


def _gate_python_draft(draft_path: Path | None) -> dict[str, Any]:
    if draft_path is None or not draft_path.exists():
        return _gate("python_draft_generated", "failed", "Python draft was not generated.")
    try:
        tree = ast.parse(draft_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return _gate("python_draft_generated", "failed", f"Python draft has syntax errors: {exc}")
    has_build = any(isinstance(node, ast.FunctionDef) and node.name == "build_worktable" for node in tree.body)
    if not has_build:
        return _gate("python_draft_generated", "failed", "Python draft does not define build_worktable().")
    return _gate("python_draft_generated", "passed", "Python draft exists and defines build_worktable().")


def _gate_simulation(context: dict[str, Any]) -> dict[str, Any]:
    if "simulation_passed" in context:
        return _gate(
            "simulation_passes",
            "passed" if context.get("simulation_passed") else "failed",
            "Simulation passed." if context.get("simulation_passed") else "Simulation did not pass.",
        )
    data = context.get("simulation")
    if isinstance(data, dict) and str(data.get("status") or "").lower() in {"ok", "passed", "pass", "success"} and not data.get("failure"):
        return _gate("simulation_passes", "passed", "Simulation JSON reports a passing status.")
    return _gate("simulation_passes", "failed", "No passing simulation result was provided.")


def _gate_repair_plan(context: dict[str, Any]) -> dict[str, Any]:
    plan = context.get("repair_plan")
    if plan is None:
        return _gate("repair_plan_clear", "failed", "No repair plan was provided.")
    actions = plan.get("actions") or [] if isinstance(plan, dict) else []
    critical = [action for action in actions if action.get("status") == "needs_review"]
    if critical:
        return _gate("repair_plan_clear", "failed", "Repair plan has unresolved needs_review actions.", {"actions": critical})
    return _gate("repair_plan_clear", "passed", "Repair plan has no unresolved critical errors.")


def _gate_xscr(compiled_xscr: Path, context: dict[str, Any]) -> dict[str, Any]:
    compile_passed = context.get("compile_passed")
    if compile_passed is False:
        return _gate("xscr_compiles", "failed", "Compile step failed.")
    if not compiled_xscr.exists() or compiled_xscr.stat().st_size == 0:
        return _gate("xscr_compiles", "failed", "Compiled XSCR file is missing or empty.")
    return _gate("xscr_compiles", "passed", "Compiled XSCR file exists.")


def _gate_recreate(ir: dict[str, Any] | None, recreate_guide: Path | None) -> dict[str, Any]:
    if ir is None:
        return _gate("recreate_matches_ir", "failed", "RECREATE_SCRIPT.md cannot be checked without valid protocol IR.")
    expected = render_recreate_markdown(
        ir,
        generated_files={
            "ir": "protocol.ir.json",
            "python": "protocol_draft.py",
            "xscr": "generated_script.xscr",
            "gwl": "generated_worklist.gwl",
        },
    )
    if recreate_guide is None or not recreate_guide.exists():
        return _gate("recreate_matches_ir", "passed", "Bundle recreate guide will be generated directly from protocol IR.")
    actual = recreate_guide.read_text(encoding="utf-8")
    protocol_name = (ir.get("protocol") or {}).get("name") or "Generated protocol"
    required_fragments = [
        f"# Recreate Script: {protocol_name}",
        "This guide is generated from the same canonical protocol IR",
        "## Manual FluentControl Steps",
        "## IR Command Reference",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in actual]
    expected_command_count = len(ir.get("steps") or [])
    actual_command_count = actual.count("   - Command name:")
    if missing or actual_command_count != expected_command_count:
        return _gate(
            "recreate_matches_ir",
            "failed",
            "Provided RECREATE_SCRIPT.md does not match the protocol IR structure.",
            {
                "missing_fragments": missing,
                "expected_command_count": expected_command_count,
                "actual_command_count": actual_command_count,
            },
        )
    if expected == actual:
        return _gate("recreate_matches_ir", "passed", "Provided RECREATE_SCRIPT.md exactly matches protocol IR.")
    return _gate("recreate_matches_ir", "passed", "Provided RECREATE_SCRIPT.md matches the protocol IR structure.")


def _gate_post_compile_xscr(
    compiled_ir: dict[str, Any] | None,
    error: str,
    inventory: dict[str, Any],
) -> dict[str, Any]:
    if compiled_ir is None:
        return _gate(
            "post_compile_xscr_reinspect",
            "failed",
            error or "Compiled XSCR could not be parsed after compile.",
        )
    if not inventory.get("command_ids") and not compiled_ir.get("steps"):
        return _gate(
            "post_compile_xscr_reinspect",
            "failed",
            "Compiled XSCR parsed but no command objects or IR steps were found.",
        )
    command_validation = inventory.get("command_validation") if isinstance(inventory.get("command_validation"), dict) else {}
    generic_validation = (
        inventory.get("generic_command_validation")
        if isinstance(inventory.get("generic_command_validation"), dict)
        else {}
    )
    fc_native_compare = inventory.get("fc_native_xml_compare") if isinstance(inventory.get("fc_native_xml_compare"), dict) else {}
    recipe_golden = (
        inventory.get("verification_recipe_add_labware_golden")
        if isinstance(inventory.get("verification_recipe_add_labware_golden"), dict)
        else {}
    )
    fluent_findings = inventory.get("fluentcontrol_findings") or []
    expression_inventory = (
        inventory.get("expression_inventory")
        if isinstance(inventory.get("expression_inventory"), dict)
        else {}
    )
    details = {
        "compiled_step_count": len(compiled_ir.get("steps") or []),
        "compiled_command_count": len(inventory.get("command_ids") or []),
    }
    if command_validation:
        details["command_validation"] = command_validation
        details["command_validation_failures"] = bool(command_validation.get("failures"))
    if generic_validation:
        details["generic_command_validation"] = generic_validation
        details["generic_command_validation_failures"] = bool(generic_validation.get("failures"))
    if fc_native_compare:
        details["fc_native_xml_compare"] = fc_native_compare
    if recipe_golden:
        details["verification_recipe_add_labware_golden"] = recipe_golden
    if expression_inventory:
        details["expression_inventory"] = {
            "valid": expression_inventory.get("valid"),
            "record_count": expression_inventory.get("record_count", 0),
            "failure_count": expression_inventory.get("failure_count", 0),
            "failures": (expression_inventory.get("failures") or [])[:20],
        }
    expression_provenance = inventory.get("expression_provenance")
    if isinstance(expression_provenance, dict):
        details["expression_provenance"] = expression_provenance

    needs_review = any(
        summary.get("status") == "needs_review"
        for summary in (fc_native_compare, recipe_golden)
        if isinstance(summary, dict)
    )
    if needs_review:
        details["needs_review"] = True
    if fluent_findings:
        details["findings"] = fluent_findings
        return _gate(
            "post_compile_xscr_reinspect",
            "failed",
            "Compiled XSCR contains FluentControl field/runtime issues that must be fixed before handoff.",
            details,
        )
    return _gate(
        "post_compile_xscr_reinspect",
        "passed",
        "Compiled XSCR parses back into canonical IR.",
        details,
    )


def _gate_xscr_ir_roundtrip(
    ir: dict[str, Any] | None,
    compiled_ir: dict[str, Any] | None,
) -> dict[str, Any]:
    if ir is None:
        return _gate("xscr_ir_roundtrip_matches", "failed", "Roundtrip comparison needs valid protocol IR.")
    if compiled_ir is None:
        return _gate("xscr_ir_roundtrip_matches", "failed", "Roundtrip comparison needs re-inspected compiled XSCR.")

    canonical_ir = migrate_protocol_ir(ir)
    canonical_compiled_ir = migrate_protocol_ir(compiled_ir)
    expected = _step_signatures(canonical_ir)
    actual = _step_signatures(canonical_compiled_ir)
    expected_setup_expressions = _setup_expression_signatures(canonical_ir)
    actual_setup_expressions = _setup_expression_signatures(canonical_compiled_ir)
    setup_expressions_match = not expected_setup_expressions or all(
        actual_setup_expressions.get(label) == expressions
        for label, expressions in expected_setup_expressions.items()
    )
    if expected != actual or not setup_expressions_match:
        return _gate(
            "xscr_ir_roundtrip_matches",
            "failed",
            "Compiled XSCR IR differs from the source protocol IR.",
            {
                "expected_step_count": len(expected),
                "actual_step_count": len(actual),
                "expected": expected[:20],
                "actual": actual[:20],
                "expected_setup_expressions": expected_setup_expressions,
                "actual_setup_expressions": actual_setup_expressions,
            },
        )
    return _gate(
        "xscr_ir_roundtrip_matches",
        "passed",
        "Compiled XSCR roundtrip preserves operation order, targets, volumes, and liquid classes.",
        {"step_count": len(expected)},
    )


def _liquid_handling_step_count(ir: dict[str, Any] | None) -> int:
    if not isinstance(ir, dict):
        return 0
    return sum(
        1
        for step in ir.get("steps") or []
        if isinstance(step, dict) and step.get("operation") in VOLUME_OPERATIONS
    )


def _gate_volume_bounds(ir: dict[str, Any] | None, context: dict[str, Any]) -> dict[str, Any]:
    if ir is None:
        return _gate("volume_bounds_valid", "failed", "Volume bounds cannot be checked without valid protocol IR.")
    failures = []
    checked = 0
    default_max = _number(context.get("max_volume_ul")) or 1000
    mca_max = _number(context.get("max_mca_volume_ul")) or 100
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") not in VOLUME_OPERATIONS:
            continue
        checked += 1
        volume = _number(step.get("volume_ul"))
        max_volume = mca_max if _is_mca_step(step) else default_max
        if volume is None:
            failures.append(_step_failure(step, "missing_volume_ul", "Volume is not numeric."))
        elif volume <= 0:
            failures.append(_step_failure(step, "non_positive_volume", f"Volume {volume:g} uL must be greater than 0."))
        elif volume > max_volume:
            failures.append(
                _step_failure(
                    step,
                    "volume_exceeds_bound",
                    f"Volume {volume:g} uL exceeds the {max_volume:g} uL bound.",
                    {"max_volume_ul": max_volume},
                )
            )
    if failures:
        return _gate("volume_bounds_valid", "failed", "Some liquid handling volumes are outside allowed bounds.", {"failures": failures})
    if checked == 0:
        return _gate(
            "volume_bounds_valid",
            "passed",
            "No liquid handling volume operations were present in the IR.",
            {"trivial": True},
        )
    return _gate("volume_bounds_valid", "passed", f"All {checked} liquid handling volume(s) are within configured bounds.")


def _gate_well_ranges(ir: dict[str, Any] | None) -> dict[str, Any]:
    if ir is None:
        return _gate("well_ranges_valid", "failed", "Well ranges cannot be checked without valid protocol IR.")
    labware_by_label = {
        str(item.get("label") or ""): item
        for item in ir.get("labware") or []
        if isinstance(item, dict) and item.get("label")
    }
    failures = []
    checked = 0
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        target = str(step.get("target_labware") or step.get("source_labware") or step.get("destination_labware") or "")
        dimensions = _labware_dimensions(labware_by_label.get(target) or {})
        for key, value in _well_values(step):
            for well in _expand_well_tokens(value):
                parsed = _parse_well(well)
                if parsed is None:
                    failures.append(_step_failure(step, "invalid_well_syntax", f"{key}={well!r} is not a valid well reference."))
                    continue
                checked += 1
                row, column = parsed
                if row > dimensions["rows"] or column > dimensions["columns"]:
                    failures.append(
                        _step_failure(
                            step,
                            "well_out_of_range",
                            f"{key}={well!r} is outside {dimensions['rows']}x{dimensions['columns']} labware bounds.",
                            {"rows": dimensions["rows"], "columns": dimensions["columns"]},
                        )
                    )
    if failures:
        return _gate("well_ranges_valid", "failed", "Some explicit well references are outside labware bounds.", {"failures": failures})
    if checked == 0:
        return _gate("well_ranges_valid", "passed", "No explicit well references were present.", {"trivial": True})
    return _gate("well_ranges_valid", "passed", f"Validated {checked} explicit well reference(s).")


def _gate_tip_capacity(ir: dict[str, Any] | None) -> dict[str, Any]:
    if ir is None:
        return _gate("tip_capacity_valid", "failed", "Tip capacity cannot be checked without valid protocol IR.")
    steps = [step for step in ir.get("steps") or [] if isinstance(step, dict)]
    has_tip_strategy = any(_is_tip_pickup(step) or _is_tip_release(step) for step in steps)
    if not has_tip_strategy:
        return _gate("tip_capacity_valid", "passed", "No explicit tip handling was present in IR.", {"trivial": True})

    labware_by_label = {
        str(item.get("label") or ""): item
        for item in ir.get("labware") or []
        if isinstance(item, dict) and item.get("label")
    }
    active_capacity: float | None = None
    failures = []
    checked = 0
    for step in steps:
        if _is_tip_pickup(step):
            target = str(step.get("target_labware") or "")
            active_capacity = _tip_capacity_ul(labware_by_label.get(target) or {}, step)
            if active_capacity is None:
                failures.append(_step_failure(step, "unknown_tip_capacity", f"Tip capacity for {target or 'selected tip box'} is unknown."))
            continue
        if _is_tip_release(step):
            active_capacity = None
            continue
        if step.get("operation") in VOLUME_OPERATIONS:
            volume = _number(step.get("volume_ul"))
            if active_capacity is None:
                failures.append(_step_failure(step, "liquid_handling_without_active_tips", "Liquid handling occurs without active picked-up tips in the IR sequence."))
            elif volume is not None:
                checked += 1
                if volume > active_capacity:
                    failures.append(
                        _step_failure(
                            step,
                            "volume_exceeds_tip_capacity",
                            f"Volume {volume:g} uL exceeds active tip capacity {active_capacity:g} uL.",
                            {"tip_capacity_ul": active_capacity},
                        )
                    )
    if failures:
        return _gate("tip_capacity_valid", "failed", "Tip strategy cannot support all liquid handling steps.", {"failures": failures})
    return _gate("tip_capacity_valid", "passed", f"Tip capacity checked for {checked} liquid handling step(s).")


def _gate_liquid_class_compatibility(
    ir: dict[str, Any] | None,
    source_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    if ir is None:
        return _gate("liquid_class_compatible", "failed", "Liquid class compatibility cannot be checked without valid protocol IR.")
    alias_maps = load_alias_maps()
    declared = {
        resolve_alias(item.get("name"), "liquid_class", alias_maps)
        for item in ir.get("liquid_classes") or []
        if isinstance(item, dict) and item.get("name")
    }
    available = {
        resolve_alias(name, "liquid_class", alias_maps)
        for name in (source_manifest or {}).get("liquid_classes") or []
        if name
    }
    compatibility = _liquid_class_compatibility_map(source_manifest, alias_maps)
    failures = []
    checked = 0
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") not in LIQUID_CLASS_OPERATIONS:
            continue
        checked += 1
        raw_liquid_class = str(step.get("liquid_class") or "")
        liquid_class = resolve_alias(raw_liquid_class, "liquid_class", alias_maps)
        if not raw_liquid_class:
            failures.append(_step_failure(step, "missing_liquid_class", "Liquid handling step has no liquid class."))
            continue
        if declared and liquid_class not in declared:
            failures.append(_step_failure(step, "undeclared_liquid_class", f"{raw_liquid_class!r} is not listed in IR liquid_classes."))
        if available and liquid_class not in available:
            failures.append(_step_failure(step, "unavailable_liquid_class", f"{raw_liquid_class!r} is not available in the source context."))
        allowed_operations = compatibility.get(liquid_class)
        if allowed_operations and str(step.get("operation")) not in allowed_operations:
            failures.append(
                _step_failure(
                    step,
                    "operation_not_supported_by_liquid_class",
                    f"{raw_liquid_class!r} is not declared compatible with {step.get('operation')!r}.",
                    {"allowed_operations": sorted(allowed_operations)},
                )
            )
    if failures:
        return _gate("liquid_class_compatible", "failed", "Some liquid classes are incompatible with their operations.", {"failures": failures})
    if checked == 0:
        return _gate(
            "liquid_class_compatible",
            "passed",
            "No liquid-class operations were present in the IR.",
            {"trivial": True},
        )
    return _gate("liquid_class_compatible", "passed", f"Liquid classes are compatible for {checked} liquid handling step(s).")


def _gate_no_unapproved_raw_xml(
    draft_path: Path | None,
    compiled_inventory: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    draft_calls = _raw_xml_calls(draft_path)
    unsupported = compiled_inventory.get("unsupported_commands") or []
    approved = bool(
        context.get("allow_unsupported_raw_xml")
        or context.get("unsupported_raw_xml_approved")
        or context.get("raw_xml_approved")
    )
    approved_ids = {str(value) for value in context.get("approved_unsupported_command_ids") or []}
    unapproved_draft_calls = [] if approved else [
        item
        for item in draft_calls
        if not _raw_xml_call_approved(item, approved_ids)
    ]
    unapproved = [
        item
        for item in unsupported
        if not approved and str(item.get("command_id") or "") not in approved_ids
    ]
    if unapproved_draft_calls or unapproved:
        return _gate(
            "no_unapproved_raw_xml",
            "failed",
            "Unsupported raw XML or compiled command gaps require explicit approval.",
            {
                "draft_raw_xml_calls": unapproved_draft_calls,
                "unsupported_commands": unapproved,
                "approval_keys": [
                    "allow_unsupported_raw_xml",
                    "unsupported_raw_xml_approved",
                    "raw_xml_approved",
                    "approved_unsupported_command_ids",
                ],
            },
        )
    if draft_calls or unsupported:
        return _gate(
            "no_unapproved_raw_xml",
            "passed",
            "Unsupported raw XML usage was explicitly approved.",
            {
                "approved_draft_raw_xml_calls": draft_calls,
                "approved_unsupported_commands": unsupported,
            },
        )
    return _gate("no_unapproved_raw_xml", "passed", "No unsupported raw XML or unsupported compiled commands were found.")


def _gate_liquid_state(ir: dict[str, Any] | None, context: dict[str, Any]) -> dict[str, Any]:
    if ir is None:
        return _gate("liquid_state_valid", "failed", "Liquid state cannot be checked without valid protocol IR.")
    report = context.get("liquid_state")
    if not isinstance(report, dict):
        report = validate_liquid_state(ir)
    details = {
        "status": report.get("status"),
        "failure_count": report.get("failure_count"),
        "unresolved_count": report.get("unresolved_count"),
        "warning_count": report.get("warning_count"),
        "failures": (report.get("failures") or [])[:20],
        "unresolved": (report.get("unresolved") or [])[:20],
        "assumptions": (report.get("assumptions") or [])[:20],
    }
    if report.get("status") == "passed":
        if _liquid_handling_step_count(ir) == 0:
            details = {**details, "trivial": True}
        return _gate(
            "liquid_state_valid",
            "passed",
            report.get("summary") or "Robotools-style liquid state validation passed.",
            details,
        )
    return _gate(
        "liquid_state_valid",
        "failed",
        report.get("summary") or "Robotools-style liquid state validation did not pass.",
        details,
    )


def _gate_fluent_context_check(context: dict[str, Any]) -> dict[str, Any] | None:
    required = bool(context.get("fluent_context_check_required"))
    report = context.get("fluent_context_check")
    if report is None and not required:
        return None
    if not isinstance(report, dict):
        return _gate(
            "fluent_context_check",
            "failed",
            "The optional FluentControl import/load diagnostic was requested but no runtime result was provided.",
            {"required": required},
        )

    status = str(report.get("status") or "").lower()
    ok = bool(report.get("ok")) and status not in {"failed", "unavailable"}
    errors = [
        *[str(value) for value in (report.get("errors") or []) if str(value).strip()],
        *[str(value) for value in (report.get("runtime_errors") or []) if str(value).strip()],
    ]
    if ok and not errors:
        return _gate(
            "fluent_context_check",
            "passed",
            report.get("summary") or "FluentControl import/load diagnostic passed.",
            _compact_fluent_context_details(report),
        )
    return _gate(
        "fluent_context_check",
        "failed",
        report.get("summary") or "FluentControl import/load diagnostic did not pass.",
        _compact_fluent_context_details(report),
    )


def _compact_fluent_context_details(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "provider",
        "method",
        "simulation_mode",
        "status",
        "xscr_path",
        "state",
        "last_error",
        "errors",
        "runtime_errors",
        "diagnostics",
        "messages",
        "command",
        "returncode",
        "duration_seconds",
        "stdout_sample",
        "stderr_sample",
    )
    details = {key: report.get(key) for key in keys if report.get(key) not in (None, "", [], {})}
    nested = report.get("details")
    if isinstance(nested, dict):
        details["details"] = {
            key: value
            for key, value in nested.items()
            if value not in (None, "", [], {})
        }
    return details


def _load_or_derive_ir(protocol_ir: Path | None, draft_path: Path | None) -> tuple[dict[str, Any] | None, str]:
    if protocol_ir is not None and protocol_ir.exists():
        try:
            return load_protocol_ir(protocol_ir), ""
        except Exception as exc:
            return None, str(exc)
    if draft_path is not None and draft_path.exists():
        try:
            return protocol_ir_from_python(draft_path), ""
        except Exception as exc:
            return None, str(exc)
    return None, "No protocol IR or Python draft was provided."


def _inspect_compiled_xscr(
    path: Path,
    *,
    source_preserved_allowlist: Iterable[dict[str, Any]] | None = None,
    source_manifest: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str, dict[str, Any]]:
    try:
        compiled_ir = protocol_ir_from_xscr(path)
        return compiled_ir, "", _compiled_command_inventory(
            path,
            ir=compiled_ir,
            source_preserved_allowlist=source_preserved_allowlist,
            source_manifest=source_manifest,
        )
    except Exception as exc:
        return None, str(exc), _compiled_command_inventory(
            path,
            source_preserved_allowlist=source_preserved_allowlist,
            source_manifest=source_manifest,
        )


# FluentControl command XML carries literal catalog/labware/liquid-class/device
# strings under these element tags. ``LabwareLable`` is FluentControl's own
# (misspelled) tag for a labware label; both spellings appear in the wild.
COMMAND_NAME_FIELD_TAGS: dict[str, str] = {
    "LabwareType": "labware_types",
    "LabwareName": "labware_names",
    "LabwareLable": "labware_names",
    "LabwareLabel": "labware_names",
    "DeviceAlias": "device_aliases",
    "AvailableID": "available_ids",
    "LiquidClassName": "liquid_class_names",
    "LiquidClassNameBySelection": "liquid_class_names",
}


def _empty_command_name_fields() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for category in COMMAND_NAME_FIELD_TAGS.values():
        out.setdefault(category, [])
    return out


def _compiled_command_inventory(
    path: Path,
    *,
    ir: dict[str, Any] | None = None,
    source_preserved_allowlist: Iterable[dict[str, Any]] | None = None,
    preferred_label_catalogs: Mapping[str, str] | None = None,
    source_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    preferred = preferred_label_catalogs
    if preferred is None:
        preferred = preferred_label_catalogs_from_manifest(source_manifest)
    if not preferred and isinstance(ir, Mapping):
        preferred = resolve_preferred_label_catalogs(manifest=ir.get("source_manifest"))
    command_validation = validate_compiled_xscr_commands(path).as_dict()
    generic_validation = validate_passthrough_commands_from_xscr(path).as_dict()
    if not path.exists():
        return enrich_compiled_inventory_with_golden_compare({
            "command_ids": [],
            "unsupported_commands": [],
            "name_fields": _empty_command_name_fields(),
            "fluentcontrol_findings": [],
            "command_validation": command_validation,
            "generic_command_validation": generic_validation,
        }, ir=ir, xscr_path=path, xscr_text="")
    text = ""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        text = ""
    namespace_findings = _compiled_xsi_type_namespace_findings(text)
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        return enrich_compiled_inventory_with_golden_compare({
            "command_ids": [],
            "unsupported_commands": [],
            "name_fields": _empty_command_name_fields(),
            "fluentcontrol_findings": [
                *namespace_findings,
                *(command_validation.get("failures") or []),
                *(generic_validation.get("failures") or []),
            ][:50],
            "command_validation": command_validation,
            "generic_command_validation": generic_validation,
            "error": str(exc),
        }, ir=ir, xscr_path=path, xscr_text=text)
    expression_inventory = expression_inventory_from_xscr_text(
        text,
        script=path.stem,
        entry=str(path),
        source_preserved_allowlist=source_preserved_allowlist,
    )
    command_ids = []
    unsupported = []
    name_fields = _empty_command_name_fields()
    seen: dict[str, set[str]] = {category: set() for category in name_fields}
    for element in root.iter():
        local = _local_name(element.tag)
        category = COMMAND_NAME_FIELD_TAGS.get(local)
        if category is not None:
            value = (element.text or "").strip()
            if value and value not in seen[category]:
                seen[category].add(value)
                name_fields[category].append(value)
        if local != "Object":
            continue
        object_type = str(element.attrib.get("Type") or "")
        if not object_type:
            continue
        command_id = _compiled_command_id(element)
        if not command_id:
            continue
        if command_id in NON_EXECUTABLE_OBJECT_TYPES:
            continue
        supported_operation = _supported_compiled_operation(command_id)
        if ".Commands." not in object_type and supported_operation is None:
            continue
        command_ids.append(command_id)
        if supported_operation is None:
            unsupported.append({"command_id": command_id, "object_type": object_type})
    return enrich_compiled_inventory_with_golden_compare({
        "command_ids": command_ids,
        "unsupported_commands": unsupported,
        "name_fields": name_fields,
        "fluentcontrol_findings": [
            *namespace_findings,
            *_compiled_fluentcontrol_findings(
                root,
                name_fields,
                preferred_label_catalogs=preferred,
            ),
            *_expression_inventory_findings(expression_inventory),
            *(command_validation.get("failures") or []),
            *(generic_validation.get("failures") or []),
        ][:50],
        "expression_inventory": expression_inventory,
        "command_validation": command_validation,
        "generic_command_validation": generic_validation,
    }, ir=ir, xscr_path=path, xscr_text=text)


def _compiled_xsi_type_namespace_findings(text: str) -> list[dict[str, Any]]:
    if "VariableDefinitionHelper" not in text:
        return []
    declared = {
        match.group(1): match.group(2)
        for match in re.finditer(r'\bxmlns:([A-Za-z_][\w.-]*)="([^"]+)"', text)
    }
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    fragment_error = variable_declaration_fragment_error(text)
    if fragment_error:
        findings.append(
            {
                "reason": "variable_declaration_fragment_namespace_unbound",
                "message": (
                    "VariableDeclarations are not standalone XML; FluentControl Script Editor "
                    "deserializes this property from inner XML and cannot use VxData namespace declarations."
                ),
                "error": fragment_error,
            }
        )
    for match in re.finditer(r'\b((?:xsi|i):type)="([^"]*VariableDefinitionHelper)"', text):
        attr_name = match.group(1)
        type_value = match.group(2)
        prefix = type_value.split(":", 1)[0] if ":" in type_value else ""
        uri = declared.get(prefix, "") if prefix else ""
        if prefix and uri == VARIABLE_HANDLING_NAMESPACE:
            continue
        key = (attr_name, prefix, type_value)
        if key in seen:
            continue
        seen.add(key)
        reason = "variable_declaration_type_namespace_missing" if prefix else "variable_declaration_type_namespace_empty"
        findings.append(
            {
                "reason": reason,
                "message": (
                    "VariableDeclarations use a VariableDefinitionHelper xsi:type whose namespace is not "
                    "bound to Tecan.VisionX.VariableHandling.Shared; FluentControl Script Editor will fail "
                    "to deserialize startup variables."
                ),
                "attribute": attr_name,
                "type_value": type_value,
                "prefix": prefix,
                "namespace": uri,
                "expected_namespace": VARIABLE_HANDLING_NAMESPACE,
            }
        )
    for match in re.finditer(
        r'<(?:[A-Za-z_][\w.-]*:)?anyType\b(?=[^>]*\b(?:xsi|i):type="([^"]*VariableDefinitionHelper)")[^>]*>',
        text,
    ):
        tag = match.group(0)
        type_match = re.search(r'\b(?:xsi|i):type="([^"]*VariableDefinitionHelper)"', tag)
        type_value = type_match.group(1) if type_match else ""
        prefix = type_value.split(":", 1)[0] if ":" in type_value else ""
        if not prefix:
            continue
        if re.search(rf'\bxmlns:{re.escape(prefix)}="{re.escape(VARIABLE_HANDLING_NAMESPACE)}"', tag):
            continue
        key = ("local", prefix, type_value)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            {
                "reason": "variable_declaration_type_namespace_not_local",
                "message": (
                    "VariableDefinitionHelper xsi:type must declare its namespace on the anyType element; "
                    "FluentControl Script Editor deserializes variable declarations from inner XML and loses "
                    "ancestor namespace declarations."
                ),
                "type_value": type_value,
                "prefix": prefix,
                "namespace": declared.get(prefix, ""),
                "expected_namespace": VARIABLE_HANDLING_NAMESPACE,
            }
        )
    return findings


def _expression_inventory_findings(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    findings = []
    for failure in inventory.get("failures") or []:
        if not isinstance(failure, dict):
            continue
        findings.append({
            "reason": "invalid_expression",
            "message": "FluentControl expression did not pass typed expression validation.",
            "line_number": failure.get("line"),
            "script": failure.get("script"),
            "entry": failure.get("entry"),
            "command": failure.get("command"),
            "field": failure.get("field"),
            "variable": failure.get("variable"),
            "raw_expression": failure.get("raw_expression"),
            "parse_reason": failure.get("reason"),
            "offset": failure.get("offset"),
            "semantic_issues": failure.get("semantic_issues"),
        })
    return [
        {key: value for key, value in finding.items() if value not in (None, "", [], {})}
        for finding in findings
    ]


def _compiled_fluentcontrol_findings(
    root: ET.Element,
    name_fields: dict[str, list[str]],
    *,
    preferred_label_catalogs: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    declared_variables = _compiled_variable_declarations(root)
    preferred = preferred_label_catalogs or {}

    for add_labware in _elements_by_local_name(root, "AddLabwareDataV1"):
        if _compiled_element_disabled(add_labware):
            continue
        label = _first_text(add_labware, "LabwareLable") or _first_text(add_labware, "LabwareLabel")
        labware_type = _first_text(add_labware, "LabwareType")
        issue = label_catalog_issue(
            label=label,
            catalog=labware_type,
            path="AddLabwareDataV1.LabwareType",
            preferred_label_catalogs=preferred,
        )
        if issue is not None:
            findings.append(
                _compiled_finding(
                    LABEL_CATALOG_MISMATCH_CODE,
                    issue["message"],
                    add_labware,
                    {
                        "labware_name": label,
                        "labware_type": labware_type,
                        "expected_labware_type": issue["expected"],
                    },
                )
            )

    rup_statements = [
        *list(_elements_by_local_name(root, "RUPWorktableStatement")),
        *list(_elements_by_local_name(root, "RUPStandardStatement")),
    ]
    for statement in rup_statements:
        auto_close_text = _first_text(statement, "RUPAutoClose")
        display_wait_text = _first_text(statement, "RUPDisplayAndWait")
        timeout_text = _first_text(statement, "RUPTimeOut")
        if auto_close_text and str(auto_close_text).strip().casefold() not in {"true", "false"}:
            findings.append(
                _compiled_finding(
                    "rup_auto_close_invalid",
                    "RUPAutoClose must be a boolean value.",
                    statement,
                    {"value": auto_close_text},
                )
            )
        if display_wait_text and str(display_wait_text).strip().casefold() not in {"true", "false"}:
            findings.append(
                _compiled_finding(
                    "rup_display_and_wait_invalid",
                    "RUPDisplayAndWait must be a boolean value.",
                    statement,
                    {"value": display_wait_text},
                )
            )
        timeout = _number(timeout_text)
        auto_close = str(auto_close_text).strip().casefold() == "true"
        if timeout is None or timeout < (1 if auto_close else 0) or timeout > 7200:
            findings.append(
                _compiled_finding(
                    "rup_timeout_out_of_range",
                    "RUPTimeOut must be 0-7200 seconds, and at least 1 second when auto-close is enabled.",
                    statement,
                    {"timeout": timeout_text, "auto_close": auto_close_text},
                )
            )

    worktable_statements = list(_elements_by_local_name(root, "RUPWorktableStatement"))
    for statement in worktable_statements:
        for field_name in ("Grid", "Site"):
            value = _first_text(statement, field_name)
            if not re.fullmatch(r"-?\d+", str(value or "").strip()):
                findings.append(
                    _compiled_finding(
                        "rup_worktable_grid_site_invalid",
                        (
                            f"RUP Worktable ConfigureDataLabwareDataModel `{field_name}` must be an integer. "
                            "FluentControl 3.5.x Script Editor throws a FormatException on blank values."
                        ),
                        statement,
                        {"field": field_name, "value": value},
                    )
                )
        labware_name = _first_text(statement, "LabwareName")
        if not str(labware_name or "").strip():
            findings.append(
                _compiled_finding(
                    "rup_worktable_labware_name_missing",
                    (
                        "RUP Worktable ConfigureDataLabwareDataModel requires a concrete LabwareName. "
                        "A blank value causes the FluentControl Script Editor Infopad error "
                        "'Name must not be empty'. Use RUP Standard for non-deck prompts."
                    ),
                    statement,
                )
            )
        match = re.search(r"\[([^\]]+)\]", str(labware_name or ""))
        if match and not re.fullmatch(r"\d+", match.group(1).strip()):
            findings.append(
                _compiled_finding(
                    "rup_worktable_variable_labware_index_invalid",
                    (
                        "RUP Worktable ConfigureDataLabwareDataModel LabwareName must use a concrete numeric "
                        "instance such as `[001]`, or be blank. FluentControl Script Editor throws a "
                        "FormatException when a variable name is used inside the labware index."
                    ),
                    statement,
                    {"labware_name": labware_name, "index": match.group(1)},
                )
            )
    workspace_base_mismatch = _workspace_base_reference_mismatch(root)
    if worktable_statements and workspace_base_mismatch:
        findings.append(
            _compiled_finding(
                "rup_worktable_base_workspace_mismatch",
                (
                    "RUP Worktable prompts require VxWorkspaceData BaseWorkspaceName to match the packaged "
                    "WorktableWorkspace reference. A stale source workspace GUID can cause deltaId null warnings."
                ),
                worktable_statements[0],
                workspace_base_mismatch,
            )
        )
    if worktable_statements and not _has_workspace_delta_identifier(root):
        findings.append(
            _compiled_finding(
                "rup_worktable_workspace_delta_missing",
                (
                    "RUP Worktable prompts require source-backed VxWorkspaceData with a non-empty "
                    "WorkspaceDeltas Identifier. Missing metadata can cause deltaId null failures."
                ),
                worktable_statements[0],
            )
        )

    for statement in _elements_by_local_name(root, "UserPromptStatement"):
        auto_close_text = _first_text(statement, "AutoClose")
        if auto_close_text and str(auto_close_text).strip().casefold() not in {"true", "false"}:
            findings.append(
                _compiled_finding(
                    "prompt_auto_close_invalid",
                    "User prompt AutoClose must be a boolean value.",
                    statement,
                    {"value": auto_close_text},
                )
            )
        timeout_text = _first_text(statement, "Timeout")
        timeout = _number(timeout_text)
        if timeout is None or timeout < 1 or timeout > 7200:
            findings.append(
                _compiled_finding(
                    "prompt_timeout_out_of_range",
                    "User prompt timeout must be between 1 and 7200 seconds.",
                    statement,
                    {"timeout": timeout_text},
                )
            )

    seen_labware_variables: set[tuple[str, str]] = set()
    for command_object in _elements_by_local_name(root, "Object"):
        labels = [
            _first_text(command_object, "LabwareName"),
            _first_text(command_object, "LabwareLable"),
            _first_text(command_object, "LabwareLabel"),
        ]
        for label in labels:
            for variable in _bracket_variable_names(label):
                key = (label, variable)
                if key in seen_labware_variables:
                    continue
                seen_labware_variables.add(key)
                if variable not in declared_variables:
                    findings.append(
                        _compiled_finding(
                            "undeclared_variable",
                            f"Labware expression {label!r} references undeclared variable {variable!r}.",
                            command_object,
                            {"labware_name": label, "variable": variable},
                        )
                    )

    referenced_scripts = _compiled_script_reference_names(root)
    for statement in _elements_by_local_name(root, "SubRoutineStatement"):
        subroutine = _clean_subroutine_name(_first_text(statement, "SubRoutine"))
        if subroutine and not _subroutine_reference_matches(subroutine, referenced_scripts):
            findings.append(
                _compiled_finding(
                    "subroutine_reference_missing",
                    "Subroutine call has no matching Script reference in the compiled XSCR.",
                    statement,
                    {"subroutine": subroutine, "script_references": sorted(referenced_scripts)},
                )
            )

    labware_types = _compiled_labware_types_by_label(root)
    for command_name in ("CgaGetFingersScriptCommandDataV1", "CgaDropFingersScriptCommandDataV1"):
        for command in _elements_by_local_name(root, command_name):
            labware_name = _first_text(command, "LabwareName")
            labware_type = labware_types.get(_norm_name(labware_name), "")
            if labware_type and "adapter" in labware_type.casefold():
                findings.append(
                    _compiled_finding(
                        "rga_fingers_incompatible_labware",
                        "RGA finger pickup/drop commands cannot target adapter labware.",
                        command,
                        {"labware_name": labware_name, "labware_type": labware_type},
                    )
                )
    return findings[:50]


def _has_workspace_delta_identifier(root: ET.Element) -> bool:
    for workspace_data in _elements_by_local_name(root, "VxWorkspaceData"):
        if not str(_first_text(workspace_data, "BaseWorkspaceName") or "").strip():
            continue
        for deltas in _elements_by_local_name(workspace_data, "WorkspaceDeltas"):
            for item in deltas.iter():
                if _local_name(item.tag) != "string":
                    continue
                payload = "".join(item.itertext())
                match = re.search(r"<Identifier>\s*([^<\s][^<]*)</Identifier>", payload)
                if match and match.group(1).strip():
                    return True
    return False


def _workspace_base_reference_mismatch(root: ET.Element) -> dict[str, str]:
    worktable_guid = ""
    for reference in _elements_by_local_name(root, "Reference"):
        if str(_first_text(reference, "TypeId") or "").strip() != "WorktableWorkspace":
            continue
        worktable_guid = str(_first_text(reference, "Guid") or "").strip()
        if worktable_guid:
            break
    if not worktable_guid:
        return {}
    for workspace_data in _elements_by_local_name(root, "VxWorkspaceData"):
        base = str(_first_text(workspace_data, "BaseWorkspaceName") or "").strip()
        if base and base.casefold() != worktable_guid.casefold():
            return {"base_workspace_name": base, "worktable_reference_guid": worktable_guid}
    return {}


def _compiled_finding(
    reason: str,
    message: str,
    element: ET.Element,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "reason": reason,
        "message": message,
        "line_number": _first_text(element, "LineNumber"),
    }
    if extra:
        payload.update({key: value for key, value in extra.items() if value not in (None, "", [], {})})
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _compiled_variable_declarations(root: ET.Element) -> set[str]:
    names: set[str] = set()
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "anyType":
            type_hint = " ".join(str(value) for value in element.attrib.values())
            if "VariableDefinitionHelper" not in type_hint:
                continue
        elif local != "VariableDefinitionHelper":
            continue
        name = _first_text(element, "Name")
        if name:
            names.add(name)
    return names


def _compiled_script_reference_names(root: ET.Element) -> set[str]:
    names: set[str] = set()
    for ref in _elements_by_local_name(root, "Reference"):
        type_id = _first_text(ref, "TypeId")
        object_name = _first_text(ref, "ObjectName")
        if type_id == "Script" and object_name:
            names.add(_clean_subroutine_name(object_name))
    return names


def _compiled_labware_types_by_label(root: ET.Element) -> dict[str, str]:
    labels: dict[str, str] = {}
    for add_labware in _elements_by_local_name(root, "AddLabwareDataV1"):
        if _compiled_element_disabled(add_labware):
            continue
        label = _first_text(add_labware, "LabwareLable") or _first_text(add_labware, "LabwareLabel")
        labware_type = _first_text(add_labware, "LabwareType")
        if label and labware_type:
            labels[_norm_name(label)] = labware_type
    return labels


def _first_text(root: ET.Element, name: str) -> str:
    for element in root.iter():
        if _local_name(element.tag) == name:
            return (element.text or "").strip()
    return ""


def _compiled_element_disabled(element: ET.Element) -> bool:
    return _first_text(element, "IsDisabledForExecution").casefold() == "true"


def _elements_by_local_name(root: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in root.iter() if _local_name(element.tag) == name]


def _bracket_variable_names(value: Any) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", str(value or ""))
    }


def _clean_subroutine_name(value: Any) -> str:
    return str(value or "").strip().strip('"').replace("/", "\\")


def _subroutine_reference_matches(subroutine: str, references: set[str]) -> bool:
    subroutine_norm = _norm_name(subroutine.rsplit("\\", 1)[-1])
    return any(
        _norm_name(reference) == _norm_name(subroutine)
        or _norm_name(reference.rsplit("\\", 1)[-1]) == subroutine_norm
        for reference in references
    )


def _step_signatures(ir: dict[str, Any]) -> list[dict[str, Any]]:
    signatures = []
    setup_operations = {"add_labware", "load_labware"}
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation = str(step.get("operation") or "")
        if not operation:
            continue
        if operation in setup_operations:
            continue
        signature = {
            "operation": operation,
            "target_labware": _canonical_first_instance_label(
                step.get("target_labware")
                or step.get("source_labware")
                or step.get("destination_labware")
                or ""
            ),
            "volume_ul": _normalized_number(step.get("volume_ul")),
            "liquid_class": str(step.get("liquid_class") or ""),
        }
        expressions = _registered_step_expression_signatures(step)
        if expressions:
            signature["expressions"] = expressions
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        if operation == "move_plate":
            labware = str(params.get("labware") or step.get("target_labware") or "")
            onto_labware = str(params.get("onto_labware") or params.get("onto") or "")
            destination_location = str(
                params.get("destination_location")
                or params.get("to_location")
                or step.get("destination_location")
                or ""
            )
            destination_site = _normalized_number(
                params.get("destination_site") or params.get("to_site") or step.get("destination_site")
            )
            cover_match = re.fullmatch(r'GetCoverSiteName\("([^"]+)"\)', destination_location)
            if cover_match and (not onto_labware or onto_labware == cover_match.group(1)):
                onto_labware = cover_match.group(1)
                destination_location = ""
                destination_site = ""
            elif onto_labware:
                # Source IR onto-moves omit deck coords; ignore residual site literals.
                destination_location = ""
                destination_site = ""
            signature["target_labware"] = _canonical_first_instance_label(labware)
            signature.update(
                {
                    "labware": labware,
                    "onto_labware": onto_labware,
                    "destination_location": destination_location,
                    "destination_site": destination_site,
                }
            )
        elif operation == "call_subroutine":
            signature["subroutine"] = _clean_subroutine_name(params.get("subroutine") or params.get("SubRoutine"))
        elif operation == "prompt_user":
            image_path = str(
                params.get("image_path")
                or prompt_step_worktable_media_path(params)
                or prompt_step_media_path(params)
                or ""
            )
            sound_file = str(params.get("sound_file") or "")
            signature.update(
                {
                    "prompt": str(params.get("prompt") or ""),
                    "image_file": _media_basename(image_path),
                    "image_step_label": _media_step_label(image_path),
                    "sound_file": _media_basename(sound_file),
                    "sound_step_label": _media_step_label(sound_file),
                }
            )
        signatures.append(signature)
    return signatures


def _setup_expression_signatures(ir: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    signatures: dict[str, dict[str, list[str]]] = {}
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("operation") or "") not in {
            "add_labware",
            "load_labware",
        }:
            continue
        expressions = _registered_step_expression_signatures(step)
        if not expressions:
            continue
        label = _canonical_first_instance_label(
            step.get("target_labware")
            or (step.get("parameters") or {}).get("label")
            or ""
        )
        if label:
            signatures[label] = expressions
    return signatures


def _registered_step_expression_signatures(step: dict[str, Any]) -> dict[str, list[str]]:
    command_id = str(step.get("command_id") or "")
    operation = str(step.get("operation") or "")
    if not expression_fields_for_command(command_id):
        command_id = {
            "add_labware": "AddLabwareDataV1",
            "set_variable": "SetVariableStatement",
            "set_remaining_runtime": "SetVariableStatement",
            "conditional_branch": "ConditionalGroup",
            "loop": "LoopGroup",
            "wait": "Wait",
            "delay": "Delay",
            "set_location": "SetLocation",
            "aspirate": "Mca384Aspirate",
            "dispense": "Mca384Dispense",
            "mca384_mix": "Mca384Mix",
            "mca384_empty_tips": "Mca384EmptyTips",
            "liha_aspirate": "LihaAspirate",
            "liha_dispense": "LihaDispense",
            "liha_mix": "LihaMix",
            "liha_empty_tips": "LihaEmptyTips",
            "wait_for_timer": "WaitForTimer",
            "tegio_set_pwm_output": "TeGioSetPWMOutput",
            "move_axis_command": "MoveAxisCommand",
        }.get(operation, "")
    allowed_keys = {
        canonical_expression_key(field_path)
        for field_path in expression_fields_for_command(command_id)
    }
    if operation == "call_subroutine":
        allowed_keys.add(canonical_expression_key("Source"))
    if not allowed_keys:
        return {}
    collected: dict[str, set[str]] = {}

    for record in walk_expression_values(step):
        if record.key not in allowed_keys:
            continue
        if not isinstance(record.expression, dict):
            continue
        try:
            rendered = render_expression(expression_from_mapping(record.expression))
        except (TypeError, ValueError):
            rendered = json.dumps(record.expression, sort_keys=True, separators=(",", ":"))
        collected.setdefault(record.key, set()).add(rendered)
    if operation == "call_subroutine":
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        for section in ("variable_mappings_start", "variable_mappings_end"):
            for mapping in params.get(section) or []:
                if not isinstance(mapping, dict) or mapping.get("source") in (None, ""):
                    continue
                expression = mapping.get("source_expression")
                if isinstance(expression, dict):
                    continue
                parsed = parse_or_preserve_source_expression(str(mapping.get("source")))
                collected.setdefault("source_expression", set()).add(
                    render_expression(parsed)
                )
    return {
        key: sorted(values)
        for key, values in sorted(collected.items())
        if values
    }


def _canonical_first_instance_label(value: Any) -> str:
    """Treat FluentControl's optional ``[001]`` suffix as the first base instance."""
    return re.sub(r"\[0*1\]$", "", str(value or "").strip())


def _media_basename(value: Any) -> str:
    return re.split(r"[\\/]", str(value or "").strip())[-1]


def _media_step_label(value: Any) -> str:
    basename = _media_basename(value)
    match = re.match(r"(step[_-]?\d+)", basename, flags=re.IGNORECASE)
    return match.group(1).replace("_", "").replace("-", "").casefold() if match else ""


def _step_failure(
    step: dict[str, Any],
    reason: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "step_id": step.get("id"),
        "index": step.get("index"),
        "operation": step.get("operation"),
        "target_labware": step.get("target_labware"),
        "reason": reason,
        "message": message,
    }
    if extra:
        payload.update(extra)
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _normalized_number(value: Any) -> float | int | str:
    number = _number(value)
    if number is None:
        return str(value or "")
    return int(number) if number.is_integer() else number


def _is_mca_step(step: dict[str, Any]) -> bool:
    text = " ".join(
        str(value)
        for value in (
            step.get("operation"),
            step.get("command_id"),
            step.get("compiled_path"),
            step.get("source_path"),
            (step.get("parameters") or {}).get("device_alias") if isinstance(step.get("parameters"), dict) else "",
        )
        if value
    ).lower()
    return "mca" in text


def _well_values(step: dict[str, Any]) -> list[tuple[str, Any]]:
    keys = (
        "well",
        "wells",
        "source_well",
        "source_wells",
        "destination_well",
        "destination_wells",
        "well_position",
        "well_positions",
        "well_range",
        "well_ranges",
    )
    out = []
    for key in keys:
        if step.get(key) not in (None, "", []):
            out.append((key, step[key]))
    params = step.get("parameters") or {}
    if isinstance(params, dict):
        for key in keys:
            if params.get(key) not in (None, "", []):
                out.append((f"parameters.{key}", params[key]))
    return out


def _expand_well_tokens(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        tokens = []
        for item in value:
            tokens.extend(_expand_well_tokens(item))
        return tokens
    if isinstance(value, dict):
        tokens = []
        for item in value.values():
            tokens.extend(_expand_well_tokens(item))
        return tokens
    text = str(value or "")
    tokens = []
    for piece in re.split(r"[,;\s]+", text):
        piece = piece.strip()
        if not piece:
            continue
        if ":" in piece:
            tokens.extend(part for part in piece.split(":") if part)
        else:
            tokens.append(piece)
    return tokens


def _parse_well(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", value.strip())
    if not match:
        return None
    row = 0
    for char in match.group(1).upper():
        row = row * 26 + (ord(char) - ord("A") + 1)
    return row, int(match.group(2))


def _labware_dimensions(item: dict[str, Any]) -> dict[str, int]:
    text = f"{item.get('catalog') or ''} {item.get('label') or ''} {item.get('python_class') or ''}".lower()
    if "384" in text:
        return {"rows": 16, "columns": 24}
    if "1536" in text:
        return {"rows": 32, "columns": 48}
    if "24" in text:
        return {"rows": 4, "columns": 6}
    if "12" in text and "96" not in text:
        return {"rows": 3, "columns": 4}
    return {"rows": 8, "columns": 12}


def _is_tip_pickup(step: dict[str, Any]) -> bool:
    return str(step.get("operation") or "") in {"pick_up_tips", "mca384_get_tips", "liha_get_tips"}


def _is_tip_release(step: dict[str, Any]) -> bool:
    return str(step.get("operation") or "") in {"set_tips_back", "drop_tips", "mca384_drop_tips", "liha_drop_tips"}


def _tip_capacity_ul(labware: dict[str, Any], step: dict[str, Any]) -> float | None:
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    for value in (
        labware.get("capacity_ul"),
        labware.get("tip_capacity_ul"),
        params.get("capacity_ul") if isinstance(params, dict) else None,
        params.get("tip_capacity_ul") if isinstance(params, dict) else None,
    ):
        number = _number(value)
        if number is not None:
            return number
    text = " ".join(
        str(value)
        for value in (
            labware.get("catalog"),
            labware.get("label"),
            labware.get("python_class"),
            params.get("catalog") if isinstance(params, dict) else "",
            params.get("labware_type") if isinstance(params, dict) else "",
        )
        if value
    )
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:u\s*l|ul|µl)", text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None


def _liquid_class_compatibility_map(
    source_manifest: dict[str, Any] | None,
    alias_maps: dict[str, dict[str, str]],
) -> dict[str, set[str]]:
    if not isinstance(source_manifest, dict):
        return {}
    out: dict[str, set[str]] = {}
    for key in ("liquid_class_compatibility", "liquid_class_capabilities"):
        raw = source_manifest.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = resolve_alias(item.get("name"), "liquid_class", alias_maps)
            operations = item.get("operations") or item.get("supported_operations") or item.get("compatible_operations")
            if name and isinstance(operations, list):
                out[name] = {str(operation) for operation in operations if operation}
    return out


def _raw_xml_calls(draft_path: Path | None) -> list[dict[str, Any]]:
    if draft_path is None or not draft_path.exists():
        return []
    try:
        source = draft_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:
        return []
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name in {"raw_xml_step", "raw_xml_command"}:
            command_id = _raw_xml_call_command_id(node)
            call = {"line": getattr(node, "lineno", None), "function": name}
            if command_id:
                call["command_id"] = command_id
                support_status = registry_command_support_status(command_id)
                if support_status:
                    call["support_status"] = support_status
            calls.append(call)
    return calls


def _raw_xml_call_command_id(node: ast.Call) -> str | None:
    if not node.args:
        return None
    try:
        value = ast.literal_eval(node.args[0])
    except (ValueError, SyntaxError):
        return None
    text = str(value or "").strip()
    return text or None


def _raw_xml_call_approved(item: dict[str, Any], approved_ids: set[str]) -> bool:
    command_id = str(item.get("command_id") or "").strip()
    if not command_id:
        return False
    if command_id in approved_ids:
        return True
    return registry_command_support_status(command_id) is not None


def _compiled_command_id(element: ET.Element) -> str:
    for child in list(element):
        return _local_name(child.tag)
    return str(element.attrib.get("Type") or "").rsplit(".", 1)[-1]


def _supported_compiled_operation(command_id: str) -> str | None:
    support_status = registry_command_support_status(command_id)
    if support_status:
        return registry_command_operation(command_id) or support_status
    registry_operation = registry_command_operation(command_id)
    if registry_operation:
        return registry_operation
    lowered = command_id.lower()
    if "addlabware" in lowered:
        return "add_labware"
    if "pickuptips" in lowered or "gettips" in lowered:
        return "pick_up_tips"
    if "settipsback" in lowered:
        return "set_tips_back"
    if "droptips" in lowered:
        return "drop_tips"
    if "aspirate" in lowered:
        return "aspirate"
    if "dispense" in lowered:
        return "dispense"
    if "mix" in lowered:
        return "mix"
    return None


def _local_name(tag: Any) -> str:
    text = str(tag)
    if "}" in text:
        return text.rsplit("}", 1)[-1]
    return text


def _source_irs(*, source_xscr: Path | None, source_scripts: list[Path]) -> list[dict[str, Any]]:
    out = []
    for source in [*source_scripts, *([source_xscr] if source_xscr is not None else [])]:
        if source.suffix.lower() != ".xscr" or not source.exists():
            continue
        try:
            out.append(protocol_ir_from_xscr(source))
        except Exception:
            continue
    return out
