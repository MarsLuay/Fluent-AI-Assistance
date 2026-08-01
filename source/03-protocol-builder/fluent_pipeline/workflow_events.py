"""Structured workflow events and phase timing for generate / validate / package."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import sys
import time
from typing import Any, Callable

_EVENT_SINK: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar(
    "workflow_event_sink",
    default=None,
)
_WORKFLOW_START: ContextVar[float | None] = ContextVar("workflow_start_mono", default=None)
_LAST_PHASE_END: ContextVar[float | None] = ContextVar("workflow_last_phase_end", default=None)
_TIMING_PHASES: ContextVar[list[dict[str, Any]] | None] = ContextVar("workflow_timing_phases", default=None)


def configure_workflow_sinks(
    *,
    event_sink: Callable[[dict[str, Any]], None] | None = None,
    workflow_start: float | None = None,
    reset_timing: bool = True,
) -> None:
    _EVENT_SINK.set(event_sink)
    if workflow_start is not None:
        _WORKFLOW_START.set(workflow_start)
        _LAST_PHASE_END.set(workflow_start)
    if reset_timing:
        _TIMING_PHASES.set([])


def reset_workflow_sinks() -> None:
    _EVENT_SINK.set(None)
    _WORKFLOW_START.set(None)
    _LAST_PHASE_END.set(None)
    _TIMING_PHASES.set(None)


def elapsed_ms() -> int:
    start = _WORKFLOW_START.get()
    if start is None:
        return 0
    return int((time.monotonic() - start) * 1000)


def emit_workflow_event(payload: dict[str, Any]) -> None:
    sink = _EVENT_SINK.get()
    if sink is None:
        return
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": elapsed_ms(),
        **payload,
    }
    try:
        sink(event)
    except Exception:
        pass


def append_timing_phase(record: dict[str, Any]) -> None:
    phases = _TIMING_PHASES.get()
    if phases is None:
        phases = []
        _TIMING_PHASES.set(phases)
    phases.append(record)


def timing_summary() -> dict[str, Any]:
    phases = list(_TIMING_PHASES.get() or [])
    total_ms = sum(int(item.get("duration_ms") or 0) for item in phases)
    slowest = sorted(phases, key=lambda item: int(item.get("duration_ms") or 0), reverse=True)[:10]
    return {
        "phase_count": len(phases),
        "tracked_duration_ms": total_ms,
        "slowest_phases": slowest,
        "phases": phases,
    }


@contextmanager
def workflow_phase(stage: str, message: str = "", **fields: Any):
    """Emit start/done events with duration_ms and since_previous_ms."""
    label = message or stage
    now = time.monotonic()
    last = _LAST_PHASE_END.get()
    since_previous_ms = int((now - last) * 1000) if last is not None else None
    emit_workflow_event(
        {
            "stage": stage,
            "status": "start",
            "message": label,
            "since_previous_ms": since_previous_ms,
            **fields,
        }
    )
    start = now
    error_text = ""
    status = "done"
    try:
        yield
    except Exception as exc:
        status = "error"
        error_text = str(exc)
        emit_workflow_event(
            {
                "stage": stage,
                "status": "error",
                "message": f"{label} failed: {exc}",
                "duration_ms": int((time.monotonic() - start) * 1000),
                **fields,
            }
        )
        raise
    else:
        duration_ms = int((time.monotonic() - start) * 1000)
        end = time.monotonic()
        _LAST_PHASE_END.set(end)
        record = {
            "stage": stage,
            "status": status,
            "message": label,
            "duration_ms": duration_ms,
            "since_previous_ms": since_previous_ms,
            **fields,
        }
        if error_text:
            record["error"] = error_text
        emit_workflow_event(record)
        append_timing_phase(record)


def write_progress_line(message: str, *, use_stderr: bool = True) -> None:
    text = f"  {message}"
    try:
        if use_stderr:
            sys.stderr.write(f"{text}\n")
            sys.stderr.flush()
        else:
            print(text, flush=True)
    except Exception:
        pass
