"""Shared types for the offline VisionX API V2 scaffold."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class StateMachineStates(str, Enum):
    """Subset of FluentControl StateMachineStates used by Gate 27 waits."""

    UNKNOWN = "Unknown"
    STARTUP = "Startup"
    EDIT_MODE = "EditMode"
    READY = "Ready"
    RUNNING = "Running"
    PAUSED = "Paused"
    ERROR = "Error"


@dataclass(frozen=True)
class CedButton:
    label: str
    is_safe_default: bool = False


@dataclass(frozen=True)
class ICedInfo:
    """Structured Common Error Dialog payload (api-v2-035/042)."""

    error_id: str
    message: str
    title: str = ""
    buttons: tuple[CedButton, ...] = ()


@dataclass
class CedHandlerResult:
    button_index: int
    dismissed: bool
    fail_gate: bool = False
    log_message: str = ""


@dataclass
class VariableSeed:
    name: str
    value: str


@dataclass
class SteppedCommand:
    """Minimal stepped-runner command shape for offline tracing and validation tests."""

    type_name: str
    index: int = 0
    group: str = ""
    payload_xml: str = ""
    execute_xml: str = ""
    operation: str | None = None
    api_v2_type: str = ""
    source: str = ""
    ir_step_id: str = ""


def variable_seed_fields(item: VariableSeed | Mapping[str, Any]) -> tuple[str, str]:
    """Return ``(name, value)`` from a seed object or mapping.

    Duck-typed so duplicate ``VariableSeed`` class objects from alternate
    import paths still match during ``unittest discover``.
    """
    if isinstance(item, Mapping):
        name = str(item.get("name") or "")
        raw_value = item.get("value")
        value = str(raw_value if raw_value is not None else "")
        return name, value
    name = str(getattr(item, "name", "") or "")
    raw_value = getattr(item, "value", None)
    value = str(raw_value if raw_value is not None else "")
    return name, value


@dataclass
class PrepareMethodResult:
    ok: bool
    state: StateMachineStates
    last_error: str = ""
    messages: tuple[str, ...] = ()
    runtime_errors: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class RunMethodResult:
    ok: bool
    state: StateMachineStates
    last_error: str = ""
    runtime_errors: tuple[str, ...] = ()
    progress_last: int = 0
    details: Mapping[str, Any] = field(default_factory=dict)


class ApiV2ValidationError(ValueError):
    """Raised when an offline Validate() check fails."""

    def __init__(self, message: str, *, field: str = "", command: str = ""):
        super().__init__(message)
        self.field = field
        self.command = command
