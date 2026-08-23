"""Validation gates for ready-to-import Tecan protocol bundles."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping
from . import xml_compat as ET
from fluentcoder.expressions import expression_inventory_from_xscr_text

from .api_v2.add_labware_golden import enrich_compiled_inventory_with_golden_compare
from .api_v2.command_validate import validate_compiled_xscr_commands
from .api_v2.generic_command_validate import validate_passthrough_commands_from_xscr
from .api_v2.xml_compare import NON_EXECUTABLE_OBJECT_TYPES
from .aliases import alias_candidates, resolve_alias
from .checksums import checksum_bridge_available
from .command_registry import registry_command_operation, registry_command_support_status
from .expression_provenance import (
    load_expression_provenance_ledger,
    source_preserved_expression_allowlist_from_verified_ledger,
    verify_expression_provenance_ledger,
    verify_protocol_ir_expression_provenance,
)
from .gates import ValidationContext, readiness_evaluator
from .gates.evaluators import (
    evaluate_checksums,
    evaluate_command_inventory,
    evaluate_fluent_context_check,
    evaluate_generated_zeia,
    evaluate_labware,
    evaluate_liquid_class_compatibility,
    evaluate_liquid_classes,
    evaluate_liquid_state,
    evaluate_no_unapproved_raw_xml,
    evaluate_post_compile_xscr,
    evaluate_python_draft,
    evaluate_recreate,
    evaluate_repair_plan,
    evaluate_simulation,
    evaluate_subroutine_additions,
    evaluate_subroutine_dependencies,
    evaluate_tip_capacity,
    evaluate_volume_bounds,
    evaluate_well_ranges,
    evaluate_worklists,
    evaluate_xscr,
    evaluate_xscr_ir_roundtrip,
    _number,
    _norm_name,
    _registered_step_expression_signatures,
)
from .gates.ir import a200_adapter_catalog_issues
from .labware_contracts import (
    LABEL_CATALOG_MISMATCH_CODE,
    label_catalog_issue,
    preferred_label_catalogs_from_manifest,
    resolve_preferred_label_catalogs,
)
from .protocol_ir import (
    load_protocol_ir,
    prompt_step_media_path,
    prompt_step_worktable_media_path,
    protocol_ir_from_python,
    protocol_ir_from_xscr,
)
from .protocol_ir_schema import VOLUME_OPERATIONS
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
        _evaluate_registered_gate("labware_resolves", evaluator_context),
        _evaluate_registered_gate("liquid_classes_resolve", evaluator_context),
        _evaluate_registered_gate("worklist_paths_valid", evaluator_context),
        _evaluate_registered_gate("python_draft_generated", evaluator_context),
        _evaluate_registered_gate("simulation_passes", evaluator_context),
        _evaluate_registered_gate("repair_plan_clear", evaluator_context),
        _evaluate_registered_gate("xscr_compiles", evaluator_context),
        _evaluate_registered_gate("recreate_matches_ir", evaluator_context),
        _evaluate_registered_gate("post_compile_xscr_reinspect", evaluator_context),
        _evaluate_registered_gate("xscr_ir_roundtrip_matches", evaluator_context),
        _evaluate_registered_gate("volume_bounds_valid", evaluator_context),
        _evaluate_registered_gate("well_ranges_valid", evaluator_context),
        _evaluate_registered_gate("tip_capacity_valid", evaluator_context),
        _evaluate_registered_gate("liquid_class_compatible", evaluator_context),
        _evaluate_registered_gate("no_unapproved_raw_xml", evaluator_context),
        _evaluate_registered_gate("liquid_state_valid", evaluator_context),
        _evaluate_registered_gate("tip_boxes_resolve", evaluator_context),
        _evaluate_registered_gate("carriers_resolve", evaluator_context),
        _evaluate_registered_gate("device_aliases_resolve", evaluator_context),
        _evaluate_registered_gate("deck_layout_consistent", evaluator_context),
        _evaluate_registered_gate("checksums_valid", evaluator_context),
        _evaluate_registered_gate("generated_zeia_valid", evaluator_context),
        _evaluate_registered_gate("command_inventory_resolves", evaluator_context),
        _evaluate_registered_gate("subroutine_dependencies_valid", evaluator_context),
    ]
    fluent_gate = _evaluate_registered_gate("fluent_context_check", evaluator_context)
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


def _evaluator_context(**kwargs: Any) -> ValidationContext:
    """Build the typed context used by canonical gate evaluators."""
    return ValidationContext(make_gate=_gate, **kwargs)


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
    """Compatibility facade for the canonical labware evaluator."""
    return evaluate_labware(_evaluator_context(worktable_diff=diff))


def _gate_liquid_classes(diff: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility facade for the canonical liquid-class evaluator."""
    return evaluate_liquid_classes(_evaluator_context(worktable_diff=diff))


def _gate_worklists(diff: dict[str, Any] | None, worklist: Path | None) -> dict[str, Any]:
    """Compatibility facade for the canonical worklist evaluator."""
    return evaluate_worklists(_evaluator_context(worktable_diff=diff, worklist=worklist))


def _gate_worktable_resource(
    gate_id: str,
    label_plural: str,
    detail_key: str,
    items: list[dict[str, Any]] | None,
    diff_present: bool,
) -> dict[str, Any]:
    """Compatibility adapter for the package-owned worktable evaluators."""
    evaluators = {
        gate_id: readiness_evaluator(gate_id).evaluate
        for gate_id in ("tip_boxes_resolve", "carriers_resolve", "device_aliases_resolve")
    }
    evaluator = evaluators.get(gate_id)
    if evaluator is None:
        raise KeyError(f"No worktable evaluator for readiness gate {gate_id!r}.")
    diff_key = {
        "tip_boxes_resolve": "required_tip_boxes",
        "carriers_resolve": "required_carriers",
        "device_aliases_resolve": "device_aliases",
    }[gate_id]
    diff = None if not diff_present else {diff_key: items or []}
    return evaluator(_evaluator_context(worktable_diff=diff))


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
    """Compatibility facade for the checksum evaluator."""
    return evaluate_checksums(
        _evaluator_context(
            compiled_xscr=compiled_xscr,
            source_projects=tuple(source_projects),
            validation_options={**context, "_checksum_bridge_available": checksum_bridge_available},
        )
    )


def _gate_generated_zeia(
    source_projects: list[Path],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility facade for the packaged-ZEIA evaluator."""
    return evaluate_generated_zeia(
        _evaluator_context(
            source_projects=tuple(source_projects),
            validation_options=context,
        )
    )


def _gate_subroutine_additions(
    source_projects: list[Path],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility facade for added-subroutine metadata checks."""
    return evaluate_subroutine_additions(
        _evaluator_context(
            source_projects=tuple(source_projects),
            validation_options=context,
        )
    )


def _gate_subroutine_dependencies(
    protocol_ir: dict[str, Any] | None,
    compiled_xscr: Path | None,
    source_manifest: dict[str, Any] | None,
    source_projects: list[Path],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility facade for the subroutine dependency evaluator."""
    return evaluate_subroutine_dependencies(
        _evaluator_context(
            domain_ir=protocol_ir,
            compiled_xscr=compiled_xscr,
            source_manifest=source_manifest,
            source_projects=tuple(source_projects),
            validation_options=context,
        )
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
    """Compatibility facade for the compiled command inventory evaluator."""
    return evaluate_command_inventory(
        _evaluator_context(
            compiled_inventory=compiled_inventory,
            source_manifest=source_manifest,
            validation_options=context,
        )
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


def _gate_python_draft(draft_path: Path | None) -> dict[str, Any]:
    """Compatibility facade for the canonical Python-draft evaluator."""
    return evaluate_python_draft(_evaluator_context(draft_path=draft_path))


def _gate_simulation(context: dict[str, Any]) -> dict[str, Any]:
    """Compatibility facade for the canonical simulation evaluator."""
    return evaluate_simulation(_evaluator_context(validation_options=context))


def _gate_repair_plan(context: dict[str, Any]) -> dict[str, Any]:
    """Compatibility facade for the canonical repair-plan evaluator."""
    return evaluate_repair_plan(_evaluator_context(validation_options=context))


def _gate_xscr(compiled_xscr: Path, context: dict[str, Any]) -> dict[str, Any]:
    """Compatibility facade for the canonical XSCR evaluator."""
    return evaluate_xscr(_evaluator_context(compiled_xscr=compiled_xscr, validation_options=context))


def _gate_recreate(ir: dict[str, Any] | None, recreate_guide: Path | None) -> dict[str, Any]:
    """Compatibility facade for the canonical recreate-guide evaluator."""
    return evaluate_recreate(_evaluator_context(protocol_ir=ir, recreate_guide=recreate_guide))


def _gate_post_compile_xscr(
    compiled_ir: dict[str, Any] | None,
    compiled_error: str,
    compiled_inventory: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility facade for the post-compile XSCR evaluator."""
    return evaluate_post_compile_xscr(
        _evaluator_context(
            compiled_ir=compiled_ir,
            compiled_ir_error=compiled_error,
            compiled_inventory=compiled_inventory,
        )
    )


def _gate_xscr_ir_roundtrip(
    ir: dict[str, Any] | None,
    compiled_ir: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compatibility facade for the XSCR/IR roundtrip evaluator."""
    return evaluate_xscr_ir_roundtrip(
        _evaluator_context(protocol_ir=ir, compiled_ir=compiled_ir)
    )


def _liquid_handling_step_count(ir: dict[str, Any] | None) -> int:
    if not isinstance(ir, dict):
        return 0
    return sum(
        1
        for step in ir.get("steps") or []
        if isinstance(step, dict) and step.get("operation") in VOLUME_OPERATIONS
    )


def _gate_volume_bounds(
    ir: dict[str, Any] | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility facade for the volume-bounds evaluator."""
    return evaluate_volume_bounds(_evaluator_context(domain_ir=ir, validation_options=context))


def _gate_well_ranges(ir: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility facade for the well-range evaluator."""
    return evaluate_well_ranges(_evaluator_context(domain_ir=ir))


def _gate_tip_capacity(ir: dict[str, Any] | None) -> dict[str, Any]:
    """Compatibility facade for the tip-capacity evaluator."""
    return evaluate_tip_capacity(_evaluator_context(domain_ir=ir))


def _gate_liquid_class_compatibility(
    ir: dict[str, Any] | None,
    source_manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compatibility facade for the liquid-class compatibility evaluator."""
    return evaluate_liquid_class_compatibility(
        _evaluator_context(domain_ir=ir, source_manifest=source_manifest)
    )


def _gate_no_unapproved_raw_xml(
    draft_path: Path | None,
    compiled_inventory: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Compatibility facade for the raw-XML approval evaluator."""
    return evaluate_no_unapproved_raw_xml(
        _evaluator_context(
            draft_path=draft_path,
            compiled_inventory=compiled_inventory,
            validation_options=context,
        )
    )


def _gate_liquid_state(ir: dict[str, Any] | None, context: dict[str, Any]) -> dict[str, Any]:
    """Compatibility facade for the canonical liquid-state evaluator."""
    return evaluate_liquid_state(_evaluator_context(domain_ir=ir, validation_options=context))


def _gate_fluent_context_check(context: dict[str, Any]) -> dict[str, Any] | None:
    """Compatibility facade for the optional FluentControl diagnostic evaluator."""
    return evaluate_fluent_context_check(_evaluator_context(validation_options=context))


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
    arg = node.args[0]
    if isinstance(arg, ast.Constant):
        value = arg.value
    else:
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
