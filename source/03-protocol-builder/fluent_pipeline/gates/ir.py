"""Protocol-IR readiness evaluators."""

from __future__ import annotations

from typing import Any

from ..labware_contracts import (
    ir_label_catalog_issues,
    preferred_label_catalogs_from_manifest,
)
from ..protocol_ir import CANONICAL_IR_VERSION, validate_protocol_ir
from .models import GateRecord, ValidationContext


def evaluate_protocol_ir_schema(context: ValidationContext) -> GateRecord:
    """Validate canonical IR and ZEIA-derived labware label/catalog contracts."""
    ir = context.protocol_ir
    if ir is None:
        return context.make_gate(
            "protocol_ir_schema",
            "failed",
            context.protocol_ir_error or "Protocol IR could not be loaded.",
        )
    issues = validate_protocol_ir(ir)
    if issues:
        return context.make_gate(
            "protocol_ir_schema",
            "failed",
            "Protocol IR failed schema validation.",
            {"issues": [issue.as_dict() for issue in issues]},
        )
    preferred = preferred_label_catalogs_from_manifest(context.source_manifest)
    semantic_issues = ir_label_catalog_issues(ir, preferred)
    if semantic_issues:
        return context.make_gate(
            "protocol_ir_schema",
            "failed",
            "Protocol IR failed ZEIA labware catalog validation.",
            {"issues": semantic_issues},
        )
    return context.make_gate(
        "protocol_ir_schema",
        "passed",
        f"Protocol IR matches {CANONICAL_IR_VERSION}.",
    )


def a200_adapter_catalog_issues(
    ir: dict[str, Any],
    preferred_label_catalogs: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Compatibility wrapper around ZEIA preferred label/catalog checks."""
    return ir_label_catalog_issues(ir, preferred_label_catalogs)


# Backward-compatible alias for older imports/tests.
script2_a200_catalog_issues = a200_adapter_catalog_issues
