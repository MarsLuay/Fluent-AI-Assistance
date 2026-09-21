"""Typed script-side labware and custom-attribute references.

The FluentControl expression language exposes attribute access through
``GetAttribute`` and ``SetAttribute``.  Keeping the reference separate from
the call AST gives the simulator and later consumers (including liquid-class
microscript support) one shared, provenance-carrying representation without
claiming a vendor-specific well syntax that has not been sourced.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from .ast import (
    BinaryExpression,
    Expression,
    FunctionCall,
    IndexExpression,
    ReviewedRawExpression,
    SourcePreservedExpression,
    StringLiteral,
    UnaryExpression,
)

ExpressionType = Literal["unknown", "string", "number", "boolean", "any"]


AttributeOperation = Literal["get", "set"]


@dataclass(frozen=True)
class AttributeReference:
    """Normalized reference to a runtime labware or well attribute.

    ``well`` remains optional because the currently supported source-backed
    ``GetAttribute``/``SetAttribute`` forms identify only a labware target.
    Callers that have a verified well target can populate it explicitly; the
    scope then becomes ``well`` and the identity includes that target.
    """

    operation: AttributeOperation
    labware: Expression
    attribute: Expression
    value: Expression | None = None
    well: Expression | None = None
    value_type: ExpressionType = "unknown"
    source_step: str | None = None
    provenance: str | None = None

    @property
    def scope(self) -> Literal["labware", "well"]:
        return "well" if self.well is not None else "labware"

    def identity_key(self) -> tuple[str, str, str]:
        return (
            _render_target(self.labware),
            _render_target(self.well) if self.well is not None else "",
            _render_target(self.attribute),
        )

    def to_dict(self) -> dict[str, Any]:
        rendered_value = render_expression(self.value) if self.value is not None else None
        return {
            "operation": self.operation,
            "scope": self.scope,
            "labware": _render_target(self.labware),
            "well": _render_target(self.well) if self.well is not None else None,
            "attribute": _render_target(self.attribute),
            "value": rendered_value,
            "value_type": self.value_type,
            "source_step": self.source_step,
            "provenance": self.provenance,
        }


def attribute_reference_from_call(
    expression: Expression,
    *,
    source_step: str | None = None,
    provenance: str | None = None,
    value_type: ExpressionType = "unknown",
) -> AttributeReference | None:
    """Normalize one source-backed attribute call, if present."""

    if not isinstance(expression, FunctionCall):
        return None
    name = expression.name.casefold()
    if name == "getattribute" and len(expression.arguments) == 2:
        labware, attribute = expression.arguments
        return AttributeReference(
            operation="get",
            labware=labware,
            attribute=attribute,
            value_type="string",
            source_step=source_step,
            provenance=provenance,
        )
    if name == "setattribute" and len(expression.arguments) == 3:
        labware, attribute, value = expression.arguments
        return AttributeReference(
            operation="set",
            labware=labware,
            attribute=attribute,
            value=value,
            value_type=value_type,
            source_step=source_step,
            provenance=provenance,
        )
    return None


def attribute_references_in_expression(
    expression: Expression,
    *,
    source_step: str | None = None,
    provenance: str | None = None,
    value_type: ExpressionType = "unknown",
) -> tuple[AttributeReference, ...]:
    """Collect nested ``GetAttribute``/``SetAttribute`` references."""

    references: list[AttributeReference] = []

    def visit(node: Expression) -> None:
        reference = attribute_reference_from_call(
            node,
            source_step=source_step,
            provenance=provenance,
            value_type=value_type,
        )
        if reference is not None:
            references.append(reference)
        if isinstance(node, FunctionCall):
            for argument in node.arguments:
                visit(argument)
        elif isinstance(node, IndexExpression):
            visit(node.base)
            visit(node.index)
        elif isinstance(node, UnaryExpression):
            visit(node.operand)
        elif isinstance(node, BinaryExpression):
            visit(node.left)
            visit(node.right)
        elif isinstance(node, (SourcePreservedExpression, ReviewedRawExpression)):
            return

    visit(expression)
    return tuple(references)


def attribute_conflict_diagnostics(
    references: Iterable[AttributeReference],
) -> tuple[dict[str, Any], ...]:
    """Report writes that make a later consumer's value ambiguous.

    The current IR does not model branch reachability for custom attributes.
    Repeated writes to one normalized target are therefore surfaced as a
    warning for review.  The warning is intentionally not an error: ordered
    overwrites can be valid, while an unmodeled liquid-class consumer must not
    silently pick one write.
    """

    writes: dict[tuple[str, str, str], list[AttributeReference]] = defaultdict(list)
    for reference in references:
        if reference.operation == "set":
            writes[reference.identity_key()].append(reference)

    diagnostics: list[dict[str, Any]] = []
    for key, entries in writes.items():
        if len(entries) < 2:
            continue
        diagnostics.append(
            {
                "code": "ambiguous_attribute_write",
                "severity": "warning",
                "message": (
                    "Multiple SetAttribute writes may reach the same custom-attribute "
                    f"consumer: {key[0]}"
                    + (f"[{key[1]}]" if key[1] else "")
                    + f".{key[2]}."
                ),
                "target": {
                    "labware": key[0],
                    "well": key[1] or None,
                    "attribute": key[2],
                },
                "writes": [entry.to_dict() for entry in entries],
            }
        )
    return tuple(diagnostics)


def render_expression(expression: Expression | None) -> str:
    """Import the renderer lazily to keep the AST/renderer dependency acyclic."""

    if expression is None:
        return ""
    from .renderer import render_expression as _render_expression

    return _render_expression(expression)


def _render_target(expression: Expression) -> str:
    if isinstance(expression, StringLiteral):
        return expression.value
    return render_expression(expression)
