"""Opt-in, privacy-safe timing markers for logical processing stages.

Set ``TECAN_PERFORMANCE_MARKERS=1`` to emit one compact JSON record per marked
stage to stderr. Markers describe work performed, not which implementation
language or accelerator handled it.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
import sys
from time import perf_counter_ns
from typing import Any, Iterator

_ENABLED_VALUES = frozenset({"1", "true", "yes", "on"})


def performance_markers_enabled() -> bool:
    """Return whether logical-stage timings should be emitted."""
    return (
        os.environ.get("TECAN_PERFORMANCE_MARKERS", "").strip().lower()
        in _ENABLED_VALUES
    )


@dataclass(frozen=True)
class _MarkerStart:
    name: str
    started_ns: int
    metrics: dict[str, Any]


def start_performance_marker(name: str, **metrics: Any) -> _MarkerStart | None:
    """Start a marker for a large block without changing its indentation."""
    if not performance_markers_enabled():
        return None
    return _MarkerStart(name=name, started_ns=perf_counter_ns(), metrics=metrics)


def finish_performance_marker(
    marker: _MarkerStart | None,
    *,
    outcome: str = "ok",
    **metrics: Any,
) -> None:
    """Emit a marker started by :func:`start_performance_marker`."""
    if marker is None:
        return
    _emit_marker(
        marker.name,
        (perf_counter_ns() - marker.started_ns) / 1_000_000,
        outcome,
        {**marker.metrics, **metrics},
    )


@contextmanager
def performance_marker(name: str, **metrics: Any) -> Iterator[dict[str, Any]]:
    """Time one logical stage, emitting a JSON line only when opted in.

    The yielded dictionary can be populated with measurements known only after
    the stage completes. Exceptions are re-raised unchanged and are represented
    only by ``outcome=error``; exception text and input data are never logged.
    """
    marker = start_performance_marker(name, **metrics)
    measured: dict[str, Any] = {}
    outcome = "ok"
    try:
        yield measured
    except BaseException:
        outcome = "error"
        raise
    finally:
        finish_performance_marker(
            marker,
            outcome=outcome,
            **measured,
        )


def _emit_marker(
    name: str,
    duration_ms: float,
    outcome: str,
    metrics: dict[str, Any],
) -> None:
    safe_metrics = {
        key: value
        for key, value in metrics.items()
        if isinstance(key, str)
        and len(key) <= 64
        and isinstance(value, (bool, int, float))
        and not (isinstance(value, float) and not math.isfinite(value))
    }
    record = {
        "type": "performance_marker",
        "name": name,
        "duration_ms": round(duration_ms, 3),
        "outcome": outcome,
        "metrics": safe_metrics,
    }
    try:
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"))
        sys.stderr.write("PERF " + encoded + "\n")
        sys.stderr.flush()
    except (OSError, TypeError, ValueError):
        # Diagnostics must never change the behavior of the measured operation.
        pass


__all__ = [
    "finish_performance_marker",
    "performance_marker",
    "performance_markers_enabled",
    "start_performance_marker",
]
