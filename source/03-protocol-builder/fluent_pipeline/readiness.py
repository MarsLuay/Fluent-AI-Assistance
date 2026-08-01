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


STRICT_READY = frozenset({ReadinessGateStatus.PASSED})
REVIEWABLE_READY = frozenset({ReadinessGateStatus.PASSED, ReadinessGateStatus.NEEDS_REVIEW})


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
    load_status = str(load_diag.get("status") or "not_run")
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

    if offline_status == "ready_to_import":
        hardware_status = "hardware_review_required"
    elif offline_status == "not_validated":
        hardware_status = "not_validated"
    else:
        hardware_status = "validated_not_ready"

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
            "status": load_status,
            "summary": str(
                load_diag.get("summary")
                or "Optional FluentControl import/load diagnostic did not run."
            ),
            "requested": bool(load_diag.get("requested")),
            "gate": str(load_diag.get("gate") or "Gate 27"),
            "gate_present": bool(load_diag.get("gate_present")),
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
            "status": load_status,
            "gate_27_fluent_context": fluent_context_gate_status,
            "summary": str(
                load_diag.get("summary")
                or "Optional FluentControl import/load diagnostic did not run."
            ),
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
            "status": hardware_status,
            "summary": (
                "Requires operator review, target-system dependencies, and real FluentControl validation before instrument use."
                if hardware_status == "hardware_review_required"
                else "Hardware readiness is blocked until offline validation passes."
                if hardware_status == "validated_not_ready"
                else "Hardware readiness is unavailable because validation did not run."
            ),
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
    load_status = str(((profile.get("script_editor_load") or {}).get("status")) or "not_run")

    if workflow_status == "scaffold_not_validated" or offline_status == "not_validated":
        return "scaffold_not_validated"
    if offline_status != "ready_to_import":
        return "validated_not_ready"
    if load_status == "load_clean":
        return "load_clean"
    if load_status == "load_failed":
        return "load_failed"
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
