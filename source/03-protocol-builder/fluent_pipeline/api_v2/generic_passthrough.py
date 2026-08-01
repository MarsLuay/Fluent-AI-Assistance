"""Gate 27 GenericCommand passthrough routing (api-v2-004).

Maps compiled XSCR statements that are ``approved_passthrough`` in
the packaged command registry (and have no dedicated API V2 typed command) to
``GenericCommand.ToXML()`` raw XML for ``ExecutionChannel.ExecuteCommand``.
"""

from __future__ import annotations

from .. import xml_compat as ET
from typing import Any

from ..command_registry import (
    registry_command_approved_passthrough,
    registry_command_operation,
)
from .commands import GenericCommand, command_from_xscr_object, command_to_xml


TYPED_API_V2_CLASS_NAMES = frozenset(
    {
        "AddLabware",
        "DropFingers",
        "GetFingers",
        "RemoveLabware",
        "SetLocation",
        "Subroutine",
        "TransferLabware",
        "UserPrompt",
    }
)


def stepped_command_from_xscr(
    command_object: ET.Element,
    *,
    command_id: str,
) -> tuple[str, str, str | None]:
    """Return ``(api_v2_type, execute_xml, operation)`` for one XSCR statement."""
    payload_xml = ET.tostring(command_object, encoding="unicode")
    api_command = command_from_xscr_object(command_object, command_id=command_id)
    if api_command is None:
        return "", payload_xml, registry_command_operation(command_id)

    api_v2_type = type(api_command).__name__
    execute_xml = command_to_xml(api_command)
    operation = registry_command_operation(command_id)
    if not operation and api_v2_type in TYPED_API_V2_CLASS_NAMES:
        operation = _operation_for_typed_class(api_v2_type)
    return api_v2_type, execute_xml, operation


def uses_generic_command_passthrough(*, api_v2_type: str, command_id: str) -> bool:
    """True when Gate 27 should exercise this step via ``GenericCommand`` raw XML."""
    if api_v2_type == "GenericCommand":
        return True
    if api_v2_type in TYPED_API_V2_CLASS_NAMES:
        return False
    return registry_command_approved_passthrough(command_id)


def generic_command_from_stepped(
    *,
    type_name: str,
    payload_xml: str,
    execute_xml: str,
    line_number: str | None,
) -> GenericCommand:
    """Rebuild a ``GenericCommand`` from a stepped-runner ``ICommand`` payload."""
    return GenericCommand(
        object_type=_object_type_from_payload(payload_xml or execute_xml),
        payload_xml=execute_xml or payload_xml,
        command_id=type_name,
        line_number=_parse_line_number(line_number),
    )


def validate_generic_passthrough_execute_xml(
    *,
    type_name: str,
    api_v2_type: str,
    execute_xml: str,
    payload_xml: str,
    line_number: str | None,
) -> str | None:
    """Return an error message when GenericCommand passthrough XML is invalid."""
    if not uses_generic_command_passthrough(api_v2_type=api_v2_type, command_id=type_name):
        return None
    from .generic_command_validate import validate_generic_command_before_execute

    xml_payload = execute_xml or payload_xml
    if not xml_payload.strip():
        return "GenericCommand passthrough requires non-empty execute_xml from ToXML()."
    return validate_generic_command_before_execute(
        command_id=type_name,
        payload_xml=xml_payload,
        object_type=_object_type_from_payload(xml_payload),
        line_number=_parse_line_number(line_number),
    )


def _operation_for_typed_class(api_v2_type: str) -> str | None:
    mapping = {
        "UserPrompt": "prompt_user",
        "AddLabware": "add_labware",
        "Subroutine": "call_subroutine",
        "TransferLabware": "move_plate",
        "SetLocation": "set_location",
        "RemoveLabware": "remove_labware",
    }
    return mapping.get(api_v2_type)


def _object_type_from_payload(payload_xml: str) -> str:
    text = str(payload_xml or "").strip()
    if not text:
        return ""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return ""
    return str(root.attrib.get("Type") or "")


def _parse_line_number(line_number: str | None) -> int:
    try:
        return int(str(line_number or "0").strip())
    except ValueError:
        return 0
