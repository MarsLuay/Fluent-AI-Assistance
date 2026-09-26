"""Orientation-aware MCA pickup addressing.

Row and column stay at the authored offsets when placement orientation is the
zero literal. Any other orientation, or a variable expression, is
``cannot_determine``. This does not apply a 180-degree forum transform.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from ..expressions.ast import NumberLiteral
from ..ir.schema import PickUpTipsStep

ADDRESS_FIELDS = (
    "row",
    "column",
    "row_offset",
    "column_offset",
    "partial_rows",
    "partial_columns",
    "partial_rows_offset",
    "partial_column_offset",
    "orientation_phi",
    "orientation_psi",
    "orientation_theta",
)


def resolve_mca_pickup_address(
    step: PickUpTipsStep | Mapping[str, Any],
    *,
    placement_orientation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve source-backed pickup fields without inventing rotation semantics.

    ``placement_orientation`` is the resolved worktable placement context.  A
    known zero rotation is safe because the existing address contract leaves
    authored row/column values unchanged.  Non-zero or unavailable placement
    rotation remains ``cannot_determine`` until a source-backed transform is
    available; the forum's 180-degree observation is not applied here.
    """
    values = {name: _read(step, name) for name in ADDRESS_FIELDS}
    values["tip_columns"] = _read(step, "tip_columns")
    values["tip_count"] = _read(step, "tip_count")
    literals = {name: _literal(values[name]) for name in ADDRESS_FIELDS}
    unknown = [name for name, literal in literals.items() if literal is None]
    command_orientation = ("orientation_phi", "orientation_psi", "orientation_theta")
    nonzero_command_orientation = [
        name for name in command_orientation
        if literals[name] not in (None, 0)
    ]
    worktable_orientation = _normalize_placement_orientation(placement_orientation)
    placement_rotation = _literal(worktable_orientation["rotation"])
    placement_unknown = placement_orientation is not None and placement_rotation is None
    placement_nonzero = placement_orientation is not None and placement_rotation not in (None, 0)
    if placement_nonzero:
        status = "cannot_determine"
        reason = "worktable placement rotation transform is not proven for nonzero angles"
    elif placement_unknown:
        status = "cannot_determine"
        reason = "worktable placement orientation is not available as a numeric rotation"
    elif nonzero_command_orientation:
        status = "cannot_determine"
        reason = "command-local orientation transform is not proven for nonzero angles"
    elif unknown:
        status = "cannot_determine"
        reason = "address expression is not a numeric literal"
    else:
        status = "resolved"
        reason = "zero placement orientation leaves authored row and column offsets unchanged"
    record = {
        "status": status,
        "reason": reason,
        "base": {"row": _render(values["row"]), "column": _render(values["column"])},
        "offsets": {
            "row": _render(values["row_offset"]),
            "column": _render(values["column_offset"]),
            "partial_rows": _render(values["partial_rows_offset"]),
            "partial_column": _render(values["partial_column_offset"]),
        },
        "selection": {
            "partial_rows": _render(values["partial_rows"]),
            "partial_columns": _render(values["partial_columns"]),
            "tip_columns": values["tip_columns"],
            "tip_count": values["tip_count"],
        },
        "grid": {
            "rows": _render(values["partial_rows"]),
            "columns": _render(values["partial_columns"]),
        },
        # Retained for compatibility: these are command-local orientation
        # fields, not the worktable placement rotation.
        "placement_orientation": {
            "phi": _render(values["orientation_phi"]),
            "psi": _render(values["orientation_psi"]),
            "theta": _render(values["orientation_theta"]),
        },
        "worktable_orientation": {
            "rotation": _render(worktable_orientation["rotation"]),
            "known": placement_orientation is not None and placement_rotation is not None,
        },
        "provenance": "mca_pickup_fields+worktable_placement" if placement_orientation is not None else "mca_pickup_fields",
    }
    record["fingerprint"] = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return record


def validate_mca_pickup(
    step: PickUpTipsStep | Mapping[str, Any],
    *,
    loop_bounds: Mapping[str, tuple[int, int]] | None = None,
    target_orientation: Mapping[str, Any] | None = None,
    placement_orientation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Static pickup validation. Physical alignment stays unverified."""
    address = resolve_mca_pickup_address(step, placement_orientation=placement_orientation)
    findings: list[dict[str, str]] = []
    if address["status"] != "resolved":
        orientation_reason = any(
            marker in address["reason"] for marker in ("orientation", "rotation", "placement")
        )
        code = "orientation_unproven" if orientation_reason else "unresolved_mapping"
        findings.append({
            "code": code,
            "status": "review",
            "message": address["reason"],
        })
        if loop_bounds and code == "unresolved_mapping":
            if _range_exceeds_grid(step, loop_bounds):
                findings.append({
                    "code": "out_of_bounds",
                    "status": "review",
                    "message": "A value inside the known loop range leaves the pickup grid.",
                })
        elif code == "unresolved_mapping" and not loop_bounds:
            findings.append({
                "code": "unprovable_range",
                "status": "review",
                "message": "Variable offset has no proven loop range.",
            })
    else:
        row = address["base"]["row"] + address["offsets"]["row"]
        column = address["base"]["column"] + address["offsets"]["column"]
        rows = address["grid"]["rows"]
        columns = address["grid"]["columns"]
        if any(not isinstance(value, (int, float)) for value in (row, column, rows, columns)):
            pass
        elif row < 0 or column < 0 or row >= rows or column >= columns:
            findings.append({
                "code": "out_of_bounds",
                "status": "review",
                "message": "Authored row or column offset is outside the pickup grid.",
            })
    if target_orientation is not None:
        if "rotation" in target_orientation:
            source = address["worktable_orientation"]
            keys = ("rotation",)
        else:
            source = address["placement_orientation"]
            keys = ("phi", "psi", "theta")
        for key in keys:
            target = target_orientation.get(key, 0)
            if source[key] != target:
                dropped = source[key] not in (0, "0") and target in (0, "0")
                findings.append({
                    "code": "semantic_loss" if dropped else "orientation_drift",
                    "status": "review",
                    "message": f"Placement orientation {key} changed from {source[key]!r} to {target!r}.",
                })
    return {
        "address": address,
        "findings": findings,
        "logical_selection": address["status"],
        "precise_occupancy_allowed": address["status"] == "resolved" and not any(
            item["code"] in {"out_of_bounds", "semantic_loss", "orientation_drift"}
            for item in findings
        ),
        "physical_readiness": {"status": "unverified", "owner": "physical_verification"},
    }


def _normalize_placement_orientation(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {"rotation": None}
    if "rotation" in value:
        return {"rotation": value["rotation"]}
    # Accept a normalized theta alias for callers that already expose Euler
    # orientation, but do not infer a transform from phi/psi/theta combinations.
    if "theta" in value and "phi" not in value and "psi" not in value:
        return {"rotation": value["theta"]}
    return {"rotation": None}


def _range_exceeds_grid(step: PickUpTipsStep | Mapping[str, Any], loop_bounds: Mapping[str, tuple[int, int]]) -> bool:
    rows = _literal(_read(step, "partial_rows"))
    columns = _literal(_read(step, "partial_columns"))
    if rows is None or columns is None:
        return False
    for name, bounds in loop_bounds.items():
        low, high = bounds
        for value in (low, high):
            if value < 0 or value >= rows or value >= columns:
                return True
            if str(_read(step, "row")) == name or str(_read(step, "column")) == name:
                if value < 0 or value >= (rows if str(_read(step, "row")) == name else columns):
                    return True
    return False


def _read(step: PickUpTipsStep | Mapping[str, Any], name: str) -> Any:
    if isinstance(step, Mapping):
        return step.get(name)
    return getattr(step, name)


def _literal(value: Any) -> int | float | None:
    if isinstance(value, NumberLiteral):
        return value.value
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


def _render(value: Any) -> Any:
    if isinstance(value, NumberLiteral):
        return value.value
    if isinstance(value, (int, float, str)) or value is None or isinstance(value, list):
        return value
    return str(value)
