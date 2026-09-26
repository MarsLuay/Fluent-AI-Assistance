"""Evidence-backed external-device labware handoff contracts.

The contract in this module joins two deliberately separate identities:
external-device state and FluentControl's logical deck state.  It is a
source-backed mapping boundary, not a device protocol or a physical-state
simulator.  In particular, an external invocation next to ``AddLabware`` is
not evidence of a handoff unless an imported pattern, wrapper contract,
source command contract, or explicit user mapping links the two operations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


EXTERNAL_LABWARE_HANDOFF_SCHEMA = "tecan.external_labware_handoff.v1"
EXTERNAL_LABWARE_HANDOFF_SCHEMA_VERSION = EXTERNAL_LABWARE_HANDOFF_SCHEMA

HANDOFF_DIRECTIONS = (
    "device_to_deck",
    "deck_to_device",
    "device_to_device",
    "unknown",
)
HANDOFF_SYNC_MODES = (
    "synchronous",
    "asynchronous_with_confirmation",
    "unknown",
)
HANDOFF_STATES = (
    "planned",
    "external_action_started",
    "external_action_succeeded",
    "external_action_failed",
    "physical_presence_unconfirmed",
    "logical_state_updated",
    "reconciled",
    "state_ambiguous",
)
LOGICAL_MUTATIONS = ("add", "remove", "move", "attribute_update", "unknown")
PROVENANCE_KINDS = (
    "source_command_contract",
    "external_process_contract",
    "imported_pattern",
    "wrapper_metadata",
    "explicit_user_intent",
    "device_session",
    "canonical_ir",
)
CONFIDENCE_LEVELS = ("high", "medium", "low", "unknown")

HANDOFF_DIAGNOSTIC_IDS = {
    "schema": "external_labware_handoff.schema_invalid",
    "required": "external_labware_handoff.required_field_missing",
    "direction": "external_labware_handoff.direction_invalid",
    "sync_mode": "external_labware_handoff.sync_mode_invalid",
    "state": "external_labware_handoff.state_invalid",
    "mutation": "external_labware_handoff.mutation_invalid",
    "provenance": "external_labware_handoff.provenance_missing",
    "external_identity": "external_labware_handoff.external_identity_missing",
    "logical_identity": "external_labware_handoff.logical_identity_missing",
    "position": "external_labware_handoff.position_not_opaque",
    "fingerprint": "external_labware_handoff.fingerprint_mismatch",
    "untrusted_effect": "external_labware_handoff.external_effect_unknown",
    "unlinked_mutation": "external_labware_handoff.logical_mutation_unlinked",
}


@dataclass(frozen=True)
class HandoffProvenance:
    """One explicit source for a handoff relationship or field."""

    kind: str
    source_ref: str
    reason: str
    confidence: str = "high"
    relationship: str = "handoff_mapping"
    lineage: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in PROVENANCE_KINDS:
            raise ValueError(f"unsupported handoff provenance kind: {self.kind!r}")
        if self.confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"unsupported handoff provenance confidence: {self.confidence!r}")
        if not self.source_ref.strip():
            raise ValueError("handoff provenance source_ref is required")
        if not self.reason.strip():
            raise ValueError("handoff provenance reason is required")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "HandoffProvenance":
        return cls(
            kind=str(value.get("kind") or ""),
            source_ref=str(value.get("source_ref") or value.get("source") or ""),
            reason=str(value.get("reason") or ""),
            confidence=str(value.get("confidence") or "high"),
            relationship=str(value.get("relationship") or "handoff_mapping"),
            lineage=tuple(str(item) for item in value.get("lineage") or ()),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_ref": self.source_ref,
            "reason": self.reason,
            "confidence": self.confidence,
            "relationship": self.relationship,
            "lineage": list(self.lineage),
        }


@dataclass(frozen=True)
class ExternalLabwareHandoff:
    """Normalized, JSON-serializable external-to-logical handoff contract."""

    handoff_id: str
    direction: str
    external: Mapping[str, Any]
    logical: Mapping[str, Any]
    synchronization: Mapping[str, Any]
    logical_mutation: Mapping[str, Any]
    expected_transitions: tuple[Mapping[str, Any], ...]
    provenance: tuple[HandoffProvenance, ...]
    unknown_fields: Mapping[str, Any] = field(default_factory=dict)
    fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        errors = _validate_mapping(self._as_dict_without_fingerprint(), check_fingerprint=False)
        if errors:
            raise ValueError("invalid external labware handoff: " + "; ".join(errors))
        object.__setattr__(
            self,
            "fingerprint",
            fingerprint_handoff(self._as_dict_without_fingerprint()),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExternalLabwareHandoff":
        if not isinstance(value, Mapping):
            raise TypeError("external labware handoff must be a mapping")
        known = {
            "schema_version",
            "handoff_id",
            "direction",
            "external",
            "logical",
            "synchronization",
            "logical_mutation",
            "expected_transitions",
            "provenance",
            "unknown_fields",
            "fingerprint",
        }
        unknown = dict(value.get("unknown_fields") or {})
        unknown.update({key: item for key, item in value.items() if key not in known})
        provenance = tuple(
            item if isinstance(item, HandoffProvenance) else HandoffProvenance.from_mapping(item)
            for item in value.get("provenance") or ()
            if isinstance(item, (HandoffProvenance, Mapping))
        )
        return cls(
            handoff_id=str(value.get("handoff_id") or ""),
            direction=str(value.get("direction") or "unknown"),
            external=_mapping(value.get("external")),
            logical=_mapping(value.get("logical")),
            synchronization=_mapping(value.get("synchronization")),
            logical_mutation=_mapping(value.get("logical_mutation")),
            expected_transitions=tuple(
                dict(item) for item in value.get("expected_transitions") or () if isinstance(item, Mapping)
            ),
            provenance=provenance,
            unknown_fields=unknown,
        )

    def _as_dict_without_fingerprint(self) -> dict[str, Any]:
        return {
            "schema_version": EXTERNAL_LABWARE_HANDOFF_SCHEMA,
            "handoff_id": self.handoff_id,
            "direction": self.direction,
            "external": _json_value(self.external),
            "logical": _json_value(self.logical),
            "synchronization": _json_value(self.synchronization),
            "logical_mutation": _json_value(self.logical_mutation),
            "expected_transitions": [_json_value(item) for item in self.expected_transitions],
            "provenance": [item.as_dict() for item in self.provenance],
            "unknown_fields": _json_value(self.unknown_fields),
        }

    def as_dict(self) -> dict[str, Any]:
        return {**self._as_dict_without_fingerprint(), "fingerprint": self.fingerprint}


def make_handoff_contract(
    *,
    handoff_id: str,
    direction: str,
    external: Mapping[str, Any],
    logical: Mapping[str, Any],
    synchronization: Mapping[str, Any],
    logical_mutation: Mapping[str, Any],
    expected_transitions: Sequence[Mapping[str, Any]] = (),
    provenance: Sequence[HandoffProvenance | Mapping[str, Any]] = (),
    unknown_fields: Mapping[str, Any] | None = None,
) -> ExternalLabwareHandoff:
    """Construct one validated handoff with a deterministic fingerprint."""
    return ExternalLabwareHandoff(
        handoff_id=handoff_id,
        direction=direction,
        external=dict(external),
        logical=dict(logical),
        synchronization=dict(synchronization),
        logical_mutation=dict(logical_mutation),
        expected_transitions=tuple(dict(item) for item in expected_transitions),
        provenance=tuple(
            item if isinstance(item, HandoffProvenance) else HandoffProvenance.from_mapping(item)
            for item in provenance
        ),
        unknown_fields=dict(unknown_fields or {}),
    )


def validate_handoff_contract(
    value: ExternalLabwareHandoff | Mapping[str, Any],
) -> dict[str, Any]:
    """Return deterministic validation details without mutating the input."""
    if isinstance(value, ExternalLabwareHandoff):
        payload = value.as_dict()
    elif isinstance(value, Mapping):
        payload = _json_value(dict(value))
    else:
        payload = {}
    errors = _validate_mapping(payload, check_fingerprint=True)
    return {
        "schema_version": EXTERNAL_LABWARE_HANDOFF_SCHEMA,
        "ok": not errors,
        "errors": [
            {"id": _diagnostic_id(error), "message": error}
            for error in errors
        ],
        "fingerprint": fingerprint_handoff(payload) if not errors else None,
    }


def validate_external_labware_handoff(
    value: ExternalLabwareHandoff | Mapping[str, Any],
) -> dict[str, Any]:
    """Descriptive alias for adapters and callers."""
    return validate_handoff_contract(value)


def contract_validation_errors(value: ExternalLabwareHandoff | Mapping[str, Any]) -> list[str]:
    """Return only stable validation messages for small validation callers."""
    result = validate_handoff_contract(value)
    return [str(item["message"]) for item in result["errors"]]


def fingerprint_handoff(value: ExternalLabwareHandoff | Mapping[str, Any]) -> str:
    """Fingerprint contract semantics while excluding a supplied fingerprint."""
    payload = value.as_dict() if isinstance(value, ExternalLabwareHandoff) else dict(value)
    payload.pop("fingerprint", None)
    payload["schema_version"] = EXTERNAL_LABWARE_HANDOFF_SCHEMA
    encoded = json.dumps(_json_value(payload), sort_keys=True, separators=(",", ":"))
    return "elh_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def derive_handoff_candidates(
    external_contracts: Sequence[Mapping[str, Any]] = (),
    logical_steps: Sequence[Mapping[str, Any]] = (),
    *,
    source_patterns: Sequence[Mapping[str, Any]] = (),
    wrapper_metadata: Sequence[Mapping[str, Any]] = (),
    explicit_mappings: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Derive only explicitly linked handoffs and retain unknown effects.

    The function deliberately does not inspect neighboring list items as a
    relationship.  A mapping must carry an external reference and a logical
    step/identity reference, plus provenance.  Unmapped external commands are
    returned as unknown effects so callers cannot mistake adjacency for proof.
    """
    contracts = [dict(item) for item in external_contracts if isinstance(item, Mapping)]
    steps = [dict(item) for item in logical_steps if isinstance(item, Mapping)]
    by_external = _index_references(contracts)
    by_step = _index_references(steps)
    mappings = [
        *(_mapping_entries(source_patterns, "source_pattern")),
        *(_mapping_entries(wrapper_metadata, "wrapper_metadata")),
        *(_mapping_entries(explicit_mappings, "explicit_user_intent")),
    ]
    candidates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    linked_external: set[str] = set()
    linked_steps: set[str] = set()

    for mapping in mappings:
        external_ref = _first_ref(mapping, "external_ref", "invocation_ref", "contract_id")
        logical_ref = _first_ref(mapping, "logical_step_ref", "logical_ref", "step_id")
        if not external_ref or not logical_ref:
            diagnostics.append(_finding(
                HANDOFF_DIAGNOSTIC_IDS["required"],
                "Evidence mapping must identify both the external invocation and logical mutation",
                mapping=mapping,
            ))
            continue
        external = by_external.get(external_ref)
        logical = by_step.get(logical_ref)
        if external is None or logical is None:
            diagnostics.append(_finding(
                HANDOFF_DIAGNOSTIC_IDS["required"],
                "Evidence mapping refers to an external contract or logical step that is not present",
                external_ref=external_ref,
                logical_ref=logical_ref,
            ))
            continue
        provenance = _mapping_provenance(mapping)
        if not provenance:
            diagnostics.append(_finding(
                HANDOFF_DIAGNOSTIC_IDS["provenance"],
                "A trusted handoff mapping requires explicit provenance",
                external_ref=external_ref,
                logical_ref=logical_ref,
            ))
            continue
        contract = _candidate_contract(
            external,
            logical,
            mapping=mapping,
            provenance=provenance,
        )
        validation = validate_handoff_contract(contract)
        if validation["ok"]:
            candidates.append(contract)
            linked_external.add(external_ref)
            linked_steps.add(logical_ref)
        else:
            diagnostics.extend(validation["errors"])

    for external_ref, contract in sorted(by_external.items()):
        if external_ref in linked_external:
            continue
        diagnostics.append(_finding(
            HANDOFF_DIAGNOSTIC_IDS["untrusted_effect"],
            "External command effect is unknown; no source-backed handoff mapping was supplied",
            external_ref=external_ref,
            command_ref=external_ref,
        ))
    for logical_ref in sorted(by_step):
        if logical_ref in linked_steps or not _is_logical_labware_step(by_step[logical_ref]):
            continue
        diagnostics.append(_finding(
            HANDOFF_DIAGNOSTIC_IDS["unlinked_mutation"],
            "Logical labware mutation is not linked to a source-backed external handoff",
            logical_ref=logical_ref,
        ))

    candidates.sort(key=lambda item: (str(item.get("handoff_id") or ""), str(item.get("fingerprint") or "")))
    diagnostics.sort(key=lambda item: (str(item.get("id") or ""), str(item.get("external_ref") or item.get("logical_ref") or "")))
    return {
        "schema_version": EXTERNAL_LABWARE_HANDOFF_SCHEMA,
        "status": "ready" if candidates and not diagnostics else ("needs_review" if candidates or diagnostics else "unknown"),
        "candidates": candidates,
        "diagnostics": diagnostics,
        "unknown_external_effects": [
            ref for ref in sorted(by_external) if ref not in linked_external
        ],
    }


def build_external_labware_handoff_report(
    external_contracts: Sequence[Mapping[str, Any]] = (),
    logical_steps: Sequence[Mapping[str, Any]] = (),
    **kwargs: Any,
) -> dict[str, Any]:
    """Descriptive report alias used by generation and diagnostics callers."""
    return derive_handoff_candidates(external_contracts, logical_steps, **kwargs)


def _candidate_contract(
    external: Mapping[str, Any],
    logical: Mapping[str, Any],
    *,
    mapping: Mapping[str, Any],
    provenance: Sequence[HandoffProvenance],
) -> dict[str, Any]:
    external_ref = _first_ref(external, "contract_id", "invocation_ref", "id") or "external"
    logical_ref = _first_ref(logical, "step_id", "id", "name") or "logical"
    handoff_id = str(mapping.get("handoff_id") or f"handoff:{external_ref}->{logical_ref}")
    direction = str(mapping.get("direction") or "unknown")
    mutation = str(mapping.get("mutation") or mapping.get("logical_mutation") or _mutation_for_step(logical))
    position = mapping.get("external_position")
    if position is None:
        position = external.get("external_position") or external.get("position")
    external_identity = {
        "integration_ref": mapping.get("integration_ref") or external.get("integration_ref"),
        "device_ref": mapping.get("device_ref") or external.get("device_ref") or external.get("module_name"),
        "invocation_ref": external_ref,
        "position": _opaque_position(position),
        "identity": _mapping(mapping.get("external_identity") or external.get("external_identity")),
    }
    logical_identity = {
        "step_ref": logical_ref,
        "labware_ref": mapping.get("labware_ref") or logical.get("labware_ref") or logical.get("label"),
        "label": logical.get("label") or logical.get("labware_label"),
        "catalog": logical.get("catalog") or logical.get("labware_type"),
        "guid": logical.get("guid") or logical.get("component_guid"),
        "location": _mapping(logical.get("location") or logical.get("destination")),
        "identity_expression": mapping.get("identity_expression") or logical.get("barcode_expression"),
    }
    sync_mode = str(mapping.get("sync_mode") or mapping.get("synchronization_mode") or "unknown")
    confirmation_ref = mapping.get("confirmation_ref") or mapping.get("completion_ref")
    transitions = mapping.get("expected_transitions") or _default_transitions(sync_mode, mutation)
    return {
        "schema_version": EXTERNAL_LABWARE_HANDOFF_SCHEMA,
        "handoff_id": handoff_id,
        "direction": direction,
        "external": external_identity,
        "logical": logical_identity,
        "synchronization": {
            "mode": sync_mode,
            "confirmation_ref": confirmation_ref,
            "success_evidence": _mapping(mapping.get("success_evidence")),
        },
        "logical_mutation": {
            "kind": mutation,
            "step_ref": logical_ref,
            "guard_ref": mapping.get("guard_ref"),
        },
        "expected_transitions": [dict(item) for item in transitions if isinstance(item, Mapping)],
        "provenance": [item.as_dict() for item in provenance],
        "unknown_fields": _mapping(mapping.get("unknown_fields")),
    }


def _validate_mapping(value: Mapping[str, Any], *, check_fingerprint: bool) -> list[str]:
    errors: list[str] = []
    if value.get("schema_version") not in {None, EXTERNAL_LABWARE_HANDOFF_SCHEMA}:
        errors.append(f"schema_version must be {EXTERNAL_LABWARE_HANDOFF_SCHEMA!r}")
    if not str(value.get("handoff_id") or "").strip():
        errors.append("handoff_id is required")
    if value.get("direction") not in HANDOFF_DIRECTIONS:
        errors.append(f"direction must be one of {HANDOFF_DIRECTIONS!r}")
    external = value.get("external")
    if not isinstance(external, Mapping):
        errors.append("external identity is required")
    else:
        if not any(str(external.get(key) or "").strip() for key in ("device_ref", "integration_ref", "invocation_ref")):
            errors.append("external identity requires device_ref, integration_ref, or invocation_ref")
        position = external.get("position")
        if position is not None and not _is_opaque_position(position):
            errors.append("external position must remain an opaque source-backed mapping")
    logical = value.get("logical")
    if not isinstance(logical, Mapping):
        errors.append("logical identity is required")
    elif not any(logical.get(key) for key in ("labware_ref", "label", "guid", "step_ref")):
        errors.append("logical identity requires labware_ref, label, guid, or step_ref")
    synchronization = value.get("synchronization")
    if not isinstance(synchronization, Mapping):
        errors.append("synchronization is required")
    elif synchronization.get("mode") not in HANDOFF_SYNC_MODES:
        errors.append(f"synchronization.mode must be one of {HANDOFF_SYNC_MODES!r}")
    mutation = value.get("logical_mutation")
    if not isinstance(mutation, Mapping):
        errors.append("logical_mutation is required")
    elif mutation.get("kind") not in LOGICAL_MUTATIONS:
        errors.append(f"logical_mutation.kind must be one of {LOGICAL_MUTATIONS!r}")
    transitions = value.get("expected_transitions")
    if not isinstance(transitions, list):
        errors.append("expected_transitions must be a list")
    else:
        for index, transition in enumerate(transitions):
            if not isinstance(transition, Mapping):
                errors.append(f"expected_transitions[{index}] must be an object")
                continue
            for key in ("from", "to"):
                if transition.get(key) not in HANDOFF_STATES:
                    errors.append(f"expected_transitions[{index}].{key} must be a known handoff state")
    provenance = value.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        errors.append("at least one explicit provenance record is required")
    else:
        for index, item in enumerate(provenance):
            if not isinstance(item, Mapping):
                errors.append(f"provenance[{index}] must be an object")
                continue
            if item.get("kind") not in PROVENANCE_KINDS:
                errors.append(f"provenance[{index}].kind is unsupported")
            if not str(item.get("source_ref") or item.get("source") or "").strip():
                errors.append(f"provenance[{index}].source_ref is required")
            if not str(item.get("reason") or "").strip():
                errors.append(f"provenance[{index}].reason is required")
            if item.get("confidence") not in CONFIDENCE_LEVELS:
                errors.append(f"provenance[{index}].confidence is unsupported")
    if check_fingerprint and value.get("fingerprint") and value.get("fingerprint") != fingerprint_handoff(value):
        errors.append("fingerprint does not match contract semantics")
    return errors


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _json_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, HandoffProvenance):
        return value.as_dict()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _is_opaque_position(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and str(value.get("kind") or "") == "opaque"
        and value.get("value") not in (None, "")
        and str(value.get("source_ref") or "").strip() != ""
    )


def _opaque_position(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        result = dict(value)
        result.setdefault("kind", "opaque")
        return result
    return {"kind": "opaque", "value": value, "source_ref": "external-contract"}


def _index_references(values: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for value in values:
        for key in ("contract_id", "invocation_ref", "step_id", "id", "name"):
            ref = str(value.get(key) or "").strip()
            if ref:
                result.setdefault(ref, dict(value))
                break
    return result


def _first_ref(value: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        result = str(value.get(key) or "").strip()
        if result:
            return result
    return ""


def _mapping_entries(values: Sequence[Mapping[str, Any]], default_kind: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, Mapping):
            continue
        nested = value.get("handoffs") or value.get("mappings") or value.get("external_labware_handoffs")
        if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes)):
            for item in nested:
                if isinstance(item, Mapping):
                    entries.append({**dict(item), "_default_provenance_kind": default_kind})
        else:
            entries.append({**dict(value), "_default_provenance_kind": default_kind})
    return entries


def _mapping_provenance(mapping: Mapping[str, Any]) -> list[HandoffProvenance]:
    raw = mapping.get("provenance") or mapping.get("evidence") or ()
    if isinstance(raw, Mapping):
        raw = [raw]
    result: list[HandoffProvenance] = []
    for item in raw:
        if isinstance(item, HandoffProvenance):
            result.append(item)
        elif isinstance(item, Mapping):
            try:
                result.append(HandoffProvenance.from_mapping(item))
            except ValueError:
                continue
    if result:
        return result
    source_ref = str(mapping.get("source_ref") or "").strip()
    reason = str(mapping.get("reason") or "").strip()
    if source_ref and reason:
        kind = str(mapping.get("source_kind") or mapping.get("_default_provenance_kind") or "explicit_user_intent")
        try:
            return [
                HandoffProvenance(
                    kind=kind,
                    source_ref=source_ref,
                    reason=reason,
                    confidence=str(mapping.get("confidence") or "high"),
                )
            ]
        except ValueError:
            return []
    return []


def _mutation_for_step(step: Mapping[str, Any]) -> str:
    operation = str(step.get("operation") or step.get("type") or "").casefold().replace("-", "_")
    if operation in {"add_labware", "addlabware"}:
        return "add"
    if operation in {"remove_labware", "removelabware"}:
        return "remove"
    if operation in {"move_labware", "set_location", "setlocation", "rga_transfer_labware"}:
        return "move"
    if operation in {"set_attribute", "update_labware_attribute"}:
        return "attribute_update"
    return "unknown"


def _is_logical_labware_step(step: Mapping[str, Any]) -> bool:
    return _mutation_for_step(step) != "unknown"


def _default_transitions(sync_mode: str, mutation: str) -> list[dict[str, Any]]:
    transitions = [
        {"from": "planned", "to": "external_action_started", "evidence": "source_mapping"},
    ]
    if sync_mode == "synchronous":
        transitions.append({"from": "external_action_started", "to": "external_action_succeeded", "evidence": "source_mapping"})
    elif sync_mode == "asynchronous_with_confirmation":
        transitions.extend([
            {"from": "external_action_started", "to": "physical_presence_unconfirmed", "evidence": "source_mapping"},
            {"from": "physical_presence_unconfirmed", "to": "external_action_succeeded", "evidence": "confirmation"},
        ])
    else:
        transitions.append({"from": "external_action_started", "to": "state_ambiguous", "evidence": "review_required"})
    transitions.append({"from": "external_action_succeeded", "to": "logical_state_updated", "evidence": mutation})
    transitions.append({"from": "logical_state_updated", "to": "reconciled", "evidence": "source_mapping"})
    return transitions


def _finding(identifier: str, message: str, **details: Any) -> dict[str, Any]:
    return {"id": identifier, "message": message, **_json_value(details)}


def _diagnostic_id(message: str) -> str:
    if "schema_version" in message:
        return HANDOFF_DIAGNOSTIC_IDS["schema"]
    if "direction" in message:
        return HANDOFF_DIAGNOSTIC_IDS["direction"]
    if "synchronization" in message:
        return HANDOFF_DIAGNOSTIC_IDS["sync_mode"]
    if "state" in message:
        return HANDOFF_DIAGNOSTIC_IDS["state"]
    if "logical_mutation" in message:
        return HANDOFF_DIAGNOSTIC_IDS["mutation"]
    if "provenance" in message:
        return HANDOFF_DIAGNOSTIC_IDS["provenance"]
    if "external" in message:
        return HANDOFF_DIAGNOSTIC_IDS["external_identity"]
    if "logical" in message:
        return HANDOFF_DIAGNOSTIC_IDS["logical_identity"]
    if "fingerprint" in message:
        return HANDOFF_DIAGNOSTIC_IDS["fingerprint"]
    return HANDOFF_DIAGNOSTIC_IDS["required"]

