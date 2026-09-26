"""Offline injection of external labware handoff outcomes.

Nothing here starts a program, opens a serial port, or talks to a device.
Physical readiness stays with the hardware-verification owner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .external_labware_handoff import build_external_labware_handoff
from .external_labware_reconcile import reconcile_external_labware_outcome

EXTERNAL_LABWARE_HANDOFF_REPORT_SCHEMA_VERSION = "tecan.external_labware_handoffs.v1"

INJECTED_OUTCOMES = frozenset({
    "success",
    "failure",
    "pending",
    "confirmed",
    "timeout",
    "unknown",
    "store",
})


def simulate_external_labware_handoffs(
    cases: list[Mapping[str, Any]],
) -> dict[str, Any]:
    """Apply declared logical transitions for injected outcomes only."""
    handoffs: list[dict[str, Any]] = []
    lineage: dict[str, list[str | None]] = {}
    for case in cases:
        injected = str(case.get("injected_outcome") or "unknown")
        if injected not in INJECTED_OUTCOMES:
            injected = "unknown"
        handoff = build_external_labware_handoff(_handoff_raw(case, injected))
        outcome, completion = _injected_outcome(injected, case)
        logical = case.get("logical_update")
        reconciliation = reconcile_external_labware_outcome(
            handoff,
            outcome,
            logical if isinstance(logical, Mapping) else None,
            completion_boundary_verified=completion,
        )
        position = handoff.get("external_position")
        identity = (handoff.get("logical_labware") or {}).get("identity")
        if isinstance(position, str) and position:
            lineage.setdefault(position, []).append(identity)
        handoffs.append({
            "injected_outcome": injected,
            "external_position": position,
            "logical_identity": identity,
            "logical_reconciliation": reconciliation,
            "provenance": handoff.get("provenance"),
            "diagnostics": reconciliation["findings"],
            "physical_uncertainty": True,
        })
    return {
        "schema_version": EXTERNAL_LABWARE_HANDOFF_REPORT_SCHEMA_VERSION,
        "assumptions": [
            "outcomes are injected",
            "no external executable, serial port, or device is used",
            "storage positions are opaque identifiers, not carousel geometry",
        ],
        "physical_readiness": {"status": "unverified", "owner": "physical_verification"},
        "handoffs": handoffs,
        "position_lineage": lineage,
    }


def write_external_labware_handoff_report(report: Mapping[str, Any], path: Path) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _handoff_raw(case: Mapping[str, Any], injected: str) -> dict[str, Any]:
    sync_mode = "after_named_join" if injected in {"pending", "confirmed"} else str(case.get("sync_mode") or "synchronous")
    effect = "unknown" if injected in {"timeout", "unknown"} else "stated"
    raw = {
        "direction": "to_external" if injected == "store" else str(case.get("direction") or "from_external"),
        "external_position": case.get("external_position"),
        "logical_labware": case.get("logical_labware") or {},
        "sync_mode": sync_mode,
        "effect": effect,
        "expected_state_transitions": [] if effect == "unknown" else [{"from": "declared", "to": injected}],
        "provenance": case.get("provenance") or {"source": "user_intent", "evidence": ["injected"]},
    }
    return raw


def _injected_outcome(injected: str, case: Mapping[str, Any]) -> tuple[dict[str, Any], bool | None]:
    barcode = case.get("barcode")
    if injected == "failure":
        return {"meaning": "failure", "barcode": barcode}, True
    if injected in {"timeout", "unknown"}:
        return {"meaning": "unknown", "barcode": barcode}, None
    if injected == "pending":
        return {"meaning": "success", "barcode": barcode}, False
    if injected == "confirmed":
        return {"meaning": "success", "barcode": barcode}, True
    return {"meaning": "success", "barcode": barcode}, True
