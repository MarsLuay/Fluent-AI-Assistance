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


def resolve_mca_pickup_address(step: PickUpTipsStep | Mapping[str, Any]) -> dict[str, Any]:
    values = {name: _read(step, name) for name in ADDRESS_FIELDS}
    values["tip_columns"] = _read(step, "tip_columns")
    values["tip_count"] = _read(step, "tip_count")
    literals = {name: _literal(values[name]) for name in ADDRESS_FIELDS}
    unknown = [name for name, literal in literals.items() if literal is None]
    orientation = ("orientation_phi", "orientation_psi", "orientation_theta")
    nonzero_orientation = [
        name for name in orientation
        if literals[name] not in (None, 0)
    ]
    if unknown or nonzero_orientation:
        status = "cannot_determine"
        reason = (
            "orientation transform is not proven for nonzero placement angles"
            if nonzero_orientation
            else "address expression is not a numeric literal"
        )
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
        "placement_orientation": {
            "phi": _render(values["orientation_phi"]),
            "psi": _render(values["orientation_psi"]),
            "theta": _render(values["orientation_theta"]),
        },
        "provenance": "mca_pickup_fields",
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
) -> dict[str, Any]:
    """Static pickup validation. Physical alignment stays unverified."""
    address = resolve_mca_pickup_address(step)
    findings: list[dict[str, str]] = []
    if address["status"] != "resolved":
        code = "orientation_unproven" if "orientation" in address["reason"] else "unresolved_mapping"
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
        source = address["placement_orientation"]
        for key in ("phi", "psi", "theta"):
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
            item["code"] == "out_of_bounds" for item in findings
        ),
        "physical_readiness": {"status": "unverified", "owner": "physical_verification"},
    }


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
