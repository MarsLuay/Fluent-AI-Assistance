"""Shared progress events for long-running application workflows."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import threading
import time
from typing import Callable, Iterator, Literal, Sequence


ProgressStatus = Literal[
    "started",
    "running",
    "completed",
    "skipped",
    "warning",
    "failed",
]


@dataclass(frozen=True)
class ProgressEvent:
    stage_id: str
    stage_name: str
    status: ProgressStatus
    current_stage: int
    total_stages: int
    operation_id: str = "generate"
    message: str | None = None
    elapsed_seconds: float | None = None
    completed_units: int | None = None
    total_units: int | None = None
    unit_name: str | None = None


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass(frozen=True)
class ProgressStage:
    stage_id: str
    stage_name: str


GENERATION_PROGRESS_STAGES: tuple[ProgressStage, ...] = (
    ProgressStage("load_context", "Loading project context"),
    ProgressStage("infer_missing_details", "Inferring missing details"),
    ProgressStage("validate_request", "Validating request"),
    ProgressStage("build_protocol_ir", "Building protocol IR"),
    ProgressStage("render_script", "Rendering script"),
    ProgressStage("simulate", "Running simulation"),
    ProgressStage("repair", "Repairing findings"),
    ProgressStage("compile_xscr", "Compiling XSCR"),
    ProgressStage("finalize_xscr", "Finalizing XSCR"),
    ProgressStage("validate_bundle", "Validating bundle"),
    ProgressStage("publish_bundle", "Publishing bundle"),
)


class ProgressEmitter:
    """Small stateful helper that keeps timing out of workflow call sites."""

    def __init__(
        self,
        stages: Sequence[ProgressStage],
        callback: ProgressCallback | None,
        *,
        operation_id: str = "generate",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stages = tuple(stages)
        self._callback = callback
        self._operation_id = operation_id
        self._clock = clock
        self._index_by_id = {stage.stage_id: index + 1 for index, stage in enumerate(self._stages)}
        self._name_by_id = {stage.stage_id: stage.stage_name for stage in self._stages}
        self._started_at: dict[str, float] = {}
        self._active_stage_id: str | None = None

    def started(
        self,
        stage_id: str,
        message: str | None = None,
        *,
        completed_units: int | None = None,
        total_units: int | None = None,
        unit_name: str | None = None,
    ) -> None:
        self._started_at[stage_id] = self._clock()
        self._active_stage_id = stage_id
        self.report(
            stage_id,
            "started",
            message=message,
            completed_units=completed_units,
            total_units=total_units,
            unit_name=unit_name,
        )

    def running(
        self,
        stage_id: str,
        message: str | None = None,
        *,
        completed_units: int | None = None,
        total_units: int | None = None,
        unit_name: str | None = None,
    ) -> None:
        if stage_id not in self._started_at:
            self._started_at[stage_id] = self._clock()
        self._active_stage_id = stage_id
        self.report(
            stage_id,
            "running",
            message=message,
            completed_units=completed_units,
            total_units=total_units,
            unit_name=unit_name,
        )

    def completed(
        self,
        stage_id: str,
        message: str | None = None,
        *,
        completed_units: int | None = None,
        total_units: int | None = None,
        unit_name: str | None = None,
    ) -> None:
        self.report(
            stage_id,
            "completed",
            message=message,
            completed_units=completed_units,
            total_units=total_units,
            unit_name=unit_name,
        )
        self._clear_active(stage_id)

    def skipped(self, stage_id: str, message: str | None = None) -> None:
        if stage_id not in self._started_at:
            self._started_at[stage_id] = self._clock()
        self.report(stage_id, "skipped", message=message)
        self._clear_active(stage_id)

    def warning(self, stage_id: str, message: str | None = None) -> None:
        self.report(stage_id, "warning", message=message)
        self._clear_active(stage_id)

    def failed(self, stage_id: str, message: str | None = None) -> None:
        if stage_id not in self._started_at:
            self._started_at[stage_id] = self._clock()
        self.report(stage_id, "failed", message=message)
        self._clear_active(stage_id)

    def failed_current(self, message: str | None = None) -> None:
        if self._active_stage_id is not None:
            self.failed(self._active_stage_id, message=message)

    def report(
        self,
        stage_id: str,
        status: ProgressStatus = "running",
        *,
        message: str | None = None,
        completed_units: int | None = None,
        total_units: int | None = None,
        unit_name: str | None = None,
    ) -> None:
        """Emit one event, including optional item-level progress."""
        if stage_id not in self._started_at:
            self._started_at[stage_id] = self._clock()
        self._emit(
            stage_id,
            status,
            message=message,
            completed_units=completed_units,
            total_units=total_units,
            unit_name=unit_name,
        )

    @contextmanager
    def heartbeat(
        self,
        stage_id: str,
        *,
        interval_seconds: float = 30.0,
        message: str | None = None,
    ) -> Iterator[None]:
        """Emit elapsed-time heartbeats while an uncountable operation runs."""
        if self._callback is None or interval_seconds <= 0:
            yield
            return

        stopped = threading.Event()

        def emit_heartbeats() -> None:
            while not stopped.wait(interval_seconds):
                self.running(stage_id, message)

        thread = threading.Thread(
            target=emit_heartbeats,
            name=f"progress-{self._operation_id}-{stage_id}",
            daemon=True,
        )
        thread.start()
        try:
            yield
        finally:
            stopped.set()
            thread.join()

    def _emit(
        self,
        stage_id: str,
        status: ProgressStatus,
        *,
        message: str | None,
        completed_units: int | None = None,
        total_units: int | None = None,
        unit_name: str | None = None,
    ) -> None:
        if self._callback is None:
            return
        start = self._started_at.get(stage_id)
        elapsed = (self._clock() - start) if start is not None else None
        event = ProgressEvent(
            operation_id=self._operation_id,
            stage_id=stage_id,
            stage_name=self._name_by_id.get(stage_id, stage_id),
            status=status,
            current_stage=self._index_by_id.get(stage_id, len(self._stages)),
            total_stages=len(self._stages),
            message=message,
            elapsed_seconds=elapsed,
            completed_units=completed_units,
            total_units=total_units,
            unit_name=unit_name,
        )
        try:
            self._callback(event)
        except Exception:
            pass

    def _clear_active(self, stage_id: str) -> None:
        if self._active_stage_id == stage_id:
            self._active_stage_id = None


def render_plain_progress_event(event: ProgressEvent) -> str:
    base = f"[{event.current_stage}/{event.total_stages}] {event.stage_name}"
    elapsed = _format_elapsed(event.elapsed_seconds)
    count = _format_count(event)
    if event.status == "running" and count:
        line = f"{base}: {count}"
    elif event.status == "started":
        line = f"{base}... started"
    elif event.status == "running":
        line = f"{base}... running ({elapsed})" if elapsed else f"{base}... running"
    elif event.status == "completed":
        suffix = f"done ({elapsed})" if elapsed else "done"
        line = f"{base}... {suffix}"
    elif event.status == "skipped":
        suffix = f"skipped ({elapsed})" if elapsed else "skipped"
        line = f"{base}... {suffix}"
    elif event.status == "warning":
        suffix = f"warning ({elapsed})" if elapsed else "warning"
        line = f"{base}... {suffix}"
    else:
        suffix = f"failed ({elapsed})" if elapsed else "failed"
        line = f"{base}... {suffix}"
    lines = [line]
    if event.message:
        lines.append(f"       {event.message}")
    return "\n".join(lines)


def _format_count(event: ProgressEvent) -> str:
    if event.completed_units is None:
        return ""
    completed = f"{event.completed_units:,}"
    if event.total_units is not None:
        completed = f"{completed}/{event.total_units:,}"
    if event.unit_name:
        completed = f"{completed} {event.unit_name}"
    return completed


def _format_elapsed(seconds: float | None) -> str:
    if seconds is None:
        return ""
    if seconds < 10:
        return f"{seconds:.1f}s"
    return f"{seconds:.0f}s"
