"""Offline XSCR/XML validation helpers for compile parity and ready gates."""

from .add_labware_golden import (
    compare_verification_recipe_add_labware_golden,
    enrich_compiled_inventory_with_golden_compare,
    is_verification_recipe_ir,
    verification_recipe_add_labware_steps,
    verification_recipe_add_labware_summary,
)
from .command_summary import transfer_labware_summary, user_prompt_summary
from .command_validate import (
    CommandValidationFailure,
    CommandValidationReport,
    validate_compiled_xscr_commands,
    validate_command,
)
from .commands import (
    AddLabware,
    GenericCommand,
    GetFingers,
    ICommand,
    SetLocation,
    Subroutine,
    TransferLabware,
    UserPrompt,
    command_from_ir_step,
    command_from_xscr_object,
    command_to_xml,
    get_fingers_from_ir_step,
    get_fingers_from_xscr_element,
    set_location_from_ir_step,
    subroutine_from_ir_step,
    transfer_labware_from_ir_step,
    user_prompt_from_ir_step,
)
try:
    from .context_enrichment import emit_ir_deck_step_events
except ModuleNotFoundError:
    def emit_ir_deck_step_events(*_args, **_kwargs):
        return []
from .generic_command_validate import (
    GenericCommandValidationReport,
    validate_generic_command_payload,
    validate_passthrough_commands_from_xscr,
)
from .golden_compare import compare_xscr_commands_to_native_xml, golden_compare_summary
from .native_provider import NativeToXmlProvider, native_to_xml_available
from .subroutine_identity import audit_subroutine_identity, subroutine_identity_summary
from .xml_compare import compare_command_xml, extract_command_objects_from_xscr, normalize_command_xml

__all__ = [
    "AddLabware",
    "CommandValidationFailure",
    "CommandValidationReport",
    "GenericCommand",
    "GenericCommandValidationReport",
    "GetFingers",
    "ICommand",
    "NativeToXmlProvider",
    "SetLocation",
    "Subroutine",
    "TransferLabware",
    "UserPrompt",
    "audit_subroutine_identity",
    "command_from_ir_step",
    "command_from_xscr_object",
    "command_to_xml",
    "compare_command_xml",
    "compare_verification_recipe_add_labware_golden",
    "compare_xscr_commands_to_native_xml",
    "emit_ir_deck_step_events",
    "enrich_compiled_inventory_with_golden_compare",
    "extract_command_objects_from_xscr",
    "get_fingers_from_ir_step",
    "get_fingers_from_xscr_element",
    "golden_compare_summary",
    "is_verification_recipe_ir",
    "native_to_xml_available",
    "normalize_command_xml",
    "set_location_from_ir_step",
    "subroutine_from_ir_step",
    "subroutine_identity_summary",
    "transfer_labware_from_ir_step",
    "transfer_labware_summary",
    "user_prompt_from_ir_step",
    "user_prompt_summary",
    "validate_compiled_xscr_commands",
    "validate_command",
    "validate_generic_command_payload",
    "validate_passthrough_commands_from_xscr",
    "verification_recipe_add_labware_steps",
    "verification_recipe_add_labware_summary",
]
