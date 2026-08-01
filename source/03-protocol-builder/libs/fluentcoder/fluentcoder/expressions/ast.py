"""Closed AST for FluentControl expression fields."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, TypeAlias, Union


@dataclass(frozen=True, eq=False)
class StringLiteral:
    value: str = ""
    kind: Literal["string_literal"] = field(default="string_literal", init=False)

    def __str__(self) -> str:
        return self.value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, StringLiteral):
            return self.value == other.value
        if isinstance(other, str):
            return self.value == other
        return False


@dataclass(frozen=True, eq=False)
class NumberLiteral:
    value: int | float = 0
    kind: Literal["number_literal"] = field(default="number_literal", init=False)

    def __post_init__(self) -> None:
        if isinstance(self.value, bool):
            raise ValueError("number_literal value cannot be boolean")
        if not isinstance(self.value, (int, float)):
            raise ValueError("number_literal value must be int or float")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("number_literal value must be finite")

    def __str__(self) -> str:
        return str(self.value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, NumberLiteral):
            return self.value == other.value
        if isinstance(other, bool):
            return False
        if isinstance(other, (int, float)):
            return self.value == other
        return False


@dataclass(frozen=True, eq=False)
class BooleanLiteral:
    value: bool = False
    kind: Literal["boolean_literal"] = field(default="boolean_literal", init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.value, bool):
            raise ValueError("boolean_literal value must be boolean")

    def __str__(self) -> str:
        return "True" if self.value else "False"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, BooleanLiteral):
            return self.value == other.value
        if isinstance(other, bool):
            return self.value == other
        return False


@dataclass(frozen=True)
class VariableReference:
    name: str = ""
    kind: Literal["variable_reference"] = field(default="variable_reference", init=False)

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ValueError("variable_reference name is required")

    def __str__(self) -> str:
        return self.name

    def __eq__(self, other: object) -> bool:
        if isinstance(other, VariableReference):
            return self.name == other.name
        if isinstance(other, str):
            return self.name == other
        return False


@dataclass(frozen=True)
class FunctionCall:
    name: str = ""
    arguments: tuple[Expression, ...] = ()
    kind: Literal["function_call"] = field(default="function_call", init=False)

    def __post_init__(self) -> None:
        if not str(self.name or "").strip():
            raise ValueError("function_call name is required")
        if not isinstance(self.arguments, tuple):
            object.__setattr__(self, "arguments", tuple(self.arguments))
        for argument in self.arguments:
            _require_expression(argument, "function_call argument")

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FunctionCall):
            return self.name == other.name and self.arguments == other.arguments
        if isinstance(other, str):
            rendered_arguments = ", ".join(_legacy_expression_text(arg) for arg in self.arguments)
            return f"{self.name}({rendered_arguments})" == other
        return False


@dataclass(frozen=True)
class UnaryExpression:
    operator: Literal["+", "-"] = "+"
    operand: Expression | None = None
    kind: Literal["unary_expression"] = field(default="unary_expression", init=False)

    def __post_init__(self) -> None:
        if self.operator not in {"+", "-"}:
            raise ValueError(f"Unsupported unary operator: {self.operator!r}")
        _require_expression(self.operand, "unary_expression operand")


@dataclass(frozen=True)
class BinaryExpression:
    operator: Literal["+", "-", "*", "/", "^", "=", "<>", "<", ">", "<=", ">=", "AND", "OR"] = "+"
    left: Expression | None = None
    right: Expression | None = None
    kind: Literal["binary_expression"] = field(default="binary_expression", init=False)

    def __post_init__(self) -> None:
        if self.operator not in {"+", "-", "*", "/", "^", "=", "<>", "<", ">", "<=", ">=", "AND", "OR"}:
            raise ValueError(f"Unsupported binary operator: {self.operator!r}")
        _require_expression(self.left, "binary_expression left")
        _require_expression(self.right, "binary_expression right")


@dataclass(frozen=True)
class SourcePreservedExpression:
    source: str = ""
    source_hash: str = ""
    source_entry: str = ""
    provenance_id: str = ""
    byte_stable: bool = True
    reference_metadata_origin: str = ""
    referenced_variables: tuple[str, ...] = ()
    referenced_functions: tuple[str, ...] = ()
    kind: Literal["source_preserved_expression"] = field(default="source_preserved_expression", init=False)

    def __post_init__(self) -> None:
        if not str(self.source or "").strip():
            raise ValueError("source_preserved_expression source is required")
        if not str(self.source_hash or "").strip():
            raise ValueError("source_preserved_expression source_hash is required")
        object.__setattr__(self, "referenced_variables", _normalize_reference_names(self.referenced_variables))
        object.__setattr__(self, "referenced_functions", _normalize_reference_names(self.referenced_functions))

    def __str__(self) -> str:
        return self.source


@dataclass(frozen=True)
class ReviewedRawExpression:
    source: str = ""
    approval_id: str = ""
    reviewer: str = ""
    reference_metadata_origin: str = ""
    referenced_variables: tuple[str, ...] = ()
    referenced_functions: tuple[str, ...] = ()
    kind: Literal["reviewed_raw_expression"] = field(default="reviewed_raw_expression", init=False)

    def __post_init__(self) -> None:
        if not str(self.source or "").strip():
            raise ValueError("reviewed_raw_expression source is required")
        object.__setattr__(self, "referenced_variables", _normalize_reference_names(self.referenced_variables))
        object.__setattr__(self, "referenced_functions", _normalize_reference_names(self.referenced_functions))

    def __str__(self) -> str:
        return self.source


Expression: TypeAlias = Union[
    StringLiteral,
    NumberLiteral,
    BooleanLiteral,
    VariableReference,
    FunctionCall,
    UnaryExpression,
    BinaryExpression,
    SourcePreservedExpression,
    ReviewedRawExpression,
]


_EXPRESSION_TYPES = (
    StringLiteral,
    NumberLiteral,
    BooleanLiteral,
    VariableReference,
    FunctionCall,
    UnaryExpression,
    BinaryExpression,
    SourcePreservedExpression,
    ReviewedRawExpression,
)


def is_expression(value: object) -> bool:
    return isinstance(value, _EXPRESSION_TYPES)


def expression_kind(value: Expression) -> str:
    return str(getattr(value, "kind", type(value).__name__))


def expression_to_mapping(expression: Expression) -> dict[str, Any]:
    if isinstance(expression, FunctionCall):
        return {
            "kind": expression.kind,
            "name": expression.name,
            "arguments": [expression_to_mapping(arg) for arg in expression.arguments],
        }
    if isinstance(expression, UnaryExpression):
        return {
            "kind": expression.kind,
            "operator": expression.operator,
            "operand": expression_to_mapping(expression.operand),
        }
    if isinstance(expression, BinaryExpression):
        return {
            "kind": expression.kind,
            "operator": expression.operator,
            "left": expression_to_mapping(expression.left),
            "right": expression_to_mapping(expression.right),
        }
    if isinstance(expression, SourcePreservedExpression):
        payload = {
            "kind": expression.kind,
            "source": expression.source,
            "source_hash": expression.source_hash,
            "source_entry": expression.source_entry,
            "byte_stable": expression.byte_stable,
            "reference_metadata_origin": expression.reference_metadata_origin,
            "referenced_variables": list(expression.referenced_variables),
            "referenced_functions": list(expression.referenced_functions),
        }
        if expression.provenance_id:
            payload["provenance_id"] = expression.provenance_id
        return payload
    if isinstance(expression, ReviewedRawExpression):
        payload = {
            "kind": expression.kind,
            "source": expression.source,
            "approval_id": expression.approval_id,
            "reviewer": expression.reviewer,
            "reference_metadata_origin": expression.reference_metadata_origin,
            "referenced_variables": list(expression.referenced_variables),
            "referenced_functions": list(expression.referenced_functions),
        }
        return payload
    return asdict(expression)


def expression_from_mapping(value: dict[str, Any]) -> Expression:
    kind = str(value.get("kind") or "").strip()
    if kind == "string_literal":
        return StringLiteral(value=str(value.get("value") or ""))
    if kind == "number_literal":
        raw = value.get("value", 0)
        if isinstance(raw, bool):
            raise ValueError("number_literal value cannot be boolean")
        if isinstance(raw, int):
            return NumberLiteral(value=raw)
        if isinstance(raw, float):
            return NumberLiteral(value=raw)
        text = str(raw).strip()
        try:
            parsed = float(text)
        except ValueError as exc:
            raise ValueError(f"invalid number_literal value: {raw!r}") from exc
        return NumberLiteral(value=int(parsed) if parsed.is_integer() else parsed)
    if kind == "boolean_literal":
        raw = value.get("value", False)
        if isinstance(raw, bool):
            return BooleanLiteral(value=raw)
        text = str(raw).strip().casefold()
        if text == "true":
            return BooleanLiteral(value=True)
        if text == "false":
            return BooleanLiteral(value=False)
        raise ValueError(f"invalid boolean_literal value: {raw!r}")
    if kind == "variable_reference":
        return VariableReference(name=str(value.get("name") or ""))
    if kind == "function_call":
        raw_arguments = value.get("arguments") or []
        if not isinstance(raw_arguments, (list, tuple)):
            raise ValueError("function_call arguments must be a list")
        arguments = []
        for item in raw_arguments:
            if not isinstance(item, dict):
                raise ValueError("function_call arguments must be expression mappings")
            arguments.append(expression_from_mapping(item))
        return FunctionCall(
            name=str(value.get("name") or ""),
            arguments=tuple(arguments),
        )
    if kind == "unary_expression":
        operand = value.get("operand")
        if not isinstance(operand, dict):
            raise ValueError("unary_expression requires an operand expression mapping")
        return UnaryExpression(
            operator=str(value.get("operator") or "+"),  # type: ignore[arg-type]
            operand=expression_from_mapping(operand),
        )
    if kind == "binary_expression":
        left = value.get("left")
        right = value.get("right")
        if not isinstance(left, dict) or not isinstance(right, dict):
            raise ValueError("binary_expression requires left and right expression mappings")
        return BinaryExpression(
            operator=str(value.get("operator") or "+"),  # type: ignore[arg-type]
            left=expression_from_mapping(left),
            right=expression_from_mapping(right),
        )
    if kind == "source_preserved_expression":
        return SourcePreservedExpression(
            source=str(value.get("source") or ""),
            source_hash=str(value.get("source_hash") or ""),
            source_entry=str(value.get("source_entry") or ""),
            provenance_id=str(value.get("provenance_id") or ""),
            byte_stable=bool(value.get("byte_stable", True)),
            reference_metadata_origin=str(value.get("reference_metadata_origin") or ""),
            referenced_variables=tuple(value.get("referenced_variables") or ()),
            referenced_functions=tuple(value.get("referenced_functions") or ()),
        )
    if kind == "reviewed_raw_expression":
        return ReviewedRawExpression(
            source=str(value.get("source") or ""),
            approval_id=str(value.get("approval_id") or ""),
            reviewer=str(value.get("reviewer") or ""),
            reference_metadata_origin=str(value.get("reference_metadata_origin") or ""),
            referenced_variables=tuple(value.get("referenced_variables") or ()),
            referenced_functions=tuple(value.get("referenced_functions") or ()),
        )
    raise ValueError(f"Unsupported expression kind: {kind!r}")


def _require_expression(value: object, label: str) -> None:
    if not is_expression(value):
        raise ValueError(f"{label} must be an Expression node")


def _legacy_expression_text(value: Expression) -> str:
    if isinstance(value, StringLiteral):
        return '"' + value.value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(value, NumberLiteral):
        return str(value.value)
    if isinstance(value, BooleanLiteral):
        return "True" if value.value else "False"
    if isinstance(value, VariableReference):
        return value.name
    if isinstance(value, FunctionCall):
        rendered_arguments = ", ".join(_legacy_expression_text(arg) for arg in value.arguments)
        return f"{value.name}({rendered_arguments})"
    if isinstance(value, UnaryExpression):
        return f"({value.operator}{_legacy_expression_text(value.operand)})"
    if isinstance(value, BinaryExpression):
        return f"({_legacy_expression_text(value.left)} {value.operator} {_legacy_expression_text(value.right)})"
    if isinstance(value, (SourcePreservedExpression, ReviewedRawExpression)):
        return value.source
    return str(value)


def _normalize_reference_names(values: Any) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)
