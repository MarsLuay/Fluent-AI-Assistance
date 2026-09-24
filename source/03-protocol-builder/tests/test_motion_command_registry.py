from __future__ import annotations

from tecan_common.command_registry import (
    registry_command_approved_passthrough,
    registry_command_operation,
    registry_command_support_status,
    registry_field_value,
    lookup_command,
)


def test_typed_motion_operations_are_mapped_not_opaque_passthrough() -> None:
    expected = {
        "MoveAxisCommandScriptStatement": "move_axis_command",
        "StartMoveCommandScriptStatement": "start_move_command",
        "WaitForAsyncResponseScriptStatement": "wait_for_async_response",
    }
    for command_id, operation in expected.items():
        assert registry_command_operation(command_id) == operation
        assert registry_command_support_status(command_id) == "mapped"
        assert registry_command_approved_passthrough(command_id) is False
        assert lookup_command(command_id)["confidence"] == "verified"


def test_typed_motion_fields_use_canonical_ir_names() -> None:
    fields = {"AvailableID": "axis-available", "IdLabel": "axis-label", "Position": "300"}
    assert registry_field_value("MoveAxisCommandScriptStatement", "available_id", fields) == "axis-available"
    assert registry_field_value("MoveAxisCommandScriptStatement", "id_label", fields) == "axis-label"
    assert registry_field_value("MoveAxisCommandScriptStatement", "position_expression", fields) == "300"


def test_unknown_motion_variant_is_not_promoted() -> None:
    assert registry_command_operation("MoveAxisCommandPreviewV99") is None
    assert registry_command_support_status("MoveAxisCommandPreviewV99") is None
