"""Single renderer for FluentControl expression AST nodes."""

from __future__ import annotations

import math
import re
import hashlib

from .ast import (
    BinaryExpression,
    BooleanLiteral,
    Expression,
    FunctionCall,
    NumberLiteral,
    ReviewedRawExpression,
    SourcePreservedExpression,
    StringLiteral,
    UnaryExpression,
    VariableReference,
)


class ExpressionRenderError(ValueError):
    pass


_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_INDEXED_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\[(?:[0-9]+|[A-Za-z_][A-Za-z0-9_]*)\]$")
_OPERATORS = {"+", "-", "*", "/", "^", "=", "<>", "<", ">", "<=", ">=", "AND", "OR"}


def render_expression(expression: Expression) -> str:
    if isinstance(expression, StringLiteral):
        return render_string_literal(expression.value)
    if isinstance(expression, NumberLiteral):
        return render_number(expression.value)
    if isinstance(expression, BooleanLiteral):
        return "True" if expression.value else "False"
    if isinstance(expression, VariableReference):
        validate_variable_name(expression.name)
        return expression.name
    if isinstance(expression, FunctionCall):
        validate_function_name(expression.name)
        rendered = ", ".join(render_expression(arg) for arg in expression.arguments)
        return f"{expression.name}({rendered})"
    if isinstance(expression, UnaryExpression):
        return f"({expression.operator}{render_expression(expression.operand)})"
    if isinstance(expression, BinaryExpression):
        if expression.operator not in _OPERATORS:
            raise ExpressionRenderError(f"Unsupported binary operator: {expression.operator!r}")
        return f"({render_expression(expression.left)} {expression.operator} {render_expression(expression.right)})"
    if isinstance(expression, SourcePreservedExpression):
        if not expression.byte_stable:
            raise ExpressionRenderError("Source-preserved expression is not marked byte-stable")
        expected = _normalize_sha256(expression.source_hash)
        actual = hashlib.sha256(expression.source.encode("utf-8")).hexdigest()
        if expected != actual:
            raise ExpressionRenderError("Source-preserved expression hash does not match source text")
        return expression.source
    if isinstance(expression, ReviewedRawExpression):
        if not expression.approval_id:
            raise ExpressionRenderError("Raw FluentControl expression lacks approval metadata")
        return expression.source
    raise ExpressionRenderError(f"Unsupported expression node: {type(expression).__name__}")


def render_string_literal(value: str) -> str:
    return f'"{escape_fluent_string_content(value)}"'


def escape_fluent_string_content(value: str) -> str:
    text = "" if value is None else str(value)
    return (
        text.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\t", "\\t")
    )


def render_number(value: int | float) -> str:
    if isinstance(value, bool):
        raise ExpressionRenderError("Boolean is not a numeric literal")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ExpressionRenderError("Numeric literal must be finite")
        return str(int(value)) if value.is_integer() else str(value)
    return str(value)


def validate_variable_name(name: str) -> None:
    text = str(name or "")
    if not (_NAME_RE.fullmatch(text) or _INDEXED_NAME_RE.fullmatch(text)):
        raise ExpressionRenderError(f"Invalid FluentControl variable name: {name!r}")


def validate_function_name(name: str) -> None:
    if not _NAME_RE.fullmatch(str(name or "")):
        raise ExpressionRenderError(f"Invalid FluentControl function name: {name!r}")


def _normalize_sha256(value: str) -> str:
    text = str(value or "").strip()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1]
    if len(text) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in text):
        raise ExpressionRenderError(f"Invalid source-preserved expression hash: {value!r}")
    return text.casefold()
