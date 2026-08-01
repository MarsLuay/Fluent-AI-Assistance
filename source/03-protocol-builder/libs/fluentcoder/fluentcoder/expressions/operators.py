"""Shared FluentControl binary operator semantics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeAlias


ExpressionTypeName: TypeAlias = Literal["string", "number", "boolean", "unknown", "any"]
ExpectedTypeName: TypeAlias = ExpressionTypeName | tuple[ExpressionTypeName, ...]


@dataclass(frozen=True)
class BinaryOperatorDefinition:
    operator: str
    category: Literal["additive", "numeric", "logical", "equality", "ordering"]
    result_type: ExpressionTypeName
    operand_type: ExpectedTypeName | None = None


@dataclass(frozen=True)
class BinaryOperatorTypeResult:
    result_type: ExpressionTypeName
    valid: bool = True
    issue_code: str = ""
    expected_type: str = ""
    actual_type: str = ""
    message: str = ""


BINARY_OPERATOR_DEFINITIONS: dict[str, BinaryOperatorDefinition] = {
    "+": BinaryOperatorDefinition("+", "additive", "unknown"),
    "-": BinaryOperatorDefinition("-", "numeric", "number", "number"),
    "*": BinaryOperatorDefinition("*", "numeric", "number", "number"),
    "/": BinaryOperatorDefinition("/", "numeric", "number", "number"),
    "^": BinaryOperatorDefinition("^", "numeric", "number", "number"),
    "AND": BinaryOperatorDefinition("AND", "logical", "boolean", "boolean"),
    "OR": BinaryOperatorDefinition("OR", "logical", "boolean", "boolean"),
    "=": BinaryOperatorDefinition("=", "equality", "boolean"),
    "<>": BinaryOperatorDefinition("<>", "equality", "boolean"),
    "<": BinaryOperatorDefinition("<", "ordering", "boolean", "number"),
    ">": BinaryOperatorDefinition(">", "ordering", "boolean", "number"),
    "<=": BinaryOperatorDefinition("<=", "ordering", "boolean", "number"),
    ">=": BinaryOperatorDefinition(">=", "ordering", "boolean", "number"),
}


def binary_operator_definition(operator: str) -> BinaryOperatorDefinition | None:
    return BINARY_OPERATOR_DEFINITIONS.get(_normalize_operator(operator))


def infer_binary_operator_type(
    operator: str,
    left_type: ExpressionTypeName,
    right_type: ExpressionTypeName,
) -> BinaryOperatorTypeResult:
    op = _normalize_operator(operator)
    definition = binary_operator_definition(op)
    if definition is None:
        return BinaryOperatorTypeResult(
            result_type="unknown",
            valid=False,
            issue_code="unsupported_binary_operator",
            message=f"Unsupported binary operator {op!r}.",
        )

    if definition.category == "additive":
        if left_type == "number" and right_type == "number":
            return BinaryOperatorTypeResult(result_type="number")
        if left_type == "string" or right_type == "string":
            return BinaryOperatorTypeResult(result_type="string")
        if "unknown" in {left_type, right_type} or "any" in {left_type, right_type}:
            return BinaryOperatorTypeResult(result_type="unknown")
        return BinaryOperatorTypeResult(
            result_type="unknown",
            valid=False,
            issue_code="invalid_binary_operands",
            expected_type="number|string",
            actual_type=f"{left_type},{right_type}",
            message=(
                "Operator '+' requires numeric operands or a string operand, "
                f"got {left_type} and {right_type}."
            ),
        )

    if definition.category == "equality":
        if _are_comparable(left_type, right_type):
            return BinaryOperatorTypeResult(result_type=definition.result_type)
        return BinaryOperatorTypeResult(
            result_type=definition.result_type,
            valid=False,
            issue_code="incompatible_comparison_operands",
            expected_type="matching comparable types",
            actual_type=f"{left_type},{right_type}",
            message=f"Operator {op!r} cannot compare {left_type} to {right_type}.",
        )

    return BinaryOperatorTypeResult(result_type=definition.result_type)


def evaluate_binary_operator(operator: str, left: Any, right: Any) -> Any:
    op = _normalize_operator(operator)
    definition = binary_operator_definition(op)
    if definition is None:
        raise ValueError(f"Unsupported binary operator {op!r}.")

    if definition.category == "logical":
        return (bool(left) and bool(right)) if op == "AND" else (bool(left) or bool(right))

    if definition.category == "additive":
        if _is_runtime_number(left) and _is_runtime_number(right):
            return _runtime_number(left) + _runtime_number(right)
        if isinstance(left, str) or isinstance(right, str):
            return _fluent_string(left) + _fluent_string(right)
        raise TypeError(
            "Operator '+' requires numeric operands or a string operand, "
            f"got {type(left).__name__} and {type(right).__name__}."
        )

    if definition.category == "numeric":
        left_number = _runtime_number(left)
        right_number = _runtime_number(right)
        if op == "-":
            return left_number - right_number
        if op == "*":
            return left_number * right_number
        if op == "^":
            return left_number ** right_number
        return left_number / right_number

    if definition.category in {"equality", "ordering"}:
        left_cmp, right_cmp = _comparison_values(left, right)
        return {
            "=": left_cmp == right_cmp,
            "<>": left_cmp != right_cmp,
            "<": left_cmp < right_cmp,
            ">": left_cmp > right_cmp,
            "<=": left_cmp <= right_cmp,
            ">=": left_cmp >= right_cmp,
        }[op]

    raise ValueError(f"Unsupported binary operator category {definition.category!r}.")


def format_expected_type(expected_type: ExpectedTypeName) -> str:
    if isinstance(expected_type, tuple):
        return "|".join(expected_type)
    return expected_type


def is_type_compatible(actual_type: ExpressionTypeName, expected_type: ExpectedTypeName) -> bool:
    if isinstance(expected_type, tuple):
        return any(is_type_compatible(actual_type, item) for item in expected_type)
    if expected_type in {"any", "unknown"} or actual_type in {"any", "unknown"}:
        return True
    return actual_type == expected_type


def _are_comparable(left_type: ExpressionTypeName, right_type: ExpressionTypeName) -> bool:
    if left_type in {"unknown", "any"} or right_type in {"unknown", "any"}:
        return True
    return left_type == right_type


def _comparison_values(left: Any, right: Any) -> tuple[Any, Any]:
    try:
        return _runtime_number(left), _runtime_number(right)
    except (TypeError, ValueError):
        return left, right


def _fluent_string(value: Any) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else str(value)
    if value is None:
        return ""
    return str(value)


def _is_runtime_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _runtime_number(value: Any) -> float:
    if isinstance(value, bool):
        raise TypeError("boolean is not a numeric expression value")
    return float(value)


def _normalize_operator(operator: str) -> str:
    op = str(operator or "").strip()
    return op.upper() if op.casefold() in {"and", "or"} else op
