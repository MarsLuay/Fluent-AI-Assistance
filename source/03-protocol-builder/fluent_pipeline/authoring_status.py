"""Canonical authoring and recovery status shared by Python, CLI, and MCP."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping

from .readiness import (
    LiveHandoffStatus,
    coerce_live_handoff_status,
    live_handoff_next_action,
)


class AuthoringState(StrEnum):
    """Stable states for the existing authoring and recovery workflow."""

    REQUEST_SPEC_CREATED = "request_spec_created"
    REQUEST_SPEC_INVALID = "invalid_request_spec"
    REQUEST_SPEC_VALID = "request_spec_valid"
    SCAFFOLD_NEEDS_REVIEW = "scaffold_needs_review"
    FINAL_GENERATION_BLOCKED = "final_generation_blocked"
    REPAIR_READY = "repair_ready"
    REPAIR_NEEDS_REVIEW = "repair_needs_review"
    REPAIR_APPLIED = "repair_applied"
    REPAIR_NOOP = "repair_noop"
    VERIFICATION_BLOCKED = "verification_blocked"
    VERIFICATION_READY = "verification_ready"
    FINAL_READY_HANDOFF = "final_ready_handoff"


@dataclass(frozen=True)
class AuthoringFinding:
    """One adapter-neutral finding that explains an authoring state."""

    code: str
    severity: str
    message: str
    location: str | None = None
    next_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "location": self.location,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class HandoffAction:
    """One canonical live-system action retained in the final handoff."""

    key: str
    label: str
    status: str
    next_action: str

    def to_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "next_action": self.next_action,
        }


@dataclass(frozen=True)
class AuthoringStatus:
    """The single status contract consumed by every public adapter."""

    status: AuthoringState
    findings: tuple[AuthoringFinding, ...]
    artifacts: tuple[str, ...]
    allowed_action: str
    next_action: str
    handoff_actions: tuple[HandoffAction, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "findings": [finding.to_dict() for finding in self.findings],
            "artifacts": list(self.artifacts),
            "allowed_action": self.allowed_action,
            "next_action": self.next_action,
            "handoff_actions": [action.to_dict() for action in self.handoff_actions],
        }


def request_spec_created_status(output_path: Path) -> AuthoringStatus:
    return AuthoringStatus(
        status=AuthoringState.REQUEST_SPEC_CREATED,
        findings=(),
        artifacts=(str(output_path),),
        allowed_action="validate_request_spec",
        next_action="Review and validate request.spec.yaml before generating a scaffold.",
    )


def request_spec_validation_status(
    *,
    ok: bool,
    findings: Iterable[Mapping[str, Any]],
    spec_path: Path,
) -> AuthoringStatus:
    normalized = tuple(_finding_from_mapping(item, default_code="request_spec") for item in findings)
    if ok:
        return AuthoringStatus(
            status=AuthoringState.REQUEST_SPEC_VALID,
            findings=normalized,
            artifacts=(str(spec_path),),
            allowed_action="generate_scaffold",
            next_action="Generate a scaffold, then review request.spec.yaml and the grounded Protocol IR.",
        )
    return AuthoringStatus(
        status=AuthoringState.REQUEST_SPEC_INVALID,
        findings=normalized,
        artifacts=(str(spec_path),),
        allowed_action="fix_request_spec",
        next_action="Fix every error finding in request.spec.yaml, then validate it again.",
    )


def generation_status(manifest: Mapping[str, Any]) -> AuthoringStatus:
    workflow_status = str(manifest.get("workflow_status") or "scaffold_not_validated")
    artifacts = _generation_artifacts(manifest)
    if bool(manifest.get("ready_to_import")):
        handoff_actions = _handoff_actions(manifest.get("readiness"))
        findings = tuple(
            AuthoringFinding(
                code=action.key,
                severity=(
                    "error"
                    if action.status == LiveHandoffStatus.FAILED.value
                    else "warning"
                ),
                message=_handoff_summary(manifest.get("readiness"), action.key, action.label),
                next_action=action.next_action,
            )
            for action in handoff_actions
            if action.status != LiveHandoffStatus.PASSED.value
        )
        next_action = next(
            (
                action.next_action
                for action in handoff_actions
                if action.status != LiveHandoffStatus.PASSED.value
            ),
            "Complete target-system review before any hardware run.",
        )
        return AuthoringStatus(
            status=AuthoringState.FINAL_READY_HANDOFF,
            findings=findings,
            artifacts=artifacts,
            allowed_action="complete_live_handoff",
            next_action=next_action,
            handoff_actions=handoff_actions,
        )
    if workflow_status == "scaffold_not_validated":
        inference_findings = _inference_findings(manifest.get("inference"))
        return AuthoringStatus(
            status=AuthoringState.SCAFFOLD_NEEDS_REVIEW,
            findings=(
                AuthoringFinding(
                    code="scaffold_not_validated",
                    severity="warning",
                    message="The scaffold has not run the required offline readiness gates.",
                ),
                *inference_findings,
            ),
            artifacts=artifacts,
            allowed_action="review_scaffold",
            next_action=(
                "Review inference.md, request.spec.yaml, and the grounded Protocol IR, then run "
                "final generation with simulation and compilation enabled."
            ),
        )
    findings = _generation_blocking_findings(manifest, workflow_status=workflow_status)
    if workflow_status == "needs_full_zeia_export":
        allowed_action = "provide_full_zeia_export"
        next_action = (
            "Provide and inspect a full FluentControl ZEIA export, or record explicit approval "
            "before continuing with a partial export."
        )
    else:
        allowed_action = "resolve_generation_blockers"
        next_action = "Resolve the reported generation/readiness blockers, then rerun final generation."
    return AuthoringStatus(
        status=AuthoringState.FINAL_GENERATION_BLOCKED,
        findings=findings,
        artifacts=artifacts,
        allowed_action=allowed_action,
        next_action=next_action,
    )


def repair_plan_status(
    plan: Mapping[str, Any],
    *,
    artifacts: Iterable[str | Path] = (),
) -> AuthoringStatus:
    actions = tuple(item for item in (plan.get("actions") or []) if isinstance(item, Mapping))
    findings = tuple(_finding_from_mapping(item, default_code="repair") for item in actions)
    ready_count = sum(1 for item in actions if str(item.get("status") or "") == "ready")
    review_count = sum(
        1 for item in actions if str(item.get("status") or "") in {"suggested", "needs_review"}
    )
    normalized_artifacts = _normalize_artifacts(artifacts)
    if ready_count:
        return AuthoringStatus(
            status=AuthoringState.REPAIR_READY,
            findings=findings,
            artifacts=normalized_artifacts,
            allowed_action="apply_repair",
            next_action="Review the ready repair actions, then apply the approved repair plan.",
        )
    if review_count:
        return AuthoringStatus(
            status=AuthoringState.REPAIR_NEEDS_REVIEW,
            findings=findings,
            artifacts=normalized_artifacts,
            allowed_action="review_repair_plan",
            next_action="Review the suggested repair actions; apply only actions that receive approval.",
        )
    return AuthoringStatus(
        status=AuthoringState.REPAIR_NOOP,
        findings=findings,
        artifacts=normalized_artifacts,
        allowed_action="continue_to_verification",
        next_action="No repair action is available or needed; continue to verification.",
    )


def repair_apply_status(
    *,
    plan: Mapping[str, Any],
    applied_actions: Iterable[Mapping[str, Any]],
    artifacts: Iterable[str | Path] = (),
) -> AuthoringStatus:
    applied = tuple(applied_actions)
    if not applied:
        return repair_plan_status(plan, artifacts=artifacts)
    return AuthoringStatus(
        status=AuthoringState.REPAIR_APPLIED,
        findings=tuple(_finding_from_mapping(item, default_code="repair") for item in applied),
        artifacts=_normalize_artifacts(artifacts),
        allowed_action="verify_repaired_draft",
        next_action="Verify the repaired draft before final generation or packaging.",
    )


def verification_status(
    report: Mapping[str, Any],
    *,
    artifacts: Iterable[str | Path] = (),
) -> AuthoringStatus:
    normalized_artifacts = _normalize_artifacts(artifacts)
    findings = _verification_findings(report)
    if bool(report.get("ready")):
        return AuthoringStatus(
            status=AuthoringState.VERIFICATION_READY,
            findings=findings,
            artifacts=normalized_artifacts,
            allowed_action="continue_to_final_handoff",
            next_action="Continue to the separate Script Editor and target-system handoff review.",
        )
    return AuthoringStatus(
        status=AuthoringState.VERIFICATION_BLOCKED,
        findings=findings,
        artifacts=normalized_artifacts,
        allowed_action="resolve_verification_blockers",
        next_action="Resolve every blocking readiness gate, then rerun bundle verification.",
    )


def _generation_blocking_findings(
    manifest: Mapping[str, Any],
    *,
    workflow_status: str,
) -> tuple[AuthoringFinding, ...]:
    findings: list[AuthoringFinding] = []
    findings.extend(_inference_findings(manifest.get("inference")))
    full_export = manifest.get("full_zeia_export")
    if isinstance(full_export, Mapping):
        for item in full_export.get("blocking_findings") or []:
            if isinstance(item, Mapping):
                normalized = _finding_from_mapping(item, default_code="full_zeia_export")
                findings.append(
                    AuthoringFinding(
                        code=normalized.code,
                        severity="error",
                        message=normalized.message,
                        location=normalized.location,
                        next_action=normalized.next_action,
                    )
                )
    readiness = manifest.get("readiness")
    if isinstance(readiness, Mapping):
        offline = readiness.get("offline_validation")
        if isinstance(offline, Mapping):
            summary = str(offline.get("summary") or "Required offline validation did not pass.")
            for gate in offline.get("blocking_gates") or offline.get("failing_gates") or []:
                findings.append(
                    AuthoringFinding(
                        code=str(gate),
                        severity="error",
                        message=summary,
                    )
                )
        review = readiness.get("review_state")
        if isinstance(review, Mapping):
            summary = str(review.get("summary") or "Review findings remain.")
            for gate in review.get("gates") or []:
                findings.append(
                    AuthoringFinding(
                        code=str(gate),
                        severity="warning",
                        message=summary,
                    )
                )
    if not findings:
        findings.append(
            AuthoringFinding(
                code=workflow_status,
                severity="error",
                message=f"Generation ended with workflow_status={workflow_status}.",
            )
        )
    return tuple(_deduplicate_findings(findings))


def _inference_findings(value: Any) -> tuple[AuthoringFinding, ...]:
    report = value if isinstance(value, Mapping) else {}
    inferred_count = int(report.get("inferred_count") or 0)
    unresolved_count = int(report.get("unresolved_count") or 0)
    findings: list[AuthoringFinding] = []
    if inferred_count:
        findings.append(
            AuthoringFinding(
                code="automatic_inference_review_required",
                severity="warning",
                message=f"{inferred_count} missing detail(s) were inferred automatically.",
                location="inference.md",
                next_action="Review every inferred value against the imported ZEIA and task.",
            )
        )
    if unresolved_count:
        findings.append(
            AuthoringFinding(
                code="automatic_inference_unresolved",
                severity="error",
                message=f"{unresolved_count} detail(s) remain unresolved in the candidate script.",
                location="inference.md",
                next_action="Resolve the remaining placeholders before final generation or instrument use.",
            )
        )
    return tuple(findings)


def _verification_findings(report: Mapping[str, Any]) -> tuple[AuthoringFinding, ...]:
    findings = [
        _finding_from_mapping(gate, default_code="readiness_gate")
        for gate in (report.get("gates") or [])
        if isinstance(gate, Mapping) and str(gate.get("status") or "") != "passed"
    ]
    if not findings and not bool(report.get("ready")):
        offline = report.get("offline_validation")
        summary = (
            str(offline.get("summary"))
            if isinstance(offline, Mapping) and offline.get("summary")
            else "Bundle verification did not pass."
        )
        findings.append(
            AuthoringFinding(
                code=str(report.get("readiness_status") or "verification_blocked"),
                severity="error",
                message=summary,
            )
        )
    return tuple(_deduplicate_findings(findings))


def _handoff_actions(value: Any) -> tuple[HandoffAction, ...]:
    readiness = value if isinstance(value, Mapping) else {}
    definitions = (
        ("fluentcontrol_load_diagnostic", "FluentControl load diagnostic"),
        ("script_editor_load", "Script Editor load"),
        ("hardware_run", "Hardware run"),
    )
    actions = []
    for key, label in definitions:
        item = readiness.get(key)
        item = item if isinstance(item, Mapping) else {}
        status = coerce_live_handoff_status(item.get("status"))
        actions.append(
            HandoffAction(
                key=key,
                label=label,
                status=status.value,
                next_action=str(item.get("next_action") or live_handoff_next_action(key, status)),
            )
        )
    return tuple(actions)


def _handoff_summary(value: Any, key: str, label: str) -> str:
    readiness = value if isinstance(value, Mapping) else {}
    item = readiness.get(key)
    if isinstance(item, Mapping) and item.get("summary"):
        return str(item["summary"])
    return f"{label} requires a recorded target-system result."


def _finding_from_mapping(item: Mapping[str, Any], *, default_code: str) -> AuthoringFinding:
    status = str(item.get("status") or "").strip().casefold()
    severity = str(item.get("severity") or "").strip().casefold()
    if severity not in {"error", "warning", "info"}:
        severity = "error" if status in {"failed", "error", "blocked"} else "warning"
        if status in {"passed", "ready", "applied"}:
            severity = "info"
    return AuthoringFinding(
        code=str(item.get("code") or item.get("id") or item.get("kind") or default_code),
        severity=severity,
        message=str(
            item.get("message")
            or item.get("summary")
            or item.get("reason")
            or status
            or default_code
        ),
        location=(
            str(item.get("location"))
            if item.get("location") not in (None, "")
            else None
        ),
        next_action=(
            str(item.get("next_action"))
            if item.get("next_action") not in (None, "")
            else None
        ),
    )


def _generation_artifacts(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    keys = (
        "workflow_report",
        "generation_manifest",
        "request_spec",
        "inference_report",
        "inference_json",
        "full_zeia_export_report",
        "protocol_ir",
        "python_draft",
        "repaired_draft",
        "recreate_script",
        "worktable_changes",
        "worktable_patch",
        "ready_validation",
        "validation_report_json",
        "validation_diff",
        "validation_diff_json",
        "compile_report",
        "failed_artifacts",
        "published_protocol_folder",
        "published_zeia_path",
    )
    values: list[Any] = [manifest.get(key) for key in keys]
    values.extend(manifest.get("ready_to_import_artifacts") or [])
    for key in ("published_artifacts", "internal_artifacts"):
        for item in manifest.get(key) or []:
            if isinstance(item, Mapping):
                values.append(item.get("path"))
            else:
                values.append(item)
    return _normalize_artifacts(values)


def _normalize_artifacts(values: Iterable[Any]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in (None, ""):
            continue
        text = str(value)
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _deduplicate_findings(findings: Iterable[AuthoringFinding]) -> list[AuthoringFinding]:
    result: list[AuthoringFinding] = []
    seen: set[tuple[str, str, str, str | None]] = set()
    for finding in findings:
        key = (finding.code, finding.severity, finding.message, finding.location)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result
