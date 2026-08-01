"""VisionX API V2 execution-channel abort and runtime teardown (api-v2-009).

Gate 27 stepped runtime checks can hang on UserPrompt, CommonErrorDialog, or an
overall timeout. This module centralizes ``IExecutionChannel.AbortExecution``
before ``StopMethod`` / ``CloseMethod`` and records abort metadata in
runtime-report JSON so batch ``generate`` runs do not leave
FluentControl in a hung execution state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping, Protocol


ABORT_REASON_TIMEOUT = "timeout"
ABORT_REASON_EXTERNAL_TIMEOUT = "external_timeout"
ABORT_REASON_BLOCKED_USER_PROMPT = "blocked_user_prompt"
ABORT_REASON_COMMON_ERROR_DIALOG = "common_error_dialog"
ABORT_REASON_BLOCKED_DIALOG = "blocked_dialog"
ABORT_REASON_RUNTIME_ERROR = "runtime_error"

EXECUTION_ABORT_DETAIL_KEY = "execution_abort"


class ExecutionChannelLike(Protocol):
    def AbortExecution(self) -> None:
        ...

    def FinishExecution(self) -> None:
        ...

    def Dispose(self) -> None:
        ...


class RuntimeControllerLike(Protocol):
    def StopMethod(self) -> None:
        ...

    def CloseMethod(self) -> None:
        ...


@dataclass(frozen=True)
class ExecutionAbortContext:
    reason: str
    message: str
    last_command_index: int | None = None
    last_command_type: str | None = None
    last_step_id: str | None = None
    abort_execution_called: bool = False
    stop_method_called: bool = False
    close_method_called: bool = False
    channel_disposed: bool = False
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "reason": self.reason,
            "message": self.message,
            "abort_execution_called": bool(self.abort_execution_called),
            "stop_method_called": bool(self.stop_method_called),
            "close_method_called": bool(self.close_method_called),
            "channel_disposed": bool(self.channel_disposed),
        }
        if self.last_command_index is not None:
            payload["last_command_index"] = self.last_command_index
        if self.last_command_type:
            payload["last_command_type"] = self.last_command_type
        if self.last_step_id:
            payload["last_step_id"] = self.last_step_id
        if self.errors:
            payload["errors"] = list(self.errors)
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> ExecutionAbortContext | None:
        if not isinstance(value, Mapping):
            return None
        reason = str(value.get("reason") or "").strip()
        message = str(value.get("message") or "").strip()
        if not reason and not message:
            return None
        last_index = value.get("last_command_index")
        return cls(
            reason=reason or ABORT_REASON_RUNTIME_ERROR,
            message=message or reason or "Execution aborted.",
            last_command_index=int(last_index) if last_index is not None else None,
            last_command_type=str(value.get("last_command_type") or "") or None,
            last_step_id=str(value.get("last_step_id") or "") or None,
            abort_execution_called=bool(value.get("abort_execution_called")),
            stop_method_called=bool(value.get("stop_method_called")),
            close_method_called=bool(value.get("close_method_called")),
            channel_disposed=bool(value.get("channel_disposed")),
            errors=tuple(str(item) for item in (value.get("errors") or []) if str(item).strip()),
        )


class SteppedExecutionTracker:
    """Tracks the active stepped-runner command for abort metadata (api-v2-001)."""

    def __init__(self) -> None:
        self.last_command_index: int | None = None
        self.last_command_type: str = ""
        self.last_step_id: str = ""

    def begin_command(self, index: int, command_type: str, *, step_id: str = "") -> None:
        self.last_command_index = index
        self.last_command_type = str(command_type or "")
        self.last_step_id = str(step_id or "")

    def abort_context(self, reason: str, message: str) -> ExecutionAbortContext:
        return ExecutionAbortContext(
            reason=reason,
            message=message,
            last_command_index=self.last_command_index,
            last_command_type=self.last_command_type or None,
            last_step_id=self.last_step_id or None,
        )


def execution_abort_from_timeout(
    message: str,
    *,
    tracker: SteppedExecutionTracker | None = None,
    last_command_index: int | None = None,
    last_command_type: str | None = None,
    last_step_id: str | None = None,
) -> ExecutionAbortContext:
    return _abort_context(
        ABORT_REASON_TIMEOUT,
        message,
        tracker=tracker,
        last_command_index=last_command_index,
        last_command_type=last_command_type,
        last_step_id=last_step_id,
    )


def execution_abort_from_external_timeout(
    message: str,
    *,
    tracker: SteppedExecutionTracker | None = None,
    last_command_index: int | None = None,
) -> ExecutionAbortContext:
    return _abort_context(
        ABORT_REASON_EXTERNAL_TIMEOUT,
        message,
        tracker=tracker,
        last_command_index=last_command_index,
    )


def execution_abort_from_blocked_user_prompt(
    message: str,
    *,
    tracker: SteppedExecutionTracker | None = None,
    last_command_index: int | None = None,
    last_command_type: str | None = None,
    last_step_id: str | None = None,
) -> ExecutionAbortContext:
    return _abort_context(
        ABORT_REASON_BLOCKED_USER_PROMPT,
        message,
        tracker=tracker,
        last_command_index=last_command_index,
        last_command_type=last_command_type or "UserPrompt",
        last_step_id=last_step_id,
    )


def execution_abort_from_common_error_dialog(
    message: str,
    *,
    tracker: SteppedExecutionTracker | None = None,
    ced_info: Mapping[str, Any] | str | None = None,
    last_command_index: int | None = None,
) -> ExecutionAbortContext:
    detail = message
    if ced_info is not None:
        detail = f"{message} ({ced_info})"
    return _abort_context(
        ABORT_REASON_COMMON_ERROR_DIALOG,
        detail,
        tracker=tracker,
        last_command_index=last_command_index,
    )


def execution_abort_from_runtime_error(
    message: str,
    *,
    tracker: SteppedExecutionTracker | None = None,
    last_command_index: int | None = None,
    last_command_type: str | None = None,
    last_step_id: str | None = None,
) -> ExecutionAbortContext:
    return _abort_context(
        ABORT_REASON_RUNTIME_ERROR,
        message,
        tracker=tracker,
        last_command_index=last_command_index,
        last_command_type=last_command_type,
        last_step_id=last_step_id,
    )


def execution_abort_from_blocked_dialog(
    message: str,
    *,
    tracker: SteppedExecutionTracker | None = None,
) -> ExecutionAbortContext:
    return _abort_context(ABORT_REASON_BLOCKED_DIALOG, message, tracker=tracker)


def abort_execution_channel(channel: ExecutionChannelLike | None) -> tuple[bool, str | None]:
    """Invoke ``IExecutionChannel.AbortExecution`` when a channel is available."""
    if channel is None:
        return False, None
    try:
        channel.AbortExecution()
        return True, None
    except Exception as exc:
        return False, str(exc)


def perform_runtime_teardown(
    *,
    channel: ExecutionChannelLike | None,
    runtime: RuntimeControllerLike | None,
    abort_context: ExecutionAbortContext,
    close_method: bool = True,
    dispose_channel: bool = True,
) -> ExecutionAbortContext:
    """AbortExecution, then StopMethod, then CloseMethod (api-v2-009 ordering)."""
    errors: list[str] = list(abort_context.errors)
    abort_called, abort_error = abort_execution_channel(channel)
    if abort_error:
        errors.append(f"AbortExecution failed: {abort_error}")

    stop_called = False
    if runtime is not None:
        try:
            runtime.StopMethod()
            stop_called = True
        except Exception as exc:
            errors.append(f"StopMethod failed: {exc}")

    close_called = False
    if close_method and runtime is not None:
        try:
            runtime.CloseMethod()
            close_called = True
        except Exception as exc:
            errors.append(f"CloseMethod failed: {exc}")

    channel_disposed = False
    if dispose_channel and channel is not None:
        try:
            channel.Dispose()
            channel_disposed = True
        except Exception as exc:
            errors.append(f"ExecutionChannel.Dispose failed: {exc}")

    return replace(
        abort_context,
        abort_execution_called=abort_called,
        stop_method_called=stop_called,
        close_method_called=close_called,
        channel_disposed=channel_disposed,
        errors=tuple(_compact_errors(errors)),
    )


def merge_execution_abort_into_report(
    report: dict[str, Any],
    abort_context: ExecutionAbortContext | None,
) -> dict[str, Any]:
    if abort_context is None:
        return report
    merged = dict(report)
    details = dict(merged.get("details") or {})
    details[EXECUTION_ABORT_DETAIL_KEY] = abort_context.as_dict()
    merged["details"] = details
    return merged


def execution_abort_from_report(report: Mapping[str, Any]) -> ExecutionAbortContext | None:
    details = report.get("details")
    if isinstance(details, Mapping):
        nested = ExecutionAbortContext.from_mapping(details.get(EXECUTION_ABORT_DETAIL_KEY))
        if nested is not None:
            return nested
    external = report.get("external_json")
    if isinstance(external, Mapping):
        return ExecutionAbortContext.from_mapping(external.get(EXECUTION_ABORT_DETAIL_KEY))
    return ExecutionAbortContext.from_mapping(report.get(EXECUTION_ABORT_DETAIL_KEY))


def render_execution_abort_markdown(abort: Mapping[str, Any]) -> list[str]:
    lines = ["## Execution abort", ""]
    reason = str(abort.get("reason") or "")
    if reason:
        lines.append(f"- Reason: `{reason}`")
    message = str(abort.get("message") or "")
    if message:
        lines.append(f"- Message: {message}")
    if abort.get("last_command_index") is not None:
        lines.append(f"- Last command index: `{abort.get('last_command_index')}`")
    command_type = abort.get("last_command_type")
    if command_type:
        lines.append(f"- Last command type: `{command_type}`")
    step_id = abort.get("last_step_id")
    if step_id:
        lines.append(f"- Last step id: `{step_id}`")
    for key, label in (
        ("abort_execution_called", "AbortExecution called"),
        ("stop_method_called", "StopMethod called"),
        ("close_method_called", "CloseMethod called"),
        ("channel_disposed", "ExecutionChannel disposed"),
    ):
        if key in abort:
            lines.append(f"- {label}: `{bool(abort.get(key))}`")
    errors = [str(value) for value in (abort.get("errors") or []) if str(value).strip()]
    if errors:
        lines.extend(["", "### Teardown errors", ""])
        lines.extend(f"- {value}" for value in errors[:20])
    lines.append("")
    return lines


def _abort_context(
    reason: str,
    message: str,
    *,
    tracker: SteppedExecutionTracker | None,
    last_command_index: int | None = None,
    last_command_type: str | None = None,
    last_step_id: str | None = None,
) -> ExecutionAbortContext:
    if tracker is not None:
        return tracker.abort_context(reason, message)
    return ExecutionAbortContext(
        reason=reason,
        message=message,
        last_command_index=last_command_index,
        last_command_type=last_command_type,
        last_step_id=last_step_id,
    )


def _compact_errors(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out[:25]
