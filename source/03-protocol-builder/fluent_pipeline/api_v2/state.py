"""HelperAPI.ParseStateMachineStates offline mirror (api-v2-019).

Parses ModeChanged / Error event state strings, legacy SiLA ``state=`` text, and
external provider JSON into typed :class:`StateMachineStates` for Gate 27 wait
predicates and human-readable runtime-report state fields.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any, Mapping, MutableMapping

from .types import StateMachineStates

# Best-effort offline mirror of VisionX ``StateMachineStates`` integer values.
_INT_TO_STATE: dict[int, StateMachineStates] = {
    0: StateMachineStates.UNKNOWN,
    1: StateMachineStates.EDIT_MODE,
    2: StateMachineStates.READY,
    3: StateMachineStates.RUNNING,
    4: StateMachineStates.PAUSED,
    5: StateMachineStates.ERROR,
}

# Aliases for GetStateAsString labels, legacy SiLA text, and ModeChanged payloads.
_STRING_ALIASES: dict[str, StateMachineStates] = {
    "unknown": StateMachineStates.UNKNOWN,
    "startup": StateMachineStates.STARTUP,
    "edit": StateMachineStates.EDIT_MODE,
    "editmode": StateMachineStates.EDIT_MODE,
    "ready": StateMachineStates.READY,
    "readymode": StateMachineStates.READY,
    "run": StateMachineStates.RUNNING,
    "running": StateMachineStates.RUNNING,
    "runmode": StateMachineStates.RUNNING,
    "pause": StateMachineStates.PAUSED,
    "paused": StateMachineStates.PAUSED,
    "pausemode": StateMachineStates.PAUSED,
    "error": StateMachineStates.ERROR,
    "errormode": StateMachineStates.ERROR,
}

_STATE_PREFIX_RE = re.compile(r"^state\s*=\s*", re.IGNORECASE)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def format_state_machine_state(state: StateMachineStates | Any) -> str:
    """Render a typed state as the stable human-readable label used in reports."""
    if isinstance(state, StateMachineStates):
        return state.value
    if isinstance(state, Enum):
        name = str(getattr(state, "name", "") or "")
        if name in StateMachineStates.__members__:
            return StateMachineStates[name].value
        return str(state)
    parsed = parse_state_machine_states(state)
    return parsed.value


def parse_state_machine_states(
    value: Any,
    *,
    helper_api: Any | None = None,
) -> StateMachineStates:
    """Parse arbitrary runtime state text into ``StateMachineStates``.

    Mirrors ``HelperAPI.ParseStateMachineStates(string)`` when ``helper_api`` is
    supplied (native VisionX bridge). Offline callers pass legacy SiLA strings,
    integer enum values, or external JSON ``state`` fields.
    """
    if isinstance(value, StateMachineStates):
        return value

    if helper_api is not None:
        text = str(value or "").strip()
        if text:
            try:
                native = helper_api.ParseStateMachineStates(text)
                return parse_state_machine_states(native)
            except Exception:
                pass

    if value is None:
        return StateMachineStates.UNKNOWN

    if isinstance(value, int):
        return _INT_TO_STATE.get(value, StateMachineStates.UNKNOWN)

    text = str(value).strip()
    if not text:
        return StateMachineStates.UNKNOWN

    text = _STATE_PREFIX_RE.sub("", text)

    if text.isdigit():
        return _INT_TO_STATE.get(int(text), StateMachineStates.UNKNOWN)

    for member in StateMachineStates:
        if text.casefold() == member.value.casefold() or text.casefold() == member.name.casefold():
            return member

    compact = _compact(text)
    if compact in _STRING_ALIASES:
        return _STRING_ALIASES[compact]

    if compact.endswith("mode"):
        stem = compact[: -len("mode")]
        if stem in _STRING_ALIASES:
            return _STRING_ALIASES[stem]

    return StateMachineStates.UNKNOWN


def format_state(value: Any, *, get_state_as_string: Any | None = None) -> str:
    """Normalize runtime state for runtime reports (pairs with api-v2-063)."""
    if get_state_as_string is not None:
        try:
            rendered = get_state_as_string(value)
            if rendered is not None and str(rendered).strip():
                return str(rendered).strip()
        except Exception:
            pass
    return format_state_machine_state(parse_state_machine_states(value))


def try_native_parse_state_machine_states(text: str) -> StateMachineStates | None:
    """Optional pythonnet bridge to ``HelperAPI.ParseStateMachineStates``."""
    try:
        import clr  # type: ignore[import-not-found]

        clr.AddReference("Tecan.VisionX.API.V2")
        from Tecan.VisionX.API.V2.HelperClasses import HelperAPI  # type: ignore[import-not-found]

        return parse_state_machine_states(HelperAPI.ParseStateMachineStates(str(text)))
    except Exception:
        return None


def enrich_context_check_state(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Add typed ``state_machine_states`` metadata to a runtime-report payload."""
    report = dict(payload)
    raw_state = report.get("state")
    parsed = parse_state_machine_states(raw_state)
    if raw_state is not None and str(raw_state).strip():
        report["state"] = format_state_machine_state(parsed)
    details: MutableMapping[str, Any] = dict(report.get("details") or {})
    details["state_machine_states"] = parsed.name
    details["state_machine_states_value"] = parsed.value
    report["details"] = dict(details)
    return report
