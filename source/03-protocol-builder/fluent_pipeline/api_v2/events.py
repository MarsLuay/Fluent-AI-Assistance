"""Channel/ready/deck event helpers (api-v2-068..070, api-v2-074/075)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "DeckCheckAsyncPolicy",
    "ExecutionChannelTracker",
    "ReadyModeWaiter",
    "wait_deck_check_end_invoke",
]


@dataclass
class ExecutionChannelTracker:
    """Track ``ChannelOpens`` / ``ChannelCloses`` (api-v2-068/069)."""

    open_channels: list[Any] = field(default_factory=list)
    closed_channels: list[Any] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    def on_channel_opens(self, channel: Any) -> None:
        self.open_channels.append(channel)
        self.messages.append(f"channel_open:{channel!r}")

    def on_channel_closes(self, channel: Any) -> None:
        self.closed_channels.append(channel)
        self.messages.append(f"channel_close:{channel!r}")

    @property
    def has_open_channel(self) -> bool:
        return len(self.open_channels) > len(self.closed_channels)

    def wait_until_closed(
        self,
        *,
        timeout_seconds: float = 30.0,
        poll: Callable[[], bool] | None = None,
    ) -> bool:
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            if poll is not None and poll():
                return True
            if not self.has_open_channel:
                return True
            time.sleep(0.05)
        return not self.has_open_channel


@dataclass
class ReadyModeWaiter:
    """Event-driven ready signal via ``EnterReadyMode`` (api-v2-070)."""

    ready: bool = False
    entered_at: float | None = None

    def on_enter_ready_mode(self) -> None:
        self.ready = True
        self.entered_at = time.monotonic()

    def wait(
        self,
        *,
        timeout_seconds: float = 180.0,
        is_ready: Callable[[], bool] | None = None,
    ) -> tuple[bool, str]:
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            if self.ready:
                return True, "EnterReadyMode"
            if is_ready is not None:
                try:
                    if is_ready():
                        return True, "IsReady"
                except Exception:
                    pass
            time.sleep(0.05)
        return False, "timeout"


@dataclass(frozen=True)
class DeckCheckAsyncPolicy:
    """Default headless policy for deck-check delegates (api-v2-074)."""

    use_begin_invoke: bool = False
    end_invoke_timeout_seconds: float = 30.0


def wait_deck_check_end_invoke(
    end_invoke: Callable[[Any], None] | None,
    async_result: Any,
    *,
    timeout_seconds: float = 30.0,
) -> tuple[bool, str]:
    """Bounded ``EndInvoke`` when BeginInvoke was used (api-v2-075)."""
    if end_invoke is None or async_result is None:
        return True, "skipped"

    deadline = time.monotonic() + max(0.1, timeout_seconds)
    error: str = ""
    while time.monotonic() < deadline:
        try:
            if getattr(async_result, "IsCompleted", False):
                end_invoke(async_result)
                return True, "completed"
        except Exception as exc:
            error = str(exc)
            return False, error
        time.sleep(0.05)

    return False, error or "EndInvoke timed out"
