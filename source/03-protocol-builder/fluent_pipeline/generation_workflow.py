"""Compatibility facade for the canonical generation workflow package.

The implementation lives in :mod:`fluent_pipeline.workflows.generation`.
This module keeps the historical import path stable, including the legacy
patch points used by downstream callers and tests.
"""

from __future__ import annotations

from functools import wraps
from typing import Any

from .workflows.generation import workflow as _implementation


_MISSING = object()
_LEGACY_CALLABLES: dict[str, Any] = {}
_IMPLEMENTATION_BINDINGS = {
    name: value
    for name, value in vars(_implementation).items()
    if not name.startswith("__")
}

# Re-export public implementation names as ordinary facade attributes. Private
# helpers are resolved by __getattr__ below so old imports remain compatible
# without making the facade the owner of those helpers.
for _name, _value in _IMPLEMENTATION_BINDINGS.items():
    if not _name.startswith("_") and _name != "run_generation_workflow":
        globals()[_name] = _value


def __getattr__(name: str) -> Any:
    """Resolve legacy private/helper imports from the canonical implementation."""

    try:
        target = getattr(_implementation, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    if not callable(target):
        return target
    if name in _LEGACY_CALLABLES:
        return _LEGACY_CALLABLES[name]

    @wraps(target)
    def legacy_callable(*args: Any, **kwargs: Any) -> Any:
        restored = _apply_legacy_overrides()
        try:
            return getattr(_implementation, name)(*args, **kwargs)
        finally:
            _restore_legacy_overrides(restored)

    _LEGACY_CALLABLES[name] = legacy_callable
    return legacy_callable


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_IMPLEMENTATION_BINDINGS))


def _apply_legacy_overrides() -> dict[str, Any]:
    """Temporarily mirror facade monkey-patches into the implementation.

    Older callers patch symbols on ``fluent_pipeline.generation_workflow``.
    Keep that supported while ensuring all actual execution occurs in the
    canonical workflow module.
    """

    restored: dict[str, Any] = {}
    for name, original in _IMPLEMENTATION_BINDINGS.items():
        if name == "run_generation_workflow":
            continue
        facade_value = globals().get(name, _MISSING)
        if facade_value is _MISSING or facade_value is original:
            continue
        restored[name] = getattr(_implementation, name)
        setattr(_implementation, name, facade_value)
    return restored


def _restore_legacy_overrides(restored: dict[str, Any]) -> None:
    for name, value in restored.items():
        setattr(_implementation, name, value)


def run_generation_workflow(
    request: GenerationRequest,
    *,
    progress_callback: ProgressCallback | None = None,
    progress: Any | None = None,
    event_sink: Any | None = None,
    event_log_path: str | None = None,
) -> dict[str, Any]:
    """Run the canonical workflow through the historical public entry point."""

    restored = _apply_legacy_overrides()
    try:
        return _implementation.run_generation_workflow(
            request,
            progress_callback=progress_callback,
            progress=progress,
            event_sink=event_sink,
            event_log_path=event_log_path,
        )
    finally:
        _restore_legacy_overrides(restored)


__all__ = tuple(
    name
    for name in _IMPLEMENTATION_BINDINGS
    if not name.startswith("_") and name != "run_generation_workflow"
) + ("run_generation_workflow",)
