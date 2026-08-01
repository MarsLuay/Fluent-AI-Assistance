"""Gate 27 runtime event capture (api-v2-035..037, api-v2-042)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .ced import handle_common_error_dialog
from .events import ExecutionChannelTracker, ReadyModeWaiter
from .state import format_state_machine_state, parse_state_machine_states
from .types import CedHandlerResult, ICedInfo, StateMachineStates


@dataclass
class RuntimeEventCollector:
    """Captures Error, ModeChanged, and CommonErrorDialog during prepare/run."""

    runtime_errors: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    mode_transitions: list[tuple[StateMachineStates, StateMachineStates]] = field(default_factory=list)
    ced_events: list[ICedInfo] = field(default_factory=list)
    progress_values: list[int] = field(default_factory=list)
    runtime_notifications: list[str] = field(default_factory=list)
    current_state: StateMachineStates = StateMachineStates.UNKNOWN
    channel_tracker: ExecutionChannelTracker = field(default_factory=ExecutionChannelTracker)
    ready_waiter: ReadyModeWaiter = field(default_factory=ReadyModeWaiter)
    on_progress: Callable[[int], None] | None = None

    def on_error(self, message: str) -> None:
        text = str(message or "").strip()
        if text:
            self.runtime_errors.append(text)

    def on_mode_changed(self, old: str | int, current: str | int) -> None:
        old_state = parse_state_machine_states(old)
        new_state = parse_state_machine_states(current)
        self.mode_transitions.append((old_state, new_state))
        self.current_state = new_state
        self.messages.append(
            f"ModeChanged: {format_state_machine_state(old_state)} -> {format_state_machine_state(new_state)}"
        )

    def on_common_error_dialog(self, ced_info: ICedInfo) -> CedHandlerResult:
        """``CEDNotification.Invoke`` / ``RuntimeControllerEvents.CommonErrorDialog`` handler."""
        self.ced_events.append(ced_info)
        result = handle_common_error_dialog(ced_info)
        if result.log_message:
            self.messages.append(result.log_message)
        if result.fail_gate:
            self.runtime_errors.append(result.log_message or ced_info.message)
        return result

    def on_progress_changed(self, value: int) -> None:
        clamped = max(0, min(100, int(value)))
        self.progress_values.append(clamped)
        if self.on_progress is not None:
            self.on_progress(clamped)

    def on_notify(self, message: str) -> None:
        """``FluentControlEvents.Notify(message)`` (api-v2-055)."""
        text = str(message or "").strip()
        if text:
            self.messages.append(f"Notify: {text}")

    def on_notification(self, message: str) -> None:
        """``Notification.Invoke(message)`` (api-v2-058)."""
        text = str(message or "").strip()
        if text:
            self.runtime_notifications.append(text)
            self.messages.append(f"Notification: {text}")

    def on_deck_check_discrepancy(
        self,
        description: str,
        camera_results: list[dict[str, Any]] | None = None,
    ) -> None:
        """``DeckCheckDiscrepancyDetected.Invoke`` (api-v2-047)."""
        detail = str(description or "").strip() or "Deck check discrepancy detected."
        self.runtime_errors.append(f"DeckCheckDiscrepancy: {detail}")
        if camera_results:
            self.messages.append(f"DeckCheck cameras: {len(camera_results)} result(s)")

    def on_enter_ready_mode(self) -> None:
        self.ready_waiter.on_enter_ready_mode()
        self.current_state = StateMachineStates.READY
        self.messages.append("EnterReadyMode")

    def on_channel_opens(self, channel: Any) -> None:
        self.channel_tracker.on_channel_opens(channel)

    def on_channel_closes(self, channel: Any) -> None:
        self.channel_tracker.on_channel_closes(channel)
