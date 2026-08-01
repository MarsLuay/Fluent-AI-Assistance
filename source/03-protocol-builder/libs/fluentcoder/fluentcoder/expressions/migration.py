"""One-way migration helpers for legacy untyped values."""

from __future__ import annotations

import hashlib
from typing import Any

from .ast import (
    BooleanLiteral,
    Expression,
    NumberLiteral,
    SourcePreservedExpression,
    StringLiteral,
    expression_from_mapping,
    is_expression,
)
from .parser import ExpressionParseError, parse_expression, try_parse_expression
from .renderer import render_expression


class LegacyMigrationError(ValueError):
    pass


def coerce_literal_expression(value: Any) -> Expression:
    """Coerce legacy Python values without interpreting strings as expressions."""
    if is_expression(value):
        return value
    if isinstance(value, dict) and value.get("kind"):
        return expression_from_mapping(value)
    if isinstance(value, bool):
        return BooleanLiteral(value=value)
    if isinstance(value, (int, float)):
        return NumberLiteral(value=value)
    return StringLiteral(value="" if value is None else str(value))


def coerce_source_expression(value: Any) -> Expression:
    """Coerce a FluentControl expression-bearing field into typed AST.

    Unlike ``coerce_literal_expression``, string input is parsed as source
    syntax. Use this for volumes, positions, wait durations, loop counts,
    conditions, mapping sources, and other fields that FluentControl evaluates
    rather than treating as plain text.
    """
    if is_expression(value):
        return value
    if isinstance(value, dict) and value.get("kind"):
        return expression_from_mapping(value)
    if isinstance(value, str):
        return parse_expression(value.strip())
    return coerce_literal_expression(value)


def migrate_legacy_set_variable_value(
    value: object,
    *,
    declared_variable_type: str | None = None,
) -> Expression:
    """Migrate an old SetVariable value once at load time.

    Generated Python strings stay string literals. Serialized source expressions
    should be routed through ``parse_or_preserve_source_expression`` instead.
    """
    if is_expression(value):
        return value
    if isinstance(value, dict) and value.get("kind"):
        return expression_from_mapping(value)
    if isinstance(value, bool):
        return BooleanLiteral(value=value)
    if isinstance(value, (int, float)):
        return NumberLiteral(value=value)

    text = "" if value is None else str(value)
    if _is_string_type(declared_variable_type):
        parsed = try_parse_expression(text)
        if isinstance(parsed, StringLiteral):
            return parsed
        if parsed is None:
            return StringLiteral(value=text)
        raise LegacyMigrationError(
            "String variable contains an expression-shaped legacy value; manual classification is required."
        )
    return coerce_literal_expression(text)


def parse_or_preserve_source_expression(source: str) -> Expression:
    """Parse source XSCR text or preserve unsupported syntax with provenance metadata."""
    text = str(source or "").strip()
    try:
        return parse_expression(text)
    except ExpressionParseError:
        source_hash = "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()
        referenced_variables, referenced_functions = extract_opaque_expression_references(text)
        return SourcePreservedExpression(
            source=text,
            source_hash=source_hash,
            byte_stable=True,
            reference_metadata_origin="source_ingestion",
            referenced_variables=referenced_variables,
            referenced_functions=referenced_functions,
        )


def expression_python_value(expression: Any) -> Any:
    if isinstance(expression, StringLiteral):
        return expression.value
    if isinstance(expression, NumberLiteral):
        return expression.value
    if isinstance(expression, BooleanLiteral):
        return expression.value
    if is_expression(expression):
        return render_expression(expression)
    return expression


def expression_initial_value_text(expression: Any) -> str:
    value = expression_python_value(expression)
    if value is None:
        return ""
    return str(value)


def _is_string_type(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"string", "system.string"}


_OPAQUE_RESERVED_WORDS = {"and", "or", "not", "true", "false", "none"}


def extract_opaque_expression_references(
    source: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Extract opaque dependencies during trusted source ingestion."""
    collector = _OpaqueReferenceCollector(source)
    collector.collect()
    return tuple(collector.referenced_variables), tuple(collector.referenced_functions)


class _OpaqueReferenceCollector:
    def __init__(self, source: str) -> None:
        self.source = source
        self.length = len(source)
        self.index = 0
        self.referenced_variables: list[str] = []
        self.referenced_functions: list[str] = []
        self._seen_variables: set[str] = set()
        self._seen_functions: set[str] = set()

    def collect(self) -> None:
        self._scan_until()

    def _scan_until(self, terminator: str | None = None) -> None:
        while self.index < self.length:
            char = self.source[self.index]
            if terminator and char == terminator:
                return
            if char.isspace():
                self.index += 1
                continue
            if char == '"':
                self._skip_string()
                continue
            if char.isdigit() or (char == "." and self.index + 1 < self.length and self.source[self.index + 1].isdigit()):
                self._skip_number()
                continue
            if char.isalpha() or char == "_":
                self._scan_identifier_or_call()
                continue
            if char == "(":
                self.index += 1
                self._scan_until(")")
                if self.index < self.length and self.source[self.index] == ")":
                    self.index += 1
                continue
            self.index += 1

    def _scan_identifier_or_call(self) -> None:
        name, segment_count = self._read_identifier_chain()
        if not name:
            return
        folded = name.casefold()
        if folded in _OPAQUE_RESERVED_WORDS:
            return
        self._skip_whitespace()
        if self.index < self.length and self.source[self.index] == "(":
            self._add_function(name)
            self.index += 1
            self._scan_until(")")
            if self.index < self.length and self.source[self.index] == ")":
                self.index += 1
            return
        if segment_count == 1:
            self._add_variable(name)

    def _read_identifier_chain(self) -> tuple[str, int]:
        start = self.index
        self.index += 1
        while self.index < self.length and (self.source[self.index].isalnum() or self.source[self.index] == "_"):
            self.index += 1
        segment_count = 1
        while self.index < self.length and self.source[self.index] == ".":
            dot_index = self.index
            self.index += 1
            if self.index >= self.length or not (self.source[self.index].isalpha() or self.source[self.index] == "_"):
                self.index = dot_index
                break
            segment_count += 1
            self.index += 1
            while self.index < self.length and (self.source[self.index].isalnum() or self.source[self.index] == "_"):
                self.index += 1
        if self.index < self.length and self.source[self.index] == "[":
            self._skip_index_suffix()
        return self.source[start:self.index], segment_count

    def _skip_index_suffix(self) -> None:
        depth = 0
        while self.index < self.length:
            char = self.source[self.index]
            if char == "[":
                depth += 1
            elif char == "]":
                depth -= 1
                self.index += 1
                if depth == 0:
                    return
                continue
            elif char == '"':
                self._skip_string()
                continue
            self.index += 1

    def _skip_string(self) -> None:
        self.index += 1
        while self.index < self.length:
            char = self.source[self.index]
            if char == "\\" and self.index + 1 < self.length:
                self.index += 2
                continue
            self.index += 1
            if char == '"':
                return

    def _skip_number(self) -> None:
        if self.source[self.index] == ".":
            self.index += 1
        while self.index < self.length and self.source[self.index].isdigit():
            self.index += 1
        if self.index < self.length and self.source[self.index] == ".":
            self.index += 1
            while self.index < self.length and self.source[self.index].isdigit():
                self.index += 1
        if self.index < self.length and self.source[self.index] in {"e", "E"}:
            lookahead = self.index + 1
            if lookahead < self.length and self.source[lookahead] in {"+", "-"}:
                lookahead += 1
            while lookahead < self.length and self.source[lookahead].isdigit():
                lookahead += 1
            self.index = lookahead

    def _skip_whitespace(self) -> None:
        while self.index < self.length and self.source[self.index].isspace():
            self.index += 1

    def _add_variable(self, name: str) -> None:
        base_name = name.split("[", 1)[0].strip()
        if not base_name:
            return
        if base_name in self._seen_variables:
            return
        self._seen_variables.add(base_name)
        self.referenced_variables.append(base_name)

    def _add_function(self, name: str) -> None:
        if name in self._seen_functions:
            return
        self._seen_functions.add(name)
        self.referenced_functions.append(name)
