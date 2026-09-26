"""Source-backed external labware handoff contract.

Device lifecycle stays with the external-device health owner. Invocation and
data exchange stay with the external-process owner. Expression semantics and
async subroutine joins are not computed here. Adjacent commands are not
evidence.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

EXTERNAL_LABWARE_HANDOFF_SCHEMA_VERSION = "tecan.external_labware_handoff.v1"

PROVENANCE_SOURCES = frozenset({
    "command_role",
    "imported_pattern",
    "wrapper_metadata",
    "user_intent",
})

DIRECTIONS = frozenset({"to_external", "from_external", "unknown"})
SYNC_MODES = frozenset({"synchronous", "after_named_join", "unknown"})


def build_external_labware_handoff(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one handoff record. Unknown effects stay unknown."""
    provenance = raw.get("provenance") if isinstance(raw.get("provenance"), Mapping) else {}
    source = str(provenance.get("source") or "")
    errors: list[str] = []
    if source == "adjacency":
        errors.append("adjacency is not handoff evidence")
    elif source not in PROVENANCE_SOURCES:
        errors.append("provenance source is required")
    direction = str(raw.get("direction") or "unknown")
    if direction not in DIRECTIONS:
        errors.append("direction is not a known handoff direction")
    sync_mode = str(raw.get("sync_mode") or "unknown")
    if sync_mode not in SYNC_MODES:
        errors.append("sync mode is not a known handoff sync mode")
    logical = raw.get("logical_labware") if isinstance(raw.get("logical_labware"), Mapping) else {}
    external_position = raw.get("external_position")
    if external_position == logical.get("location") and external_position not in (None, ""):
        errors.append("external position and logical location must stay distinct")
    effect = str(raw.get("effect") or "unknown")
    transitions = raw.get("expected_state_transitions")
    if effect == "unknown" and transitions:
        errors.append("unknown command effect cannot carry expected state transitions")
    if effect not in {"unknown", "stated"}:
        errors.append("effect must stay unknown unless source evidence states it")
    record = {
        "schema_version": EXTERNAL_LABWARE_HANDOFF_SCHEMA_VERSION,
        "device_ref": raw.get("device_ref"),
        "invocation_ref": raw.get("invocation_ref"),
        "direction": direction if direction in DIRECTIONS else "unknown",
        "external_position": external_position,
        "logical_labware": {
            "identity": logical.get("identity"),
            "location": logical.get("location"),
        },
        "sync_mode": sync_mode if sync_mode in SYNC_MODES else "unknown",
        "expected_state_transitions": list(transitions or []) if effect == "stated" else [],
        "provenance": {
            "source": source,
            "evidence": list(provenance.get("evidence") or []),
        },
        "unknown_fields": dict(raw.get("unknown_fields") or {}),
        "effect": effect if effect in {"unknown", "stated"} else "unknown",
        "owners": {
            "device_lifecycle": "external_device_health",
            "invocation": "external_process",
            "expressions": "expression_semantics",
            "async_subroutine": "subroutine_lifecycle",
        },
        "errors": errors,
    }
    record["status"] = "valid" if not errors else "invalid"
    json.dumps(record)
    return record


def derive_handoff_candidates(commands: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Accept only records that already carry allowed provenance.

    A following Add, Remove, or Move does not create evidence.
    """
    candidates: list[dict[str, Any]] = []
    for command in commands:
        provenance = command.get("provenance")
        source = provenance.get("source") if isinstance(provenance, Mapping) else None
        if source not in PROVENANCE_SOURCES:
            continue
        candidates.append(build_external_labware_handoff(command))
    return candidates
