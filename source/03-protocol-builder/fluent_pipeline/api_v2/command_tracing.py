"""Offline stand-ins for VisionX API V2 ``ICommand.ToString()`` tracing."""

from __future__ import annotations

import re
from .. import xml_compat as ET
from typing import Any, Mapping, MutableMapping, Protocol, Sequence


def format_remove_labware_trace(*, labware: str) -> str:
    """api-v2-079: readable RemoveLabware label for ExecuteCommand traces."""
    label = str(labware or "").strip() or "?"
    return f"RemoveLabware(LabwareName={label!r})"


def format_set_location_trace(
    *,
    labware: str,
    location: str,
    site: int | str,
    rotation: int | str | None = None,
) -> str:
    """api-v2-086: readable SetLocation label for stepped runner / event-log traces."""
    rotation_text = "" if rotation in (None, "", 0, "0") else f", Rotation={rotation}"
    return (
        f"SetLocation(LabwareName={str(labware or '?')!r}, "
        f"Location={str(location or '?')!r}, Site={site}{rotation_text})"
    )


def format_subroutine_call_trace(
    *,
    subroutine: str,
    execution_mode: str | None = None,
    variable_mappings_start: str | None = None,
    variable_mappings_end: str | None = None,
) -> str:
    """api-v2-087: FC-native subroutine call label for subroutine_load_review traces."""
    from .command_summary import subroutine_call_summary

    return subroutine_call_summary(
        path=str(subroutine or "").strip() or "?",
        execution_mode=str(execution_mode or "JoinSubroutine"),
    )


def trace_execution_command(
    command_type: str,
    *,
    trace: str,
    ir_step_id: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a structured trace record for runtime-report / event-log payloads."""
    payload: dict[str, Any] = {
        "command_type": command_type,
        "trace": trace,
    }
    if ir_step_id:
        payload["ir_step_id"] = ir_step_id
    if extra:
        payload.update(dict(extra))
    return payload


def append_command_trace(
    details: MutableMapping[str, Any],
    trace_record: Mapping[str, Any],
) -> None:
    """Append a command trace into ``details['command_traces']`` (bounded list)."""
    traces = list(details.get("command_traces") or [])
    traces.append(dict(trace_record))
    details["command_traces"] = traces[-100:]


def stepped_command_trace(command: Any) -> tuple[str, dict[str, Any]]:
    """Return ``ICommand.ToString()``-style text for a stepped-runner command (api-v2-079)."""
    payload_xml = str(getattr(command, "payload_xml", "") or "").strip()
    if not payload_xml:
        return "", {}
    from .commands import RemoveLabware, command_from_xscr_object

    try:
        element = ET.fromstring(payload_xml)
    except ET.ParseError:
        return "", {}
    typed = command_from_xscr_object(element, command_id=str(getattr(command, "type_name", "") or ""))
    if typed is None or not hasattr(typed, "to_string"):
        return "", {}
    trace = str(typed.to_string())
    extra: dict[str, Any] = {}
    if isinstance(typed, RemoveLabware):
        extra["labware_name"] = typed.labware_name
    return trace, extra


class _SteppedCommandLike(Protocol):
    type_name: str
    operation: str | None
    payload_xml: str
    source: str
    ir_step_id: str
    api_v2_type: str


def set_location_trace_for_stepped_command(command: _SteppedCommandLike) -> str | None:
    """Return FC-native SetLocation ``ToString()`` label for deck placement steps (api-v2-086)."""
    from .commands import AddLabware, SetLocation, command_from_ir_step, command_from_xscr_object

    typed: SetLocation | AddLabware | None = None
    payload = str(getattr(command, "payload_xml", "") or "").strip()
    if payload:
        try:
            element = ET.fromstring(payload)
            typed = command_from_xscr_object(element, command_id=str(command.type_name or ""))
        except ET.ParseError:
            typed = None

    if typed is None and str(getattr(command, "source", "") or "") == "ir":
        operation = str(getattr(command, "operation", "") or "").strip()
        if operation in {"set_location", "add_labware", "manual_move"}:
            typed = command_from_ir_step(
                {
                    "operation": operation,
                    "command_id": command.type_name,
                    "target_labware": "",
                    "parameters": {},
                }
            )

    if isinstance(typed, SetLocation):
        return format_set_location_trace(
            labware=typed.labware,
            location=typed.location,
            site=typed.site,
            rotation=typed.rotation,
        )
    if isinstance(typed, AddLabware):
        return format_set_location_trace(
            labware=typed.labware_label,
            location=typed.location,
            site=typed.site,
            rotation=typed.rotation,
        )
    return None


def merge_set_location_traces_into_details(
    details: MutableMapping[str, Any],
    command_log: Sequence[Mapping[str, Any]] | None,
) -> None:
    """Aggregate SetLocation traces from stepped ``command_log`` (api-v2-086)."""
    if not command_log:
        return
    for entry in command_log:
        if not isinstance(entry, Mapping):
            continue
        trace = str(entry.get("trace") or "").strip()
        if not trace.startswith("SetLocation("):
            continue
        ir_step_id = str(entry.get("ir_step_id") or entry.get("index") or "").strip() or None
        append_command_trace(
            details,
            trace_execution_command("SetLocation", trace=trace, ir_step_id=ir_step_id),
        )


def merge_remove_labware_traces_into_details(
    details: MutableMapping[str, Any],
    command_log: Sequence[Mapping[str, Any]] | None,
) -> None:
    """Aggregate RemoveLabware traces from stepped ``command_log`` (api-v2-079)."""
    if not command_log:
        return
    for entry in command_log:
        if not isinstance(entry, Mapping):
            continue
        trace = str(entry.get("trace") or "").strip()
        if not trace.startswith("RemoveLabware("):
            continue
        ir_step_id = str(entry.get("ir_step_id") or entry.get("index") or "").strip() or None
        extra: dict[str, Any] = {}
        labware_name = str(entry.get("labware_name") or "").strip()
        if labware_name:
            extra["labware_name"] = labware_name
        append_command_trace(
            details,
            trace_execution_command("RemoveLabware", trace=trace, ir_step_id=ir_step_id, extra=extra or None),
        )


def command_trace_for_stepped(command: Any) -> str:
    """Best-effort ``ICommand.ToString()`` label for stepped runner / event-log (079/086)."""
    location_trace = set_location_trace_for_stepped_command(command)  # type: ignore[arg-type]
    if location_trace:
        return location_trace

    trace, _extra = stepped_command_trace(command)
    if trace:
        return trace

    command_type = str(
        getattr(command, "api_v2_type", None)
        or getattr(command, "command_type", None)
        or getattr(command, "type_name", None)
        or ""
    ).strip()
    payload = str(getattr(command, "payload_xml", None) or getattr(command, "execute_xml", None) or "")
    if not payload.strip():
        return command_type or "GenericCommand"

    try:
        root = ET.fromstring(f"<Root>{payload}</Root>" if not payload.lstrip().startswith("<") else payload)
        from .commands import command_from_xscr_object

        for element in root.iter():
            if element.tag.endswith("Object") or element.tag == "Object":
                typed = command_from_xscr_object(element, command_id=getattr(command, "type_name", None))
                to_string = getattr(typed, "to_string", None)
                if callable(to_string):
                    return str(to_string())
    except Exception:
        pass

    if "RemoveLabware" in command_type:
        return format_remove_labware_trace(labware=_xml_field(payload, "LabwareName", "Labware"))
    if "SetLocation" in command_type:
        return format_set_location_trace(
            labware=_xml_field(payload, "Labware"),
            location=_xml_field(payload, "Location"),
            site=_xml_field(payload, "Site", "1"),
            rotation=_xml_field(payload, "Rotation") or None,
        )
    return command_type or payload[:120]


def enrich_stepped_log_entry(log_entry: dict[str, Any], command: Any) -> None:
    """Attach ``trace`` / ``ir_step_id`` to a stepped-runner log row (079/086)."""
    if not log_entry.get("trace"):
        log_entry["trace"] = command_trace_for_stepped(command)
    if not log_entry.get("ir_step_id"):
        log_entry["ir_step_id"] = str(
            getattr(command, "ir_step_id", "") or f"step_{getattr(command, 'index', 0):03d}"
        )
    trace, extra = stepped_command_trace(command)
    if extra.get("labware_name"):
        log_entry.setdefault("labware_name", extra["labware_name"])


def _xml_field(payload: str, *names: str, default: str = "?") -> str:
    for name in names:
        match = re.search(rf"<{name}>([^<]*)</{name}>", payload, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return default
