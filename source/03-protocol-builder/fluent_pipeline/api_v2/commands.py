"""Offline VisionX API V2 ICommand builders (api-v2-038..045)."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence

from fluentcoder.expressions import (
    coerce_literal_expression,
    expression_from_mapping,
    parse_or_preserve_source_expression,
    render_expression,
)

from .types import ApiV2ValidationError
from ..policies.prompt_text import normalize_operator_prompt_text, prompt_text_is_placeholder

_XML_NS_ROBOT = "http://schemas.datacontract.org/2004/07/Tecan.VisionX.Drivers.RobotDriverBase"


class ICommand(Protocol):
    def to_xml(self) -> str:
        ...

    def validate(self) -> None:
        ...

    def to_string(self) -> str:
        ...


def _xml_text(value: Any) -> str:
    return html.escape(str(value), quote=False)


def _worktable_int_text(value: Any) -> str:
    text = "" if value is None else str(value).strip()
    return text if re.fullmatch(r"-?\d+", text) else "0"


def _worktable_selected_labware_text(value: Any) -> str:
    text = str(value or "").strip()
    for bracket_value in re.findall(r"\[([^\]]+)\]", text):
        if not re.fullmatch(r"\d+", bracket_value.strip()):
            return ""
    return text


def _normalize_prompt_text(prompt: str) -> str:
    return normalize_operator_prompt_text(prompt)


def _require_non_empty(label: str, value: Any, *, command: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ApiV2ValidationError(f"{label} is required.", field=label, command=command)
    return text


@dataclass
class SetLocation:
    """api-v2-038 / api-v2-039."""

    labware: str
    location: str
    site: int | str = 1
    rotation: int | str = 0
    line_number: int = 0

    def to_string(self) -> str:
        from .command_tracing import format_set_location_trace

        return format_set_location_trace(
            labware=self.labware,
            location=self.location,
            site=self.site,
            rotation=self.rotation,
        )

    def validate(self) -> None:
        _require_non_empty("labware", self.labware, command="SetLocation")
        _require_non_empty("location", self.location, command="SetLocation")
        site_text = str(self.site).strip()
        if site_text.isdigit() and int(site_text) < 1:
            raise ApiV2ValidationError("site must be >= 1.", field="site", command="SetLocation")

    def to_xml(self) -> str:
        self.validate()
        return (
            '<Object Type="Tecan.Core.Scripting.Worktable.SetLocationStatement">\n'
            "  <SetLocationStatement>\n"
            f"    <Labware>{_xml_text(self.labware)}</Labware>\n"
            f"    <Location>{_xml_text(self.location)}</Location>\n"
            f"    <Site>{_xml_text(self.site)}</Site>\n"
            f"    <Rotation>{_xml_text(self.rotation)}</Rotation>\n"
            "    <IsBreakpoint>False</IsBreakpoint>\n"
            "    <IsDisabledForExecution>False</IsDisabledForExecution>\n"
            f"    <LineNumber>{int(self.line_number)}</LineNumber>\n"
            "  </SetLocationStatement>\n"
            "</Object>"
        )


@dataclass
class VariableMapping:
    target: str
    source: str

    def to_xml(self) -> str:
        return (
            '<Object Type="Tecan.Core.Scripting.VariableMapping">\n'
            "  <VariableMapping>\n"
            f"    <Target>{_xml_text(self.target)}</Target>\n"
            f"    <Source>{_xml_text(self.source)}</Source>\n"
            "  </VariableMapping>\n"
            "</Object>"
        )


@dataclass
class Subroutine:
    """api-v2-040 / api-v2-041."""

    path: str
    execution_mode: str = "JoinSubroutine"
    variable_mappings_start: Sequence[VariableMapping] = field(default_factory=tuple)
    variable_mappings_end: Sequence[VariableMapping] = field(default_factory=tuple)
    script_guid: str = ""
    line_number: int = 0

    def to_string(self) -> str:
        return f"Subroutine({self.path!r}, mode={self.execution_mode})"

    def validate(self) -> None:
        path = _require_non_empty("path", self.path, command="Subroutine")
        if "\\" not in path and "/" not in path:
            raise ApiV2ValidationError(
                "subroutine path should use a folder prefix (for example Folder\\SUB_Name).",
                field="path",
                command="Subroutine",
            )
        mode = str(self.execution_mode or "").strip()
        allowed = {"JoinSubroutine", "Synchronous", "Asynchronous", "FireAndForget"}
        if mode and mode not in allowed:
            raise ApiV2ValidationError(
                f"execution_mode {mode!r} is not a known Subroutine mode.",
                field="execution_mode",
                command="Subroutine",
            )

    def to_xml(self) -> str:
        self.validate()
        quoted_path = self.path if self.path.startswith('"') else f'"{self.path}"'
        start_xml = "\n".join(item.to_xml() for item in self.variable_mappings_start)
        end_xml = "\n".join(item.to_xml() for item in self.variable_mappings_end)
        return (
            '<Object Type="Tecan.Core.Scripting.SubRoutineStatement">\n'
            "  <SubRoutineStatement>\n"
            f"    <SubRoutine>{quoted_path}</SubRoutine>\n"
            "    <Mode>\n"
            f"      <ExecutionMode>{_xml_text(self.execution_mode)}</ExecutionMode>\n"
            "    </Mode>\n"
            f"    <VariableMappingsStart>\n{start_xml}\n    </VariableMappingsStart>\n"
            f"    <VariableMappingsEnd>\n{end_xml}\n    </VariableMappingsEnd>\n"
            "    <IsStarted>False</IsStarted>\n"
            "    <DepthLevel>0</DepthLevel>\n"
            "    <IsBreakpoint>False</IsBreakpoint>\n"
            "    <IsDisabledForExecution>False</IsDisabledForExecution>\n"
            f"    <LineNumber>{int(self.line_number)}</LineNumber>\n"
            "  </SubRoutineStatement>\n"
            "</Object>"
        )


@dataclass
class TransferLabware:
    """api-v2-043 / api-v2-044."""

    labware: str
    location: Any
    site: Any
    fixed_site: bool = True
    move_to_base: bool = False
    on_the_fly_tool: str = ""
    use_on_the_fly_tool: bool = False
    module_name: str = ""
    is_breakpoint: bool = False
    is_disabled_for_execution: bool = False
    execution_settings: str = ""
    line_number: int = 0

    def to_string(self) -> str:
        return (
            f"TransferLabware({self.labware!r} -> {self.location}[{self.site}], "
            f"fixed_site={self.fixed_site})"
        )

    def validate(self) -> None:
        from ..api_v2_transfer_labware_validate import TransferLabwareFields, validate_transfer_labware_fields

        result = validate_transfer_labware_fields(
            TransferLabwareFields(
                labware=str(self.labware or ""),
                location=str(self.location or ""),
                site=str(self.site or ""),
                move_to_base=bool(self.move_to_base),
                fixed_site=bool(self.fixed_site),
            ),
            # Rendering this typed command has no full IR deck state. Keep this
            # validation to required-field/shape checks; the explicit offline
            # and runtime preflight validators pass real deck labels/slots and
            # still catch missing labware or occupied destinations.
            deck_labels={str(self.labware or "").strip().casefold()},
            step_label="TransferLabware",
        )
        if not result.ok:
            raise ApiV2ValidationError(
                result.message or "TransferLabware validation failed.",
                field=result.field or "labware",
                command="TransferLabware",
            )
        if not str(self.module_name or "").strip():
            raise ApiV2ValidationError(
                "module_name is required from ZEIA/recipe — never invent 'RGA 1'.",
                field="module_name",
                command="TransferLabware",
            )

    def parameters_xml(self) -> str:
        """Inner TransferLabwareCommandParameters XML (single-escaped)."""
        self.validate()
        return (
            f"<TransferLabwareCommandParameters "
            f'xmlns:i="http://www.w3.org/2001/XMLSchema-instance" '
            f'xmlns="{_XML_NS_ROBOT}">'
            f"<FixedSite>{str(bool(self.fixed_site)).lower()}</FixedSite>"
            f"<Labware>{_xml_text(self.labware)}</Labware>"
            f"<Location>{_xml_text(self.location)}</Location>"
            f"<MoveToBase>{str(bool(self.move_to_base)).lower()}</MoveToBase>"
            f"<OnTheFlyTool>{_xml_text(self.on_the_fly_tool)}</OnTheFlyTool>"
            f"<Site>{_xml_text(self.site)}</Site>"
            f"<UseOnTheFlyTool>{str(bool(self.use_on_the_fly_tool)).lower()}</UseOnTheFlyTool>"
            f"</TransferLabwareCommandParameters>"
        )

    def to_execution_settings(self) -> str:
        """Double-escaped payload for ApplicationDriverMacro ExecutionSettings."""
        if self.execution_settings:
            return html.escape(self.execution_settings, quote=False)
        inner = self.parameters_xml()
        return inner.replace("&", "&amp;").replace("<", "&amp;lt;").replace(">", "&amp;gt;")

    def to_xml(self) -> str:
        settings = self.to_execution_settings()
        return (
            '<Object Type="Tecan.VisionX.ApplicationDriver.ApplicationDriverBase.ApplicationDriverMacro">\n'
            '  <ApplicationDriverMacro Version="1" Name="RGA1_TransferLabware" '
            f'ModuleName="{_xml_text(self.module_name)}" ExecutionTime="PT2S" '
            f'IsBreakpoint="{str(bool(self.is_breakpoint)).lower()}" '
            f'IsDisabledForExecution="{str(bool(self.is_disabled_for_execution)).lower()}" '
            f'LineNumber="{int(self.line_number)}">\n'
            f"    <ExecutionSettings>{settings}</ExecutionSettings>\n"
            "  </ApplicationDriverMacro>\n"
            "</Object>"
        )


@dataclass
class UserPrompt:
    """api-v2-045 (+ Validate scaffold for api-v2-046)."""

    prompt: str
    timeout: int | str = 0
    image_path: str = ""
    screen_title: str = ""
    line_number: int = 0
    rup_kind: str = "worktable"
    auto_close: bool | None = None
    rup_timeout: int | str | None = None
    variable_scope: str = ""
    is_breakpoint: bool = False
    is_disabled_for_execution: bool = False
    selected_labware_name: str = ""
    selected_labware_type: str = ""
    preserve_selected_labware_expression: bool = False
    grid: int | str | None = None
    site: int | str | None = None
    sound_path: str = ""
    is_labware_existence_mandatory: bool = False
    change_status_light_color: bool = False
    status_light_color_string: str = ""
    percent_complete_string: str = "0"

    @property
    def is_rup_standard(self) -> bool:
        return self.rup_kind == "standard"

    def to_string(self) -> str:
        text = _normalize_prompt_text(self.prompt)
        if self.image_path:
            kind = "worktable" if self.rup_kind != "standard" else "standard"
            return f'UserPrompt({text!r}, image={self.image_path!r}, rup={kind!r})'
        return f"UserPrompt({text!r})"

    def validate(self) -> None:
        text = _normalize_prompt_text(str(self.prompt or "")).strip()
        if not text:
            raise ApiV2ValidationError("prompt text is empty.", field="prompt", command="UserPrompt")
        if prompt_text_is_placeholder(text):
            raise ApiV2ValidationError(
                f"prompt text looks like a placeholder: {text!r}",
                field="prompt",
                command="UserPrompt",
            )

    def _to_rup_worktable_xml(self) -> str:
        message = _xml_text(_normalize_prompt_text(self.prompt))
        image = _xml_text(self.image_path)
        title = _xml_text(self.screen_title or "User Prompt")
        auto_close = bool(self.auto_close) if self.auto_close is not None else str(self.timeout).strip() not in {"", "0"}
        rup_timeout = self.rup_timeout if self.rup_timeout is not None else (self.timeout if str(self.timeout).strip() not in {"", "0"} else 1)
        grid = _xml_text(_worktable_int_text(self.grid))
        site = _xml_text(_worktable_int_text(self.site))
        if self.preserve_selected_labware_expression:
            selected_labware_name = str(self.selected_labware_name or "").strip()
        else:
            selected_labware_name = _worktable_selected_labware_text(self.selected_labware_name)
        selected_labware_type = self.selected_labware_type if selected_labware_name else ""
        return (
            '<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPWorktableStatement">\n'
            "  <RUPWorktableStatement>\n"
            "    <WorktableProperties>\n"
            "      <WorktableStatementDataClass>\n"
            "        <SelectedLabware>\n"
            "          <ConfigureDataLabwareDataModel>\n"
            f"            <LabwareName>{_xml_text(selected_labware_name)}</LabwareName>\n"
            f"            <LabwareTypeName>{_xml_text(selected_labware_type)}</LabwareTypeName>\n"
            f"            <IsLabwareExistenceMandatory>{str(bool(self.is_labware_existence_mandatory)).lower()}</IsLabwareExistenceMandatory>\n"
            "            <LabwareImageState>UseAsLabwareImageOnTheLeft</LabwareImageState>\n"
            f"            <Grid>{grid}</Grid>\n"
            f"            <Site>{site}</Site>\n"
            "            <LabwareState>Highlighted</LabwareState>\n"
            "            <LabwareDescription />\n"
            f"            <LabwareDescriptionGui>{message}</LabwareDescriptionGui>\n"
            "            <ActiveWorktableVisibility>Collapsed</ActiveWorktableVisibility>\n"
            "            <ActiveWorktable>0</ActiveWorktable>\n"
            f"            <CustomDetailImageFilePath>{image}</CustomDetailImageFilePath>\n"
            f"            <IsCustomDetailImageUsed>{str(bool(image)).lower()}</IsCustomDetailImageUsed>\n"
            "            <IsCarrier>false</IsCarrier>\n"
            "            <MarkerColorAlpha>192</MarkerColorAlpha>\n"
            "            <MarkerColorRed>255</MarkerColorRed>\n"
            "            <MarkerColorGreen>153</MarkerColorGreen>\n"
            "            <MarkerColorBlue>0</MarkerColorBlue>\n"
            "          </ConfigureDataLabwareDataModel>\n"
            "        </SelectedLabware>\n"
            "        <WorktableFilePath />\n"
            "        <IsCustomWorktableShown>false</IsCustomWorktableShown>\n"
            "        <IsAdvancedLabwaresettingsUsed>false</IsAdvancedLabwaresettingsUsed>\n"
            "        <DefaultItemState>Deactivated</DefaultItemState>\n"
            "        <IsUserAproveRequired>false</IsUserAproveRequired>\n"
            "      </WorktableStatementDataClass>\n"
            "    </WorktableProperties>\n"
            f"    <IsBreakpoint>{str(bool(self.is_breakpoint))}</IsBreakpoint>\n"
            f"    <IsDisabledForExecution>{str(bool(self.is_disabled_for_execution))}</IsDisabledForExecution>\n"
            f"    <LineNumber>{int(self.line_number)}</LineNumber>\n"
            f"    <RUPScreenTitle>{title}</RUPScreenTitle>\n"
            f"    <RUPDisplayAndWait>{str(not auto_close)}</RUPDisplayAndWait>\n"
            f"    <RUPAutoClose>{str(auto_close)}</RUPAutoClose>\n"
            f"    <RUPTimeOut>{_xml_text(rup_timeout)}</RUPTimeOut>\n"
            "    <ChangeStatusLightColor>False</ChangeStatusLightColor>\n"
            "    <StatusLightColorString />\n"
            "  </RUPWorktableStatement>\n"
            "</Object>"
        )

    def to_xml(self) -> str:
        self.validate()
        if self.image_path and self.rup_kind != "standard":
            return self._to_rup_worktable_xml()
        if self.rup_kind == "standard":
            message = _xml_text(_normalize_prompt_text(self.prompt))
            image = _xml_text(self.image_path)
            sound = _xml_text(self.sound_path)
            sound_xml = f"<SelectedSoundPath>{sound}</SelectedSoundPath>" if sound else "<SelectedSoundPath />"
            title = _xml_text(self.screen_title or "Verification")
            rup_timeout = self.rup_timeout if self.rup_timeout is not None else self.timeout
            percent_complete_string = _xml_text(self.percent_complete_string)
            status_light = _xml_text(self.status_light_color_string)
            return (
                '<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPStandardStatement">\n'
                "  <RUPStandardStatement>\n"
                "    <StandardProperties>\n"
                "      <StandardStatementDataClass>\n"
                f"        <SelectedImagePath>{image}</SelectedImagePath>\n"
                f"        <MessageText>{message}</MessageText>\n"
                f"        {sound_xml}\n"
                "        <RepeatValue />\n"
                f"        <PercentCompleteString>{percent_complete_string}</PercentCompleteString>\n"
                "        <PercentComplete>0</PercentComplete>\n"
                "        <ShowProgress>false</ShowProgress>\n"
                "        <DisplayStartTime>false</DisplayStartTime>\n"
                "        <DisplayEndTime>false</DisplayEndTime>\n"
                "        <DisplayPercentage>false</DisplayPercentage>\n"
                "        <DisplayTimeRemaining>false</DisplayTimeRemaining>\n"
                "        <DisplayTimeElapsed>false</DisplayTimeElapsed>\n"
                f"        <VariableScope>{_xml_text(self.variable_scope)}</VariableScope>\n"
                "      </StandardStatementDataClass>\n"
                "    </StandardProperties>\n"
                f"    <CheckMessagePrompt>{message}</CheckMessagePrompt>\n"
                "    <PercentComplete>0</PercentComplete>\n"
                f"    <PercentCompleteString>{percent_complete_string}</PercentCompleteString>\n"
                "    <RepeatValue></RepeatValue>\n"
                f"    <IsBreakpoint>{str(bool(self.is_breakpoint))}</IsBreakpoint>\n"
                f"    <IsDisabledForExecution>{str(bool(self.is_disabled_for_execution))}</IsDisabledForExecution>\n"
                f"    <LineNumber>{int(self.line_number)}</LineNumber>\n"
                f"    <RUPScreenTitle>{title}</RUPScreenTitle>\n"
                "    <RUPDisplayAndWait>True</RUPDisplayAndWait>\n"
                "    <RUPAutoClose>False</RUPAutoClose>\n"
                f"    <RUPTimeOut>{_xml_text(rup_timeout)}</RUPTimeOut>\n"
                f"    <ChangeStatusLightColor>{str(bool(self.change_status_light_color))}</ChangeStatusLightColor>\n"
                f"    <StatusLightColorString>{status_light}</StatusLightColorString>\n"
                "  </RUPStandardStatement>\n"
                "</Object>"
            )

        prompt = _xml_text(_normalize_prompt_text(self.prompt))
        sound = _xml_text(self.sound_path)
        sound_xml = f"<SoundFile>{sound}</SoundFile>" if sound else "<SoundFile />"
        if self.auto_close is None:
            auto_close = "True" if str(self.timeout).strip() not in {"", "0"} else "False"
        else:
            auto_close = "True" if self.auto_close else "False"
        return (
            '<Object Type="Tecan.Core.Scripting.UserPromptStatement">\n'
            "  <UserPromptStatement>\n"
            f"    <Prompt>{prompt}</Prompt>\n"
            f"    <AutoClose>{auto_close}</AutoClose>\n"
            f"    <Timeout>{_xml_text(self.timeout)}</Timeout>\n"
            f"    {sound_xml}\n"
            "    <RepeatSound>False</RepeatSound>\n"
            "    <ChangeStatusLightColor>False</ChangeStatusLightColor>\n"
            "    <StatusLightColorString>#FFFFFF00</StatusLightColorString>\n"
            f"    <IsBreakpoint>{str(bool(self.is_breakpoint))}</IsBreakpoint>\n"
            f"    <IsDisabledForExecution>{str(bool(self.is_disabled_for_execution))}</IsDisabledForExecution>\n"
            f"    <LineNumber>{int(self.line_number)}</LineNumber>\n"
            "  </UserPromptStatement>\n"
            "</Object>"
        )


_FINGER_LABWARE_BASES = frozenset({"Eccentric", "Centric", "Tube"})
_DEVICE_ALIAS_RE = re.compile(r"^Instrument=\d+/Device=(CGA|RGA):\d+$", re.IGNORECASE)
_AVAILABLE_ID_RE = re.compile(r"^USB:[^/]+/(CGA|RGA):\d+$", re.IGNORECASE)
_STALE_USB_SERIAL_RE = re.compile(r"USB:TECAN,FLUENT,0{6,}/", re.IGNORECASE)


@dataclass
class GetFingers:
    """api-v2-017 ToXML / api-v2-018 Validate for RGA/CGA finger pickup."""

    labware_name: str
    device_alias: str = ""
    available_id: str = ""
    line_number: int = 0
    group_line_number: int = 0
    is_breakpoint: bool = False
    is_disabled_for_execution: bool = False

    def to_string(self) -> str:
        return (
            f"GetFingers({self.labware_name!r}, "
            f"alias={self.device_alias!r}, id={self.available_id!r})"
        )

    def validate(self) -> None:
        labware = _require_non_empty("labware_name", self.labware_name, command="GetFingers")
        if "adapter" in labware.casefold():
            raise ApiV2ValidationError(
                "GetFingers cannot target adapter labware.",
                field="labware_name",
                command="GetFingers",
            )
        base = labware.split("[", 1)[0].strip()
        if base not in _FINGER_LABWARE_BASES:
            raise ApiV2ValidationError(
                f"labware_name {labware!r} is not a recognized finger type "
                f"(expected Eccentric, Centric, or Tube with optional [nnn] suffix).",
                field="labware_name",
                command="GetFingers",
            )
        alias = _require_non_empty("device_alias", self.device_alias, command="GetFingers")
        if not _DEVICE_ALIAS_RE.match(alias):
            raise ApiV2ValidationError(
                f"device_alias {alias!r} must match Instrument=<n>/Device=CGA:<n> or RGA:<n>.",
                field="device_alias",
                command="GetFingers",
            )
        available_id = _require_non_empty("available_id", self.available_id, command="GetFingers")
        if not _AVAILABLE_ID_RE.match(available_id):
            raise ApiV2ValidationError(
                f"available_id {available_id!r} must match USB:.../CGA:<n> or USB:.../RGA:<n>.",
                field="available_id",
                command="GetFingers",
            )
        if _STALE_USB_SERIAL_RE.search(available_id):
            raise ApiV2ValidationError(
                f"available_id {available_id!r} looks like a placeholder or stale CGA USB serial.",
                field="available_id",
                command="GetFingers",
            )
        alias_device = alias.rsplit("=", 1)[-1]
        id_device = available_id.rsplit("/", 1)[-1]
        if alias_device.casefold() != id_device.casefold():
            raise ApiV2ValidationError(
                f"device_alias device {alias_device!r} does not match available_id device {id_device!r}.",
                field="available_id",
                command="GetFingers",
            )

    def to_xml(self) -> str:
        self.validate()
        labware = _xml_text(self.labware_name)
        alias = _xml_text(self.device_alias)
        available_id = _xml_text(self.available_id)
        line = int(self.line_number)
        return (
            '<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaGetFingersScriptCommandDataV1">\n'
            "  <CgaGetFingersScriptCommandDataV1>\n"
            '    <Data Type="Tecan.Core.Instrument.Helpers.Scripting.ScriptCommandCommonDataV2">\n'
            "      <ScriptCommandCommonDataV2>\n"
            f"        <LabwareName>{labware}</LabwareName>\n"
            "        <LiquidClassVariablesNames />\n"
            "        <LiquidClassVariablesValues />\n"
            '        <Data Type="Tecan.Core.Instrument.Helpers.Scripting.DeviceAliasStatementBaseDataV1">\n'
            "          <DeviceAliasStatementBaseDataV1>\n"
            '            <Alias Type="Tecan.Core.Instrument.DeviceAlias.DeviceAlias">\n'
            f"              <DeviceAlias>{alias}</DeviceAlias>\n"
            "            </Alias>\n"
            "            <ID>\n"
            f"              <AvailableID>{available_id}</AvailableID>\n"
            "            </ID>\n"
            '            <Data Type="Tecan.Core.Scripting.Helpers.ScriptStatementBaseDataV1">\n'
            "              <ScriptStatementBaseDataV1>\n"
            "                <IsBreakpoint>False</IsBreakpoint>\n"
            "                <IsDisabledForExecution>False</IsDisabledForExecution>\n"
            "                <GroupLineNumber>0</GroupLineNumber>\n"
            f"                <LineNumber>{line}</LineNumber>\n"
            "              </ScriptStatementBaseDataV1>\n"
            "            </Data>\n"
            "          </DeviceAliasStatementBaseDataV1>\n"
            "        </Data>\n"
            "      </ScriptCommandCommonDataV2>\n"
            "    </Data>\n"
            "  </CgaGetFingersScriptCommandDataV1>\n"
            "</Object>"
        )


@dataclass
class DropFingers:
    """api-v2-048 ToXML / api-v2-049 Validate."""

    labware_name: str
    device_alias: str = ""
    available_id: str = ""
    use_source_as_back_position: bool | str = "BackToPosition"
    line_number: int = 0

    def to_string(self) -> str:
        return f"DropFingers({self.labware_name!r})"

    def validate(self) -> None:
        backs = str(self.use_source_as_back_position or "").strip()
        if not str(self.labware_name or "").strip() and backs == "BackToSource":
            return
        labware = _require_non_empty("labware_name", self.labware_name, command="DropFingers")
        if "adapter" in labware.casefold():
            raise ApiV2ValidationError(
                "DropFingers cannot target adapter labware.",
                field="labware_name",
                command="DropFingers",
            )
        # Same device gate as GetFingers — never emit empty DeviceAlias/AvailableID.
        alias = _require_non_empty("device_alias", self.device_alias, command="DropFingers")
        if not _DEVICE_ALIAS_RE.match(alias):
            raise ApiV2ValidationError(
                f"device_alias {alias!r} must match Instrument=<n>/Device=CGA:<n> or RGA:<n>.",
                field="device_alias",
                command="DropFingers",
            )
        available_id = _require_non_empty("available_id", self.available_id, command="DropFingers")
        if not _AVAILABLE_ID_RE.match(available_id):
            raise ApiV2ValidationError(
                f"available_id {available_id!r} must match USB:.../CGA:<n> or USB:.../RGA:<n>.",
                field="available_id",
                command="DropFingers",
            )
        if _STALE_USB_SERIAL_RE.search(available_id):
            raise ApiV2ValidationError(
                f"available_id {available_id!r} looks like a placeholder or stale CGA USB serial.",
                field="available_id",
                command="DropFingers",
            )
        alias_device = alias.rsplit("=", 1)[-1]
        id_device = available_id.rsplit("/", 1)[-1]
        if alias_device.casefold() != id_device.casefold():
            raise ApiV2ValidationError(
                f"device_alias device {alias_device!r} does not match available_id device {id_device!r}.",
                field="available_id",
                command="DropFingers",
            )

    def to_xml(self, *, validate: bool = True) -> str:
        if validate:
            self.validate()
        labware = _xml_text(self.labware_name)
        alias = _xml_text(self.device_alias)
        available_id = _xml_text(self.available_id)
        if isinstance(self.use_source_as_back_position, bool):
            backs = "True" if self.use_source_as_back_position else "False"
        else:
            backs = str(self.use_source_as_back_position or "BackToPosition")
        line = int(self.line_number)
        return (
            '<Object Type="Tecan.Core.Instrument.Devices.Rga.Scripting.Data.CgaDropFingersScriptCommandDataV1">\n'
            "  <CgaDropFingersScriptCommandDataV1>\n"
            f"    <UseSourceAsBackPosition><Backs>{backs}</Backs></UseSourceAsBackPosition>\n"
            '    <Data Type="Tecan.Core.Instrument.Helpers.Scripting.ScriptCommandCommonDataV2">\n'
            "      <ScriptCommandCommonDataV2>\n"
            f"        <LabwareName>{labware}</LabwareName>\n"
            "        <LiquidClassVariablesNames />\n"
            "        <LiquidClassVariablesValues />\n"
            '        <Data Type="Tecan.Core.Instrument.Helpers.Scripting.DeviceAliasStatementBaseDataV1">\n'
            "          <DeviceAliasStatementBaseDataV1>\n"
            '            <Alias Type="Tecan.Core.Instrument.DeviceAlias.DeviceAlias">\n'
            f"              <DeviceAlias>{alias}</DeviceAlias>\n"
            "            </Alias>\n"
            "            <ID>\n"
            f"              <AvailableID>{available_id}</AvailableID>\n"
            "            </ID>\n"
            '            <Data Type="Tecan.Core.Scripting.Helpers.ScriptStatementBaseDataV1">\n'
            "              <ScriptStatementBaseDataV1>\n"
            "                <IsBreakpoint>False</IsBreakpoint>\n"
            "                <IsDisabledForExecution>False</IsDisabledForExecution>\n"
            "                <GroupLineNumber>0</GroupLineNumber>\n"
            f"                <LineNumber>{line}</LineNumber>\n"
            "              </ScriptStatementBaseDataV1>\n"
            "            </Data>\n"
            "          </DeviceAliasStatementBaseDataV1>\n"
            "        </Data>\n"
            "      </ScriptCommandCommonDataV2>\n"
            "    </Data>\n"
            "  </CgaDropFingersScriptCommandDataV1>\n"
            "</Object>"
        )


@dataclass
class RemoveLabware:
    """api-v2-059 RemoveLabware.ToXML()."""

    labware_name: str
    line_number: int = 0
    is_breakpoint: bool = False
    is_disabled_for_execution: bool = False

    def to_string(self) -> str:
        from .command_tracing import format_remove_labware_trace

        return format_remove_labware_trace(labware=self.labware_name)

    def validate(self) -> None:
        _require_non_empty("labware_name", self.labware_name, command="RemoveLabware")

    def to_xml(self) -> str:
        self.validate()
        labware = _xml_text(self.labware_name)
        line = int(self.line_number)
        return (
            '<Object Type="Tecan.Core.Scripting.Worktable.Data.RemoveLabwareDataV1">\n'
            "  <RemoveLabwareDataV1>\n"
            f"    <LabwareName>{labware}</LabwareName>\n"
            '    <Data Type="Tecan.Core.Scripting.Programming.ProgrammingStatementBaseDataV1">\n'
            "      <ProgrammingStatementBaseDataV1>\n"
            f"        <IsBreakpoint>{str(bool(self.is_breakpoint))}</IsBreakpoint>\n"
            f"        <IsDisabledForExecution>{str(bool(self.is_disabled_for_execution))}</IsDisabledForExecution>\n"
            f"        <LineNumber>{line}</LineNumber>\n"
            "      </ProgrammingStatementBaseDataV1>\n"
            "    </Data>\n"
            "  </RemoveLabwareDataV1>\n"
            "</Object>"
        )


def drop_fingers_from_xscr_object(element: Any, *, line_number: int = 0) -> DropFingers:
    node = _find_descendant(element, "CgaDropFingersScriptCommandDataV1")
    if node is None:
        node = element
    backs = _child_text(_find_descendant(node, "UseSourceAsBackPosition"), "Backs", "False")
    resolved_line = line_number or _extract_line_number(element)
    return DropFingers(
        labware_name=_descendant_text(node, "LabwareName"),
        device_alias=_descendant_text(node, "DeviceAlias"),
        available_id=_descendant_text(node, "AvailableID"),
        use_source_as_back_position=backs or "BackToPosition",
        line_number=resolved_line,
    )


def remove_labware_from_xscr_object(element: Any, *, line_number: int = 0) -> RemoveLabware:
    node = _find_descendant(element, "RemoveLabwareDataV1")
    statement_data = _find_descendant(node, "ProgrammingStatementBaseDataV1")
    return RemoveLabware(
        labware_name=_child_text(node, "LabwareName"),
        line_number=line_number or _extract_line_number(element),
        is_breakpoint=_child_text(statement_data, "IsBreakpoint", "False").lower() == "true",
        is_disabled_for_execution=_child_text(statement_data, "IsDisabledForExecution", "False").lower() == "true",
    )


def get_fingers_from_xscr_element(element: Any, *, line_number: int = 0) -> GetFingers:
    """Parse ``CgaGetFingersScriptCommandDataV1`` XML into a typed GetFingers command."""
    from .. import xml_compat as ET

    if not isinstance(element, ET.Element):
        return GetFingers(labware_name="", line_number=line_number)
    resolved_line = line_number or int(_descendant_text(element, "LineNumber", "0") or 0)
    return GetFingers(
        labware_name=_descendant_text(element, "LabwareName"),
        device_alias=_descendant_text(element, "DeviceAlias"),
        available_id=_descendant_text(element, "AvailableID"),
        line_number=resolved_line,
        group_line_number=int(_descendant_text(element, "GroupLineNumber", "0") or 0),
        is_breakpoint=_descendant_text(element, "IsBreakpoint", "False").lower() == "true",
        is_disabled_for_execution=_descendant_text(element, "IsDisabledForExecution", "False").lower() == "true",
    )


def _descendant_text(element: Any, name: str, default: str = "") -> str:
    from .. import xml_compat as ET

    if not isinstance(element, ET.Element):
        return default
    for node in element.iter():
        tag = str(node.tag)
        local = tag.rsplit("}", 1)[-1] if "}" in tag else tag
        if local == name:
            return (node.text or "").strip()
    return default


def set_location_from_ir_step(step: Mapping[str, Any]) -> SetLocation:
    params = step.get("parameters") or {}
    return SetLocation(
        labware=str(params.get("labware") or params.get("label") or step.get("target_labware") or ""),
        location=str(params.get("location") or params.get("to_location") or ""),
        site=params.get("site") or params.get("to_site") or params.get("position") or 1,
        rotation=params.get("rotation") or 0,
        line_number=int(step.get("line_number") or 0),
    )


def subroutine_from_ir_step(step: Mapping[str, Any]) -> Subroutine:
    params = step.get("parameters") or {}
    start = tuple(
        VariableMapping(target=str(item["target"]), source=str(item["source"]))
        for item in (params.get("variable_mappings_start") or [])
        if isinstance(item, Mapping) and item.get("target") is not None
    )
    end = tuple(
        VariableMapping(target=str(item["target"]), source=str(item["source"]))
        for item in (params.get("variable_mappings_end") or [])
        if isinstance(item, Mapping) and item.get("target") is not None
    )
    return Subroutine(
        path=str(params.get("subroutine") or params.get("SubRoutine") or "").strip().strip('"'),
        execution_mode=str(params.get("execution_mode") or params.get("ExecutionMode") or "JoinSubroutine"),
        variable_mappings_start=start,
        variable_mappings_end=end,
        script_guid=str(params.get("script_guid") or ""),
        line_number=int(step.get("line_number") or 0),
    )


def transfer_labware_from_ir_step(step: Mapping[str, Any]) -> TransferLabware:
    params = step.get("parameters") or {}
    labware = str(params.get("labware") or step.get("target_labware") or "")
    onto = str(params.get("onto_labware") or params.get("onto") or "")
    if onto:
        location = f'GetCoverSiteName("{onto}")'
        site = f'GetCoverSiteIndex("{onto}")'
    else:
        location = str(
            params.get("destination_location")
            or params.get("to_location")
            or step.get("destination_location")
            or ""
        )
        site_expression = params.get("site_expression")
        site = (
            _expression_source_text(site_expression)
            if isinstance(site_expression, Mapping)
            else params.get("destination_site") or params.get("to_site") or step.get("destination_site") or 1
        )
    return TransferLabware(
        labware=labware,
        location=location,
        site=site,
        fixed_site=bool(params.get("fixed_site", True)),
        move_to_base=bool(params.get("move_to_base", False)),
        module_name=str(params.get("module_name") or params.get("ModuleName") or ""),
        is_breakpoint=bool(params.get("is_breakpoint", False)),
        is_disabled_for_execution=bool(params.get("is_disabled_for_execution", False)),
        line_number=int(step.get("line_number") or 0),
    )


def _expression_source_text(expression: Mapping[str, Any]) -> str:
    return render_expression(expression_from_mapping(dict(expression)))


def _child_bool(element: Any, name: str, default: bool = False) -> bool:
    text = _child_text(element, name, "False" if not default else "True")
    return text.lower() == "true"


def get_fingers_from_ir_step(step: Mapping[str, Any]) -> GetFingers:
    params = step.get("parameters") or {}
    return GetFingers(
        labware_name=str(params.get("labware_name") or params.get("labware") or "").strip(),
        device_alias=str(params.get("device_alias") or "").strip(),
        available_id=str(params.get("available_id") or "").strip(),
        line_number=int(step.get("line_number") or params.get("line_number") or 0),
        group_line_number=int(params.get("group_line_number") or 0),
        is_breakpoint=bool(params.get("is_breakpoint", False)),
        is_disabled_for_execution=bool(params.get("is_disabled_for_execution", False)),
    )


def add_labware_from_ir_step(step: Mapping[str, Any]) -> AddLabware:
    from ..api_v2_add_labware_validate import add_labware_fields_from_ir_step

    fields = add_labware_fields_from_ir_step(step)
    if fields is None:
        params = step.get("parameters") or {}
        return AddLabware(
            labware_type=str(params.get("catalog") or params.get("labware_type") or ""),
            labware_label=str(params.get("label") or params.get("labware") or ""),
            location=str(params.get("location") or ""),
            site=params.get("site") or params.get("position") or 1,
            rotation=params.get("rotation") or 0,
            has_lid=bool(params.get("has_lid")),
            line_number=int(step.get("line_number") or 0),
        )
    return AddLabware(
        labware_type=fields.labware_type,
        labware_label=fields.labware_label,
        location=fields.location,
        site=fields.site,
        rotation=fields.rotation,
        has_lid=fields.has_lid,
        line_number=int(step.get("line_number") or 0),
    )


def user_prompt_from_ir_step(step: Mapping[str, Any]) -> UserPrompt:
    from ..protocol_ir import prompt_step_media_path, prompt_step_worktable_media_path

    params = step.get("parameters") or {}
    placeholders = params.get("media_placeholders")
    image_path = ""
    rup_kind = "worktable"
    command_id = str(step.get("command_id") or "")
    if command_id == "RUPStandardStatement":
        rup_kind = "standard"
    if isinstance(placeholders, list) and placeholders:
        selector = prompt_step_media_path if rup_kind == "standard" else prompt_step_worktable_media_path
        image_path = str(selector(params) or "")
    if not image_path:
        media = params.get("media_placeholders") or {}
        if isinstance(media, Mapping):
            image_path = str(media.get("image_path") or media.get("image") or "")
        image_path = image_path or str(params.get("image_path") or "")
    return UserPrompt(
        prompt=str(params.get("prompt") or step.get("prompt") or ""),
        timeout=params.get("timeout") if params.get("timeout") is not None else 0,
        image_path=image_path,
        screen_title=str(params.get("screen_title") or ""),
        line_number=int(step.get("line_number") or 0),
        rup_kind=rup_kind,
        auto_close=params.get("auto_close") if isinstance(params.get("auto_close"), bool) else None,
        rup_timeout=params.get("rup_timeout") if params.get("rup_timeout") is not None else None,
        variable_scope=str(params.get("variable_scope") or ""),
        is_breakpoint=bool(params.get("is_breakpoint", False)),
        is_disabled_for_execution=bool(params.get("is_disabled_for_execution", False)),
        selected_labware_name=str(params.get("selected_labware_name") or ""),
        selected_labware_type=str(params.get("selected_labware_type") or ""),
        grid=params.get("grid"),
        site=params.get("site"),
        sound_path=str(params.get("sound_path") or params.get("sound_file") or params.get("selected_sound_path") or ""),
    )


def validate_command(command: ICommand) -> None:
    command.validate()


# --- api-v2-005: unified ICommand.ToXML() registry + XSCR mapping ---

API_V2_COMMAND_TYPES = frozenset(
    {
        "AddLabware",
        "DropFingers",
        "GenericCommand",
        "GetFingers",
        "QueryVariable",
        "RemoveLabware",
        "RUPVariable",
        "SetLocation",
        "SetVariable",
        "Subroutine",
        "TransferLabware",
        "UserPrompt",
    }
)

XSCR_COMMAND_ID_TO_API_V2: dict[str, str] = {
    "AddLabwareDataV1": "AddLabware",
    "UserPromptStatement": "UserPrompt",
    "RUPStandardStatement": "UserPrompt",
    "RUPWorktableStatement": "UserPrompt",
    "SubRoutineStatement": "Subroutine",
    "ApplicationDriverMacro": "TransferLabware",
    "SetLocationStatement": "SetLocation",
    "SetVariableStatement": "SetVariable",
    "QueryVariableStatement": "QueryVariable",
    "RUPVariableStatement": "RUPVariable",
    "CgaGetFingersScriptCommandDataV1": "GetFingers",
    "CgaDropFingersScriptCommandDataV1": "DropFingers",
    "RemoveLabwareStatement": "RemoveLabware",
    "RemoveLabwareDataV1": "RemoveLabware",
}


def _local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _child_text(element: Any, name: str, default: str = "") -> str:
    from .. import xml_compat as ET

    if element is None:
        return default
    if not isinstance(element, ET.Element):
        return default
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return default


def _rup_timeout_from_xscr(node: Any) -> int | str:
    """Derive UserPrompt timeout from RUP auto-close flags, not the default RUPTimeOut."""
    auto_close = _child_text(node, "RUPAutoClose", "False").lower() == "true"
    if not auto_close:
        return 0
    return _child_text(node, "RUPTimeOut", "0") or 0


def _rup_raw_timeout_from_xscr(node: Any) -> int | str:
    return _child_text(node, "RUPTimeOut", "0") or 0


def _find_descendant(element: Any, name: str) -> Any:
    from .. import xml_compat as ET

    if not isinstance(element, ET.Element):
        return None
    for node in element.iter():
        if _local_name(node.tag) == name:
            return node
    return None


def _extract_line_number(element: Any) -> int:
    from .. import xml_compat as ET

    if not isinstance(element, ET.Element):
        return 0
    for node in element.iter():
        attr_value = node.attrib.get("LineNumber")
        if attr_value is not None:
            try:
                return int(str(attr_value).strip())
            except ValueError:
                pass
        if _local_name(node.tag) == "LineNumber":
            try:
                return int((node.text or "0").strip())
            except ValueError:
                return 0
    return 0


def _execution_mode_from_statement(node: Any) -> str:
    from .. import xml_compat as ET

    if not isinstance(node, ET.Element):
        return "JoinSubroutine"
    direct = _child_text(node, "ExecutionMode")
    if direct:
        return direct
    mode_wrapper = _find_descendant(node, "Mode")
    nested = _child_text(mode_wrapper, "ExecutionMode")
    return nested or "JoinSubroutine"


def _variable_mappings_from_statement(node: Any, container_name: str) -> list[dict[str, str]]:
    from .. import xml_compat as ET

    if not isinstance(node, ET.Element):
        return []
    container = _find_descendant(node, container_name)
    if container is None:
        return []
    mappings: list[dict[str, str]] = []
    for child in container:
        if _local_name(child.tag) != "Object":
            continue
        mapping_node = _find_descendant(child, "VariableMapping")
        target = _child_text(mapping_node, "Target")
        source = _child_text(mapping_node, "Source")
        if target and source:
            mappings.append({"target": target, "source": source})
    return mappings


def _parse_transfer_setting(settings: str, tag: str, default: str = "") -> str:
    if not settings:
        return default
    import re

    text = html.unescape(settings)
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return default
    return match.group(1).strip()


@dataclass
class AddLabware:
    """api-v2-007 ToXML + api-v2-008 ``AddLabware.Validate()``."""

    labware_type: str
    labware_label: str
    location: str
    site: int | str = 1
    rotation: int | str = 0
    has_lid: bool = False
    line_number: int = 0
    data_style: str = "nested"
    is_breakpoint: bool = False
    is_disabled_for_execution: bool = False

    def to_string(self) -> str:
        return f"AddLabware({self.labware_label!r} @ {self.location}[{self.site}])"

    def validate(self) -> None:
        from ..api_v2_add_labware_validate import AddLabwareFields, validate_add_labware_fields

        result = validate_add_labware_fields(
            AddLabwareFields(
                labware_type=self.labware_type,
                labware_label=self.labware_label,
                location=self.location,
                site=self.site,
                rotation=self.rotation,
                has_lid=self.has_lid,
            )
        )
        if not result.ok:
            raise ApiV2ValidationError(
                result.message or "AddLabware validation failed.",
                field=result.field,
                command="AddLabware",
            )

    def to_xml(self, *, validate: bool = True) -> str:
        if validate:
            self.validate()
        if self.data_style == "direct":
            data_xml = f"    <Data><LineNumber>{int(self.line_number)}</LineNumber></Data>\n"
        else:
            data_xml = (
                '    <Data Type="Tecan.Core.Scripting.Programming.ProgrammingStatementBaseDataV1">\n'
                "      <ProgrammingStatementBaseDataV1>\n"
                f"        <IsBreakpoint>{str(bool(self.is_breakpoint))}</IsBreakpoint>\n"
                f"        <IsDisabledForExecution>{str(bool(self.is_disabled_for_execution))}</IsDisabledForExecution>\n"
                f"        <LineNumber>{int(self.line_number)}</LineNumber>\n"
                "      </ProgrammingStatementBaseDataV1>\n"
                "    </Data>\n"
            )
        return (
            '<Object Type="Tecan.Core.Scripting.Worktable.Data.AddLabwareDataV1">\n'
            "  <AddLabwareDataV1>\n"
            f"    <LabwareType>{_xml_text(self.labware_type)}</LabwareType>\n"
            f"    <LabwareLable>{_xml_text(self.labware_label)}</LabwareLable>\n"
            f"    <Location>{_xml_text(self.location)}</Location>\n"
            f"    <Position>{_xml_text(self.site)}</Position>\n"
            f"    <Rotation>{_xml_text(self.rotation)}</Rotation>\n"
            f"    <HasLid>{str(bool(self.has_lid))}</HasLid>\n"
            f"{data_xml}"
            "  </AddLabwareDataV1>\n"
            "</Object>"
        )


def _format_set_variable_xml_value(value: Any) -> str:
    """Format ``<Value>`` the way FluentControl serializes SetVariableStatement."""
    if value is None:
        return ""
    return render_expression(coerce_literal_expression(value))


def _has_meaningful_value(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _query_limit_xml_blocks(minimum: Any, maximum: Any) -> str:
    blocks: list[str] = []
    if _has_meaningful_value(minimum):
        min_text = str(minimum).strip()
        try:
            min_double = float(min_text)
            double_text = str(int(min_double)) if min_double.is_integer() else str(min_double)
            blocks.append(
                '    <Minimum Type="System.Double">\n'
                f"      <double>{double_text}</double>\n"
                "    </Minimum>"
            )
        except ValueError:
            pass
        blocks.append(f"    <MinimumText>{_xml_text(min_text)}</MinimumText>")
    if _has_meaningful_value(maximum):
        max_text = str(maximum).strip()
        try:
            max_double = float(max_text)
            double_text = str(int(max_double)) if max_double.is_integer() else str(max_double)
            blocks.append(
                '    <Maximum Type="System.Double">\n'
                f"      <double>{double_text}</double>\n"
                "    </Maximum>"
            )
        except ValueError:
            pass
        blocks.append(f"    <MaximumText>{_xml_text(max_text)}</MaximumText>")
    return "\n".join(blocks)


def _rup_allowed_values_text(allowed: Any) -> str:
    if allowed is None:
        return ""
    if isinstance(allowed, (list, tuple)):
        return ";".join(_rup_allowed_value_text(item) for item in allowed)
    return str(allowed)


def _rup_allowed_value_text(value: Any) -> str:
    text = str(value)
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped[1:-1]
    return text


@dataclass
class SetVariable:
    """FluentControl ``SetVariableStatement`` (api-v2 variable parity)."""

    name: str
    value: Any
    line_number: int = 0
    is_breakpoint: bool = False
    is_disabled_for_execution: bool = False

    def to_string(self) -> str:
        return f"SetVariable({self.name!r}, {self.value!r})"

    def validate(self) -> None:
        _require_non_empty("name", self.name, command="SetVariable")

    def to_xml(self) -> str:
        self.validate()
        value_text = _xml_text(_format_set_variable_xml_value(self.value))
        return (
            '<Object Type="Tecan.Core.Scripting.SetVariableStatement">\n'
            "  <SetVariableStatement>\n"
            f"    <Name>{_xml_text(self.name)}</Name>\n"
            f"    <Value>{value_text}</Value>\n"
            f"    <IsBreakpoint>{str(bool(self.is_breakpoint))}</IsBreakpoint>\n"
            f"    <IsDisabledForExecution>{str(bool(self.is_disabled_for_execution))}</IsDisabledForExecution>\n"
            f"    <LineNumber>{int(self.line_number)}</LineNumber>\n"
            "  </SetVariableStatement>\n"
            "</Object>"
        )


@dataclass
class QueryVariable:
    """FluentControl ``QueryVariableStatement`` (api-v2 variable parity)."""

    name: str
    query_prompt: str
    limit_range: bool = False
    minimum: Any = None
    maximum: Any = None
    line_number: int = 0
    is_breakpoint: bool = False
    is_disabled_for_execution: bool = False

    def to_string(self) -> str:
        return f"QueryVariable({self.name!r}, {self.query_prompt!r}, limit_range={self.limit_range!r})"

    def validate(self) -> None:
        _require_non_empty("name", self.name, command="QueryVariable")
        _require_non_empty("query_prompt", self.query_prompt, command="QueryVariable")

    def to_xml(self) -> str:
        self.validate()
        limit_blocks = ""
        if self.limit_range:
            limit_blocks = _query_limit_xml_blocks(self.minimum, self.maximum)
            if limit_blocks:
                limit_blocks = f"\n{limit_blocks}"
        return (
            '<Object Type="Tecan.Core.Scripting.QueryVariableStatement">\n'
            "  <QueryVariableStatement>\n"
            f"    <Name>{_xml_text(self.name)}</Name>\n"
            f"    <QueryPrompt>{_xml_text(self.query_prompt)}</QueryPrompt>\n"
            f"    <LimitRange>{str(bool(self.limit_range))}</LimitRange>\n"
            f"    <IsBreakpoint>{str(bool(self.is_breakpoint))}</IsBreakpoint>\n"
            f"    <IsDisabledForExecution>{str(bool(self.is_disabled_for_execution))}</IsDisabledForExecution>\n"
            f"    <LineNumber>{int(self.line_number)}</LineNumber>"
            f"{limit_blocks}\n"
            "  </QueryVariableStatement>\n"
            "</Object>"
        )


@dataclass
class RUPVariable:
    """FluentControl ``RUPVariableStatement`` TouchTools runtime prompt form."""

    screen_title: str = "User Input"
    instructions: str = "Please make your selections"
    layout_horizontal: bool = True
    columns: int = 1
    display_and_wait: bool = True
    auto_close: bool = False
    timeout: int = 1
    disabled: bool = False
    variables: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    line_number: int = 0
    is_breakpoint: bool = False
    is_disabled_for_execution: bool = False

    def to_string(self) -> str:
        names = [
            str(item.get("name") or item.get("variable") or "")
            for item in self.variables
            if isinstance(item, Mapping)
        ]
        names = [name for name in names if name]
        suffix = f" fields={names!r}" if names else ""
        return f"RUPVariable({self.screen_title!r}{suffix})"

    def validate(self) -> None:
        if not self.variables:
            raise ApiV2ValidationError(
                "RUPVariableStatement requires at least one field.",
                field="variables",
                command="RUPVariable",
            )

    def to_params(self) -> dict[str, Any]:
        return {
            "screen_title": self.screen_title,
            "instructions": self.instructions,
            "layout_horizontal": self.layout_horizontal,
            "columns": self.columns,
            "display_and_wait": self.display_and_wait,
            "auto_close": self.auto_close,
            "timeout": self.timeout,
            "disabled": self.disabled,
            "variables": list(self.variables),
            "line_number": self.line_number,
            "is_breakpoint": self.is_breakpoint,
            "is_disabled_for_execution": self.is_disabled_for_execution,
        }

    def to_xml(self) -> str:
        self.validate()
        from ..protocol_ir import render_rup_variable_statement_xml

        return render_rup_variable_statement_xml(self.to_params())


def set_variable_from_ir_step(step: Mapping[str, Any]) -> SetVariable:
    params = step.get("parameters") or {}
    variable = str(params.get("variable") or params.get("name") or "").strip()
    if str(step.get("operation") or "") == "set_remaining_runtime" and not variable:
        variable = "RemainingRuntime"
    return SetVariable(
        name=variable,
        value=params.get("value"),
        line_number=int(step.get("line_number") or params.get("line_number") or 0),
        is_breakpoint=bool(params.get("is_breakpoint", False)),
        is_disabled_for_execution=bool(params.get("is_disabled_for_execution", False)),
    )


def query_variable_from_ir_step(step: Mapping[str, Any]) -> QueryVariable:
    params = step.get("parameters") or {}
    minimum = params.get("minimum")
    maximum = params.get("maximum")
    limit_range = bool(params.get("limit_range"))
    if not limit_range and (_has_meaningful_value(minimum) or _has_meaningful_value(maximum)):
        limit_range = True
    return QueryVariable(
        name=str(params.get("variable") or params.get("name") or "").strip(),
        query_prompt=str(params.get("prompt") or params.get("query_prompt") or "").strip(),
        limit_range=limit_range,
        minimum=minimum,
        maximum=maximum,
        line_number=int(step.get("line_number") or params.get("line_number") or 0),
        is_breakpoint=bool(params.get("is_breakpoint", False)),
        is_disabled_for_execution=bool(params.get("is_disabled_for_execution", False)),
    )


def rup_variable_from_ir_step(step: Mapping[str, Any]) -> RUPVariable:
    params = step.get("parameters") or {}
    return RUPVariable(
        screen_title=str(params.get("screen_title") or params.get("title") or "User Input"),
        instructions=str(params.get("instructions") or params.get("prompt") or "Please make your selections"),
        layout_horizontal=bool(params.get("layout_horizontal", True)),
        columns=int(params.get("columns") or params.get("number_of_columns") or 1),
        display_and_wait=bool(params.get("display_and_wait", True)),
        auto_close=bool(params.get("auto_close", False)),
        timeout=int(params.get("timeout") or params.get("rup_timeout") or 1),
        disabled=bool(params.get("disabled", False)),
        variables=tuple(item for item in (params.get("variables") or []) if isinstance(item, Mapping)),
        line_number=int(step.get("line_number") or params.get("line_number") or 0),
        is_breakpoint=bool(params.get("is_breakpoint", False)),
        is_disabled_for_execution=bool(params.get("is_disabled_for_execution", False)),
    )


def _parse_rup_variable_items(node: Any) -> list[dict[str, Any]]:
    from .. import xml_compat as ET

    if not isinstance(node, ET.Element):
        return []
    items: list[dict[str, Any]] = []
    for element in node.iter():
        if _local_name(element.tag) != "RupVariableItem":
            continue
        allowed_raw = _child_text(element, "AllowedValues")
        field: dict[str, Any] = {
            "name": _child_text(element, "VariableName"),
            "variable_type": _child_text(element, "VariableType") or "System.String",
            "display_text": _child_text(element, "DisplayText"),
            "display_type": _child_text(element, "DisplayType") or "Combobox",
            "value": _child_text(element, "VariableValue"),
            "enabled": _child_bool(element, "IsEnabled", default=True),
        }
        if allowed_raw:
            field["allowed_values"] = allowed_raw
        uses_min_max = _child_bool(element, "IsMinMaxValueUsed", default=False)
        unresolved_min = _child_text(element, "UnresolvedMinValue")
        unresolved_max = _child_text(element, "UnresolvedMaxValue")
        if uses_min_max:
            if unresolved_min or _child_text(element, "MinValue"):
                field["min"] = unresolved_min or _child_text(element, "MinValue")
            if unresolved_max or _child_text(element, "MaxValue"):
                field["max"] = unresolved_max or _child_text(element, "MaxValue")
        items.append(field)
    return items


def _parse_query_limit_value(node: Any, tag: str) -> Any:
    from .. import xml_compat as ET

    if not isinstance(node, ET.Element):
        return None
    typed = _find_descendant(node, tag)
    if typed is not None:
        for child in typed:
            if _local_name(child.tag) == "double":
                text = (child.text or "").strip()
                if text:
                    try:
                        numeric = float(text)
                        return int(numeric) if numeric.is_integer() else numeric
                    except ValueError:
                        return text
    text_value = _child_text(node, f"{tag}Text")
    return text_value or None


@dataclass
class GenericCommand:
    """api-v2-004 passthrough wrapper for unmodeled compiled statements."""

    object_type: str = ""
    payload_xml: str = ""
    command_id: str = ""
    line_number: int = 0

    def to_string(self) -> str:
        return f"GenericCommand({self.command_id or self.object_type or 'raw'})"

    def validate(self) -> None:
        from .generic_command_validate import validate_generic_command_payload

        validate_generic_command_payload(self)

    def to_xml(self) -> str:
        if self.payload_xml.strip():
            return self.payload_xml.strip()
        self.validate()
        return (
            f'<Object Type="{_xml_text(self.object_type)}">\n'
            f"  <GenericCommand><LineNumber>{int(self.line_number)}</LineNumber></GenericCommand>\n"
            "</Object>"
        )


def command_to_xml(command: ICommand, *, validate: bool = True) -> str:
    """Single entry point for ``ICommand.ToXML()`` (offline serializer)."""
    import inspect

    to_xml = command.to_xml
    try:
        parameters = inspect.signature(to_xml).parameters
    except (TypeError, ValueError):
        return to_xml()
    if "validate" in parameters:
        return to_xml(validate=validate)  # type: ignore[call-arg]
    return to_xml()


def command_from_ir_step(step: Mapping[str, Any], *, line_number: int | None = None) -> ICommand | None:
    """Map a canonical IR step to the best-known typed API V2 command."""
    operation = str(step.get("operation") or "").strip()
    resolved_line = int(line_number if line_number is not None else step.get("line_number") or 0)

    def _with_line(command: ICommand | None) -> ICommand | None:
        if command is None or not resolved_line:
            return command
        if hasattr(command, "line_number") and not getattr(command, "line_number", 0):
            command.line_number = resolved_line  # type: ignore[attr-defined]
        elif hasattr(command, "line_number"):
            try:
                object.__setattr__(command, "line_number", resolved_line)
            except Exception:
                pass
        return command

    if operation == "prompt_user":
        return _with_line(user_prompt_from_ir_step(step))
    if operation == "add_labware":
        return _with_line(add_labware_from_ir_step(step))
    if operation == "call_subroutine":
        cmd = subroutine_from_ir_step(step)
        if cmd and resolved_line:
            cmd.line_number = resolved_line
        return cmd
    if operation == "move_plate":
        cmd = transfer_labware_from_ir_step(step)
        if cmd and resolved_line:
            cmd.line_number = resolved_line
        return cmd
    if operation in {"set_location", "manual_move"}:
        cmd = set_location_from_ir_step(step)
        if cmd and resolved_line:
            cmd.line_number = resolved_line
        return cmd
    if operation in {"cga_get_fingers", "get_fingers"}:
        cmd = get_fingers_from_ir_step(step)
        if cmd and resolved_line:
            cmd.line_number = resolved_line
        return cmd
    if operation in {"set_variable", "set_remaining_runtime"}:
        cmd = set_variable_from_ir_step(step)
        if cmd and resolved_line:
            cmd.line_number = resolved_line
        return cmd
    if operation == "query_variable":
        cmd = query_variable_from_ir_step(step)
        if cmd and resolved_line:
            cmd.line_number = resolved_line
        return cmd
    if operation == "runtime_variable_prompt":
        cmd = rup_variable_from_ir_step(step)
        if cmd and resolved_line:
            cmd.line_number = resolved_line
        return cmd
    return None


def command_from_xscr_object(element: Any, *, command_id: str | None = None) -> ICommand | None:
    """Parse a compiled XSCR ``Object`` node into a typed API V2 command."""
    from .. import xml_compat as ET

    if not isinstance(element, ET.Element):
        return None
    object_type = str(element.attrib.get("Type") or "")
    if command_id is None:
        command_id = object_type.rsplit(".", 1)[-1] if object_type else ""
    line_number = _extract_line_number(element)
    api_type = XSCR_COMMAND_ID_TO_API_V2.get(command_id or "")

    if api_type == "AddLabware" or command_id == "AddLabwareDataV1":
        node = _find_descendant(element, "AddLabwareDataV1")
        statement_data = _find_descendant(node, "ProgrammingStatementBaseDataV1")
        data_style = "nested" if statement_data is not None else "direct"
        return AddLabware(
            labware_type=_child_text(node, "LabwareType"),
            labware_label=_child_text(node, "LabwareLable") or _child_text(node, "LabwareLabel"),
            location=_child_text(node, "Location"),
            site=_child_text(node, "Position", "1") or 1,
            rotation=_child_text(node, "Rotation", "0") or 0,
            has_lid=_child_text(node, "HasLid", "False").lower() == "true",
            line_number=line_number,
            data_style=data_style,
            is_breakpoint=_child_text(statement_data, "IsBreakpoint", "False").lower() == "true",
            is_disabled_for_execution=_child_text(statement_data, "IsDisabledForExecution", "False").lower() == "true",
        )
    if api_type == "UserPrompt" or command_id in {
        "UserPromptStatement",
        "RUPStandardStatement",
        "RUPWorktableStatement",
    }:
        if command_id == "RUPWorktableStatement" or "RUPWorktableStatement" in object_type:
            node = _find_descendant(element, "RUPWorktableStatement")
            labware = _find_descendant(node, "ConfigureDataLabwareDataModel")
            return UserPrompt(
                prompt=_child_text(labware, "LabwareDescriptionGui")
                or _child_text(node, "MessageText")
                or _child_text(node, "CheckMessagePrompt"),
                image_path=_child_text(labware, "CustomDetailImageFilePath"),
                screen_title=_child_text(node, "RUPScreenTitle"),
                timeout=_rup_timeout_from_xscr(node),
                line_number=line_number,
                rup_kind="worktable",
                auto_close=_child_text(node, "RUPAutoClose", "False").lower() == "true",
                rup_timeout=_rup_raw_timeout_from_xscr(node),
                is_breakpoint=_child_text(node, "IsBreakpoint", "False").lower() == "true",
                is_disabled_for_execution=_child_text(node, "IsDisabledForExecution", "False").lower() == "true",
                selected_labware_name=_child_text(labware, "LabwareName"),
                selected_labware_type=_child_text(labware, "LabwareTypeName"),
                preserve_selected_labware_expression=True,
                grid=_child_text(labware, "Grid"),
                site=_child_text(labware, "Site"),
                is_labware_existence_mandatory=_child_text(
                    labware,
                    "IsLabwareExistenceMandatory",
                    "False",
                ).lower()
                == "true",
            )
        if command_id == "RUPStandardStatement" or "RUPStandardStatement" in object_type:
            node = _find_descendant(element, "RUPStandardStatement")
            std = _find_descendant(node, "StandardStatementDataClass")
            return UserPrompt(
                prompt=_child_text(std, "MessageText") or _child_text(node, "CheckMessagePrompt"),
                image_path=_child_text(std, "SelectedImagePath"),
                screen_title=_child_text(node, "RUPScreenTitle"),
                timeout=_rup_timeout_from_xscr(node),
                line_number=line_number,
                rup_kind="standard",
                auto_close=_child_text(node, "RUPAutoClose", "False").lower() == "true",
                rup_timeout=_rup_raw_timeout_from_xscr(node),
                variable_scope=_child_text(std, "VariableScope"),
                is_breakpoint=_child_text(node, "IsBreakpoint", "False").lower() == "true",
                is_disabled_for_execution=_child_text(node, "IsDisabledForExecution", "False").lower() == "true",
                sound_path=_child_text(std, "SelectedSoundPath"),
                change_status_light_color=_child_text(node, "ChangeStatusLightColor", "False").lower() == "true",
                status_light_color_string=_child_text(node, "StatusLightColorString"),
                percent_complete_string=_child_text(std, "PercentCompleteString"),
            )
        node = _find_descendant(element, "UserPromptStatement")
        return UserPrompt(
            prompt=_child_text(node, "Prompt"),
            timeout=_child_text(node, "Timeout", "0") or 0,
            line_number=line_number,
            auto_close=_child_text(node, "AutoClose", "False").lower() == "true",
            is_breakpoint=_child_text(node, "IsBreakpoint", "False").lower() == "true",
            is_disabled_for_execution=_child_text(node, "IsDisabledForExecution", "False").lower() == "true",
            sound_path=_child_text(node, "SoundFile"),
        )
    if api_type == "Subroutine" or command_id == "SubRoutineStatement":
        node = _find_descendant(element, "SubRoutineStatement")
        start = tuple(
            VariableMapping(target=item["target"], source=item["source"])
            for item in _variable_mappings_from_statement(node, "VariableMappingsStart")
        )
        end = tuple(
            VariableMapping(target=item["target"], source=item["source"])
            for item in _variable_mappings_from_statement(node, "VariableMappingsEnd")
        )
        return Subroutine(
            path=_child_text(node, "SubRoutine").strip('"'),
            execution_mode=_execution_mode_from_statement(node),
            variable_mappings_start=start,
            variable_mappings_end=end,
            line_number=line_number,
        )
    if api_type == "TransferLabware" or command_id == "ApplicationDriverMacro":
        macro = _find_descendant(element, "ApplicationDriverMacro")
        macro_name = str(macro.attrib.get("Name") or "") if macro is not None else ""
        if macro_name != "RGA1_TransferLabware":
            return GenericCommand(
                object_type=object_type,
                payload_xml=ET.tostring(element, encoding="unicode"),
                command_id=command_id or "",
                line_number=line_number,
            )
        settings = _child_text(macro, "ExecutionSettings")
        return TransferLabware(
            labware=_parse_transfer_setting(settings, "Labware"),
            location=_parse_transfer_setting(settings, "Location"),
            site=_parse_transfer_setting(settings, "Site", "1") or 1,
            move_to_base=_parse_transfer_setting(settings, "MoveToBase", "false").lower() == "true",
            fixed_site=_parse_transfer_setting(settings, "FixedSite", "true").lower() == "true",
            module_name=str(macro.attrib.get("ModuleName") or "") if macro is not None else "",
            is_breakpoint=str(macro.attrib.get("IsBreakpoint") or "false").lower() == "true" if macro is not None else False,
            is_disabled_for_execution=str(macro.attrib.get("IsDisabledForExecution") or "false").lower() == "true" if macro is not None else False,
            execution_settings=settings,
            line_number=line_number,
        )
    if api_type == "SetLocation" or command_id == "SetLocationStatement":
        node = _find_descendant(element, "SetLocationStatement")
        return SetLocation(
            labware=_child_text(node, "Labware"),
            location=_child_text(node, "Location"),
            site=_child_text(node, "Site", "1") or 1,
            rotation=_child_text(node, "Rotation", "0") or 0,
            line_number=line_number,
        )
    if api_type == "DropFingers" or command_id == "CgaDropFingersScriptCommandDataV1":
        return drop_fingers_from_xscr_object(element, line_number=line_number)
    if api_type == "RemoveLabware" or command_id == "RemoveLabwareDataV1":
        return remove_labware_from_xscr_object(element, line_number=line_number)
    if api_type == "GetFingers" or command_id == "CgaGetFingersScriptCommandDataV1":
        return get_fingers_from_xscr_element(element, line_number=line_number)
    if api_type == "SetVariable" or command_id == "SetVariableStatement":
        node = _find_descendant(element, "SetVariableStatement")
        return SetVariable(
            name=_child_text(node, "Name"),
            value=parse_or_preserve_source_expression(_child_text(node, "Value")),
            line_number=line_number,
            is_breakpoint=_child_bool(node, "IsBreakpoint", default=False),
            is_disabled_for_execution=_child_bool(node, "IsDisabledForExecution", default=False),
        )
    if api_type == "QueryVariable" or command_id == "QueryVariableStatement":
        node = _find_descendant(element, "QueryVariableStatement")
        return QueryVariable(
            name=_child_text(node, "Name"),
            query_prompt=_child_text(node, "QueryPrompt"),
            limit_range=_child_bool(node, "LimitRange", default=False),
            minimum=_parse_query_limit_value(node, "Minimum"),
            maximum=_parse_query_limit_value(node, "Maximum"),
            line_number=line_number,
            is_breakpoint=_child_bool(node, "IsBreakpoint", default=False),
            is_disabled_for_execution=_child_bool(node, "IsDisabledForExecution", default=False),
        )
    if api_type == "RUPVariable" or command_id == "RUPVariableStatement":
        node = _find_descendant(element, "RUPVariableStatement")
        model = _find_descendant(node, "VariableDataModel")
        return RUPVariable(
            screen_title=_child_text(node, "RUPScreenTitle") or "User Input",
            instructions=_child_text(model, "Instructions") or "Please make your selections",
            layout_horizontal=_child_bool(model, "IsLayoutHorizontalOriented", default=True),
            columns=int(_child_text(model, "NumberOfColumns") or _child_text(model, "UnresolvedNumberOfColumns") or 1),
            display_and_wait=_child_bool(node, "RUPDisplayAndWait", default=True),
            auto_close=_child_bool(node, "RUPAutoClose", default=False),
            timeout=int(_child_text(node, "RUPTimeOut") or 1),
            disabled=_child_bool(node, "IsDisabledForExecution", default=False),
            variables=tuple(_parse_rup_variable_items(node)),
            line_number=line_number,
            is_breakpoint=_child_bool(node, "IsBreakpoint", default=False),
            is_disabled_for_execution=_child_bool(node, "IsDisabledForExecution", default=False),
        )
    return GenericCommand(
        object_type=object_type,
        payload_xml=ET.tostring(element, encoding="unicode"),
        command_id=command_id or "",
        line_number=line_number,
    )


AddLabwareCommand = AddLabware
UserPromptCommand = UserPrompt
DropFingersCommand = DropFingers
RemoveLabwareCommand = RemoveLabware
SetVariableCommand = SetVariable
QueryVariableCommand = QueryVariable
RUPVariableCommand = RUPVariable
