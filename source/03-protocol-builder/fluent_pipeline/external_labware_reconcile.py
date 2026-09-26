"""Bind a source-backed external outcome to a logical labware update.

Generic return codes are not outcome meanings. Async completion is a boolean
already decided by the subroutine lifecycle owner. Device health is not
recomputed here.
"""

from __future__ import annotations

from typing import Any, Mapping

LOGICAL_OPERATIONS = frozenset({"AddLabware", "RemoveLabware", "SetLocation"})
OUTCOME_MEANINGS = frozenset({"success", "failure", "unknown"})


def reconcile_external_labware_outcome(
    handoff: Mapping[str, Any],
    outcome: Mapping[str, Any],
    logical_update: Mapping[str, Any] | None = None,
    *,
    completion_boundary_verified: bool | None = None,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    meaning = outcome.get("meaning")
    if "return_code" in outcome and meaning not in OUTCOME_MEANINGS:
        findings.append(_finding(
            "outcome_not_source_backed",
            "physical_uncertainty",
            "A return code is not an external outcome meaning.",
        ))
        meaning = "unknown"
    elif meaning not in OUTCOME_MEANINGS:
        findings.append(_finding(
            "outcome_not_source_backed",
            "physical_uncertainty",
            "External outcome meaning is missing.",
        ))
        meaning = "unknown"

    if handoff.get("status") != "valid" or handoff.get("effect") == "unknown":
        findings.append(_finding(
            "unknown_handoff_effect",
            "physical_uncertainty",
            "Unknown handoff effect cannot change the logical deck.",
        ))

    if handoff.get("sync_mode") == "after_named_join" and completion_boundary_verified is not True:
        findings.append(_finding(
            "async_update_before_confirmation",
            "physical_uncertainty",
            "Dependent logical update requires the matching subroutine join.",
        ))

    logical = dict(logical_update or {})
    operation = str(logical.get("operation") or "")
    if meaning == "success" and operation not in LOGICAL_OPERATIONS:
        findings.append(_finding(
            "missing_logical_update",
            "logical_inconsistency",
            "A stated success has no logical Add, Remove, or SetLocation update.",
        ))

    expected_barcode = (outcome.get("barcode") or {}).get("value")
    actual_barcode = logical.get("barcode")
    if expected_barcode and actual_barcode and expected_barcode != actual_barcode:
        findings.append(_finding(
            "barcode_mismatch",
            "logical_inconsistency",
            "Logical barcode does not match the external outcome lineage.",
        ))

    expected_identity = (handoff.get("logical_labware") or {}).get("identity")
    if expected_identity and logical.get("identity") and logical.get("identity") != expected_identity:
        findings.append(_finding(
            "identity_mismatch",
            "logical_inconsistency",
            "Logical labware identity does not match the handoff.",
        ))

    if logical.get("occupancy") == "stale":
        findings.append(_finding(
            "stale_occupancy",
            "logical_inconsistency",
            "Logical occupancy is marked stale.",
        ))

    if outcome.get("retry") == "ambiguous":
        findings.append(_finding(
            "ambiguous_retry",
            "logical_inconsistency",
            "Retry outcome does not identify which attempt is current.",
        ))

    if meaning == "failure":
        findings.append(_finding(
            "device_failure",
            "device_failure",
            "Source-backed external failure does not reconcile the logical deck.",
        ))

    blocking = [item for item in findings if item["category"] != "device_failure" or meaning == "failure"]
    reconciled = meaning == "success" and not findings
    return {
        "reconciled": reconciled,
        "outcome_meaning": meaning,
        "logical_operation": operation or None,
        "lineage": {
            "barcode": outcome.get("barcode"),
            "external_position": handoff.get("external_position"),
            "logical_identity": expected_identity,
            "provenance": handoff.get("provenance"),
        },
        "findings": findings,
        "blocking_count": len(blocking) if not reconciled else 0,
    }


def _finding(code: str, category: str, message: str) -> dict[str, str]:
    return {"code": code, "category": category, "message": message}
