"""Generation and validation decisions for external labware handoffs.

Both the external position and the logical labware identity must be evidenced.
A logical mutation after an external command is not accepted from adjacency.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .external_labware_handoff import PROVENANCE_SOURCES, build_external_labware_handoff
from .external_labware_handoff_sim import simulate_external_labware_handoffs

LOGICAL_MUTATIONS = frozenset({
    "AddLabware",
    "RemoveLabware",
    "SetLocation",
    "add_labware",
    "remove_labware",
    "set_location",
})
EXTERNAL_COMMANDS = frozenset({
    "execute_application",
    "legacy_driver_macro",
    "application_driver_macro",
})


def assess_generated_handoffs(commands: list[Mapping[str, Any]]) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    evidenced: list[Mapping[str, Any]] = []
    awaiting_guard = False
    for command in commands:
        operation = str(command.get("command") or command.get("operation") or "")
        provenance = command.get("provenance") if isinstance(command.get("provenance"), Mapping) else {}
        source = provenance.get("source")
        if operation in LOGICAL_MUTATIONS and awaiting_guard and source not in PROVENANCE_SOURCES:
            findings.append({
                "code": "unconditional_logical_mutation",
                "status": "review",
                "message": "Logical deck mutation follows an external command without handoff evidence.",
            })
            awaiting_guard = False
            continue
        if source in PROVENANCE_SOURCES:
            logical = command.get("logical_labware") if isinstance(command.get("logical_labware"), Mapping) else {}
            external_position = command.get("external_position")
            if not external_position or not logical.get("identity"):
                findings.append({
                    "code": "one_sided_boundary",
                    "status": "review",
                    "message": "Handoff evidence names only one side of the physical/logical boundary.",
                })
            else:
                evidenced.append(command)
            awaiting_guard = False
            continue
        if operation in EXTERNAL_COMMANDS:
            awaiting_guard = True
    simulation = simulate_external_labware_handoffs([
        {**dict(command), "injected_outcome": command.get("injected_outcome") or "unknown"}
        for command in evidenced
    ]) if evidenced else None
    if simulation and any(
        not handoff["logical_reconciliation"]["reconciled"]
        for handoff in simulation["handoffs"]
    ):
        findings.append({
            "code": "handoff_not_reconciled",
            "status": "review",
            "message": "External outcome does not prove that the logical labware state is reconciled.",
        })
    report = {
        "status": "review" if findings else ("accepted" if evidenced else "not_applicable"),
        "findings": findings,
        "simulation": simulation,
        "owners": {
            "device_lifecycle": "external_device_health",
            "invocation": "external_process",
            "async_subroutine": "subroutine_lifecycle",
        },
    }
    report["fingerprint"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return report


def handoff_contract_for(command: Mapping[str, Any]) -> dict[str, Any]:
    return build_external_labware_handoff(command)
