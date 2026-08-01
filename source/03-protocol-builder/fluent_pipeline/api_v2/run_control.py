"""Method run control and teardown (api-v2-022, api-v2-064, api-v2-066, api-v2-085)."""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Protocol

from .state import format_state, parse_state_machine_states
from .types import StateMachineStates

_OPEN_METHOD_STATE_KEYS = frozenset(
    {
        "editmode",
        "readymode",
        "runmode",
        "pausemode",
        "ready",
        "running",
        "paused",
        "edit",
    }
)

_USER_PROMPT_TYPES = frozenset(
    {
        "UserPrompt",
        "UserPromptStatement",
        "RUPStandardStatement",
    }
)
_PAUSED_STATE_LABELS = frozenset({"PauseMode", "Paused", "PAUSED"})


@dataclass(frozen=True)
class RunControlOptions:
    """Opt-in live run control flags."""

    pause_run_enabled: bool = False
    stop_before_close: bool = True
    resume_run_after_prompts: bool = False
    operator_ack_path: str | None = None
    resume_ack_timeout_seconds: float = 300.0
    resume_poll_interval_seconds: float = 0.25


def pause_run_if_enabled(
    runtime: Any,
    *,
    options: RunControlOptions | None = None,
    at_prompt_boundary: bool = False,
) -> bool:
    """Call ``PauseRun`` only when explicitly enabled (api-v2-064).

    Default Gate 27 stays prepare-only; hardware verification runs opt in via
    ``RunControlOptions.pause_run_enabled``.
    """
    opts = options or RunControlOptions()
    if not opts.pause_run_enabled or not at_prompt_boundary:
        return False
    pause = getattr(runtime, "PauseRun", None) or getattr(runtime, "pause_run", None)
    if pause is None:
        return False
    try:
        pause()
        return True
    except Exception:
        return False


class OperatorAckSource(Protocol):
    """External operator acknowledgement for semi-automated ``ResumeRun`` (api-v2-085)."""

    def wait_for_ack(self, timeout_seconds: float, *, poll_interval_seconds: float = 0.25) -> bool:
        ...


@dataclass(frozen=True)
class ResumeRunResult:
    attempted: bool
    resumed: bool
    ack_received: bool
    reason: str = ""
    prompt_index: int | None = None
    prompt_type: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "attempted": self.attempted,
            "resumed": self.resumed,
            "ack_received": self.ack_received,
            "reason": self.reason,
        }
        if self.prompt_index is not None:
            payload["prompt_index"] = self.prompt_index
        if self.prompt_type:
            payload["prompt_type"] = self.prompt_type
        if self.error:
            payload["error"] = self.error
        return payload


class CallableOperatorAckSource:
    """Test/double ack source backed by a predicate."""

    def __init__(self, predicate: Callable[[], bool]) -> None:
        self._predicate = predicate

    def wait_for_ack(self, timeout_seconds: float, *, poll_interval_seconds: float = 0.25) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            if self._predicate():
                return True
            time.sleep(max(0.05, poll_interval_seconds))
        return False


class FileOperatorAckSource:
    """Wait for an operator to touch/create an ack file (``TECAN_OPERATOR_ACK_FILE``)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def wait_for_ack(self, timeout_seconds: float, *, poll_interval_seconds: float = 0.25) -> bool:
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        while time.monotonic() < deadline:
            if self.path.is_file():
                try:
                    text = self.path.read_text(encoding="utf-8").strip().casefold()
                except OSError:
                    text = ""
                if not text or text in {"1", "true", "yes", "ack", "resume"}:
                    return True
            time.sleep(max(0.05, poll_interval_seconds))
        return False


def is_user_prompt_type_name(type_name: str) -> bool:
    """True when a FluentControl type name is an operator UserPrompt command."""
    return str(type_name or "").strip() in _USER_PROMPT_TYPES


def run_control_options_from_env() -> RunControlOptions:
    """Build run-control flags from optional ``TECAN_*`` environment variables."""
    resume_enabled = os.environ.get("TECAN_RESUME_RUN_AFTER_PROMPTS", "").strip().casefold() in {
        "1",
        "true",
        "yes",
    }
    ack_path = os.environ.get("TECAN_OPERATOR_ACK_FILE", "").strip() or None
    timeout_raw = os.environ.get("TECAN_OPERATOR_ACK_TIMEOUT_SECONDS", "").strip()
    timeout = float(timeout_raw) if timeout_raw else 300.0
    return RunControlOptions(
        resume_run_after_prompts=resume_enabled,
        operator_ack_path=ack_path,
        resume_ack_timeout_seconds=timeout,
    )


def operator_ack_source_from_options(options: RunControlOptions) -> OperatorAckSource:
    if options.operator_ack_path:
        return FileOperatorAckSource(options.operator_ack_path)
    env_path = os.environ.get("TECAN_OPERATOR_ACK_FILE", "").strip()
    if env_path:
        return FileOperatorAckSource(env_path)
    return CallableOperatorAckSource(
        lambda: os.environ.get("TECAN_OPERATOR_ACK", "").strip().casefold() in {"1", "true", "yes", "ack", "resume"}
    )


def call_resume_run(runtime: Any) -> tuple[bool, str | None]:
    resume = getattr(runtime, "ResumeRun", None) or getattr(runtime, "resume_run", None)
    if resume is None:
        return False, "ResumeRun is not available on the runtime controller."
    try:
        resume()
        return True, None
    except Exception as exc:
        return False, str(exc)


def resume_run_after_ack(
    runtime: Any,
    *,
    options: RunControlOptions | None = None,
    ack_source: OperatorAckSource | None = None,
    prompt_index: int | None = None,
    prompt_type: str = "",
) -> ResumeRunResult:
    """Call ``ResumeRun`` only after an external operator-ack signal (api-v2-085).

    Manual resume remains the default: ``resume_run_after_prompts`` is false unless
    explicitly enabled for semi-automated Gate 27 / live verification runs.
    """
    opts = options or RunControlOptions()
    if not opts.resume_run_after_prompts:
        return ResumeRunResult(
            attempted=False,
            resumed=False,
            ack_received=False,
            reason="disabled",
            prompt_index=prompt_index,
            prompt_type=prompt_type,
        )
    if runtime is None:
        return ResumeRunResult(
            attempted=True,
            resumed=False,
            ack_received=False,
            reason="no_runtime",
            prompt_index=prompt_index,
            prompt_type=prompt_type,
        )
    source = ack_source or operator_ack_source_from_options(opts)
    ack_received = source.wait_for_ack(
        opts.resume_ack_timeout_seconds,
        poll_interval_seconds=opts.resume_poll_interval_seconds,
    )
    if not ack_received:
        return ResumeRunResult(
            attempted=True,
            resumed=False,
            ack_received=False,
            reason="ack_timeout",
            prompt_index=prompt_index,
            prompt_type=prompt_type,
        )
    resumed, error = call_resume_run(runtime)
    return ResumeRunResult(
        attempted=True,
        resumed=resumed,
        ack_received=True,
        reason="resumed" if resumed else "resume_failed",
        prompt_index=prompt_index,
        prompt_type=prompt_type,
        error=error or "",
    )


@dataclass
class SemiAutomatedResumeMonitor:
    """Listen for UserPrompt/pause events; resume only after external ack (api-v2-085)."""

    runtime: Any | None = None
    options: RunControlOptions = field(default_factory=RunControlOptions)
    ack_source: OperatorAckSource | None = None
    get_state: Callable[[], Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def on_mode_changed(self, old: Any, new: Any) -> ResumeRunResult | None:
        if not self.options.resume_run_after_prompts:
            return None
        new_label = format_state(new, get_state_as_string=self.get_state)
        parsed = parse_state_machine_states(new)
        if parsed != StateMachineStates.PAUSED and new_label not in _PAUSED_STATE_LABELS:
            return None
        return self._record(
            resume_run_after_ack(
                self.runtime,
                options=self.options,
                ack_source=self.ack_source,
                prompt_type="pause",
            )
        )

    def after_user_prompt_command(
        self,
        *,
        command_index: int,
        command_type: str,
    ) -> ResumeRunResult | None:
        if not self.options.resume_run_after_prompts or not is_user_prompt_type_name(command_type):
            return None
        return self._record(
            resume_run_after_ack(
                self.runtime,
                options=self.options,
                ack_source=self.ack_source,
                prompt_index=command_index,
                prompt_type=command_type,
            )
        )

    def _record(self, result: ResumeRunResult) -> ResumeRunResult:
        self.events.append(result.as_dict())
        return result


@dataclass(frozen=True)
class CloseMethodResult:
    """Recorded ``IRuntimeController.CloseMethod()`` teardown (api-v2-022)."""

    called: bool
    skipped: bool
    skip_reason: str = ""
    fluent_status: str = ""
    is_ready: bool = False
    runtime_available: bool = True
    method_prepared: bool = False
    stop_method_called: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "called": self.called,
            "skipped": self.skipped,
            "fluent_status": self.fluent_status,
            "is_ready": self.is_ready,
            "runtime_available": self.runtime_available,
            "method_prepared": self.method_prepared,
            "stop_method_called": self.stop_method_called,
        }
        if self.skip_reason:
            payload["skip_reason"] = self.skip_reason
        return payload


def _runtime_available(runtime: Any) -> bool:
    getter = getattr(runtime, "runtime_is_available", None)
    if callable(getter):
        return bool(getter())
    return True


def _runtime_is_ready(runtime: Any) -> bool:
    ready = getattr(runtime, "is_ready", None) or getattr(runtime, "IsReady", None)
    if callable(ready):
        return bool(ready())
    return True


def _runtime_fluent_status(runtime: Any) -> str:
    get_status = getattr(runtime, "get_fluent_status", None)
    if callable(get_status):
        return str(get_status() or "")
    status = getattr(runtime, "GetFluentStatus", None)
    if callable(status):
        raw = status()
        get_label = getattr(runtime, "GetStateAsString", None)
        if callable(get_label):
            rendered = get_label(raw)
            if rendered is not None:
                return str(rendered)
        return str(raw or "")
    return ""


def _status_indicates_open_method(fluent_status: str) -> bool:
    parsed = parse_state_machine_states(fluent_status)
    if isinstance(parsed, StateMachineStates):
        label = parsed.value
    elif parsed:
        label = str(parsed)
    else:
        label = format_state(fluent_status)
    compact = re.sub(r"[^a-z0-9]", "", str(label or "").casefold())
    return compact in _OPEN_METHOD_STATE_KEYS


def close_method_guarded(
    runtime: Any,
    *,
    method_prepared: bool = False,
    stop_method_called: bool = False,
    progress_tracker: Any | None = None,
) -> CloseMethodResult:
    """Call ``CloseMethod`` only when ``IsReady``/``GetFluentStatus`` allow it (api-v2-022)."""
    if runtime is None:
        return CloseMethodResult(
            called=False,
            skipped=True,
            skip_reason="no_runtime",
            method_prepared=method_prepared,
            stop_method_called=stop_method_called,
        )

    fluent_status = _runtime_fluent_status(runtime)
    is_ready = _runtime_is_ready(runtime)
    runtime_available = _runtime_available(runtime)
    base = CloseMethodResult(
        called=False,
        skipped=True,
        fluent_status=fluent_status,
        is_ready=is_ready,
        runtime_available=runtime_available,
        method_prepared=method_prepared,
        stop_method_called=stop_method_called,
    )

    if not runtime_available:
        return replace(base, skip_reason="runtime_not_available")
    if not is_ready:
        return replace(base, skip_reason="not_ready")
    if not method_prepared and not _status_indicates_open_method(fluent_status):
        return replace(base, skip_reason="no_open_method")

    close = getattr(runtime, "CloseMethod", None) or getattr(runtime, "close_method", None)
    if close is None:
        return replace(base, skip_reason="close_method_unavailable")
    try:
        close()
    except Exception:
        return replace(base, skip_reason="close_method_failed")
    return CloseMethodResult(
        called=True,
        skipped=False,
        fluent_status=fluent_status,
        is_ready=is_ready,
        runtime_available=runtime_available,
        method_prepared=method_prepared,
        stop_method_called=stop_method_called,
    )


def _call_stop(runtime: Any) -> bool:
    stop = getattr(runtime, "StopMethod", None) or getattr(runtime, "stop_method", None)
    if stop is None:
        return False
    try:
        stop()
        return True
    except Exception:
        return False


class MethodTeardown:
    """Always ``StopMethod`` before guarded ``CloseMethod`` in provider finally blocks."""

    def __init__(
        self,
        runtime: Any,
        *,
        close_method: bool = True,
        method_prepared: bool = False,
        options: RunControlOptions | None = None,
        close_progress_tracker: Any | None = None,
        on_stop: Callable[[bool], None] | None = None,
        on_close: Callable[[CloseMethodResult], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.close_method = close_method
        self.method_prepared = method_prepared
        self.options = options or RunControlOptions()
        self.close_progress_tracker = close_progress_tracker
        self.on_stop = on_stop
        self.on_close = on_close
        self.stopped = False
        self.closed = False
        self.close_result: CloseMethodResult | None = None

    def __enter__(self) -> "MethodTeardown":
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> bool:
        self.run()
        return False

    def run(self) -> None:
        if self.runtime is None:
            return
        if self.options.stop_before_close:
            self.stopped = _call_stop(self.runtime)
            if self.on_stop is not None:
                self.on_stop(self.stopped)
        if self.close_method:
            self.close_result = close_method_guarded(
                self.runtime,
                method_prepared=self.method_prepared,
                stop_method_called=self.stopped,
                progress_tracker=self.close_progress_tracker,
            )
            self.closed = self.close_result.called
            if self.on_close is not None:
                self.on_close(self.close_result)
