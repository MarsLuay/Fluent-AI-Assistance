"""Runtime controller protocol and mock (api-v2-031..034)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from .runtime_events import RuntimeEventCollector
from .state import parse_state_machine_states
from .types import PrepareMethodResult, RunMethodResult, StateMachineStates, VariableSeed, variable_seed_fields


class IRuntimeController(Protocol):
    def login_user(self, username: str, password: str) -> bool:
        ...

    def prepare_method(self, method: str) -> bool:
        ...

    def run_method(self) -> bool:
        ...

    def set_variable_value(self, name: str, value: str) -> bool:
        ...

    def close_query_at_startup_dialog(self, accept_values: bool = True) -> bool:
        ...

    def get_fluent_status(self) -> str | int:
        ...

    def is_ready(self) -> bool:
        ...

    def close_method(self) -> None:
        ...

    def stop_method(self) -> None:
        ...


@dataclass
class MockRuntimeController:
    """Deterministic runtime for unit tests and offline scaffolding."""

    runnable_methods: Sequence[str] = field(default_factory=tuple)
    variables: dict[str, str] = field(default_factory=dict)
    logged_in_user: str = ""
    prepared_method: str = ""
    run_started: bool = False
    last_error: str = ""
    state: StateMachineStates = StateMachineStates.EDIT_MODE
    events: RuntimeEventCollector = field(default_factory=RuntimeEventCollector)
    login_should_succeed: bool = True
    prepare_should_succeed: bool = True
    run_should_succeed: bool = True
    validate_user_should_succeed: bool = True
    init_progress: int = 100
    operation_progress: int = 100
    resolved_expressions: dict[str, str] = field(default_factory=dict)

    def validate_user(self, username: str, password: str) -> bool:
        user = str(username or "").strip()
        if not user:
            return False
        if not self.validate_user_should_succeed:
            return False
        expected = self.resolved_expressions.get(f"user:{user}")
        if expected is not None:
            return str(password or "") == expected
        return True

    def ValidateUser(self, username: str, password: str) -> bool:
        return self.validate_user(username, password)

    def get_progress(self) -> int:
        return max(0, min(100, int(self.operation_progress)))

    def GetProgress(self) -> int:
        return self.get_progress()

    def get_progress_initialization(self) -> int:
        return max(0, min(100, int(self.init_progress)))

    def GetProgressInitialization(self) -> int:
        return self.get_progress_initialization()

    def resolve_expression(self, expression: str) -> str:
        key = str(expression or "").strip()
        if key in self.resolved_expressions:
            return self.resolved_expressions[key]
        if key in self.variables:
            return self.variables[key]
        return "0"

    def ResolveExpression(self, expression: str) -> str:
        return self.resolve_expression(expression)

    def login_user(self, username: str, password: str) -> bool:
        if not username:
            self.last_error = "LoginUser requires a username."
            self.events.on_error(self.last_error)
            return False
        if not self.login_should_succeed:
            self.last_error = f"LoginUser failed for {username!r}."
            self.events.on_error(self.last_error)
            return False
        self.logged_in_user = username
        self.events.on_mode_changed(self.state, StateMachineStates.EDIT_MODE)
        self.state = StateMachineStates.EDIT_MODE
        return True

    def prepare_method(self, method: str) -> bool:
        if method and self.runnable_methods and method not in self.runnable_methods:
            self.last_error = f"Method {method!r} was not reported by GetAllRunnableMethods()."
            self.events.on_error(self.last_error)
            self.state = StateMachineStates.ERROR
            return False
        if not self.prepare_should_succeed:
            self.last_error = f"PrepareMethod failed for {method!r}."
            self.events.on_error(self.last_error)
            self.state = StateMachineStates.ERROR
            return False
        self.prepared_method = method
        self.events.on_mode_changed(self.state, StateMachineStates.READY)
        self.state = StateMachineStates.READY
        self.events.on_enter_ready_mode()
        self.operation_progress = 100
        return True

    def run_method(self) -> bool:
        if not self.prepared_method:
            self.last_error = "RunMethod called before PrepareMethod."
            self.events.on_error(self.last_error)
            return False
        if not self.run_should_succeed:
            self.last_error = "RunMethod failed in simulation mode."
            self.events.on_error(self.last_error)
            self.state = StateMachineStates.ERROR
            return False
        self.run_started = True
        self.events.on_mode_changed(self.state, StateMachineStates.RUNNING)
        self.state = StateMachineStates.RUNNING
        self.events.on_progress_changed(100)
        self.events.on_mode_changed(self.state, StateMachineStates.READY)
        self.state = StateMachineStates.READY
        return True

    def set_variable_value(self, name: str, value: str) -> bool:
        key = str(name or "").strip()
        if not key:
            self.last_error = "SetVariableValue requires a variable name."
            self.events.on_error(self.last_error)
            return False
        self.variables[key] = str(value)
        return True

    def get_variable_value(self, name: str) -> str | None:
        key = str(name or "").strip()
        if not key:
            return None
        if key not in self.variables:
            return None
        return self.variables[key]

    def close_query_at_startup_dialog(self, accept_values: bool = True) -> bool:
        return bool(accept_values)

    def get_fluent_status(self) -> str:
        return self.state.value

    def is_ready(self) -> bool:
        return self.state in {StateMachineStates.EDIT_MODE, StateMachineStates.READY}

    def close_method(self) -> None:
        self.prepared_method = ""
        self.run_started = False
        self.events.on_mode_changed(self.state, StateMachineStates.EDIT_MODE)
        self.state = StateMachineStates.EDIT_MODE

    def stop_method(self) -> None:
        self.run_started = False
        self.events.on_mode_changed(self.state, StateMachineStates.EDIT_MODE)
        self.state = StateMachineStates.EDIT_MODE

    def get_all_runnable_methods(self) -> list[str]:
        return list(self.runnable_methods)


def seed_simulation_values(
    runtime: IRuntimeController,
    seeds: Sequence[VariableSeed | Mapping[str, Any]],
    *,
    events: RuntimeEventCollector | None = None,
) -> tuple[bool, list[str]]:
    """Apply api-v2-034 SetVariableValue seeds before PrepareMethod."""
    errors: list[str] = []
    for item in seeds:
        name, value = variable_seed_fields(item)
        if not runtime.set_variable_value(name, value):
            message = f"SetVariableValue failed for {name!r}."
            errors.append(message)
            if events is not None:
                events.on_error(message)
    return (not errors, errors)


def wait_for_state(
    runtime: IRuntimeController,
    target: StateMachineStates,
    *,
    timeout_seconds: float = 30.0,
    poll_interval: float = 0.05,
    events: RuntimeEventCollector | None = None,
) -> bool:
    deadline = time.time() + max(0.1, timeout_seconds)
    while time.time() < deadline:
        current = parse_state_machine_states(runtime.get_fluent_status())
        if events is not None:
            events.current_state = current
        if current == target:
            return True
        if current == StateMachineStates.ERROR:
            return False
        time.sleep(poll_interval)
    return False


def login_user_or_fail(
    runtime: IRuntimeController,
    username: str | None,
    password: str | None,
    *,
    events: RuntimeEventCollector,
) -> bool:
    if not username:
        return True
    ok = runtime.login_user(str(username), str(password or ""))
    if not ok:
        events.on_error(getattr(runtime, "last_error", "") or f"LoginUser failed for {username!r}.")
    return ok


def prepare_method_checked(
    runtime: IRuntimeController,
    method: str,
    *,
    events: RuntimeEventCollector,
    close_query_dialog: bool = True,
) -> PrepareMethodResult:
    if not wait_for_state(runtime, StateMachineStates.EDIT_MODE, events=events):
        message = "Timed out waiting for EditMode before PrepareMethod."
        events.on_error(message)
        return PrepareMethodResult(
            ok=False,
            state=events.current_state,
            last_error=message,
            runtime_errors=tuple(events.runtime_errors),
            messages=tuple(events.messages),
        )
    ok = runtime.prepare_method(method)
    if close_query_dialog:
        runtime.close_query_at_startup_dialog(True)
    state = parse_state_machine_states(runtime.get_fluent_status())
    events.current_state = state
    last_error = str(getattr(runtime, "last_error", "") or "")
    if last_error:
        events.on_error(last_error)
    return PrepareMethodResult(
        ok=ok and not events.runtime_errors,
        state=state,
        last_error=last_error,
        runtime_errors=tuple(events.runtime_errors),
        messages=tuple(events.messages),
        details={"prepared_method": method},
    )


def _sync_runtime_events(runtime: IRuntimeController, events: RuntimeEventCollector) -> None:
    peer = getattr(runtime, "events", None)
    if peer is None or peer is events:
        return
    for value in getattr(peer, "progress_values", []):
        if value not in events.progress_values:
            events.progress_values.append(int(value))
            events.messages.append(f"ProgressChanged: {int(value)}")
    for message in getattr(peer, "messages", []):
        text = str(message).strip()
        if text and text not in events.messages:
            events.messages.append(text)
    for error in getattr(peer, "runtime_errors", []):
        text = str(error).strip()
        if text and text not in events.runtime_errors:
            events.on_error(text)
    peer_state = getattr(peer, "current_state", None)
    if peer_state is not None:
        events.current_state = peer_state


def run_method_checked(
    runtime: IRuntimeController,
    *,
    events: RuntimeEventCollector,
    timeout_seconds: float = 180.0,
) -> RunMethodResult:
    started = time.time()
    ok = runtime.run_method()
    _sync_runtime_events(runtime, events)
    while time.time() - started < timeout_seconds:
        state = parse_state_machine_states(runtime.get_fluent_status())
        events.current_state = state
        if state in {StateMachineStates.READY, StateMachineStates.EDIT_MODE} and ok:
            break
        if state == StateMachineStates.ERROR:
            ok = False
            break
        time.sleep(0.05)
    last_error = str(getattr(runtime, "last_error", "") or "")
    if last_error:
        events.on_error(last_error)
    _sync_runtime_events(runtime, events)
    return RunMethodResult(
        ok=ok and not events.runtime_errors,
        state=events.current_state if events.current_state is not None else state,
        last_error=last_error,
        runtime_errors=tuple(events.runtime_errors),
        progress_last=events.progress_values[-1] if events.progress_values else 0,
        details={
            "duration_seconds": round(time.time() - started, 3),
            "event_messages": tuple(events.messages[-50:]),
        },
    )


def try_import_native_runtime() -> tuple[Any | None, str]:
    """Optional pythonnet bridge; returns (controller, error_message)."""
    try:
        import clr  # type: ignore[import-not-found]
    except Exception as exc:
        return None, f"pythonnet is not importable: {exc}"
    try:
        clr.AddReference("Tecan.VisionX.API.V2")
    except Exception as exc:
        return None, f"Tecan.VisionX.API.V2 is not available: {exc}"
    return None, "Native VisionX API V2 bridge is reserved; use MockRuntimeController offline."
