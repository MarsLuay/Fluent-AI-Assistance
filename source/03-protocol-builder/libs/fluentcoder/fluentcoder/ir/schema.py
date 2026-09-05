"""
Intermediate Representation (IR) schema for Tecan protocols.

This module defines Pydantic models that represent protocol steps
in a structured, validated format before rendering to XML.
"""

from typing import Any, Optional, Literal, Union
from pydantic import BaseModel, ConfigDict, Field, field_validator
from enum import Enum

from ..expressions import (
    Expression,
    coerce_literal_expression,
    coerce_source_expression,
    loop_count_expression_error,
    parse_expression,
)


class StepType(str, Enum):
    """Enumeration of all supported step types."""
    ADD_LABWARE = "add_labware"
    REMOVE_LABWARE = "remove_labware"
    GET_HEAD_ADAPTER = "get_head_adapter"
    DROP_HEAD_ADAPTER = "drop_head_adapter"
    PICK_UP_TIPS = "pick_up_tips"
    SET_TIPS_BACK = "set_tips_back"
    ASPIRATE = "aspirate"
    DISPENSE = "dispense"
    # RGA (Robotic Gripper Arm) step types
    RGA_TRANSFER_LABWARE = "rga_transfer_labware"
    CGA_GET_FINGERS = "cga_get_fingers"
    CGA_DROP_FINGERS = "cga_drop_fingers"
    # Mixing step type
    MCA384_MIX = "mca384_mix"
    MCA384_EMPTY_TIPS = "mca384_empty_tips"
    MCA384_GET_TIPS = "mca384_get_tips"
    MCA384_DROP_TIPS = "mca384_drop_tips"
    MCA384_MOVE_ARM = "mca384_move_arm"
    # LiHa (Liquid Handler) step types
    LIHA_ASPIRATE = "liha_aspirate"
    LIHA_DISPENSE = "liha_dispense"
    LIHA_MIX = "liha_mix"
    LIHA_GET_TIPS = "liha_get_tips"
    LIHA_DROP_TIPS = "liha_drop_tips"
    # Control flow
    SCRIPT_GROUP = "script_group"
    LOOP = "loop"
    CONDITIONAL = "conditional"
    # Wait/incubation step type
    WAIT = "wait"
    # Variable operations
    SET_VARIABLE = "set_variable"
    CALCULATE_VARIABLE = "calculate_variable"
    # Annotation and control
    COMMENT = "comment"
    USER_PROMPT = "user_prompt"
    START_TIMER = "start_timer"
    WAIT_FOR_TIMER = "wait_for_timer"
    # LiHa empty tips
    LIHA_EMPTY_TIPS = "liha_empty_tips"
    LIHA_DETECT_LIQUID = "liha_detect_liquid"
    GENERATE_REPORT = "generate_report"
    # Variable import/export and runtime query
    EXPORT_VARIABLE = "export_variable"
    IMPORT_VARIABLE = "import_variable"
    QUERY_VARIABLE = "query_variable"
    # External/runtime commands
    EXECUTE_APPLICATION = "execute_application"
    DELAY = "delay"
    # Worktable and routine commands
    SET_LOCATION = "set_location"
    SUBROUTINE = "subroutine"
    INITIALIZE_DEVICE = "initialize_device"
    # Application-driver macros (RGA vectors, transfer labware, etc.)
    APPLICATION_DRIVER_MACRO = "application_driver_macro"
    # Low-level hardware driver movement (CGA axis choreography, etc.)
    MOVE_AXIS_COMMAND = "move_axis_command"
    START_MOVE_COMMAND = "start_move_command"
    WAIT_FOR_ASYNC_RESPONSE = "wait_for_async_response"
    END_SCRIPT = "end_script"
    EXECUTE_VB_SCRIPT = "execute_vb_script"
    TEGIO_SET_PWM_OUTPUT = "tegio_set_pwm_output"
    LEAVE = "leave"


class BaseStep(BaseModel):
    """Base class for all protocol steps."""
    step_type: StepType
    line_number: Optional[int] = None  # Auto-assigned if not provided
    disabled: bool = False
    breakpoint: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)


def _coerce_expression_field(value: Any) -> Expression:
    return coerce_source_expression(value)


def _coerce_optional_expression_field(value: Any) -> Expression | None:
    if value is None:
        return None
    return coerce_source_expression(value)


def _coerce_expression_list(value: Any) -> list[Expression] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise TypeError("expression list field must be a list")
    return [coerce_source_expression(item) for item in value]


def _looks_like_source_expression(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped[:1] in {'"', "+", "-"}:
        return True
    if stripped.casefold() in {"true", "false"}:
        return True
    try:
        float(stripped)
        return True
    except ValueError:
        pass
    return any(token in stripped for token in ("(", ")", "+", "-", "*", "/", "^", "=", "<", ">"))


class SetVariableStep(BaseStep):
    """Step to set a variable value."""
    step_type: Literal[StepType.SET_VARIABLE] = StepType.SET_VARIABLE
    variable_name: str = Field(..., description="Name of the variable")
    value: Expression = Field(..., description="Typed FluentControl expression value to set")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("value", mode="before")
    @classmethod
    def _coerce_value_expression(cls, value: Any) -> Expression:
        return coerce_literal_expression(value)


class CalculateVariableStep(BaseStep):
    """Step to calculate a variable value."""
    step_type: Literal[StepType.CALCULATE_VARIABLE] = StepType.CALCULATE_VARIABLE
    target_variable: str = Field(..., description="Variable to store result")
    operation: str = Field(..., description="Operation (Add, Subtract, Multiply, Divide)")
    operand_a: Expression = Field(..., description="First typed operand expression")
    operand_b: Expression = Field(..., description="Second typed operand expression")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("operand_a", "operand_b", mode="before")
    @classmethod
    def _coerce_operand_expression(cls, value: Any) -> Expression:
        if isinstance(value, str):
            return parse_expression(value.strip())
        return coerce_literal_expression(value)


class AddLabwareStep(BaseStep):
    """Step to add labware to the worktable."""
    step_type: Literal[StepType.ADD_LABWARE] = StepType.ADD_LABWARE
    labware_type: str = Field(..., description="Type of labware (e.g., 'MCA384, 50ul')")
    label: str = Field(..., description="User-defined label for this labware instance")
    location: str = Field(..., description="Location on worktable (e.g., 'Nest7mm_Pos')")
    position: Expression = Field(
        ...,
        description="Typed FluentControl position expression",
    )

    @field_validator("position", mode="before")
    @classmethod
    def _coerce_position_expression(cls, value: Any) -> Expression:
        if isinstance(value, int) and not isinstance(value, bool) and value < 1:
            raise ValueError("position must be >= 1 when numeric")
        if isinstance(value, str) and not value.strip():
            raise ValueError("position expression must be non-empty")
        return coerce_source_expression(value)
    rotation: int = Field(default=0, ge=0, lt=360, description="Rotation angle in degrees")
    has_lid: bool = Field(default=False, description="Whether labware has a lid")
    initial_volume: float = Field(default=0.0, ge=0, description="Initial volume per well in microliters")


class RemoveLabwareStep(BaseStep):
    """Step to remove labware from the worktable."""
    step_type: Literal[StepType.REMOVE_LABWARE] = StepType.REMOVE_LABWARE
    labware_name: str = Field(..., description="Label of the labware to remove")


class GetHeadAdapterStep(BaseStep):
    """Step to retrieve the head adapter."""
    step_type: Literal[StepType.GET_HEAD_ADAPTER] = StepType.GET_HEAD_ADAPTER
    labware_name: str = Field(..., description="Labware containing the head adapter")
    device_alias: Optional[str] = None  # ZEIA/recipe; empty → RenderError when template needs it
    available_id: Optional[str] = None
    blowout_airgap: int = Field(default=0, ge=0, description="Air gap during blowout")


class DropHeadAdapterStep(BaseStep):
    """Step to drop the head adapter."""
    step_type: Literal[StepType.DROP_HEAD_ADAPTER] = StepType.DROP_HEAD_ADAPTER
    labware_name: Optional[str] = Field(default=None, description="Adapter to drop (defaults to mounted adapter)")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    blowout_airgap: int = Field(default=0, ge=0)
    back_position: str = Field(default="BackToSource", description="Back position mode")
    adapter_after_drop: bool = Field(default=False)


class PickUpTipsStep(BaseStep):
    """Step to pick up tips."""
    step_type: Literal[StepType.PICK_UP_TIPS] = StepType.PICK_UP_TIPS
    labware_name: str = Field(..., description="Labware containing tips")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    blowout_airgap: int = Field(default=0, ge=0)
    partial_columns: int = Field(default=24, ge=1, le=24, description="Number of columns")
    partial_rows: int = Field(default=16, ge=1, le=16, description="Number of rows")
    tip_columns: Optional[list[int]] = Field(
        default=None,
        description="1-based column numbers to pick (MCA partial pickup)",
    )
    tip_count: Optional[int] = Field(
        default=None,
        ge=1,
        description="Pick the first N available tips in column-major order",
    )
    head_position: str = Field(default="Left", description="Head position (Left/Right)")


class SetTipsBackStep(BaseStep):
    """Step to return tips."""
    step_type: Literal[StepType.SET_TIPS_BACK] = StepType.SET_TIPS_BACK
    labware_name: Optional[str] = Field(default=None, description="Labware to return tips to (defaults to pickup source)")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    back_position: str = Field(default="BackToPosition")
    partial_columns: int = Field(default=24, ge=1, le=24, description="Number of columns")
    partial_rows: int = Field(default=16, ge=1, le=16, description="Number of rows")
    head_position: str = Field(default="Left", description="Head position (Left/Right)")


class AspirateStep(BaseStep):
    """Step to aspirate liquid."""
    step_type: Literal[StepType.ASPIRATE] = StepType.ASPIRATE
    labware_name: str = Field(..., description="Source labware")
    volume: Expression = Field(..., description="Typed FluentControl volume expression")
    liquid_class: Optional[str] = None  # Uses default if not specified
    device_alias: Optional[str] = None
    available_id: Optional[str] = None

    @field_validator("volume", mode="before")
    @classmethod
    def _coerce_volume_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class DispenseStep(BaseStep):
    """Step to dispense liquid."""
    step_type: Literal[StepType.DISPENSE] = StepType.DISPENSE
    labware_name: str = Field(..., description="Destination labware")
    volume: Expression = Field(..., description="Typed FluentControl volume expression")
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None

    @field_validator("volume", mode="before")
    @classmethod
    def _coerce_volume_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class RgaTransferLabwareStep(BaseStep):
    """Step to transfer labware using the RGA (Robotic Gripper Arm)."""
    step_type: Literal[StepType.RGA_TRANSFER_LABWARE] = StepType.RGA_TRANSFER_LABWARE
    labware_name: str = Field(..., description="Labware to transfer")
    destination_location: str = Field(..., description="Destination location (e.g., 'Nest61mm_Pos')")
    destination_site: Expression = Field(..., description="Typed FluentControl destination site expression")
    fixed_site: bool = Field(default=True, description="Use fixed site positioning")
    move_to_base: bool = Field(default=False, description="Move to base position after transfer")
    module_name: str = Field(default="", description="RGA module name from ZEIA/recipe (never invent 'RGA 1')")
    available_id: Optional[str] = None

    @field_validator("destination_site", mode="before")
    @classmethod
    def _coerce_destination_site_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class CgaGetFingersStep(BaseStep):
    """Step to get gripper fingers from a labware position."""
    step_type: Literal[StepType.CGA_GET_FINGERS] = StepType.CGA_GET_FINGERS
    labware_name: Optional[str] = Field(default=None, description="Labware/adapter to grip fingers from")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class CgaDropFingersStep(BaseStep):
    """Step to drop gripper fingers/release labware."""
    step_type: Literal[StepType.CGA_DROP_FINGERS] = StepType.CGA_DROP_FINGERS
    labware_name: Optional[str] = Field(default=None, description="Labware/adapter to drop fingers to")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    use_source_as_back_position: str = Field(default="BackToPosition", description="Back position mode")


class Mca384MixStep(BaseStep):
    """Step to mix liquid using MCA384 pipetting head."""
    step_type: Literal[StepType.MCA384_MIX] = StepType.MCA384_MIX
    labware_name: str = Field(..., description="Labware containing liquid to mix")
    volume: Expression = Field(..., description="Typed FluentControl mix volume expression")
    cycles: Expression = Field(default=10, description="Typed FluentControl mix cycle-count expression")
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None

    @field_validator("volume", "cycles", mode="before")
    @classmethod
    def _coerce_mix_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class Mca384EmptyTipsStep(BaseStep):
    """Step to empty tips using MCA384 pipetting head."""
    step_type: Literal[StepType.MCA384_EMPTY_TIPS] = StepType.MCA384_EMPTY_TIPS
    labware_name: str = Field(..., description="Destination labware (usually Waste)")
    volume: Expression = Field(..., description="Typed FluentControl empty-tip volume expression")
    liquid_class: Optional[str] = Field(default=None, description="Liquid class for emptying (ZEIA/recipe; never invent)")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None

    @field_validator("volume", mode="before")
    @classmethod
    def _coerce_volume_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class LihaAspirateStep(BaseStep):
    """Step to aspirate liquid using LiHa."""
    step_type: Literal[StepType.LIHA_ASPIRATE] = StepType.LIHA_ASPIRATE
    labware_name: str = Field(..., description="Source labware")
    volume: Expression = Field(..., description="Typed FluentControl volume expression")
    well_offset: Optional[Expression] = Field(
        default=None,
        description="Optional well offset expression for LiHa selection"
    )
    selection: Optional[str] = Field(
        default=None,
        description="Deprecated; selection is auto-derived. Use well_offset inside loops."
    )
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    volumes: Optional[list[Expression]] = Field(
        default=None,
        description="Per-channel volumes when LiHa uses multi-channel pipetting",
    )
    tip_channels: Optional[list[int]] = Field(
        default=None,
        description="Selected LiHa tip channel indices (0-7)",
    )
    raw_xml: Optional[str] = Field(default=None, description="Original Object inner XML for round-trip")

    @field_validator("volume", mode="before")
    @classmethod
    def _coerce_volume_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)

    @field_validator("well_offset", mode="before")
    @classmethod
    def _coerce_well_offset_expression(cls, value: Any) -> Expression | None:
        return _coerce_optional_expression_field(value)

    @field_validator("volumes", mode="before")
    @classmethod
    def _coerce_volume_expressions(cls, value: Any) -> list[Expression] | None:
        return _coerce_expression_list(value)


class LihaDispenseStep(BaseStep):
    """Step to dispense liquid using LiHa."""
    step_type: Literal[StepType.LIHA_DISPENSE] = StepType.LIHA_DISPENSE
    labware_name: str = Field(..., description="Destination labware")
    volume: Expression = Field(..., description="Typed FluentControl volume expression")
    well_offset: Optional[Expression] = Field(
        default=None,
        description="Optional well offset expression for LiHa selection"
    )
    selection: Optional[str] = Field(
        default=None,
        description="Deprecated; selection is auto-derived. Use well_offset inside loops."
    )
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    volumes: Optional[list[Expression]] = Field(
        default=None,
        description="Per-channel volumes when LiHa uses multi-channel pipetting",
    )
    tip_channels: Optional[list[int]] = Field(
        default=None,
        description="Selected LiHa tip channel indices (0-7)",
    )
    raw_xml: Optional[str] = Field(default=None, description="Original Object inner XML for round-trip")

    @field_validator("volume", mode="before")
    @classmethod
    def _coerce_volume_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)

    @field_validator("well_offset", mode="before")
    @classmethod
    def _coerce_well_offset_expression(cls, value: Any) -> Expression | None:
        return _coerce_optional_expression_field(value)

    @field_validator("volumes", mode="before")
    @classmethod
    def _coerce_volume_expressions(cls, value: Any) -> list[Expression] | None:
        return _coerce_expression_list(value)


class LihaMixStep(BaseStep):
    """Step to mix liquid using LiHa."""
    step_type: Literal[StepType.LIHA_MIX] = StepType.LIHA_MIX
    labware_name: str = Field(..., description="Labware containing liquid to mix")
    volume: Expression = Field(..., description="Typed FluentControl mix volume expression")
    cycles: Expression = Field(default=10, description="Typed FluentControl mix cycle-count expression")
    well_offset: Optional[Expression] = Field(
        default=None,
        description="Optional well offset expression for LiHa selection"
    )
    selection: Optional[str] = Field(
        default=None,
        description="Deprecated; selection is auto-derived. Use well_offset inside loops."
    )
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    volumes: Optional[list[Expression]] = Field(
        default=None,
        description="Per-channel volumes when LiHa uses multi-channel pipetting",
    )
    tip_channels: Optional[list[int]] = Field(
        default=None,
        description="Selected LiHa tip channel indices (0-7)",
    )
    raw_xml: Optional[str] = Field(default=None, description="Original Object inner XML for round-trip")

    @field_validator("volume", "cycles", mode="before")
    @classmethod
    def _coerce_mix_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)

    @field_validator("well_offset", mode="before")
    @classmethod
    def _coerce_well_offset_expression(cls, value: Any) -> Expression | None:
        return _coerce_optional_expression_field(value)

    @field_validator("volumes", mode="before")
    @classmethod
    def _coerce_volume_expressions(cls, value: Any) -> list[Expression] | None:
        return _coerce_expression_list(value)


class LihaGetTipsStep(BaseStep):
    """Step to get tips using LiHa."""
    step_type: Literal[StepType.LIHA_GET_TIPS] = StepType.LIHA_GET_TIPS
    labware_name: Optional[str] = Field(default=None, description="Labware containing tips")
    tip_index: Optional[int] = Field(default=None, description="Single tip channel index (0-7)")
    tip_channels: Optional[list[int]] = Field(
        default=None,
        description="Selected tip channel indices (0-7); mounts only these channels",
    )
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class LihaDropTipsStep(BaseStep):
    """Step to drop tips using LiHa."""
    step_type: Literal[StepType.LIHA_DROP_TIPS] = StepType.LIHA_DROP_TIPS
    labware_name: Optional[str] = Field(default=None, description="Location to drop tips")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class Mca384GetTipsStep(BaseStep):
    """Step to get mounted MCA384 tips."""
    step_type: Literal[StepType.MCA384_GET_TIPS] = StepType.MCA384_GET_TIPS
    labware_name: Optional[str] = Field(default=None, description="Labware containing tips")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class Mca384DropTipsStep(BaseStep):
    """Step to drop mounted MCA384 tips."""
    step_type: Literal[StepType.MCA384_DROP_TIPS] = StepType.MCA384_DROP_TIPS
    labware_name: Optional[str] = Field(default=None, description="Labware to receive tips")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class Mca384MoveArmStep(BaseStep):
    """Step to move the MCA384 arm."""
    step_type: Literal[StepType.MCA384_MOVE_ARM] = StepType.MCA384_MOVE_ARM
    movement_type: str = Field(default="GlobalZTravel", description="MCA384 arm movement type")
    labware_name: Optional[str] = Field(default=None, description="Reference labware")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None


class WaitStep(BaseStep):
    """Step to wait/incubate for a specified duration."""
    step_type: Literal[StepType.WAIT] = StepType.WAIT
    duration_seconds: Expression = Field(..., description="Typed FluentControl wait duration expression")
    comment: Optional[str] = Field(default=None, description="Optional comment describing the wait")

    @field_validator("duration_seconds", mode="before")
    @classmethod
    def _coerce_duration_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class CommentStep(BaseStep):
    """Step to add a comment to the protocol."""
    step_type: Literal[StepType.COMMENT] = StepType.COMMENT
    comment: str = Field(..., description="Comment text")


class UserPromptStep(BaseStep):
    """Step to prompt the operator during execution.

    A plain prompt (no ``image_path``) compiles to a
    ``Tecan.Core.Scripting.UserPromptStatement`` (text only). When
    ``image_path`` is set, the step compiles to a TouchTools/RUP
    ``RUPWorktableStatement`` whose ``CustomDetailImageFilePath`` renders detail
    media on the worktable prompt path (including GIF motion clips). Set
    ``rup_kind="standard"`` to emit a standalone ``RUPStandardStatement`` with
    ``SelectedImagePath`` instead; that variant requires no deck labware binding.
    """
    step_type: Literal[StepType.USER_PROMPT] = StepType.USER_PROMPT
    prompt: str = Field(..., description="Prompt message shown to operator")
    timeout: int = Field(default=0, ge=0, description="Auto-close timeout in seconds (0 = wait for user)")
    auto_close: Optional[bool] = Field(
        default=None,
        description="Explicit FluentControl AutoClose/RUPAutoClose value; defaults from timeout when omitted.",
    )
    image_path: Optional[str] = Field(
        default=None,
        description=(
            "Optional image path. When set, the prompt compiles to an "
            "image-capable RUPWorktableStatement (CustomDetailImageFilePath) "
            "unless rup_kind=\"standard\", in which case it compiles to "
            "RUPStandardStatement (SelectedImagePath). An empty/missing image "
            "is graceful."
        ),
    )
    is_image_used: Optional[bool] = Field(
        default=None,
        description=(
            "Whether the prompt image is in use. Defaults to True when "
            "image_path is provided, else False. Informational; the compiled "
            "RUP statement shows an image whenever the image path field is "
            "non-empty."
        ),
    )
    screen_title: Optional[str] = Field(
        default=None,
        description="Optional RUP screen title for image prompts (RUPScreenTitle).",
    )
    rup_kind: Optional[Literal["standard", "worktable"]] = Field(
        default=None,
        description=(
            "TouchTools prompt variant. Image prompts default to worktable "
            "(RUPWorktableStatement); set standard for RUPStandardStatement."
        ),
    )
    selected_labware_name: Optional[str] = Field(default=None, description="RUP worktable SelectedLabware label")
    selected_labware_type: Optional[str] = Field(default=None, description="RUP worktable LabwareTypeName")
    grid: Optional[int] = Field(default=None, description="RUP worktable grid index")
    site: Optional[int] = Field(default=None, description="RUP worktable site index")
    sound_path: Optional[str] = Field(
        default=None,
        description="Optional sound file path (SoundFile / SelectedSoundPath).",
    )
    raw_xml: Optional[str] = Field(default=None, description="Original XML for RUP worktable round-trip")


class ExecuteVbScriptStep(BaseStep):
    """Execute an external VB script synchronously or asynchronously."""
    step_type: Literal[StepType.EXECUTE_VB_SCRIPT] = StepType.EXECUTE_VB_SCRIPT
    vb_script: str = Field(..., description="Path to the .vb script")
    execution_mode: str = Field(default="Synchronous", description="Synchronous or Asynchronous")
    raw_xml: Optional[str] = None


class TeGioSetPwmOutputStep(BaseStep):
    """Set a TeGIO PWM output pin (hardware IO passthrough)."""
    step_type: Literal[StepType.TEGIO_SET_PWM_OUTPUT] = StepType.TEGIO_SET_PWM_OUTPUT
    pin_number: str = Field(default="", description="GIO pin identifier")
    duty_cycle: Expression = Field(default=0, description="Typed FluentControl PWM duty cycle expression")
    duration: Expression = Field(default=0, description="Typed FluentControl PWM duration expression")
    raw_xml: Optional[str] = None

    @field_validator("duty_cycle", "duration", mode="before")
    @classmethod
    def _coerce_pwm_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class LeaveStep(BaseStep):
    """Exit the innermost FluentControl loop or group."""
    step_type: Literal[StepType.LEAVE] = StepType.LEAVE
    raw_xml: Optional[str] = None


class StartTimerStep(BaseStep):
    """Step to start an asynchronous timer."""
    step_type: Literal[StepType.START_TIMER] = StepType.START_TIMER
    timer: int = Field(default=1, ge=1, description="Timer ID")


class WaitForTimerStep(BaseStep):
    """Step to wait until a timer reaches a specified duration."""
    step_type: Literal[StepType.WAIT_FOR_TIMER] = StepType.WAIT_FOR_TIMER
    timer: int = Field(default=1, ge=1, description="Timer ID to wait for")
    duration_seconds: Expression = Field(..., description="Typed FluentControl target duration expression")

    @field_validator("duration_seconds", mode="before")
    @classmethod
    def _coerce_duration_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class LihaEmptyTipsStep(BaseStep):
    """Step to empty LiHa tips into a labware (usually waste)."""
    step_type: Literal[StepType.LIHA_EMPTY_TIPS] = StepType.LIHA_EMPTY_TIPS
    labware_name: str = Field(..., description="Destination labware (usually waste)")
    volume: Expression = Field(default=0, description="Typed FluentControl empty-tip volume expression")
    liquid_class: Optional[str] = Field(default=None, description="Liquid class for emptying (ZEIA/recipe; never invent)")
    device_alias: Optional[str] = None
    available_id: Optional[str] = None

    @field_validator("volume", mode="before")
    @classmethod
    def _coerce_volume_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class LihaDetectLiquidStep(BaseStep):
    """LiHa liquid-detection command (hardware side effect; twin no-op)."""
    step_type: Literal[StepType.LIHA_DETECT_LIQUID] = StepType.LIHA_DETECT_LIQUID
    labware_name: str = Field(default="", description="Target labware")
    sensitivity: Optional[float] = Field(default=None, description="Detection sensitivity")
    detection_speed: Optional[float] = Field(default=None, description="Detection speed")
    tip_channels: Optional[list[int]] = Field(default=None, description="Selected tip channels")
    raw_xml: Optional[str] = Field(default=None, description="Original Object inner XML for round-trip")


class GenerateReportStep(BaseStep):
    """Sample-tracking report generation (side effect; twin no-op)."""
    step_type: Literal[StepType.GENERATE_REPORT] = StepType.GENERATE_REPORT
    report_name: str = Field(default="", description="Report template name")
    write_csv: bool = Field(default=False, description="Whether CSV export is enabled")
    print_report: bool = Field(default=False, description="Whether report is printed")
    all_labware_selected: bool = Field(default=True, description="All-labware report scope flag")
    raw_xml: Optional[str] = Field(default=None, description="Original Object inner XML for round-trip")


class ExportVariableStep(BaseStep):
    """Export one or more variables to a file."""
    step_type: Literal[StepType.EXPORT_VARIABLE] = StepType.EXPORT_VARIABLE
    variables: list[str] = Field(default_factory=list, description="Variables to export")
    export_file: str = Field(..., description="Destination file path")
    write_header: bool = Field(default=False, description="Whether to write a header row")
    replace_existing_file: bool = Field(default=False, description="Whether to overwrite an existing file")
    export_strings_with_quotes: bool = Field(default=False, description="Whether string values should be quoted")
    delimiter_code: int = Field(default=59, description="ASCII delimiter code; 59 is semicolon")


class ImportVariableStep(BaseStep):
    """Import one or more variables from a file."""
    step_type: Literal[StepType.IMPORT_VARIABLE] = StepType.IMPORT_VARIABLE
    variables: list[str] = Field(default_factory=list, description="Variables to import")
    import_file: str = Field(..., description="Source file path")
    read_line: bool = Field(default=False, description="Whether to read a specific line")
    line: int = Field(default=1, ge=1, description="1-based line to read when read_line is enabled")
    start_in_column: bool = Field(default=False, description="Whether to start import in a specific column")
    column: int = Field(default=1, ge=1, description="1-based column to start from")
    has_header: bool = Field(default=False, description="Whether the source file has a header row")
    delimiter_code: int = Field(default=59, description="ASCII delimiter code; 59 is semicolon")


class QueryVariableStep(BaseStep):
    """Prompt the operator to provide a variable value."""
    step_type: Literal[StepType.QUERY_VARIABLE] = StepType.QUERY_VARIABLE
    variable_name: str = Field(..., description="Variable to query")
    query_prompt: str = Field(..., description="Prompt text shown to the operator")
    limit_range: bool = Field(default=False, description="Whether to enforce min/max limits")


class InitializeDeviceStep(BaseStep):
    """Initialize, home, reset, or prime an instrument device."""
    step_type: Literal[StepType.INITIALIZE_DEVICE] = StepType.INITIALIZE_DEVICE
    device_alias: Optional[str] = Field(default=None, description="FluentControl device alias")
    available_id: Optional[str] = Field(default=None, description="Device AvailableID when distinct from alias")
    init_type: str = Field(default="Initialize", description="Initialization mode (Initialize, Home, Reset, Prime, ...)")


class ExecuteApplicationStep(BaseStep):
    """Run an external application."""
    step_type: Literal[StepType.EXECUTE_APPLICATION] = StepType.EXECUTE_APPLICATION
    application: str = Field(..., description="Application path or command")
    arguments: str = Field(default="", description="Application arguments")
    wait: bool = Field(default=True, description="Whether to wait for the process to finish")
    store_return: bool = Field(default=False, description="Whether to store the return code")
    variable: str = Field(default="", description="Variable to receive the return code")


class DelayStep(BaseStep):
    """Delay execution for a number of milliseconds."""
    step_type: Literal[StepType.DELAY] = StepType.DELAY
    delay: Expression = Field(..., description="Typed FluentControl delay duration expression in milliseconds")

    @field_validator("delay", mode="before")
    @classmethod
    def _coerce_delay_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class SetLocationStep(BaseStep):
    """Move an existing labware item to a specific worktable location."""
    step_type: Literal[StepType.SET_LOCATION] = StepType.SET_LOCATION
    labware: str = Field(..., description="Labware label")
    location: str = Field(..., description="Destination location")
    site: Expression = Field(..., description="Typed FluentControl destination site expression")
    rotation: int = Field(default=0, ge=0, lt=360, description="Rotation angle in degrees")

    @field_validator("site", mode="before")
    @classmethod
    def _coerce_site_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class VariableMapping(BaseModel):
    """Subroutine variable mapping."""
    target: str = Field(..., description="Target variable in the subroutine")
    source: Expression = Field(..., description="Typed source expression in the caller")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("source", mode="before")
    @classmethod
    def _coerce_source_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)


class SubRoutineStep(BaseStep):
    """Call a FluentControl subroutine."""
    step_type: Literal[StepType.SUBROUTINE] = StepType.SUBROUTINE
    subroutine: str = Field(..., description="Subroutine path")
    execution_mode: str = Field(default="Synchronous", description="Execution mode")
    variable_mappings_start: list[VariableMapping] = Field(default_factory=list, description="Start mappings")
    variable_mappings_end: list[VariableMapping] = Field(default_factory=list, description="End mappings")


class MoveAxisCommandStep(BaseStep):
    """Low-level axis move queued before StartMove (hardware driver passthrough)."""
    step_type: Literal[StepType.MOVE_AXIS_COMMAND] = StepType.MOVE_AXIS_COMMAND
    available_id: Optional[str] = Field(default=None, description="Drive AvailableID")
    id_label: Optional[str] = Field(default=None, description="IdLabel field from XML")
    position: Expression = Field(default=0, description="Typed FluentControl axis target position expression")
    charge_condition: Optional[Expression] = Field(default=None, description="Typed ChargeCondition expression")
    max_speed: Optional[str] = None
    acceleration: Optional[str] = None
    deceleration: Optional[str] = None
    raw_xml: Optional[str] = Field(default=None, description="Original Object inner XML for round-trip")

    @field_validator("position", mode="before")
    @classmethod
    def _coerce_position_expression(cls, value: Any) -> Expression:
        return _coerce_expression_field(value)

    @field_validator("charge_condition", mode="before")
    @classmethod
    def _coerce_charge_condition_expression(cls, value: Any) -> Expression | None:
        return _coerce_optional_expression_field(value)


class StartMoveCommandStep(BaseStep):
    """Start queued hardware driver axis moves."""
    step_type: Literal[StepType.START_MOVE_COMMAND] = StepType.START_MOVE_COMMAND
    available_id: Optional[str] = Field(default=None, description="TeControl AvailableID")
    id_label: Optional[str] = Field(default=None, description="IdLabel field from XML")
    raw_xml: Optional[str] = Field(default=None, description="Original Object inner XML for round-trip")


class WaitForAsyncResponseStep(BaseStep):
    """Wait for asynchronous hardware driver response."""
    step_type: Literal[StepType.WAIT_FOR_ASYNC_RESPONSE] = StepType.WAIT_FOR_ASYNC_RESPONSE
    raw_xml: Optional[str] = Field(default=None, description="Original Object inner XML for round-trip")


class EndScriptStep(BaseStep):
    """Early script termination with a return code."""
    step_type: Literal[StepType.END_SCRIPT] = StepType.END_SCRIPT
    return_code: str = Field(default="Success", description="ScriptExecutionResult value")
    raw_xml: Optional[str] = Field(default=None, description="Original Object inner XML for round-trip")


class ApplicationDriverMacroStep(BaseStep):
    """VisionX application-driver macro (RGA transfer, execute vector, etc.).

    Parsed from verification/production .xscr scripts. The simulator treats
    these as non-motion by default (verification safety / gate 28 parity).
    """
    step_type: Literal[StepType.APPLICATION_DRIVER_MACRO] = StepType.APPLICATION_DRIVER_MACRO
    macro_name: str = Field(..., description="Macro Name attribute, e.g. RGA1_TransferLabware")
    module_name: str = Field(default="", description="ModuleName attribute from ZEIA/recipe (never invent 'RGA 1')")
    available_id: Optional[str] = Field(
        default=None,
        description="Optional device AvailableID when present in the macro XML",
    )
    execution_settings: str = Field(
        default="",
        description="HTML-entity-escaped ExecutionSettings payload from the .xscr",
    )
    parameters: dict = Field(
        default_factory=dict,
        description="Parsed inner ExecutionSettings fields (Labware, Location, VectorName, …)",
    )
    raw_xml: Optional[str] = Field(
        default=None,
        description="Full <Object> XML preserved for byte-exact round-trip when set",
    )


class GenericStep(BaseModel):
    """
    Generic step that accepts any step type from the reference.

    Used for commands extracted from protocols that don't have
    dedicated Pydantic classes yet.
    """
    step_type: str = Field(..., description="Step type from reference")
    line_number: Optional[int] = None
    disabled: bool = False
    breakpoint: bool = False
    # All other parameters stored as dict
    parameters: dict = Field(default_factory=dict, description="Step parameters")

    @property
    def name(self) -> str:
        return self.step_type

    def __init__(self, **data):
        # Extract known fields, put rest in parameters
        known_fields = {'step_type', 'line_number', 'disabled', 'breakpoint', 'parameters'}
        params = {k: v for k, v in data.items() if k not in known_fields}
        if 'parameters' not in data:
            data['parameters'] = params
        else:
            data['parameters'].update(params)
        # Remove extra keys
        data = {k: v for k, v in data.items() if k in known_fields}
        super().__init__(**data)


class ScriptGroupStep(BaseStep):
    """A nested FluentControl script group inside another group."""
    step_type: Literal[StepType.SCRIPT_GROUP] = StepType.SCRIPT_GROUP
    name: str = Field(default="Steps", description="Nested script group name")
    steps: list["Step"] = Field(default_factory=list, description="Steps in this nested group")


class LoopStep(BaseStep):
    """A loop that repeats a sequence of steps."""
    step_type: Literal[StepType.LOOP] = StepType.LOOP
    name: str = Field(default="Loop", description="Name of the loop")
    iterations: int = Field(default=2, ge=1, description="Number of times to repeat")
    loop_variable: Optional[str] = Field(default=None, description="Optional loop variable name")
    number_of_loops: Optional[Expression] = Field(
        default=None,
        description="Typed FluentControl loop count expression. Defaults to iterations when omitted."
    )
    steps: list["Step"] = Field(default_factory=list, description="Steps to repeat")

    @field_validator("number_of_loops", mode="before")
    @classmethod
    def _coerce_number_of_loops_expression(cls, value: Any) -> Expression | None:
        return _coerce_optional_expression_field(value)

    @field_validator("number_of_loops")
    @classmethod
    def _validate_number_of_loops_expression(cls, value: Expression | None) -> Expression | None:
        if value is not None and (error := loop_count_expression_error(value)):
            raise ValueError(error)
        return value


class ConditionalStep(BaseStep):
    """A typed if/else conditional block."""
    step_type: Literal[StepType.CONDITIONAL] = StepType.CONDITIONAL
    name: str = Field(default="If", description="Name of the conditional branch")
    condition: Optional[Expression] = Field(default=None, description="Typed FluentControl condition expression")
    left_variable: str = Field(..., description="Declared variable name on the left-hand side")
    operator: str = Field(..., description="Comparator operator")
    right_value: Expression = Field(..., description="Typed right-hand operand expression")
    right_is_variable: bool = Field(default=False, description="Whether right_value refers to another variable")
    then_steps: list["Step"] = Field(default_factory=list, description="Steps executed when the condition is true")
    else_steps: list["Step"] = Field(default_factory=list, description="Steps executed when the condition is false")

    @field_validator("condition", mode="before")
    @classmethod
    def _coerce_condition_expression(cls, value: Any) -> Expression | None:
        return _coerce_optional_expression_field(value)

    @field_validator("right_value", mode="before")
    @classmethod
    def _coerce_right_value_expression(cls, value: Any) -> Expression:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return coerce_literal_expression("")
            if text in {"True", "False"}:
                return coerce_source_expression(text)
            try:
                return parse_expression(text) if _looks_like_source_expression(text) else coerce_literal_expression(text)
            except Exception:
                return coerce_literal_expression(text)
        return coerce_source_expression(value)


# Union type for all step variants - includes GenericStep for dynamic types
Step = Union[
    AddLabwareStep,
    RemoveLabwareStep,
    GetHeadAdapterStep,
    DropHeadAdapterStep,
    PickUpTipsStep,
    SetTipsBackStep,
    AspirateStep,
    DispenseStep,
    RgaTransferLabwareStep,
    CgaGetFingersStep,
    CgaDropFingersStep,
    Mca384MixStep,
    Mca384EmptyTipsStep,
    Mca384GetTipsStep,
    Mca384DropTipsStep,
    Mca384MoveArmStep,
    LihaAspirateStep,
    LihaDispenseStep,
    LihaMixStep,
    LihaGetTipsStep,
    LihaDropTipsStep,
    LihaEmptyTipsStep,
    LihaDetectLiquidStep,
    GenerateReportStep,
    WaitStep,
    SetVariableStep,
    CalculateVariableStep,
    CommentStep,
    UserPromptStep,
    ExecuteVbScriptStep,
    TeGioSetPwmOutputStep,
    LeaveStep,
    StartTimerStep,
    WaitForTimerStep,
    ExportVariableStep,
    ImportVariableStep,
    QueryVariableStep,
    InitializeDeviceStep,
    ExecuteApplicationStep,
    DelayStep,
    SetLocationStep,
    SubRoutineStep,
    ApplicationDriverMacroStep,
    MoveAxisCommandStep,
    StartMoveCommandStep,
    WaitForAsyncResponseStep,
    EndScriptStep,
    ScriptGroupStep,
    LoopStep,
    ConditionalStep,
    GenericStep
]


class Group(BaseModel):
    """A group of related steps in the protocol."""
    name: str = Field(..., description="Group name (e.g., 'Worktable Setup', 'Pipetting')")
    steps: list[Step] = Field(default_factory=list, description="Steps in this group")
    line_number: Optional[int] = None  # Auto-assigned


class Protocol(BaseModel):
    """
    Complete protocol representation.

    This is the top-level IR that gets rendered to XML.
    """
    name: str = Field(..., description="Protocol name")
    comment: str = Field(default="", description="Optional protocol comment")
    variables: list[str] = Field(default_factory=list, description="List of variable names used in protocol")
    variable_defaults: dict[str, Expression] = Field(
        default_factory=dict,
        description="Declared variable default expressions emitted into VariableDeclarations",
    )
    variable_metadata: dict[str, dict[str, str]] = Field(
        default_factory=dict,
        description="Optional FluentControl variable metadata such as scope and type name",
    )
    groups: list[Group] = Field(default_factory=list, description="Protocol groups")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("variable_defaults", mode="before")
    @classmethod
    def _coerce_variable_defaults(cls, value: Any) -> dict[str, Expression]:
        if not value:
            return {}
        if not isinstance(value, dict):
            raise TypeError("variable_defaults must be a mapping")
        return {str(name): coerce_literal_expression(default) for name, default in value.items()}

    # Optional overrides for config defaults
    worktable_guid: Optional[str] = None
    worktable_name: Optional[str] = None
    liquid_class: Optional[str] = None
    device_alias: Optional[str] = None
    available_id: Optional[str] = None
    rga_module_name: Optional[str] = None

    def total_steps(self) -> int:
        """Count total steps across all groups."""
        return sum(self._count_steps(g.steps) for g in self.groups)

    def _count_steps(self, steps: list[Step]) -> int:
        count = 0
        for step in steps:
            count += 1
            if isinstance(step, LoopStep):
                count += self._count_steps(step.steps)
            elif isinstance(step, ScriptGroupStep):
                count += self._count_steps(step.steps)
            elif isinstance(step, ConditionalStep):
                count += self._count_steps(step.then_steps)
                count += self._count_steps(step.else_steps)
        return count

    def assign_line_numbers(self) -> None:
        """Auto-assign line numbers to all groups and steps."""
        self._current_line = 1
        for group in self.groups:
            group.line_number = self._current_line
            self._current_line += 1
            self._assign_steps_line_numbers(group.steps)

    def _assign_steps_line_numbers(self, steps: list[Step]) -> None:
        for step in steps:
            step.line_number = self._current_line
            self._current_line += 1
            if isinstance(step, LoopStep):
                self._assign_steps_line_numbers(step.steps)
            elif isinstance(step, ScriptGroupStep):
                self._assign_steps_line_numbers(step.steps)
            elif isinstance(step, ConditionalStep):
                self._assign_steps_line_numbers(step.then_steps)
                self._assign_steps_line_numbers(step.else_steps)


# Type mapping from IR step types to command IDs
# Note: MCA384 commands use device-prefixed IDs in the reference
STEP_TO_COMMAND_ID = {
    StepType.ADD_LABWARE: "AddLabware",
    StepType.REMOVE_LABWARE: "RemoveLabware",
    StepType.GET_HEAD_ADAPTER: "Mca384GetHeadAdapter",
    StepType.DROP_HEAD_ADAPTER: "Mca384DropHeadAdapter",
    StepType.PICK_UP_TIPS: "Mca384PickUpTips",
    StepType.SET_TIPS_BACK: "Mca384SetTipsBack",
    StepType.ASPIRATE: "Mca384Aspirate",
    StepType.DISPENSE: "Mca384Dispense",
    # LiHa commands
    StepType.LIHA_ASPIRATE: "LihaAspirate",
    StepType.LIHA_DISPENSE: "LihaDispense",
    StepType.LIHA_MIX: "LihaMix",
    StepType.LIHA_GET_TIPS: "LihaGetTips",
    StepType.LIHA_DROP_TIPS: "LihaDropTips",
    StepType.MCA384_GET_TIPS: "Mca384GetTips",
    StepType.MCA384_DROP_TIPS: "Mca384DropTips",
    StepType.MCA384_MOVE_ARM: "Mca384MoveArm",
    # RGA (Robotic Gripper Arm) commands
    StepType.RGA_TRANSFER_LABWARE: "ApplicationDriverMacro",  # Uses RGA1_TransferLabware macro
    StepType.CGA_GET_FINGERS: "CgaGetFingers",
    StepType.CGA_DROP_FINGERS: "CgaDropFingers",
    # Mixing command
    StepType.MCA384_MIX: "Mca384Mix",
    StepType.MCA384_EMPTY_TIPS: "Mca384EmptyTips",
    # Loop command
    StepType.LOOP: "LoopGroup",
    # Variable commands
    StepType.SET_VARIABLE: "SetVariable",
    # CalculateVariable uses SetVariable with Expression in Tecan, but let's assume we map to something
    # For now map to SetVariable, Renderer will handle the specifics
    StepType.CALCULATE_VARIABLE: "SetVariable", 
    # Wait/Timer command
    StepType.WAIT: "Wait",  # Uses Timer command
    # Annotation and control
    StepType.COMMENT: "Comment",
    StepType.USER_PROMPT: "UserPrompt",
    StepType.START_TIMER: "StartTimer",
    StepType.WAIT_FOR_TIMER: "WaitForTimer",
    # LiHa empty tips
    StepType.LIHA_EMPTY_TIPS: "LihaEmptyTips",
    StepType.LIHA_DETECT_LIQUID: "LihaDetectLiquid",
    StepType.GENERATE_REPORT: "GenerateReportStatement",
    # Variable import/export/query
    StepType.EXPORT_VARIABLE: "ExportVariable",
    StepType.IMPORT_VARIABLE: "ImportVariable",
    StepType.QUERY_VARIABLE: "QueryVariable",
    # Device initialization
    StepType.INITIALIZE_DEVICE: "InitializeDevice",
    # External/runtime commands
    StepType.EXECUTE_APPLICATION: "ExecuteApplication",
    StepType.DELAY: "Delay",
    # Worktable/routines
    StepType.SET_LOCATION: "SetLocation",
    StepType.SUBROUTINE: "SubRoutine",
    StepType.APPLICATION_DRIVER_MACRO: "ApplicationDriverMacro",
    StepType.MOVE_AXIS_COMMAND: "MoveAxisCommand",
    StepType.START_MOVE_COMMAND: "StartMoveCommand",
    StepType.WAIT_FOR_ASYNC_RESPONSE: "WaitForAsyncResponse",
    StepType.END_SCRIPT: "EndScript",
    StepType.EXECUTE_VB_SCRIPT: "ExecuteVbScript",
    StepType.TEGIO_SET_PWM_OUTPUT: "TeGioSetPWMOutput",
    StepType.LEAVE: "Leave",
}
