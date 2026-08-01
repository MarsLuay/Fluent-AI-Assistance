"""Parse a FluentControl .xscr into a Pydantic ``Protocol`` IR.

Walks the XML tree, identifies each ``<Object>``'s command type, and
emits the corresponding Step subclass. Inverse of
``fluentcoder.compiler.renderer``.

Scope (v1.1): the steps fluentcoder itself emits — AddLabware,
GetHeadAdapter / DropHeadAdapter, PickUpTips / SetTipsBack, Aspirate /
Dispense, Loop, Conditional, RGA gripper transfers, CGA finger
operations, basic variable / wait / comment ops. Anything else lands
as a ``GenericStep`` with the raw type retained, so round-trip parity
flags it explicitly rather than silently corrupting.

Field extraction is intentionally minimal: only the fields the IR Step
classes actually carry. Defaults (line numbers, device aliases, blowout
airgaps) are recomputed by the renderer at re-emit time.
"""

from __future__ import annotations

import re
from .. import xml_compat as ET
from pathlib import Path
from typing import Iterator, Optional, Union

from ..catalog.xcmp import _find, _local, _text
from ..expressions import is_expression_field, parse_or_preserve_source_expression
from ..ir.schema import (
    AddLabwareStep, ApplicationDriverMacroStep, AspirateStep, CgaDropFingersStep, CgaGetFingersStep,
    CommentStep, ConditionalStep, DelayStep, DispenseStep, DropHeadAdapterStep,
    ExecuteApplicationStep, ExportVariableStep, GenericStep, GetHeadAdapterStep,
    EndScriptStep, MoveAxisCommandStep, StartMoveCommandStep, WaitForAsyncResponseStep,
    Group, ImportVariableStep, InitializeDeviceStep, LihaAspirateStep,
    LihaDispenseStep, LihaDropTipsStep, LihaEmptyTipsStep, LihaGetTipsStep,
    LihaMixStep, LihaDetectLiquidStep, GenerateReportStep, LoopStep, Mca384DropTipsStep, Mca384EmptyTipsStep,
    Mca384GetTipsStep, Mca384MixStep, Mca384MoveArmStep, PickUpTipsStep,
    Protocol, QueryVariableStep, RemoveLabwareStep, RgaTransferLabwareStep,
    ScriptGroupStep, SetLocationStep, SetTipsBackStep, SetVariableStep,
    StartTimerStep, STEP_TO_COMMAND_ID, Step, StepType, SubRoutineStep,
    UserPromptStep, VariableMapping, WaitForTimerStep, WaitStep,
    ExecuteVbScriptStep, TeGioSetPwmOutputStep, LeaveStep,
)


# Reverse mapping: command-id string -> StepType enum. Preserve the first
# mapping so "SetVariable" resolves to SET_VARIABLE instead of the secondary
# CALCULATE_VARIABLE renderer alias.
COMMAND_ID_TO_STEP_TYPE: dict[str, StepType] = {}
for _step_type, _command_id in STEP_TO_COMMAND_ID.items():
    COMMAND_ID_TO_STEP_TYPE.setdefault(_command_id, _step_type)

COMMAND_ID_ALIASES = {
    "LihaPickUp": "LihaGetTips",
    "LihaSetTipsBack": "LihaDropTips",
    "CommentStatement": "Comment",
    "DelayStatement": "Delay",
    "SetVariableStatement": "SetVariable",
    "UserPromptStatement": "UserPrompt",
    "StartTimerStatement": "StartTimer",
    "WaitForTimerStatement": "WaitForTimer",
    "SubRoutineStatement": "SubRoutine",
    "RUPStandardStatement": "UserPrompt",
    "InitDevice": "InitializeDevice",
    "HomeDevice": "InitializeDevice",
    "ResetDevice": "InitializeDevice",
    "PrimeDevice": "InitializeDevice",
    "InitializeCommand": "InitializeDevice",
    "HomeCommand": "InitializeDevice",
    "ResetCommand": "InitializeDevice",
    "PrimeCommand": "InitializeDevice",
    "LihaDetectLiquidScriptCommand": "LihaDetectLiquid",
    "GenerateReportStatementDataV1": "GenerateReportStatement",
}

_INIT_TYPE_BY_COMMAND_ID = {
    "InitializeDevice": "Initialize",
    "InitDevice": "Initialize",
    "InitializeCommand": "Initialize",
    "HomeDevice": "Home",
    "HomeCommand": "Home",
    "ResetDevice": "Reset",
    "ResetCommand": "Reset",
    "PrimeDevice": "Prime",
    "PrimeCommand": "Prime",
}

RAW_PRESERVE_COMMAND_IDS = {
    "Mca384Aspirate",
    "Mca384Dispense",
}

# Suffix-strip regex: '<Cmd>ScriptCommandDataV2' → '<Cmd>'
_CMD_ID_RE = re.compile(r"(ScriptCommand)?DataV?\d*$")


def parse_xscr(path: Union[Path, str]) -> Protocol:
    """Parse a .xscr file into a ``Protocol``."""
    tree = ET.parse(str(path))
    root = tree.getroot()

    payload = _find(root, "Payload")
    name = _text(_find(payload, "ObjectName")) or "Untitled Protocol"
    comment = _text(_find(payload, "Comment")) or ""
    worktable_guid, worktable_name = _parse_worktable_reference(payload)

    variables, variable_defaults, variable_metadata = _parse_variable_declarations(root)

    groups: list[Group] = []
    script_group = next(
        (e for e in root.iter()
         if isinstance(e.tag, str) and _local(e.tag) == "ScriptGroup"),
        None,
    )
    if script_group is not None:
        objects = _find(script_group, "Objects")
        if objects is not None:
            object_children = [
                obj for obj in objects
                if isinstance(obj.tag, str) and _local(obj.tag) == "Object"
            ]
            if object_children and any(
                "ScriptGroupData" in (obj.attrib.get("Type") or "")
                for obj in object_children
            ):
                current_group: Optional[Group] = None
                for group_obj in object_children:
                    if "ScriptGroupData" in (group_obj.attrib.get("Type") or ""):
                        group_name = _extract_field(group_obj, "Name") or "Steps"
                        stmts = _find_inner_statements(group_obj)
                        steps = _parse_statements(stmts) if stmts is not None else []
                        current_group = Group(name=group_name, steps=steps)
                        groups.append(current_group)
                        continue
                    if current_group is None:
                        current_group = Group(name="Steps", steps=[])
                        groups.append(current_group)
                    step = _parse_step_object(group_obj)
                    if step is not None:
                        current_group.steps.append(step)
            elif object_children:
                groups.append(Group(
                    name=_text(_find(script_group, "Name")) or "Steps",
                    steps=_parse_statements(objects),
                ))

    return Protocol(
        name=name,
        comment=comment,
        variables=variables,
        variable_defaults=variable_defaults,
        variable_metadata=variable_metadata,
        groups=groups,
        worktable_guid=worktable_guid,
        worktable_name=worktable_name,
    )


def _parse_worktable_reference(payload: Optional[ET.Element]) -> tuple[Optional[str], Optional[str]]:
    if payload is None:
        return None, None
    for child in list(payload):
        if not isinstance(child.tag, str) or _local(child.tag) != "Reference":
            continue
        if _text(_find(child, "TypeId")) != "WorktableWorkspace":
            continue
        return _text(_find(child, "Guid")), _text(_find(child, "ObjectName"))
    return None, None


def _parse_variable_declarations(
    root: ET.Element,
) -> tuple[
    list[str],
    dict[str, Union[float, int, str]],
    dict[str, dict[str, str]],
]:
    """Read ``<VariableDeclarations>`` (double-nested) entries.

    Each ``<anyType>`` block carries ``<Name>``, ``<TypeName>``, and
    ``<Values>/<string>`` (the default). FluentControl uses
    ``Floating Point``/``Integer``/``String`` as type names; coerce
    to the matching Python type.
    """
    var_root = next(
        (e for e in root.iter()
         if isinstance(e.tag, str) and _local(e.tag) == "VariableDeclarations"),
        None,
    )
    if var_root is None:
        return [], {}, {}

    variables: list[str] = []
    defaults: dict[str, Union[float, int, str]] = {}
    metadata: dict[str, dict[str, str]] = {}
    for elem in var_root.iter():
        if not isinstance(elem.tag, str):
            continue
        if _local(elem.tag) != "anyType":
            continue
        name_el = next((c for c in elem.iter()
                        if isinstance(c.tag, str) and _local(c.tag) == "Name"), None)
        type_el = next((c for c in elem.iter()
                        if isinstance(c.tag, str) and _local(c.tag) == "TypeName"), None)
        values_el = next((c for c in elem.iter()
                          if isinstance(c.tag, str) and _local(c.tag) == "Values"), None)
        if name_el is None or not name_el.text:
            continue
        var_name = name_el.text.strip()
        type_name = (type_el.text or "").strip() if type_el is not None else ""
        default_text = ""
        if values_el is not None:
            for sub in values_el:
                if isinstance(sub.tag, str) and _local(sub.tag) == "string":
                    default_text = (sub.text or "").strip()
                    break
        default_value = _coerce_variable_default(default_text, type_name)
        variables.append(var_name)
        defaults[var_name] = default_value
        scope_el = next((c for c in elem.iter()
                         if isinstance(c.tag, str) and _local(c.tag) == "Scope"), None)
        scope = (scope_el.text or "").strip() if scope_el is not None else ""
        metadata[var_name] = {
            "scope": scope or "Script",
            "type_name": type_name or "Floating Point",
        }
    return variables, defaults, metadata


def _coerce_variable_default(text: str, type_name: str) -> Union[float, int, str]:
    if not text:
        return ""
    t = type_name.lower()
    if "integer" in t:
        try:
            return int(float(text))
        except ValueError:
            return text
    if "float" in t or "double" in t or "point" in t:
        try:
            return float(text)
        except ValueError:
            return text
    return text


# ── Step parsing ────────────────────────────────────────────────────


def _parse_statements(stmts: ET.Element) -> list[Step]:
    out: list[Step] = []
    pending_conditional: Optional[ConditionalStep] = None
    for child in stmts:
        if not isinstance(child.tag, str):
            continue
        if _local(child.tag) != "Object":
            continue
        if _is_command_suffix(child, "AlternateGroup") and pending_conditional is not None:
            pending_conditional.else_steps = _parse_body(child)
            pending_conditional = None
            continue
        step = _parse_step_object(child)
        if step is not None:
            _apply_step_metadata(step, child)
            out.append(step)
            pending_conditional = step if isinstance(step, ConditionalStep) else None
    return out


def _parse_step_object(obj: ET.Element) -> Optional[Step]:
    type_attr = obj.attrib.get("Type") or ""
    suffix = type_attr.rsplit(".", 1)[-1]

    if "LoopGroup" in suffix:
        return _parse_loop(obj)
    if "ConditionalGroup" in suffix:
        return _parse_conditional(obj)
    if "AlternateGroup" in suffix:
        return _raw_step("AlternateGroup", type_attr, obj)
    if "ScriptGroupData" in suffix:
        return ScriptGroupStep(
            name=_extract_field(obj, "Name") or "Steps",
            steps=_parse_body(obj),
        )

    raw_command_id = _CMD_ID_RE.sub("", suffix)
    command_id = _normalise_command_id(suffix)

    if "ApplicationDriverMacro" in suffix or command_id == "ApplicationDriverMacro":
        return _parse_application_driver_macro(obj)

    if "LegacyDriverMacro" in suffix or command_id == "LegacyDriverMacro":
        return _parse_legacy_driver_macro(obj)

    if "GenerateReportStatement" in suffix or command_id == "GenerateReportStatement":
        return _parse_generate_report(obj)

    if "MoveAxisCommandScriptStatement" in suffix:
        return _parse_move_axis_command(obj)
    if "StartMoveCommandScriptStatement" in suffix:
        return _parse_start_move_command(obj)
    if "WaitForAsyncResponseScriptStatement" in suffix:
        return _parse_wait_for_async_response(obj)
    if "EndScriptStatement" in suffix:
        return _parse_end_script(obj)
    if "RUPWorktableStatement" in suffix:
        return _parse_user_prompt(obj, suffix=suffix)
    if "ExecuteVbScriptStatement" in suffix:
        return _parse_execute_vb_script(obj)
    if "TeGioSetPWMOutputStatement" in suffix:
        return _parse_tegio_set_pwm_output(obj)
    if "LeaveStatement" in suffix:
        return _parse_leave(obj)

    if command_id in RAW_PRESERVE_COMMAND_IDS:
        return _raw_step(command_id, type_attr, obj)
    step_type = COMMAND_ID_TO_STEP_TYPE.get(command_id)

    if step_type == StepType.ADD_LABWARE:
        return AddLabwareStep(
            labware_type=_extract_field(obj, "LabwareType") or "",
            label=_extract_field(obj, "LabwareLable") or "",
            location=_extract_field(obj, "Location") or "Site",
            position=_parse_registered_expression(
                command_id,
                "Position",
                _extract_field(obj, "Position") or "1",
            ),
        )
    if step_type == StepType.REMOVE_LABWARE:
        return RemoveLabwareStep(labware_name=_extract_field(obj, "LabwareName") or "")
    if step_type == StepType.GET_HEAD_ADAPTER:
        return GetHeadAdapterStep(
            labware_name=_extract_field(obj, "LabwareName") or "EVA1",
            device_alias=_extract_field(obj, "DeviceAlias"),
            available_id=_extract_available_id(obj),
        )
    if step_type == StepType.DROP_HEAD_ADAPTER:
        return DropHeadAdapterStep(
            labware_name=_extract_field(obj, "LabwareName"),
            device_alias=_extract_field(obj, "DeviceAlias"),
            available_id=_extract_available_id(obj),
        )
    if step_type == StepType.PICK_UP_TIPS:
        return PickUpTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            partial_columns=_parse_int(_extract_field(obj, "PartialColumns"), default=24),
            partial_rows=_parse_int(_extract_field(obj, "PartialRows"), default=16),
            device_alias=_extract_field(obj, "DeviceAlias"),
            available_id=_extract_available_id(obj),
        )
    if step_type == StepType.SET_TIPS_BACK:
        return SetTipsBackStep(
            labware_name=_extract_field(obj, "LabwareName"),
            device_alias=_extract_field(obj, "DeviceAlias"),
            available_id=_extract_available_id(obj),
        )
    if step_type == StepType.ASPIRATE:
        return AspirateStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_registered_expression(
                command_id,
                "Volume",
                _extract_field(obj, "Volume") or "0",
            ),
            liquid_class=_extract_field(obj, "LiquidClassName"),
        )
    if step_type == StepType.DISPENSE:
        return DispenseStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_registered_expression(
                command_id,
                "Volume",
                _extract_field(obj, "Volume") or "0",
            ),
            liquid_class=_extract_field(obj, "LiquidClassName"),
        )
    if step_type == StepType.RGA_TRANSFER_LABWARE:
        return _parse_rga_transfer(obj)
    if step_type == StepType.CGA_GET_FINGERS:
        return CgaGetFingersStep(
            labware_name=_extract_field(obj, "LabwareName"),
            device_alias=_extract_field(obj, "DeviceAlias"),
            available_id=_extract_available_id(obj),
        )
    if step_type == StepType.CGA_DROP_FINGERS:
        return CgaDropFingersStep(
            labware_name=_extract_field(obj, "LabwareName"),
            device_alias=_extract_field(obj, "DeviceAlias"),
            available_id=_extract_available_id(obj),
            use_source_as_back_position=_extract_field(obj, "Backs") or "BackToPosition",
        )
    if step_type == StepType.SET_VARIABLE:
        return SetVariableStep(
            variable_name=_extract_field(obj, "VariableName") or _extract_field(obj, "Name") or "",
            value=_parse_registered_expression(
                command_id,
                "Value",
                _extract_field(obj, "Value") or "",
            ),
        )
    if step_type == StepType.COMMENT:
        return CommentStep(comment=_extract_field(obj, "Text") or _extract_field(obj, "Comment") or "")
    if step_type == StepType.WAIT:
        seconds_text = _extract_field(obj, "Seconds") or _extract_field(obj, "Duration")
        return WaitStep(
            duration_seconds=_parse_registered_expression(
                command_id,
                "Duration",
                seconds_text or "0",
            )
        )
    if step_type == StepType.DELAY:
        return DelayStep(
            delay=_parse_registered_expression(
                command_id,
                "Delay",
                _extract_field(obj, "Delay") or "0",
            )
        )
    if step_type == StepType.USER_PROMPT:
        return _parse_user_prompt(obj, suffix=suffix)
    if step_type == StepType.START_TIMER:
        return StartTimerStep(timer=_parse_int(_extract_field(obj, "Timer"), default=1))
    if step_type == StepType.WAIT_FOR_TIMER:
        return WaitForTimerStep(
            timer=_parse_int(_extract_field(obj, "Timer"), default=1),
            duration_seconds=_parse_registered_expression(
                command_id,
                "Duration",
                _extract_field(obj, "Duration") or "0",
            ),
        )
    if step_type == StepType.EXPORT_VARIABLE:
        return ExportVariableStep(
            variables=_list_values(obj, "Variables"),
            export_file=_strip_wrapping_quotes(_extract_field(obj, "ExportFile") or ""),
            write_header=_parse_bool(_extract_field(obj, "WriteHeader")),
            replace_existing_file=_parse_bool(_extract_field(obj, "ReplaceExistingFile")),
            export_strings_with_quotes=_parse_bool(_extract_field(obj, "ExportStringsWithQuotes")),
            delimiter_code=_parse_int(_extract_field(obj, "DelimiterCode"), default=59),
        )
    if step_type == StepType.IMPORT_VARIABLE:
        return ImportVariableStep(
            variables=_list_values(obj, "Variables"),
            import_file=_strip_wrapping_quotes(_extract_field(obj, "ImportFile") or ""),
            read_line=_parse_bool(_extract_field(obj, "ReadLine")),
            line=_parse_int(_extract_field(obj, "Line"), default=1),
            start_in_column=_parse_bool(_extract_field(obj, "StartInColumn")),
            column=_parse_int(_extract_field(obj, "Column"), default=1),
            has_header=_parse_bool(_extract_field(obj, "HasHeader")),
            delimiter_code=_parse_int(_extract_field(obj, "DelimiterCode"), default=59),
        )
    if step_type == StepType.QUERY_VARIABLE:
        return QueryVariableStep(
            variable_name=_extract_field(obj, "VariableName") or _extract_field(obj, "Name") or "",
            query_prompt=_extract_field(obj, "QueryPrompt") or "",
            limit_range=_parse_bool(_extract_field(obj, "LimitRange")),
        )
    if step_type == StepType.INITIALIZE_DEVICE:
        return _parse_initialize_device(obj, raw_command_id=raw_command_id)
    if step_type == StepType.EXECUTE_APPLICATION:
        return ExecuteApplicationStep(
            application=_extract_field(obj, "Application") or "",
            arguments=_extract_field(obj, "Arguments") or "",
            wait=_parse_bool(_extract_field(obj, "Wait"), default=True),
            store_return=_parse_bool(_extract_field(obj, "StoreReturn")),
            variable=_extract_field(obj, "Variable") or "",
        )
    if step_type == StepType.SET_LOCATION:
        return SetLocationStep(
            labware=_extract_field(obj, "Labware") or "",
            location=_extract_field(obj, "Location") or "Site",
            site=_parse_registered_expression(
                command_id,
                "Site",
                _extract_field(obj, "Site") or "1",
            ),
            rotation=_parse_int(_extract_field(obj, "Rotation"), default=0),
        )
    if step_type == StepType.LIHA_GET_TIPS:
        if raw_command_id == "LihaPickUp" or _liha_get_tips_requires_raw(obj):
            return _raw_step(command_id, type_attr, obj)
        return LihaGetTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or None,
        )
    if step_type == StepType.LIHA_DROP_TIPS:
        return LihaDropTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or None,
        )
    if step_type == StepType.LIHA_ASPIRATE:
        return _parse_liha_pipette(obj, step_type=StepType.LIHA_ASPIRATE)
    if step_type == StepType.LIHA_DISPENSE:
        return _parse_liha_pipette(obj, step_type=StepType.LIHA_DISPENSE)
    if step_type == StepType.LIHA_MIX:
        return _parse_liha_pipette(obj, step_type=StepType.LIHA_MIX)
    if step_type == StepType.LIHA_DETECT_LIQUID:
        return _parse_liha_detect_liquid(obj)
    if step_type == StepType.LIHA_EMPTY_TIPS:
        return LihaEmptyTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_registered_expression(
                command_id,
                "Volume",
                _extract_field(obj, "Volume") or "0",
            ),
            liquid_class=_extract_field(obj, "LiquidClassName"),
        )
    if step_type == StepType.MCA384_GET_TIPS:
        return Mca384GetTipsStep(labware_name=_extract_field(obj, "LabwareName") or None)
    if step_type == StepType.MCA384_DROP_TIPS:
        return Mca384DropTipsStep(labware_name=_extract_field(obj, "LabwareName") or None)
    if step_type == StepType.MCA384_MOVE_ARM:
        return Mca384MoveArmStep(
            movement_type=_extract_field(obj, "MovementType") or "GlobalZTravel",
            labware_name=_extract_field(obj, "LabwareName") or None,
        )
    if step_type == StepType.MCA384_MIX:
        return Mca384MixStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_registered_expression(
                command_id,
                "Volume",
                _extract_field(obj, "Volume") or "0",
            ),
            cycles=_parse_registered_expression(
                command_id,
                "Cycles",
                _extract_field(obj, "Cycles") or "10",
            ),
            liquid_class=_extract_field(obj, "LiquidClassName"),
        )
    if step_type == StepType.MCA384_EMPTY_TIPS:
        return Mca384EmptyTipsStep(
            labware_name=_extract_field(obj, "LabwareName") or "",
            volume=_parse_registered_expression(
                command_id,
                "Volume",
                _extract_field(obj, "Volume") or "0",
            ),
            liquid_class=_extract_field(obj, "LiquidClassName") or None,
        )
    if step_type == StepType.SUBROUTINE:
        return SubRoutineStep(
            subroutine=_strip_wrapping_quotes(_extract_field(obj, "SubRoutine") or ""),
            execution_mode=_extract_field(obj, "ExecutionMode") or "Synchronous",
            variable_mappings_start=_parse_variable_mappings(obj, "VariableMappingsStart"),
            variable_mappings_end=_parse_variable_mappings(obj, "VariableMappingsEnd"),
        )

    return _raw_step(command_id or suffix, type_attr, obj)


def _parse_initialize_device(obj: ET.Element, *, raw_command_id: str) -> InitializeDeviceStep:
    init_type = _extract_field(obj, "InitType") or _INIT_TYPE_BY_COMMAND_ID.get(raw_command_id, "Initialize")
    device_alias = _extract_field(obj, "DeviceAlias")
    available_id = _extract_field(obj, "AvailableID")
    if not device_alias:
        device_alias = _extract_field(obj, "Name")
    return InitializeDeviceStep(
        device_alias=device_alias or None,
        available_id=available_id or None,
        init_type=init_type,
    )


def _parse_user_prompt(obj: ET.Element, *, suffix: str) -> UserPromptStep:
    """Decode plain ``UserPromptStatement`` and image ``RUPStandardStatement``."""
    if suffix == "RUPStandardStatement" or "RUPStandardStatement" in suffix:
        image_path = _extract_field(obj, "SelectedImagePath")
        if image_path is None:
            image_path = ""
        return UserPromptStep(
            prompt=(
                _extract_field(obj, "MessageText")
                or _extract_field(obj, "CheckMessagePrompt")
                or ""
            ),
            timeout=_parse_int(_extract_field(obj, "RUPTimeOut"), default=0),
            auto_close=_parse_optional_bool(_extract_field(obj, "RUPAutoClose")),
            image_path=image_path,
            screen_title=_extract_field(obj, "RUPScreenTitle") or None,
            rup_kind="standard",
        )
    if "RUPWorktableStatement" in suffix:
        return UserPromptStep(
            prompt=_extract_field(obj, "LabwareDescriptionGui") or "",
            timeout=_parse_int(_extract_field(obj, "RUPTimeOut"), default=0),
            auto_close=_parse_optional_bool(_extract_field(obj, "RUPAutoClose")),
            image_path=_extract_field(obj, "CustomDetailImageFilePath") or None,
            screen_title=_extract_field(obj, "RUPScreenTitle") or None,
            rup_kind="worktable",
            selected_labware_name=_extract_field(obj, "LabwareName"),
            selected_labware_type=_extract_field(obj, "LabwareTypeName"),
            grid=_parse_int(_extract_field(obj, "Grid"), default=0) or None,
            site=_parse_int(_extract_field(obj, "Site"), default=0) or None,
            raw_xml=ET.tostring(obj, encoding="unicode"),
        )
    return UserPromptStep(
        prompt=_extract_field(obj, "Prompt") or "",
        timeout=_parse_int(_extract_field(obj, "Timeout"), default=0),
        auto_close=_parse_optional_bool(_extract_field(obj, "AutoClose")),
    )


def _parse_execute_vb_script(obj: ET.Element) -> ExecuteVbScriptStep:
    mode = _extract_field(obj, "ExecutionMode") or "Synchronous"
    return ExecuteVbScriptStep(
        vb_script=_strip_wrapping_quotes(_extract_field(obj, "VbScript") or ""),
        execution_mode=mode,
        raw_xml=ET.tostring(obj, encoding="unicode"),
    )


def _parse_tegio_set_pwm_output(obj: ET.Element) -> TeGioSetPwmOutputStep:
    return TeGioSetPwmOutputStep(
        pin_number=_extract_field(obj, "PinNumber") or "",
        duty_cycle=_parse_registered_expression(
            "TeGioSetPWMOutput",
            "DutyCycle",
            _extract_field(obj, "DutyCycle") or "0",
        ),
        duration=_parse_registered_expression(
            "TeGioSetPWMOutput",
            "Duration",
            _extract_field(obj, "Duration") or "0",
        ),
        raw_xml=ET.tostring(obj, encoding="unicode"),
    )


def _parse_leave(obj: ET.Element) -> LeaveStep:
    return LeaveStep(raw_xml=ET.tostring(obj, encoding="unicode"))


def _parse_move_axis_command(obj: ET.Element) -> MoveAxisCommandStep:
    charge = None
    for elem in obj.iter():
        if isinstance(elem.tag, str) and _local(elem.tag) == "ChargeCondition" and elem.text:
            charge = elem.text.strip()
            break
    return MoveAxisCommandStep(
        available_id=_extract_field(obj, "AvailableID") or _extract_nested_available_id(obj),
        id_label=_extract_field(obj, "IdLabel"),
        position=_parse_registered_expression(
            "MoveAxisCommand",
            "Position",
            _extract_field(obj, "Position") or "0",
        ),
        charge_condition=parse_or_preserve_source_expression(charge) if charge else None,
        max_speed=_extract_field(obj, "MaxSpeed"),
        acceleration=_extract_field(obj, "Acceleration"),
        deceleration=_extract_field(obj, "Deceleration"),
        raw_xml=ET.tostring(obj, encoding="unicode"),
    )


def _parse_start_move_command(obj: ET.Element) -> StartMoveCommandStep:
    return StartMoveCommandStep(
        available_id=_extract_field(obj, "AvailableID") or _extract_nested_available_id(obj),
        id_label=_extract_field(obj, "IdLabel"),
        raw_xml=ET.tostring(obj, encoding="unicode"),
    )


def _parse_wait_for_async_response(obj: ET.Element) -> WaitForAsyncResponseStep:
    return WaitForAsyncResponseStep(
        raw_xml=ET.tostring(obj, encoding="unicode"),
    )


def _parse_end_script(obj: ET.Element) -> EndScriptStep:
    return_code = "Success"
    for elem in obj.iter():
        if isinstance(elem.tag, str) and _local(elem.tag) == "ScriptExecutionResult" and elem.text:
            return_code = elem.text.strip()
            break
    return EndScriptStep(
        return_code=return_code,
        raw_xml=ET.tostring(obj, encoding="unicode"),
    )


def _extract_nested_available_id(obj: ET.Element) -> Optional[str]:
    for elem in obj.iter():
        if isinstance(elem.tag, str) and _local(elem.tag) == "AvailableID" and elem.text:
            return elem.text.strip()
    return None


def _parse_position(text: Optional[str]) -> Union[int, str]:
    raw = (text or "1").strip()
    try:
        return max(1, int(float(raw)))
    except (ValueError, TypeError):
        return raw


def _raw_step(command_id: str, type_attr: str, obj: ET.Element) -> GenericStep:
    return GenericStep(
        step_type=command_id,
        parameters={
            "raw_type": type_attr,
            "raw_xml": ET.tostring(obj, encoding="unicode"),
        },
    )


def _liha_get_tips_requires_raw(obj: ET.Element) -> bool:
    diti_type = _extract_diti_type_available_id(obj)
    if diti_type and diti_type != "TOOLTYPE:LiHa.TecanDiTi/TOOLNAME:FCA, 1000ul SBS":
        return True
    return False


def _liha_pipette_requires_raw(obj: ET.Element) -> bool:
    if _parse_bool(_extract_field(obj, "IsLiquidClassNameByExpressionEnabled")):
        return True
    if _extract_field(obj, "LiquidClassNameByExpression"):
        return True
    if _extract_field(obj, "LiquidClassSelectionMode") in {"SingleByExpression", "MultiByExpression"}:
        return True
    return False


def _liha_pipette_preserves_raw_xml(obj: ET.Element) -> bool:
    if _liha_pipette_requires_raw(obj):
        return True
    volumes = _list_values(obj, "Volumes")
    if len(volumes) > 1:
        return True
    tip_spacing = _extract_field(obj, "TipSpacing")
    if tip_spacing not in (None, "", "9"):
        return True
    selected = _extract_field(obj, "SelectedWellsString")
    if selected not in (None, "", "A1 - H1", "A1"):
        return True
    serialized = _extract_field(obj, "SerializedWellIndexes")
    if serialized not in (None, "", "0>1>7;", "0;"):
        return True
    return False


def _parse_liha_pipette(obj: ET.Element, *, step_type: StepType) -> Step:
    if _liha_pipette_requires_raw(obj):
        command_id = STEP_TO_COMMAND_ID.get(step_type, step_type.value)
        return _raw_step(command_id, obj.attrib.get("Type") or "", obj)

    command_id = STEP_TO_COMMAND_ID.get(step_type, step_type.value)
    volumes = _list_values(obj, "Volumes")
    parsed_volumes = [
        _parse_registered_expression(command_id, "Volumes/string", value)
        for value in volumes
    ]
    first_volume = parsed_volumes[0] if parsed_volumes else 0.0
    liquid_class = (
        _extract_field(obj, "LiquidClassName")
        or _extract_field(obj, "LiquidClassNameBySelection")
    )
    common = dict(
        labware_name=_extract_field(obj, "LabwareName") or "",
        volume=first_volume,
        liquid_class=liquid_class,
        well_offset=(
            _parse_registered_expression(
                command_id,
                "WellOffset",
                _extract_field(obj, "WellOffset"),
            )
            if _extract_field(obj, "WellOffset") not in (None, "")
            else None
        ),
        volumes=parsed_volumes if len(parsed_volumes) > 1 else None,
        tip_channels=_parse_int_list(obj, "SelectedTipsIndexes") or None,
        raw_xml=ET.tostring(obj, encoding="unicode") if _liha_pipette_preserves_raw_xml(obj) else None,
    )
    if step_type == StepType.LIHA_ASPIRATE:
        return LihaAspirateStep(**common)
    if step_type == StepType.LIHA_DISPENSE:
        return LihaDispenseStep(**common)
    return LihaMixStep(
        **common,
        cycles=_parse_registered_expression(
            command_id,
            "Cycles",
            _extract_field(obj, "Cycles") or "10",
        ),
    )


def _parse_liha_detect_liquid(obj: ET.Element) -> LihaDetectLiquidStep:
    return LihaDetectLiquidStep(
        labware_name=_extract_field(obj, "LabwareName") or "",
        sensitivity=_parse_optional_float(_extract_field(obj, "SensitivityEx")),
        detection_speed=_parse_optional_float(_extract_field(obj, "DetectionSpeed")),
        tip_channels=_parse_int_list(obj, "SelectedTipsIndexes") or None,
        raw_xml=ET.tostring(obj, encoding="unicode"),
    )


def _parse_generate_report(obj: ET.Element) -> GenerateReportStep:
    return GenerateReportStep(
        report_name=_extract_field(obj, "Report") or "",
        write_csv=_parse_bool(_extract_field(obj, "WriteCSV")),
        print_report=_parse_bool(_extract_field(obj, "PrintReport")),
        all_labware_selected=_parse_bool(_extract_field(obj, "AllLabwareSelected"), default=True),
        raw_xml=ET.tostring(obj, encoding="unicode"),
    )


def _parse_legacy_driver_macro(obj: ET.Element) -> ApplicationDriverMacroStep:
    return _parse_driver_macro_element(obj, element_name="LegacyDriverMacro")


def _parse_int_list(obj: ET.Element, field_name: str) -> list[int]:
    container = _find_first(obj, field_name)
    if container is None:
        return []
    values: list[int] = []
    for el in container.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el.tag) == "int" and el.text:
            try:
                values.append(int(el.text.strip()))
            except ValueError:
                continue
    return values


def _parse_optional_float(text: Optional[str]) -> Optional[float]:
    if text is None or not str(text).strip():
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None


def _parse_registered_expression(command_id: str, field_path: str, source: str):
    if not is_expression_field(command_id, field_path):
        raise ValueError(
            f"{command_id}.{field_path} is not registered as a FluentControl expression field"
        )
    return parse_or_preserve_source_expression(str(source))


def _extract_diti_type_available_id(obj: ET.Element) -> Optional[str]:
    for elem in _walk_skipping_objects(obj):
        if not isinstance(elem.tag, str) or _local(elem.tag) != "DitiType":
            continue
        for child in elem.iter():
            if isinstance(child.tag, str) and _local(child.tag) == "AvailableID" and child.text:
                return child.text.strip()
    return None


def _parse_loop(obj: ET.Element) -> LoopStep:
    """``<LoopGroup>`` carries: ``<Name>``, ``<LoopVariable>``,
    ``<NumberOfLoops>``, and ``<Objects>`` (the body — direct child
    step Objects, no wrapping ``<Statements>``).
    """
    name_field = _extract_field(obj, "Name") or "Loop"
    loop_var = _extract_field(obj, "LoopVariable") or None
    iter_text = (
        _extract_field(obj, "NumberOfLoops")
        or _extract_field(obj, "NumberOfIterations")
        or _extract_field(obj, "Iterations")
        or "1"
    )
    body = _parse_body(obj)

    number_of_loops = _parse_registered_expression("LoopGroup", "NumberOfLoops", iter_text)
    try:
        iterations = max(1, int(iter_text))
    except (ValueError, TypeError):
        iterations = 1

    return LoopStep(
        name=name_field,
        iterations=iterations,
        loop_variable=loop_var,
        number_of_loops=number_of_loops,
        steps=body,
    )


def _parse_conditional(obj: ET.Element) -> ConditionalStep:
    """``<ConditionalGroup>`` carries ``<Name>``, ``<Condition>`` (a
    single string like ``'ph>=7'``), and ``<Objects>`` for the
    then-branch. A sibling ``<AlternateGroup>`` in the parent
    ``<Objects>`` list is attached as ``else_steps`` by
    ``_parse_statements``.
    """
    name_field = _extract_field(obj, "Name") or "If"
    condition = _extract_field(obj, "Condition") or ""
    left, op, right_value = _parse_condition_string(condition)
    body = _parse_body(obj)

    return ConditionalStep(
        name=name_field,
        disabled=_parse_bool(_extract_field(obj, "IsDisabledForExecution")),
        breakpoint=_parse_bool(_extract_field(obj, "IsBreakpoint")),
        condition=(
            _parse_registered_expression("ConditionalGroup", "Condition", condition)
            if condition
            else None
        ),
        left_variable=left,
        operator=op,
        right_value=right_value,
        right_is_variable=False,
        then_steps=body,
        else_steps=[],
    )


_BOOLEAN_CONDITION_SPLIT_RE = re.compile(r"\s+(?:AND|OR)\s+|\s*&\s*", re.IGNORECASE)
_CONDITION_RE = re.compile(r"^\s*(.+?)\s*(<>|==|!=|<=|>=|=|<|>)\s*(.+?)\s*$")


def _parse_condition_string(text: str) -> tuple[str, str, Union[str, int, float, bool]]:
    """Split ``'ph>=7'`` into ``('ph', '>=', 7)``."""
    if not text:
        return "", "==", ""
    first_clause = _BOOLEAN_CONDITION_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    m = _CONDITION_RE.match(first_clause)
    if not m:
        return text, "==", ""
    left, op, right = m.group(1), m.group(2), m.group(3)
    left = left.strip()
    if op == "=":
        op = "=="
    elif op == "<>":
        op = "!="
    coerced: Union[str, int, float, bool]
    try:
        coerced = int(right)
    except ValueError:
        try:
            coerced = float(right)
        except ValueError:
            stripped = right.strip().strip("'\"")
            if stripped.lower() in ("true", "false"):
                coerced = stripped.lower() == "true"
            else:
                coerced = stripped
    return left, op, coerced


def _normalise_command_id(suffix: str) -> str:
    command_id = _CMD_ID_RE.sub("", suffix)
    command_id = COMMAND_ID_ALIASES.get(command_id, command_id)
    if command_id.endswith("Statement"):
        base = command_id.removesuffix("Statement")
        if base in COMMAND_ID_TO_STEP_TYPE:
            return base
    return command_id


def _is_command_suffix(obj: ET.Element, command_id: str) -> bool:
    type_attr = obj.attrib.get("Type") or ""
    suffix = type_attr.rsplit(".", 1)[-1]
    return _normalise_command_id(suffix) == command_id or suffix == command_id


def _parse_body(obj: ET.Element) -> list[Step]:
    """Loop / conditional body lives in ``<Objects>`` (direct child step
    Objects). Plain groups use ``<Statements>``. Try both."""
    body_container = _find_first(obj, "Objects")
    if body_container is None:
        body_container = _find_first(obj, "Statements")
    if body_container is None:
        return []
    return _parse_statements(body_container)


def _parse_application_driver_macro(obj: ET.Element) -> ApplicationDriverMacroStep:
    """Parse ``ApplicationDriverMacro`` into a typed IR step."""
    return _parse_driver_macro_element(obj, element_name="ApplicationDriverMacro")


def _parse_driver_macro_element(
    obj: ET.Element,
    *,
    element_name: str,
) -> ApplicationDriverMacroStep:
    macro_el = _find_first(obj, element_name)
    macro_name = ""
    module_name = ""
    execution_settings = ""
    if macro_el is not None:
        macro_name = (macro_el.attrib.get("Name") or "").strip()
        # Exact ModuleName from XSCR only — never invent "RGA 1".
        module_name = (macro_el.attrib.get("ModuleName") or "").strip()
        settings_el = _find_first(macro_el, "ExecutionSettings")
        if settings_el is not None and settings_el.text:
            execution_settings = settings_el.text.strip()
    if not execution_settings:
        execution_settings = _extract_field(obj, "ExecutionSettings") or ""
    available_id = _extract_available_id(obj)
    parameters = _parse_execution_settings_parameters(execution_settings)
    return ApplicationDriverMacroStep(
        macro_name=macro_name,
        module_name=module_name,
        available_id=available_id,
        execution_settings=execution_settings,
        parameters=parameters,
        raw_xml=ET.tostring(obj, encoding="unicode"),
    )


def _extract_available_id(obj: ET.Element) -> Optional[str]:
    for sub in _walk_skipping_objects(obj):
        if isinstance(sub.tag, str) and _local(sub.tag) == "AvailableID" and sub.text:
            return sub.text.strip()
    return None


def _parse_execution_settings_parameters(execution_settings: str) -> dict[str, str]:
    """Best-effort parse of HTML-escaped ``ExecutionSettings`` inner XML."""
    if not execution_settings:
        return {}
    from html import unescape as _html_unescape
    unescaped = _html_unescape(execution_settings)
    try:
        inner = ET.fromstring(unescaped)
    except ET.ParseError:
        return {}
    params: dict[str, str] = {}
    for el in inner.iter():
        if not isinstance(el.tag, str):
            continue
        tag = _local(el.tag)
        if tag in params or not el.text or not el.text.strip():
            continue
        params[tag] = el.text.strip()
    return params


def _parse_rga_transfer(obj: ET.Element) -> RgaTransferLabwareStep:
    """RGA transfer is rendered as ``ApplicationDriverMacro`` with the
    actual labware / location / site stored as an XML-encoded string
    inside ``<ExecutionSettings>`` (a ``TransferLabwareCommandParameters``
    block). Parse the inner XML to recover them.

    For ``gripper.move(plate, onto=other)``, the renderer emits cover-
    site macros like ``GetCoverSiteName("Other")`` /
    ``GetCoverSiteIndex("Other")``. These are kept verbatim in
    ``destination_location`` so the renderer re-emits them byte-equal;
    ``destination_site`` falls back to ``1`` when the site value is a
    macro expression rather than a literal int.
    """
    inner_text = _extract_field(obj, "ExecutionSettings") or ""
    labware_name = ""
    dest_loc = "Site"
    dest_pos: Union[int, str] = 1
    if inner_text:
        # The inner XML is stored HTML-entity-escaped as character data
        # (`&lt;TransferLabwareCommandParameters&gt;…`).  Unescape before
        # parsing.
        from html import unescape as _html_unescape
        unescaped = _html_unescape(inner_text)
        try:
            inner = ET.fromstring(unescaped)
            for el in inner.iter():
                if not isinstance(el.tag, str):
                    continue
                tag = _local(el.tag)
                if tag == "Labware" and el.text:
                    labware_name = el.text.strip()
                elif tag == "Location" and el.text:
                    dest_loc = el.text.strip()
                elif tag == "Site" and el.text:
                    raw = el.text.strip()
                    try:
                        dest_pos = max(1, int(raw))
                    except (ValueError, TypeError):
                        dest_pos = raw
        except ET.ParseError:
            pass
    module_name = ""
    for el in obj.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el.tag) == "ApplicationDriverMacro":
            module_name = (el.attrib.get("ModuleName") or "").strip()
            break
    if not module_name:
        module_name = (_extract_field(obj, "ModuleName") or "").strip()
    available_id = _extract_available_id(obj)
    return RgaTransferLabwareStep(
        labware_name=labware_name,
        destination_location=dest_loc,
        destination_site=dest_pos,
        module_name=module_name,
        available_id=available_id,
    )


# ── XML helpers ─────────────────────────────────────────────────────


def _extract_field(obj: ET.Element, field_name: str) -> Optional[str]:
    """Return the text of the first descendant with the given local name,
    *not* descending into nested step ``<Object>`` siblings of the body.
    """
    for sub in _walk_skipping_objects(obj):
        if not isinstance(sub.tag, str):
            continue
        if _local(sub.tag) == field_name:
            txt = sub.text
            if txt is not None:
                return txt.strip()
    return None


def _extract_step_metadata(obj: ET.Element, field_name: str) -> Optional[str]:
    """Read a standard FluentControl step field from an element or attribute."""
    value = _extract_field(obj, field_name)
    if value is not None:
        return value
    for sub in _walk_skipping_objects(obj):
        value = sub.attrib.get(field_name)
        if value is not None:
            return value.strip()
    return None


def _apply_step_metadata(step: Step, obj: ET.Element) -> None:
    """Preserve standard execution metadata shared by typed parsed steps."""
    step.disabled = _parse_bool(_extract_step_metadata(obj, "IsDisabledForExecution"))
    step.breakpoint = _parse_bool(_extract_step_metadata(obj, "IsBreakpoint"))
    line_number = _extract_step_metadata(obj, "LineNumber")
    if line_number:
        step.line_number = _parse_int(line_number, default=0) or None


def _find_inner_statements(obj: ET.Element) -> Optional[ET.Element]:
    """First ``<Statements>`` block inside ``obj`` that belongs to ``obj``
    itself, not to a nested step's loop/conditional body."""
    return _find_first(obj, "Statements")


def _find_first(obj: ET.Element, local_name: str) -> Optional[ET.Element]:
    """First descendant of ``obj`` (in document order) with the given
    local name, *not* descending into nested step ``<Object>`` siblings.
    """
    for child in _walk_skipping_objects(obj):
        if isinstance(child.tag, str) and _local(child.tag) == local_name:
            return child
    return None


def _walk_skipping_objects(root: ET.Element) -> Iterator[ET.Element]:
    """Pre-order walk of ``root``'s descendants that does not enter any
    child ``<Object>`` element. Use to scan a step Object's payload
    fields without recursing into its loop/conditional body's child
    step Objects.
    """
    for child in root:
        if not isinstance(child.tag, str):
            continue
        if _local(child.tag) == "Object" and child is not root:
            continue
        yield child
        yield from _walk_skipping_objects(child)


def _parse_volume(text: Optional[str]) -> Union[float, str]:
    if text is None:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return text  # variable name reference


def _coerce_scalar(text: str) -> Union[int, float, str]:
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text


def _parse_int(text: Optional[str], *, default: int) -> int:
    if text is None or text == "":
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def _parse_bool(text: Optional[str], *, default: bool = False) -> bool:
    if text is None or text == "":
        return default
    return text.strip().lower() == "true"


def _parse_optional_bool(text: Optional[str]) -> Optional[bool]:
    if text is None or text == "":
        return None
    return text.strip().lower() == "true"


def _strip_wrapping_quotes(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped[1:-1]
    return stripped


def _parse_offset(text: Optional[str]) -> Optional[Union[int, str]]:
    if text is None or text == "":
        return None
    try:
        return int(text)
    except ValueError:
        return text


def _first_list_value(obj: ET.Element, list_name: str) -> Optional[str]:
    container = _find_first(obj, list_name)
    if container is None:
        return None
    for el in container.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el.tag) in {"string", "double", "int"} and el.text is not None:
            return el.text.strip()
    return None


def _parse_variable_mappings(obj: ET.Element, container_name: str) -> list[VariableMapping]:
    container = _find_first(obj, container_name)
    if container is None:
        return []
    mappings: list[VariableMapping] = []
    for child in container:
        if not isinstance(child.tag, str) or _local(child.tag) != "Object":
            continue
        target = _extract_field(child, "Target")
        source = _extract_field(child, "Source")
        if target and source is not None:
            mappings.append(VariableMapping(
                target=target,
                source=_parse_registered_expression(
                    "VariableMapping",
                    "Source",
                    source,
                ),
            ))
    return mappings


def _list_values(obj: ET.Element, list_name: str) -> list[str]:
    container = _find_first(obj, list_name)
    if container is None:
        return []
    values: list[str] = []
    for el in container.iter():
        if not isinstance(el.tag, str):
            continue
        if _local(el.tag) in {"string", "String"} and el.text is not None:
            values.append(el.text.strip())
    return values
