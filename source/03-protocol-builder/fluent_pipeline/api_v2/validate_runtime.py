"""Shared runtime error helpers for API V2 pre-execute validators."""

from __future__ import annotations

from typing import Any, Protocol


class _ValidateResultLike(Protocol):
    message: str | None


class _CommandLike(Protocol):
    index: int
    type_name: str
    group: str


def runtime_error_for_validate_failure(
    result: _ValidateResultLike | Any,
    command: _CommandLike | Any,
    *,
    kind: str,
) -> str:
    """Prefer validator message; otherwise emit a step-scoped fallback."""
    message = getattr(result, "message", None)
    if message:
        return str(message)
    return (
        f"{kind} validation failed at step {command.index + 1} "
        f"({command.type_name} in {command.group})."
    )
