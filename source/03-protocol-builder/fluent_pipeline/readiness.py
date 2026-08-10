"""Canonical readiness helpers shared across workflow artifacts."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


class ReadinessGateStatus(StrEnum):
    """Canonical readiness statuses for required gate evaluation."""

    PASSED = "passed"
    NEEDS_REVIEW = "needs_review"
    FAILED = "failed"
    ERROR = "error"
    SKIPPED = "skipped"
    NOT_RUN = "not_run"


class LiveHandoffStatus(StrEnum):
    """Terminal statuses for checks that require a target-system handoff."""

    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    NOT_RUN = "not_run"


STRICT_READY = frozenset({ReadinessGateStatus.PASSED})
REVIEWABLE_READY = frozenset({ReadinessGateStatus.PASSED, ReadinessGateStatus.NEEDS_REVIEW})
LIVE_HANDOFF_STATUSES = frozenset(LiveHandoffStatus)


CANONICAL_READINESS_KEYS = (
    "offline_validation",
    "review_state",
    "fluentcontrol_load_diagnostic",
    "generated_zeia_import",
    "script_editor_load",
    "simulation",
    "hardware_run",
)


def normalize_readiness_gate_status(value: Any) -> ReadinessGateStatus | None:
    """Return the canonical readiness status for a gate value, if known."""
    if isinstance(value, ReadinessGateStatus):
        return value
    text = str(value or "").strip().casefold()
    if not text:
        return None
    try:
        return ReadinessGateStatus(text)
    except ValueError:
        return None


def coerce_readiness_gate_status(value: Any) -> ReadinessGateStatus | str:
    """Normalize known statuses and preserve unknown ones as fail-closed strings."""
    normalized = normalize_readiness_gate_status(value)
    if normalized is not None:
        return normalized
    text = str(value or "").strip().casefold()
    return text or "unknown"


def normalize_readiness_gate_policy(value: Iterable[Any] | None) -> frozenset[ReadinessGateStatus]:
    """Normalize a selected readiness policy into canonical gate statuses."""
    if value is None:
        return STRICT_READY
    if isinstance(value, str):
        label = value.strip().casefold()
        if label == "strict_ready":
            return STRICT_READY
        if label == "reviewable_ready":
            return REVIEWABLE_READY
        value = [value]
    elif isinstance(value, ReadinessGateStatus):
        value = [value]
    statuses = {
        status
        for item in value
        if (status := normalize_readiness_gate_status(item)) is not None
    }
    return frozenset(statuses)


def readiness_policy_name(value: Iterable[Any] | None) -> str:
    """Return a stable name for a normalized readiness policy."""
    policy = normalize_readiness_gate_policy(value)
    if policy == STRICT_READY:
        return "STRICT_READY"
    if policy == REVIEWABLE_READY:
        return "REVIEWABLE_READY"
    return "CUSTOM_READY_POLICY"


def readiness_policy_statuses(value: Iterable[Any] | None) -> tuple[str, ...]:
    """Return the sorted canonical statuses accepted by a readiness policy."""
    policy = normalize_readiness_gate_policy(value)
    return tuple(sorted(status.value for status in policy))


def gate_status_in_policy(value: Any, policy: Iterable[Any] | None) -> bool:
    """Return whether a gate status is allowed by the selected policy."""
    normalized = normalize_readiness_gate_status(value)
    if normalized is None:
        return False
    return normalized in normalize_readiness_gate_policy(policy)


_LIVE_HANDOFF_STATUS_ALIASES = {
    "load_clean": LiveHandoffStatus.PASSED,
    "load_failed": LiveHandoffStatus.FAILED,
    "error": LiveHandoffStatus.FAILED,
    "unavailable": LiveHandoffStatus.NOT_RUN,
    "hardware_review_required": LiveHandoffStatus.NEEDS_REVIEW,
    "validated_not_ready": LiveHandoffStatus.NEEDS_REVIEW,
    "import_ready_needs_review": LiveHandoffStatus.NEEDS_REVIEW,
    "not_validated": LiveHandoffStatus.NOT_RUN,
    "not_configured": LiveHandoffStatus.NOT_RUN,
    "skipped": LiveHandoffStatus.NOT_RUN,
}


def normalize_live_handoff_status(value: Any) -> LiveHandoffStatus | None:
    """Normalize legacy live-handoff values into the four terminal statuses."""
    if isinstance(value, LiveHandoffStatus):
        return value
    text = str(value or "").strip().casefold()
    if not text:
        return None
    try:
        return LiveHandoffStatus(text)
    except ValueError:
        return _LIVE_HANDOFF_STATUS_ALIASES.get(text)


def coerce_live_handoff_status(value: Any) -> LiveHandoffStatus:
    """Return a deterministic live-handoff status, failing closed to review."""
    normalized = normalize_live_handoff_status(value)
    if normalized is not None:
        return normalized
    if value is None or not str(value).strip():
        return LiveHandoffStatus.NOT_RUN
    return LiveHandoffStatus.NEEDS_REVIEW


def live_handoff_next_action(kind: str, status: Any) -> str:
    """Return one plain-language next action for a live-handoff state."""
    normalized = coerce_live_handoff_status(status)
    actions = {
        "fluentcontrol_load_diagnostic": {
            LiveHandoffStatus.PASSED: "No action: Gate 27 passed; continue with the separate hardware review.",
            LiveHandoffStatus.FAILED: "Resolve the Gate 27 FluentControl load errors, then rerun Gate 27 or manually reopen the generated script in Script Editor.",
            LiveHandoffStatus.NEEDS_REVIEW: "Review the Gate 27 diagnostic details, then rerun the check or manually open the generated script in Script Editor.",
            LiveHandoffStatus.NOT_RUN: "Run optional Gate 27 on the target FluentControl system, or manually open the generated script in Script Editor.",
        },
        "script_editor_load": {
            LiveHandoffStatus.PASSED: "No action: Script Editor load is confirmed; complete hardware review before running.",
            LiveHandoffStatus.FAILED: "Resolve the Script Editor load error, then reopen the generated script and record a clean load.",
            LiveHandoffStatus.NEEDS_REVIEW: "Review the Script Editor handoff details, then reopen the generated script and record the result.",
            LiveHandoffStatus.NOT_RUN: "Open the generated script in FluentControl Script Editor and record whether it loads cleanly.",
        },
        "hardware_run": {
            LiveHandoffStatus.PASSED: "Proceed only under the approved target-system run procedure.",
            LiveHandoffStatus.FAILED: "Resolve the target-system run failure before retrying; do not execute the method until it is reviewed.",
            LiveHandoffStatus.NEEDS_REVIEW: "Review target dependencies, deck state, labware, liquids, adapters, fingers, prompts, and instrument setup before running.",
            LiveHandoffStatus.NOT_RUN: "Resolve offline blockers first, then complete target-system review before any hardware run.",
        },
    }
    return actions.get(kind, {}).get(
        normalized,
        "Review the handoff details and record a definitive result before continuing.",
    )


_NON_EXECUTION_STATUSES = frozenset({"unavailable", "skipped", "not_configured"})
_LIVE_EVIDENCE_KEYS = (
    "errors",
    "runtime_errors",
    "diagnostics",
    "messages",
    "last_error",
    "returncode",
    "stdout_sample",
    "stderr_sample",
)


def _mapping_has_live_evidence(value: Mapping[str, Any]) -> bool:
    return any(value.get(key) not in (None, "", [], {}) for key in _LIVE_EVIDENCE_KEYS)


def _canonical_load_status(
    load_diag: Mapping[str, Any],
    gates: Mapping[str, Mapping[str, Any]],
) -> tuple[LiveHandoffStatus, str]:
    """Normalize Gate 27, accounting for compatibility-shim non-execution details."""
    detail = gates.get("fluent_context_check") or {}
    details = detail.get("details") if isinstance(detail.get("details"), Mapping) else {}
    detail_status = str(details.get("status") or "").strip().casefold()
    if detail_status in _NON_EXECUTION_STATUSES:
        return (
            LiveHandoffStatus.NEEDS_REVIEW if _mapping_has_live_evidence(details) else LiveHandoffStatus.NOT_RUN,
            "gate_details",
        )
    return coerce_live_handoff_status(load_diag.get("status")), "report"


def build_canonical_readiness(
    *,
    validation_report: Mapping[str, Any] | None,
    package_outputs: Sequence[str | Path] | None,
) -> dict[str, Any]:
    report = validation_report if isinstance(validation_report, Mapping) else {}
    gates = {
        str(gate.get("id")): gate
        for gate in (report.get("gates") or [])
        if isinstance(gate, Mapping) and gate.get("id") is not None
    }

    def gate_status(gate_id: str) -> str:
        gate = gates.get(gate_id) or {}
        return str(gate.get("status") or "not_run")

    has_project_archive = any(str(path).lower().endswith(".zeia") for path in (package_outputs or []))
    offline = report.get("offline_validation") if isinstance(report.get("offline_validation"), Mapping) else {}
    review = report.get("review_state") if isinstance(report.get("review_state"), Mapping) else {}
    load_diag = (
        report.get("fluentcontrol_load_diagnostic")
        if isinstance(report.get("fluentcontrol_load_diagnostic"), Mapping)
        else {}
    )

    offline_status = str(offline.get("status") or "not_validated")
    review_status = str(review.get("status") or "not_validated")
    load_status, load_evidence = _canonical_load_status(load_diag, gates)
    simulation_gate_status = gate_status("simulation_passes")
    fluent_context_gate_status = gate_status("fluent_context_check")

    if offline_status == "ready_to_import":
        generated_zeia_status = (
            "import_ready_needs_review"
            if review_status == "import_ready_needs_review"
            else "ready_to_import"
        )
    elif offline_status == "not_validated":
        generated_zeia_status = "not_validated"
    else:
        generated_zeia_status = "validated_not_ready"

    if simulation_gate_status == "passed":
        simulation_status = "passed"
    elif simulation_gate_status in {"failed", "needs_review"}:
        simulation_status = "not_passed"
    else:
        simulation_status = "not_verified"

    script_editor_input = report.get("script_editor_load")
    if isinstance(script_editor_input, Mapping) and "status" in script_editor_input:
        script_editor_status = coerce_live_handoff_status(script_editor_input.get("status"))
        script_editor_summary = str(
            script_editor_input.get("summary")
            or "Script Editor load handoff status was supplied by the live result."
        )
        script_editor_evidence = "reported"
    elif load_status in {LiveHandoffStatus.PASSED, LiveHandoffStatus.FAILED, LiveHandoffStatus.NEEDS_REVIEW}:
        script_editor_status = load_status
        script_editor_evidence = "gate_27" if load_evidence == "report" else "gate_27:gate_details"
        script_editor_summary = {
            LiveHandoffStatus.PASSED: "Script Editor load was confirmed by the Gate 27 FluentControl diagnostic.",
            LiveHandoffStatus.FAILED: "The Gate 27 FluentControl diagnostic reported a Script Editor load failure.",
            LiveHandoffStatus.NEEDS_REVIEW: "Gate 27 produced a result that requires Script Editor handoff review.",
        }[load_status]
    else:
        script_editor_status = LiveHandoffStatus.NOT_RUN
        script_editor_evidence = "manual_handoff_required"
        script_editor_summary = "Script Editor load was not checked by Gate 27 or a manual handoff."

    hardware_input = report.get("hardware_run")
    if isinstance(hardware_input, Mapping) and "status" in hardware_input:
        hardware_status = coerce_live_handoff_status(hardware_input.get("status"))
        hardware_summary = str(
            hardware_input.get("summary")
            or "Hardware-run handoff status was supplied by the live result."
        )
    elif offline_status == "ready_to_import":
        hardware_status = LiveHandoffStatus.NEEDS_REVIEW
        hardware_summary = (
            "Offline validation passed, but the bundle does not certify hardware-run readiness; "
            "target-system review is required."
        )
    else:
        hardware_status = LiveHandoffStatus.NOT_RUN
        hardware_summary = (
            "No hardware run was performed. Resolve offline validation blockers before target-system review."
        )

    return {
        "offline_validation": {
            "status": offline_status,
            "summary": str(offline.get("summary") or "Required offline validation did not run."),
            "policy": str(offline.get("policy") or "STRICT_READY"),
            "policy_statuses": list(offline.get("policy_statuses") or ("passed",)),
            "required_gate_count": int(offline.get("required_gate_count") or 0),
            "passed_count": int(offline.get("passed_count") or 0),
            "failed_count": int(offline.get("failed_count") or 0),
            "blocking_count": int(offline.get("blocking_count") or offline.get("failed_count") or 0),
            "blocking_gates": list(offline.get("blocking_gates") or offline.get("failing_gates") or []),
            "failing_gates": list(offline.get("failing_gates") or []),
            "host_instrument_config_blocking": bool(offline.get("host_instrument_config_blocking")),
        },
        "review_state": {
            "status": review_status,
            "summary": str(review.get("summary") or "Review state is unavailable because ready validation did not run."),
            "policy": str(review.get("policy") or "REVIEWABLE_READY"),
            "policy_statuses": list(review.get("policy_statuses") or ("passed", "needs_review")),
            "needs_review_count": int(review.get("needs_review_count") or 0),
            "gates": list(review.get("gates") or []),
        },
        "fluentcontrol_load_diagnostic": {
            "status": load_status.value,
            "summary": str(
                load_diag.get("summary")
                or "Optional FluentControl import/load diagnostic did not run."
            ),
            "requested": bool(load_diag.get("requested")),
            "gate": str(load_diag.get("gate") or "Gate 27"),
            "gate_present": bool(load_diag.get("gate_present")),
            "next_action": live_handoff_next_action("fluentcontrol_load_diagnostic", load_status),
        },
        "generated_zeia_import": {
            "status": generated_zeia_status,
            "package_kind": "generated_zeia" if has_project_archive else "direct_xscr_only",
            "gate_23_checksums": gate_status("checksums_valid"),
            "gate_24_archive": gate_status("generated_zeia_valid"),
            "summary": (
                "Archive import/checksum health only; Script Editor load still must be checked separately."
            ),
        },
        "script_editor_load": {
            "status": script_editor_status.value,
            "gate_27_fluent_context": fluent_context_gate_status,
            "summary": script_editor_summary,
            "evidence": script_editor_evidence,
            "next_action": live_handoff_next_action("script_editor_load", script_editor_status),
        },
        "simulation": {
            "status": simulation_status,
            "gate_7_simulation": simulation_gate_status,
            "summary": (
                "Offline simulation passed."
                if simulation_status == "passed"
                else "Offline simulation did not produce a passing result."
                if simulation_status == "not_passed"
                else "Offline simulation was not verified in this run."
            ),
        },
        "hardware_run": {
            "status": hardware_status.value,
            "summary": hardware_summary,
            "next_action": live_handoff_next_action("hardware_run", hardware_status),
        },
    }


def readiness_status_from_readiness(
    readiness: Mapping[str, Any] | None,
    *,
    workflow_status: str | None = None,
) -> str:
    profile = readiness if isinstance(readiness, Mapping) else {}
    offline_status = str(((profile.get("offline_validation") or {}).get("status")) or "not_validated")
    review_status = str(((profile.get("review_state") or {}).get("status")) or "not_validated")
    if workflow_status == "scaffold_not_validated" or offline_status == "not_validated":
        return "scaffold_not_validated"
    if offline_status != "ready_to_import":
        return "validated_not_ready"
    if review_status == "import_ready_needs_review":
        return "import_ready_needs_review"
    return "ready_to_import"


def embed_readiness(
    payload: dict[str, Any],
    *,
    readiness: dict[str, Any],
    readiness_status: str,
) -> dict[str, Any]:
    payload["readiness_status"] = readiness_status
    payload["readiness"] = readiness
    for key in CANONICAL_READINESS_KEYS:
        payload[key] = readiness[key]
    return payload
