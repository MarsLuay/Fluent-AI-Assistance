"""Worktable-diff readiness evaluators."""

from __future__ import annotations

from typing import Any, Mapping

from ..readiness_gates import readiness_gate, readiness_gate_approval_context_keys
from .models import GateRecord, ValidationContext


DECK_LAYOUT_GATE = readiness_gate("deck_layout_consistent")
DECK_LAYOUT_APPROVAL_KEYS = readiness_gate_approval_context_keys("deck_layout_consistent")


def evaluate_tip_boxes(context: ValidationContext) -> GateRecord:
    return _evaluate_worktable_resource(
        context,
        "tip_boxes_resolve",
        "tip boxes",
        "tip_boxes",
        (context.worktable_diff or {}).get("required_tip_boxes")
        if context.worktable_diff is not None
        else None,
    )


def evaluate_carriers(context: ValidationContext) -> GateRecord:
    return _evaluate_worktable_resource(
        context,
        "carriers_resolve",
        "carriers",
        "carriers",
        (context.worktable_diff or {}).get("required_carriers")
        if context.worktable_diff is not None
        else None,
    )


def evaluate_device_aliases(context: ValidationContext) -> GateRecord:
    return _evaluate_worktable_resource(
        context,
        "device_aliases_resolve",
        "device aliases",
        "device_aliases",
        (context.worktable_diff or {}).get("device_aliases")
        if context.worktable_diff is not None
        else None,
    )


def evaluate_deck_layout(context: ValidationContext) -> GateRecord:
    diff = context.worktable_diff
    if diff is None:
        return context.make_gate(
            DECK_LAYOUT_GATE.id,
            "failed",
            "Deck layout could not be checked without valid protocol IR.",
        )
    changes = diff.get("changed_deck_positions") or []
    metadata = _deck_layout_metadata()
    if not changes:
        return context.make_gate(
            DECK_LAYOUT_GATE.id,
            "passed",
            "No deck position changes relative to the source worktable.",
            {"trivial": True, **metadata},
        )
    approved = bool(any(context.validation_options.get(key) for key in DECK_LAYOUT_APPROVAL_KEYS))
    if approved:
        return context.make_gate(
            DECK_LAYOUT_GATE.id,
            "passed",
            (
                f"{len(changes)} deck position change(s) require manual relocation but were explicitly "
                f"approved via `{DECK_LAYOUT_GATE.cli_flag}`."
            ),
            {"approved_deck_changes": changes, **metadata},
        )
    return context.make_gate(
        DECK_LAYOUT_GATE.id,
        "failed",
        (
            "Protocol IR places labware at deck positions that differ from the source worktable; "
            f"this requires manual relocation. Approve via `{DECK_LAYOUT_GATE.cli_flag}` "
            f"or record approval at `{DECK_LAYOUT_GATE.request_spec_path}` once confirmed."
        ),
        {
            "changed_deck_positions": changes,
            "approval_keys": list(DECK_LAYOUT_APPROVAL_KEYS),
            **metadata,
        },
    )


def _evaluate_worktable_resource(
    context: ValidationContext,
    gate_id: str,
    label_plural: str,
    detail_key: str,
    items: list[dict[str, Any]] | None,
) -> GateRecord:
    """Match the worktable patch severity model for comparable resources."""
    if context.worktable_diff is None:
        return context.make_gate(
            gate_id,
            "failed",
            f"{label_plural.capitalize()} could not be checked without valid protocol IR.",
        )
    items = items or []
    missing = [item for item in items if _norm_status(item) == "missing"]
    if missing:
        return context.make_gate(
            gate_id,
            "failed",
            f"Some required {label_plural} are missing from the source context.",
            {detail_key: missing},
        )
    unverified = [item for item in items if _norm_status(item) == "unverified"]
    if unverified:
        return context.make_gate(
            gate_id,
            "passed",
            f"Required {label_plural} could not be verified against the source context; confirm before import.",
            {detail_key: unverified, "needs_review": True},
        )
    if not items:
        return context.make_gate(
            gate_id,
            "passed",
            f"No {label_plural} are required by the protocol IR.",
            {"trivial": True},
        )
    return context.make_gate(
        gate_id,
        "passed",
        f"All {len(items)} required {label_plural} resolve in the source context.",
    )


def _deck_layout_metadata() -> dict[str, Any]:
    return {
        "approval_key": DECK_LAYOUT_GATE.approval_key,
        "cli_flag": DECK_LAYOUT_GATE.cli_flag,
        "mcp_capability": DECK_LAYOUT_GATE.mcp_capability,
        "request_spec_path": DECK_LAYOUT_GATE.request_spec_path,
        "remediation": DECK_LAYOUT_GATE.remediation,
        "artifact_inputs": list(DECK_LAYOUT_GATE.artifact_inputs),
    }


def _norm_status(item: Mapping[str, Any]) -> str:
    return str(item.get("status") or "").strip().casefold()
