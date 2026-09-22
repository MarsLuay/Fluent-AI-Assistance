"""Conservative, source-backed DriverFramework device health analysis.

This module consumes the per-usage contracts produced by
``external_commands`` and the host manifest produced by ``host_environment``.
It deliberately does not infer a role from a macro name, turn execution time
into a timeout, or create a recovery/keepalive command.  Roles and state
observations must be supplied by imported source evidence, a documented host
record, runtime logs, or deterministic test injection.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


EXTERNAL_DEVICE_HEALTH_SCHEMA_VERSION = "tecan.external_device_health.v1"

# These are the exact DriverFramework states documented by Tecan in the issue
# source material. ``unknown`` is an analyzer sentinel, not a vendor state.
DOCUMENTED_DRIVER_STATES = (
    "Running",
    "Warning",
    "Enabled & Idle",
    "Simulation",
    "Error",
)
UNKNOWN_STATE = "unknown"

COMMAND_ROLES = (
    "initialize",
    "status",
    "read",
    "write",
    "wait",
    "reset",
    "shutdown",
    "unknown",
)
ROLE_CONFIDENCES = ("high", "medium", "low", "unknown")

LICENSE_STATUSES = (
    "valid",
    "missing",
    "expired",
    "simulation_only",
    "license_check_failed",
    "unknown",
)

EXTERNAL_DEVICE_DIAGNOSTIC_IDS = {
    "unknown_role": "external_device.command_role_unknown",
    "role_ambiguity": "external_device.command_role_ambiguous",
    "initialization_missing": "external_device.initialization_missing",
    "companion_missing": "external_device.companion_missing",
    "state_unknown": "external_device.state_unknown",
    "unsupported_state": "external_device.state_unsupported",
    "error_observed": "external_device.error_state_observed",
    "communication_failure": "external_device.communication_failure_observed",
    "identity_mismatch": "external_device.identity_mismatch",
    "license_blocked": "external_device.license_blocked_before_initialization",
}


@dataclass(frozen=True)
class CommandRoleEvidence:
    """An explicit role assignment for one source-backed command usage."""

    role: str
    provenance: str
    confidence: str = "high"
    contract_id: str | None = None
    macro_name: str = ""
    module_name: str = ""
    source_ref: str = ""
    required_companion: str | None = None
    requires_prior_roles: tuple[str, ...] = ()
    requires_prior_states: tuple[str, ...] = ()
    result_state: str | None = None

    def __post_init__(self) -> None:
        if self.role not in COMMAND_ROLES:
            raise ValueError(f"unsupported command role: {self.role!r}")
        if self.confidence not in ROLE_CONFIDENCES:
            raise ValueError(f"unsupported role confidence: {self.confidence!r}")
        if not self.provenance.strip():
            raise ValueError("role provenance is required")
        if self.role == "unknown" and self.result_state:
            raise ValueError("unknown command roles cannot declare a result state")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CommandRoleEvidence":
        return cls(
            role=str(value.get("role") or "unknown"),
            provenance=str(value.get("provenance") or ""),
            confidence=str(value.get("confidence") or "high"),
            contract_id=_optional_text(value.get("contract_id")),
            macro_name=str(value.get("macro_name") or ""),
            module_name=str(value.get("module_name") or ""),
            source_ref=str(value.get("source_ref") or ""),
            required_companion=_optional_text(value.get("required_companion")),
            requires_prior_roles=tuple(str(item) for item in value.get("requires_prior_roles") or ()),
            requires_prior_states=tuple(str(item) for item in value.get("requires_prior_states") or ()),
            result_state=_optional_text(value.get("result_state")),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "contract_id": self.contract_id,
            "macro_name": self.macro_name,
            "module_name": self.module_name,
            "source_ref": self.source_ref,
            "required_companion": self.required_companion,
            "requires_prior_roles": list(self.requires_prior_roles),
            "requires_prior_states": list(self.requires_prior_states),
            "result_state": self.result_state,
        }


@dataclass(frozen=True)
class ExternalDeviceIntegrationProfile:
    """Normalized identity and provenance for one external-device integration."""

    module_name: str
    driver_identity: str | None = None
    device_identity: str | None = None
    transport: str | None = None
    endpoint: str | None = None
    fluentcontrol_version: str | None = None
    fluentcontrol_build: str | None = None
    driverframework_version: str | None = None
    driverframework_build: str | None = None
    third_party_driver_version: str | None = None
    device_firmware: str | None = None
    host_environment_fingerprint: str | None = None
    source_contract_ids: tuple[str, ...] = ()
    license_status: str = "unknown"
    license_provenance: str | None = None
    integration_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.module_name.strip():
            raise ValueError("module_name is required")
        if self.license_status not in LICENSE_STATUSES:
            raise ValueError(f"unsupported license status: {self.license_status!r}")
        if self.license_status != "unknown" and not (self.license_provenance or "").strip():
            raise ValueError("non-unknown license status requires provenance")
        object.__setattr__(self, "integration_fingerprint", _fingerprint(self._fingerprint_payload()))

    @classmethod
    def from_sources(
        cls,
        *,
        module_name: str,
        source_contracts: Sequence[Mapping[str, Any]] = (),
        host_environment: Mapping[str, Any] | None = None,
        explicit_evidence: Mapping[str, Any] | None = None,
    ) -> "ExternalDeviceIntegrationProfile":
        """Build a profile without creating a second host/environment authority."""
        host = host_environment or {}
        explicit = explicit_evidence or {}
        fluent = _host_product(host, "FluentControl")
        contract_ids = tuple(
            str(item.get("contract_id"))
            for item in source_contracts
            if str(item.get("contract_id") or "")
        )
        return cls(
            module_name=module_name,
            driver_identity=_explicit_text(explicit, "driver_identity"),
            device_identity=_explicit_text(explicit, "device_identity"),
            transport=_explicit_text(explicit, "transport"),
            endpoint=_explicit_text(explicit, "endpoint"),
            fluentcontrol_version=_text_or_none(fluent.get("version")),
            fluentcontrol_build=_text_or_none(fluent.get("build")),
            driverframework_version=_explicit_text(explicit, "driverframework_version"),
            driverframework_build=_explicit_text(explicit, "driverframework_build"),
            third_party_driver_version=_explicit_text(explicit, "third_party_driver_version"),
            device_firmware=_explicit_text(explicit, "device_firmware"),
            host_environment_fingerprint=_text_or_none(host.get("fingerprint")),
            source_contract_ids=contract_ids,
            license_status=str(explicit.get("license_status") or "unknown"),
            license_provenance=_explicit_text(explicit, "license_provenance"),
        )

    def _fingerprint_payload(self) -> dict[str, Any]:
        return {
            "schema_version": EXTERNAL_DEVICE_HEALTH_SCHEMA_VERSION,
            "module_name": self.module_name,
            "driver_identity": self.driver_identity,
            "device_identity": self.device_identity,
            "transport": self.transport,
            "endpoint": self.endpoint,
            "fluentcontrol_version": self.fluentcontrol_version,
            "fluentcontrol_build": self.fluentcontrol_build,
            "driverframework_version": self.driverframework_version,
            "driverframework_build": self.driverframework_build,
            "third_party_driver_version": self.third_party_driver_version,
            "device_firmware": self.device_firmware,
            "host_environment_fingerprint": self.host_environment_fingerprint,
            "source_contract_ids": list(self.source_contract_ids),
            "license_status": self.license_status,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self._fingerprint_payload(),
            "license_provenance": self.license_provenance,
            "integration_fingerprint": self.integration_fingerprint,
        }


def analyze_external_device_session(
    profile: ExternalDeviceIntegrationProfile,
    contracts: Sequence[Mapping[str, Any]],
    role_evidence: Sequence[CommandRoleEvidence | Mapping[str, Any]] = (),
    *,
    observed_states: Mapping[str, Mapping[str, Any] | str] | None = None,
) -> dict[str, Any]:
    """Analyze an ordered source/runtime sequence without inventing transitions."""
    evidence = tuple(_role_evidence(item) for item in role_evidence)
    observations = observed_states or {}
    findings: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    prior_roles: list[str] = []
    prior_states: list[str] = []
    current_state = UNKNOWN_STATE

    for index, contract in enumerate(contracts):
        contract_id = _optional_text(contract.get("contract_id"))
        macro_name = str(contract.get("macro_name") or contract.get("name") or "")
        module_name = str(contract.get("module_name") or "")
        if module_name and module_name.casefold() != profile.module_name.casefold():
            findings.append(_finding(
                EXTERNAL_DEVICE_DIAGNOSTIC_IDS["identity_mismatch"],
                "Command belongs to a different external-device module",
                index=index,
                contract_id=contract_id,
            ))

        role, matching_evidence, ambiguous = _resolve_role(
            evidence,
            contract_id=contract_id,
            macro_name=macro_name,
            module_name=module_name,
        )
        if ambiguous:
            findings.append(_finding(
                EXTERNAL_DEVICE_DIAGNOSTIC_IDS["role_ambiguity"],
                "Command role has conflicting explicit evidence",
                index=index,
                contract_id=contract_id,
            ))
        if role == "unknown":
            findings.append(_finding(
                EXTERNAL_DEVICE_DIAGNOSTIC_IDS["unknown_role"],
                "Command role is unknown; no state transition was inferred",
                index=index,
                contract_id=contract_id,
            ))

        state_before = current_state
        explicit_state = observations.get(contract_id or "")
        if explicit_state is None and matching_evidence and matching_evidence.result_state:
            explicit_state = matching_evidence.result_state
        state_after, raw_state, state_finding = _observed_state(explicit_state)
        if state_finding:
            findings.append(_finding(state_finding, "Observed device state requires review", index=index, contract_id=contract_id, raw_state=raw_state))
        if (
            state_after in {"Running", "Enabled & Idle"}
            and profile.license_status in {"missing", "expired", "license_check_failed", "simulation_only"}
        ):
            findings.append(_finding(
                EXTERNAL_DEVICE_DIAGNOSTIC_IDS["license_blocked"],
                "License evidence does not establish real-device readiness",
                index=index,
                contract_id=contract_id,
                license_status=profile.license_status,
            ))
            state_after = UNKNOWN_STATE
        if state_after != UNKNOWN_STATE:
            current_state = state_after
            prior_states.append(state_after)
            if state_after == "Error":
                findings.append(_finding(
                    EXTERNAL_DEVICE_DIAGNOSTIC_IDS["error_observed"],
                    "DriverFramework error state was observed in the session evidence",
                    index=index,
                    contract_id=contract_id,
                ))
        elif role == "unknown":
            findings.append(_finding(
                EXTERNAL_DEVICE_DIAGNOSTIC_IDS["state_unknown"],
                "Device state remains unknown for an unclassified command",
                index=index,
                contract_id=contract_id,
            ))

        if matching_evidence:
            for required_role in matching_evidence.requires_prior_roles:
                if required_role not in prior_roles:
                    finding_id = (
                        EXTERNAL_DEVICE_DIAGNOSTIC_IDS["initialization_missing"]
                        if required_role == "initialize"
                        else EXTERNAL_DEVICE_DIAGNOSTIC_IDS["state_unknown"]
                    )
                    findings.append(_finding(
                        finding_id,
                        f"Required prior command role {required_role!r} was not observed",
                        index=index,
                        contract_id=contract_id,
                    ))
            for required_state in matching_evidence.requires_prior_states:
                if required_state not in prior_states:
                    findings.append(_finding(
                        EXTERNAL_DEVICE_DIAGNOSTIC_IDS["state_unknown"],
                        f"Required prior device state {required_state!r} was not observed",
                        index=index,
                        contract_id=contract_id,
                    ))

        companion = contract.get("following_companion")
        required_companion = matching_evidence.required_companion if matching_evidence else None
        if required_companion:
            actual = _next_contract_name(contracts, index)
            if actual != required_companion:
                findings.append(_finding(
                    EXTERNAL_DEVICE_DIAGNOSTIC_IDS["companion_missing"],
                    f"Source-backed companion {required_companion!r} is not next in the sequence",
                    index=index,
                    contract_id=contract_id,
                    actual_companion=actual,
                ))

        events.append({
            "index": index,
            "contract_id": contract_id,
            "macro_name": macro_name,
            "module_name": module_name,
            "role": role,
            "role_evidence": matching_evidence.as_dict() if matching_evidence else None,
            "state_before": state_before,
            "state_after": state_after,
            "observed_state_raw": raw_state,
            "following_companion": dict(companion) if isinstance(companion, Mapping) else None,
            # Execution time remains source data; this analyzer never treats it as a timeout.
            "source_execution_time": str(contract.get("execution_time") or ""),
        })
        if role != "unknown":
            prior_roles.append(role)

    return {
        "schema_version": EXTERNAL_DEVICE_HEALTH_SCHEMA_VERSION,
        "profile": profile.as_dict(),
        "state": current_state,
        "status": "needs_review" if findings else ("observed" if events else "unknown"),
        "commands": events,
        "findings": findings,
        "recovery_policy_owner": "#154",
        "parameter_contract_owner": "#155",
        "host_environment_owner": "#156",
    }


def correlate_external_device_logs(
    profile: ExternalDeviceIntegrationProfile,
    records: Sequence[Mapping[str, Any] | Any],
) -> dict[str, Any]:
    """Build a timeline only from records with explicit module/device evidence."""
    matched: list[tuple[int, Mapping[str, Any] | Any]] = []
    ambiguous = 0
    unmatched = 0
    for index, record in enumerate(records):
        module = _record_value(record, "module")
        device_id = _record_value(record, "device_id")
        if module and module.casefold() == profile.module_name.casefold():
            matched.append((index, record))
        elif profile.device_identity and device_id == profile.device_identity:
            matched.append((index, record))
        elif not module and not device_id:
            ambiguous += 1
        else:
            unmatched += 1

    matched.sort(key=lambda item: (_record_value(item[1], "timestamp"), item[0]))
    events: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for _, record in matched:
        message = _record_value(record, "message")
        state = _state_from_text(message)
        event = {
            "timestamp": _record_value(record, "timestamp"),
            "application": _record_value(record, "application"),
            "module": _record_value(record, "module"),
            "source": _record_value(record, "source"),
            "command_hint": _record_value(record, "command_hint"),
            "message": message,
            "state": state,
        }
        events.append(event)
        if state == "Error":
            findings.append(_finding(
                EXTERNAL_DEVICE_DIAGNOSTIC_IDS["error_observed"],
                "DriverFramework error state was observed in correlated logs",
                timestamp=event["timestamp"],
            ))
        if re.search(r"\b(?:timed?\s*out|timeout|communication\s+loss|device\s+is\s+in\s+error\s+status)\b", message, re.IGNORECASE):
            findings.append(_finding(
                EXTERNAL_DEVICE_DIAGNOSTIC_IDS["communication_failure"],
                "A communication failure was observed in correlated logs",
                timestamp=event["timestamp"],
            ))

    return {
        "schema_version": EXTERNAL_DEVICE_HEALTH_SCHEMA_VERSION,
        "profile_fingerprint": profile.integration_fingerprint,
        "matched_record_count": len(events),
        "ambiguous_record_count": ambiguous,
        "unmatched_record_count": unmatched,
        "events": events,
        "findings": findings,
        "root_cause": "unknown",
    }


def _role_evidence(value: CommandRoleEvidence | Mapping[str, Any]) -> CommandRoleEvidence:
    return value if isinstance(value, CommandRoleEvidence) else CommandRoleEvidence.from_mapping(value)


def _resolve_role(
    evidence: Sequence[CommandRoleEvidence],
    *,
    contract_id: str | None,
    macro_name: str,
    module_name: str,
) -> tuple[str, CommandRoleEvidence | None, bool]:
    if contract_id:
        exact = [item for item in evidence if item.contract_id == contract_id]
        if exact:
            roles = {item.role for item in exact}
            return (next(iter(roles)), exact[0], len(roles) > 1) if len(roles) == 1 else ("unknown", None, True)
    candidates = [
        item for item in evidence
        if item.contract_id is None
        and item.macro_name.casefold() == macro_name.casefold()
        and item.module_name.casefold() == module_name.casefold()
    ]
    roles = {item.role for item in candidates}
    if len(roles) == 1:
        return next(iter(roles)), candidates[0], False
    return "unknown", None, bool(candidates)


def _observed_state(value: Mapping[str, Any] | str | None) -> tuple[str, str, str | None]:
    if value is None:
        return UNKNOWN_STATE, "", None
    if isinstance(value, Mapping):
        raw = str(value.get("state") or value.get("value") or "")
    else:
        raw = str(value)
    if raw in DOCUMENTED_DRIVER_STATES:
        return raw, raw, None
    if not raw or raw == UNKNOWN_STATE:
        return UNKNOWN_STATE, raw, None
    return UNKNOWN_STATE, raw, EXTERNAL_DEVICE_DIAGNOSTIC_IDS["unsupported_state"]


def _state_from_text(text: str) -> str:
    for state in DOCUMENTED_DRIVER_STATES:
        if re.search(re.escape(state), text or "", re.IGNORECASE):
            return state
    return UNKNOWN_STATE


def _next_contract_name(contracts: Sequence[Mapping[str, Any]], index: int) -> str | None:
    if index + 1 >= len(contracts):
        return None
    return _optional_text(contracts[index + 1].get("macro_name") or contracts[index + 1].get("name"))


def _host_product(host: Mapping[str, Any], family: str) -> Mapping[str, Any]:
    for product in host.get("products") or ():
        if isinstance(product, Mapping) and str(product.get("family") or "").casefold() == family.casefold():
            return product
    return {}


def _record_value(record: Mapping[str, Any] | Any, name: str) -> str:
    value = record.get(name) if isinstance(record, Mapping) else getattr(record, name, "")
    return str(value or "")


def _finding(identifier: str, message: str, **details: Any) -> dict[str, Any]:
    return {"id": identifier, "message": message, **details}


def _explicit_text(values: Mapping[str, Any], key: str) -> str | None:
    return _text_or_none(values.get(key))


def _optional_text(value: Any) -> str | None:
    return _text_or_none(value)


def _text_or_none(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _fingerprint(payload: Mapping[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()
