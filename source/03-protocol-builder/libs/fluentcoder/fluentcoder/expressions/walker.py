"""Recursive discovery for canonical expression-bearing IR fields."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .fields import registered_expression_keys


EXPRESSION_KINDS = frozenset(
    {
        "string_literal",
        "number_literal",
        "boolean_literal",
        "variable_reference",
        "function_call",
        "unary_expression",
        "binary_expression",
        "source_preserved_expression",
        "reviewed_raw_expression",
    }
)


@dataclass(frozen=True)
class ExpressionWalkRecord:
    """One top-level expression candidate discovered in a nested payload."""

    path: str
    key: str
    expression: Any
    container: Mapping[str, Any] | None = None


def is_expression_mapping(value: Any) -> bool:
    return isinstance(value, Mapping) and str(value.get("kind") or "") in EXPRESSION_KINDS


def is_expression_key(key: Any) -> bool:
    text = str(key or "")
    return (
        text in registered_expression_keys()
        or text.endswith("_expression")
        or text.endswith("_expressions")
    )


def walk_expression_values(value: Any, *, path: str = "$") -> Iterator[ExpressionWalkRecord]:
    """Yield top-level expression values from arbitrary nested protocol data.

    The walker treats registered expression keys, future ``*_expression`` keys,
    plural ``*_expressions`` lists, and nested expression mappings uniformly.
    Child nodes inside a discovered expression are intentionally not yielded as
    independent records; downstream consumers can inspect the AST when they need
    variable references or imports.
    """

    yield from _walk(value, path=path, key="", container=None)


def _walk(
    value: Any,
    *,
    path: str,
    key: str,
    container: Mapping[str, Any] | None,
) -> Iterator[ExpressionWalkRecord]:
    if is_expression_mapping(value):
        yield ExpressionWalkRecord(path=path, key=key, expression=value, container=container)
        return
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            child_key_text = str(child_key)
            child_path = _join_path(path, child_key_text)
            if is_expression_key(child_key_text):
                yield from _walk_expression_candidate(
                    child,
                    path=child_path,
                    key=child_key_text,
                    container=value,
                )
                continue
            yield from _walk(
                child,
                path=child_path,
                key=child_key_text,
                container=value,
            )
        return
    if _is_sequence(value):
        for index, child in enumerate(value):
            yield from _walk(
                child,
                path=f"{path}[{index}]",
                key=key,
                container=container,
            )


def _walk_expression_candidate(
    value: Any,
    *,
    path: str,
    key: str,
    container: Mapping[str, Any],
) -> Iterator[ExpressionWalkRecord]:
    if _is_sequence(value):
        for index, child in enumerate(value):
            yield from _walk_expression_candidate(
                child,
                path=f"{path}[{index}]",
                key=key,
                container=container,
            )
        return
    yield ExpressionWalkRecord(path=path, key=key, expression=value, container=container)


def _join_path(parent: str, key: str) -> str:
    if key.isidentifier():
        return f"{parent}.{key}"
    return f"{parent}[{key!r}]"


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
