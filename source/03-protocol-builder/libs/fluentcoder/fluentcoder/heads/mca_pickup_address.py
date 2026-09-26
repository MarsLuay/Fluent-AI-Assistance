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
