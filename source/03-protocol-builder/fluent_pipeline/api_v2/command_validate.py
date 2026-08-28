"""Per-command ICommand.Validate() batch validation (api-v2-006).

Offline path: typed ``validate()`` on API V2 command objects plus script-level
heuristics that mirror FluentControl Script Editor open/load failures.

Runtime path: ``NativeApiV2CommandValidateProvider`` reserved for VisionX COM
``ICommand.Validate()`` once ``ApiV2FluentControlProvider`` is wired.
"""

from __future__ import annotations

import re
from .. import xml_compat as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from .commands import AddLabware, GetFingers, command_from_xscr_object, validate_command
from .types import ApiV2ValidationError
from .xml_compare import NON_EXECUTABLE_OBJECT_TYPES, extract_command_objects_from_xscr

API_V2_COMMAND_VALIDATE_VERSION = "tecan.api_v2_command_validate.v1"
API_V2_ISSUE_ID = "api-v2-006"
API_V2_METHOD = "ICommand.Validate()"
API_V2_GET_FINGERS_ISSUE_ID = "api-v2-018"
API_V2_GET_FINGERS_METHOD = "GetFingers.Validate()"

VARIABLE_HANDLING_NAMESPACE = "http://schemas.datacontract.org/2004/07/Tecan.VisionX.VariableHandling.Shared"
CONFIGURE_DATA_LABWARE_BOOL_TAGS = (
    "IsLabwareExistenceMandatory",
    "IsCustomDetailImageUsed",
    "IsCarrier",
)
_CONFIGURE_DATA_LABWARE_BOOL_TAG_RES = {
    tag: re.compile(rf"(<{tag}>)(True|False)(</{tag}>)")
    for tag in CONFIGURE_DATA_LABWARE_BOOL_TAGS
}

_PASCAL_BOOL_IN_CONFIGURE_DATA_LABWARE_RE = re.compile(
    r"<ConfigureDataLabwareDataModel\b[\s\S]*?</ConfigureDataLabwareDataModel>",
    re.MULTILINE,
)


@dataclass(frozen=True)
class CompiledCommandRecord:
    index: int
    command_id: str
    object_type: str
    line_number: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "command_id": self.command_id,
            "object_type": self.object_type,
            "line_number": self.line_number,
        }


@dataclass(frozen=True)
class CommandValidationFailure:
    command_index: int
    command_type: str
    reason: str
    message: str
    line_number: str | None = None
    object_type: str = ""
    source: str = "offline_heuristic"
    api_v2_method: str = API_V2_METHOD
    api_v2_issue: str = API_V2_ISSUE_ID
    extra: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "command_index": self.command_index,
            "command_type": self.command_type,
            "reason": self.reason,
            "message": self.message,
            "source": self.source,
            "api_v2_method": self.api_v2_method,
            "api_v2_issue": self.api_v2_issue,
        }
        if self.line_number:
            payload["line_number"] = self.line_number
        if self.object_type:
            payload["object_type"] = self.object_type
        if self.extra:
            payload.update({key: value for key, value in self.extra.items() if value not in (None, "", [], {})})
        return {key: value for key, value in payload.items() if value not in (None, "", [], {})}

    def as_finding(self) -> dict[str, Any]:
        finding = {"reason": self.reason, "message": self.message}
        if self.line_number:
            finding["line_number"] = self.line_number
        finding.update({key: value for key, value in self.extra.items() if value not in (None, "", [], {})})
        return {key: value for key, value in finding.items() if value not in (None, "", [], {})}


@dataclass(frozen=True)
class CommandValidationReport:
    ok: bool
    provider: str
    command_count: int
    failure_count: int
    failures: tuple[CommandValidationFailure, ...] = ()
    commands: tuple[CompiledCommandRecord, ...] = ()
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": API_V2_COMMAND_VALIDATE_VERSION,
            "ok": self.ok,
            "provider": self.provider,
            "command_count": self.command_count,
            "failure_count": self.failure_count,
            "summary": self.summary,
            "failures": [item.as_dict() for item in self.failures],
            "commands": [item.as_dict() for item in self.commands],
        }

    def fluentcontrol_findings(self) -> list[dict[str, Any]]:
        return [item.as_finding() for item in self.failures]


class CommandValidateProvider(Protocol):
    name: str

    def validate_compiled_xscr(self, path: Path) -> CommandValidationReport:
        ...


class OfflineCommandValidateProvider:
    name = "offline"

    def validate_compiled_xscr(self, path: Path) -> CommandValidationReport:
        if not path.exists():
            return CommandValidationReport(
                ok=True,
                provider=self.name,
                command_count=0,
                failure_count=0,
                summary="No compiled XSCR to validate.",
            )
        try:
            text = path.read_text(encoding="utf-8-sig")
        except OSError:
            text = ""
        try:
            root = ET.parse(path).getroot()
        except Exception as exc:
            failure = CommandValidationFailure(
                command_index=-1,
                command_type="Script",
                reason="xscr_parse_error",
                message=f"Compiled XSCR could not be parsed for command validation: {exc}",
            )
            return CommandValidationReport(
                ok=False,
                provider=self.name,
                command_count=0,
                failure_count=1,
                failures=(failure,),
                summary="Compiled XSCR parse failed during command validation.",
            )
        return validate_compiled_commands(root, text, provider=self.name)


class NativeApiV2CommandValidateProvider:
    name = "api-v2-native"

    def validate_compiled_xscr(self, path: Path) -> CommandValidationReport:
        return CommandValidationReport(
            ok=False,
            provider=self.name,
            command_count=0,
            failure_count=0,
            summary=(
                "Native API V2 ICommand.Validate() provider is not configured yet "
                f"({API_V2_ISSUE_ID}). Use the offline provider until GetRuntime() is wired."
            ),
        )


def default_command_validate_provider() -> CommandValidateProvider:
    return OfflineCommandValidateProvider()


def validate_compiled_xscr_commands(
    path: Path,
    *,
    provider: CommandValidateProvider | None = None,
) -> CommandValidationReport:
    active = provider or default_command_validate_provider()
    return active.validate_compiled_xscr(path)


def validate_compiled_commands(
    root: ET.Element,
    text: str = "",
    *,
    provider: str = "offline",
) -> CommandValidationReport:
    records = _records_from_xscr(root, text)
    commands = [
        CompiledCommandRecord(
            index=index,
            command_id=record["command_id"],
            object_type=record.get("object_type") or "",
            line_number=_extract_line_number_from_xml(record.get("xml") or "") or None,
        )
        for index, record in enumerate(records)
    ]
    declared_variables = _compiled_variable_declarations(root)
    failures = [
        *validate_script_level_commands(text),
        *validate_typed_commands(records, declared_variables=declared_variables),
        *validate_cross_command_heuristics(root, commands, declared_variables=declared_variables),
    ]
    failures.sort(
        key=lambda item: (
            0 if item.api_v2_issue == API_V2_ISSUE_ID else 1,
            item.command_index,
            item.reason,
        )
    )
    failures = failures[:50]
    ok = not failures
    summary = (
        f"All {len(commands)} compiled command(s) passed offline ICommand.Validate() checks."
        if ok
        else f"{len(failures)} command validation failure(s) in compiled XSCR."
    )
    return CommandValidationReport(
        ok=ok,
        provider=provider,
        command_count=len(commands),
        failure_count=len(failures),
        failures=tuple(failures),
        commands=tuple(commands),
        summary=summary,
    )


def validate_typed_commands(
    records: list[dict[str, str]],
    *,
    declared_variables: set[str] | None = None,
) -> list[CommandValidationFailure]:
    failures: list[CommandValidationFailure] = []
    for index, record in enumerate(records):
        xml_text = record.get("xml") or ""
        command_id = record.get("command_id") or ""
        object_type = record.get("object_type") or ""
        if not xml_text.strip():
            continue
        try:
            element = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            failures.append(
                CommandValidationFailure(
                    command_index=index,
                    command_type=command_id,
                    reason="command_xml_parse_error",
                    message=str(exc),
                    object_type=object_type,
                    source="offline_typed_validate",
                )
            )
            continue
        if _compiled_element_disabled(element):
            continue
        command = command_from_xscr_object(element, command_id=command_id)
        if command is None:
            continue
        try:
            if isinstance(command, AddLabware):
                if not str(command.location or "").strip():
                    continue
                from ..api_v2_add_labware_validate import AddLabwareFields, validate_add_labware_fields

                result = validate_add_labware_fields(
                    AddLabwareFields(
                        labware_type=command.labware_type,
                        labware_label=command.labware_label,
                        location=command.location,
                        site=command.site,
                        rotation=command.rotation,
                        has_lid=command.has_lid,
                    ),
                    declared_variables=declared_variables or set(),
                    step_index=index,
                )
                if not result.ok:
                    raise ApiV2ValidationError(
                        result.message or "AddLabware validation failed.",
                        field=result.field,
                        command="AddLabware",
                    )
            else:
                validate_command(command)
        except ApiV2ValidationError as exc:
            api_method = API_V2_METHOD
            api_issue = API_V2_ISSUE_ID
            if isinstance(command, GetFingers):
                api_method = API_V2_GET_FINGERS_METHOD
                api_issue = API_V2_GET_FINGERS_ISSUE_ID
            failures.append(
                CommandValidationFailure(
                    command_index=index,
                    command_type=str(getattr(exc, "command", "") or command_id),
                    reason="api_v2_validate_rejected",
                    message=str(exc),
                    line_number=_extract_line_number_from_xml(xml_text) or None,
                    object_type=object_type,
                    source="offline_typed_validate",
                    api_v2_method=api_method,
                    api_v2_issue=api_issue,
                    extra={"field": getattr(exc, "field", "")},
                )
            )
    return failures


def validate_script_level_commands(text: str) -> list[CommandValidationFailure]:
    failures: list[CommandValidationFailure] = []
    for finding in _compiled_xsi_type_namespace_findings(text):
        failures.append(
            CommandValidationFailure(
                command_index=-1,
                command_type="VariableDeclarations",
                reason=str(finding.get("reason") or "variable_declaration_invalid"),
                message=str(finding.get("message") or "Variable declaration namespace issue."),
                extra={key: value for key, value in finding.items() if key not in {"reason", "message"}},
            )
        )
    for finding in _rup_configure_data_labware_bool_findings(text):
        failures.append(
            CommandValidationFailure(
                command_index=-1,
                command_type="RUPWorktableStatement",
                reason=str(finding.get("reason") or "rup_configure_data_labware_bool_casing"),
                message=str(finding.get("message") or "ConfigureDataLabwareDataModel boolean casing is invalid."),
                extra={key: value for key, value in finding.items() if key not in {"reason", "message"}},
            )
        )
    for finding in _rup_configure_data_labware_variable_index_findings(text):
        failures.append(
            CommandValidationFailure(
                command_index=-1,
                command_type="RUPWorktableStatement",
                reason=str(finding.get("reason") or "rup_configure_data_labware_variable_index"),
                message=str(finding.get("message") or "ConfigureDataLabwareDataModel LabwareName index is invalid."),
                extra={key: value for key, value in finding.items() if key not in {"reason", "message"}},
            )
        )
    return failures


def _rup_configure_data_labware_bool_findings(text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for block in _PASCAL_BOOL_IN_CONFIGURE_DATA_LABWARE_RE.findall(text or ""):
        for tag in CONFIGURE_DATA_LABWARE_BOOL_TAGS:
            match = re.search(rf"<{tag}>(True|False)</{tag}>", block)
            if not match:
                continue
            findings.append(
                {
                    "reason": "rup_configure_data_labware_bool_casing",
                    "message": (
                        f"ConfigureDataLabwareDataModel field `{tag}` must use lowercase XML booleans "
                        f"(found `{match.group(1)}`; FluentControl Script Editor rejects PascalCase here)."
                    ),
                    "field": tag,
                    "value": match.group(1),
                }
            )
    return findings


def _rup_configure_data_labware_variable_index_findings(text: str) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for block in _PASCAL_BOOL_IN_CONFIGURE_DATA_LABWARE_RE.findall(text or ""):
        name_match = re.search(r"<LabwareName>(.*?)</LabwareName>", block, re.DOTALL)
        if not name_match:
            continue
        labware_name = re.sub(r"\s+", " ", name_match.group(1)).strip()
        index_match = re.search(r"\[([^\]]+)\]", labware_name)
        if not index_match or re.fullmatch(r"\d+", index_match.group(1).strip()):
            continue
        findings.append(
            {
                "reason": "rup_configure_data_labware_variable_index",
                "message": (
                    "ConfigureDataLabwareDataModel LabwareName uses a variable labware index "
                    f"(`{labware_name}`). FluentControl Script Editor expects a concrete numeric index "
                    "such as `[001]` here."
                ),
                "labware_name": labware_name,
                "index": index_match.group(1).strip(),
            }
        )
    return findings


def normalize_configure_data_labware_boolean_casing(text: str) -> tuple[str, list[dict[str, str]]]:
    fixups: list[dict[str, str]] = []

    def replace_block(block: str) -> str:
        updated = block
        for tag, pattern in _CONFIGURE_DATA_LABWARE_BOOL_TAG_RES.items():
            def replacer(match: re.Match[str], *, tag_name: str = tag) -> str:
                fixups.append(
                    {
                        "field": tag_name,
                        "from": match.group(2),
                        "to": match.group(2).lower(),
                    }
                )
                return f"{match.group(1)}{match.group(2).lower()}{match.group(3)}"

            updated = pattern.sub(replacer, updated)
        return updated

    normalized = _PASCAL_BOOL_IN_CONFIGURE_DATA_LABWARE_RE.sub(
        lambda match: replace_block(match.group(0)),
        text or "",
    )
    return normalized, fixups


def validate_cross_command_heuristics(
    root: ET.Element,
    commands: list[CompiledCommandRecord],
    *,
    declared_variables: set[str] | None = None,
) -> list[CommandValidationFailure]:
    failures: list[CommandValidationFailure] = []
    declared_variables = declared_variables if declared_variables is not None else _compiled_variable_declarations(root)
    referenced_scripts = _compiled_script_reference_names(root)
    labware_types = _compiled_labware_types_by_label(root)

    for statement in _elements_by_local_name(root, "UserPromptStatement"):
        record = _record_for_element(commands, statement, "UserPromptStatement")
        timeout_text = _first_text(statement, "Timeout")
        timeout = _number(timeout_text)
        if timeout is None or timeout < 1 or timeout > 7200:
            failures.append(
                _failure_for_record(
                    record,
                    "UserPromptStatement",
                    "prompt_timeout_out_of_range",
                    "User prompt timeout must be between 1 and 7200 seconds.",
                    {"timeout": timeout_text},
                )
            )

    for label in _compiled_labware_name_fields(root):
        for variable in _bracket_variable_names(label):
            if variable in declared_variables:
                continue
            element = _compiled_element_for_labware_name(root, label)
            record = _record_for_element(commands, element, "AddLabwareDataV1") if element is not None else None
            failures.append(
                _failure_for_record(
                    record,
                    record.command_id if record else "AddLabwareDataV1",
                    "undeclared_variable",
                    f"Labware expression {label!r} references undeclared variable {variable!r}.",
                    {"labware_name": label, "variable": variable},
                )
            )

    for statement in _elements_by_local_name(root, "SubRoutineStatement"):
        record = _record_for_element(commands, statement, "SubRoutineStatement")
        subroutine = _clean_subroutine_name(_first_text(statement, "SubRoutine"))
        if subroutine and not _subroutine_reference_matches(subroutine, referenced_scripts):
            failures.append(
                _failure_for_record(
                    record,
                    "SubRoutineStatement",
                    "subroutine_reference_missing",
                    "Subroutine call has no matching Script reference in the compiled XSCR.",
                    {"subroutine": subroutine, "script_references": sorted(referenced_scripts)},
                )
            )

    for command_name in ("CgaGetFingersScriptCommandDataV1", "CgaDropFingersScriptCommandDataV1"):
        for command in _elements_by_local_name(root, command_name):
            record = _record_for_element(commands, command, command_name)
            labware_name = _first_text(command, "LabwareName")
            labware_type = labware_types.get(_norm_name(labware_name), "")
            if labware_type and "adapter" in labware_type.casefold():
                failures.append(
                    _failure_for_record(
                        record,
                        command_name,
                        "rga_fingers_incompatible_labware",
                        "RGA finger pickup/drop commands cannot target adapter labware.",
                        {"labware_name": labware_name, "labware_type": labware_type},
                    )
                )

    failures.extend(
        _validate_add_labware_cross_command(root, commands, declared_variables=declared_variables)
    )
    return failures


def _validate_add_labware_cross_command(
    root: ET.Element,
    commands: list[CompiledCommandRecord],
    *,
    declared_variables: set[str],
) -> list[CommandValidationFailure]:
    """Sequential duplicate label/slot checks (field rules run in validate_typed_commands)."""
    from ..api_v2_add_labware_validate import AddLabwareFields

    failures: list[CommandValidationFailure] = []
    prior_labels: set[str] = set()
    prior_slots: set[tuple[str, str]] = set()
    for add_labware in _elements_by_local_name(root, "AddLabwareDataV1"):
        if _compiled_element_disabled(add_labware):
            continue
        record = _record_for_element(commands, add_labware, "AddLabwareDataV1")
        fields = AddLabwareFields(
            labware_type=_first_text(add_labware, "LabwareType"),
            labware_label=_first_text(add_labware, "LabwareLable") or _first_text(add_labware, "LabwareLabel"),
            location=_first_text(add_labware, "Location"),
            site=_first_text(add_labware, "Position", "1") or 1,
            rotation=_first_text(add_labware, "Rotation", "0") or 0,
            has_lid=_first_text(add_labware, "HasLid", "False").lower() == "true",
        )
        label_key = fields.label_key()
        slot = fields.slot_key()
        if label_key and label_key in prior_labels:
            failures.append(
                CommandValidationFailure(
                    command_index=record.index if record else -1,
                    command_type=record.command_id if record else "AddLabwareDataV1",
                    reason="duplicate_labware_label",
                    message=(
                        f"Labware label {fields.labware_label!r} is already placed "
                        "by an earlier AddLabware command."
                    ),
                    line_number=record.line_number if record else None,
                    object_type=record.object_type if record else "",
                    source="offline_typed_validate",
                    api_v2_method="AddLabware.Validate()",
                    extra={"api_v2_issue": "api-v2-008", "field": "labware_label", **fields.as_dict()},
                )
            )
            continue
        if slot[0] and slot in prior_slots:
            failures.append(
                CommandValidationFailure(
                    command_index=record.index if record else -1,
                    command_type=record.command_id if record else "AddLabwareDataV1",
                    reason="occupied_slot",
                    message=(
                        f"Deck slot {fields.location}[{fields.site}] is already occupied "
                        "by an earlier AddLabware command."
                    ),
                    line_number=record.line_number if record else None,
                    object_type=record.object_type if record else "",
                    source="offline_typed_validate",
                    api_v2_method="AddLabware.Validate()",
                    extra={"api_v2_issue": "api-v2-008", "field": "site", **fields.as_dict()},
                )
            )
            continue
        if label_key:
            prior_labels.add(label_key)
        if slot[0]:
            prior_slots.add(slot)
    return failures


def _records_from_xscr(root: ET.Element, text: str) -> list[dict[str, str]]:
    return extract_command_objects_from_xscr(text) or _records_from_root(root)


def _records_from_root(root: ET.Element) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for element in root.iter():
        if _local_name(element.tag) != "Object":
            continue
        object_type = str(element.attrib.get("Type") or "")
        if object_type.rsplit(".", 1)[-1] in NON_EXECUTABLE_OBJECT_TYPES:
            continue
        if ".Commands." not in object_type and not any(
            marker in object_type
            for marker in (
                "UserPromptStatement",
                "RUPStandardStatement",
                "SubRoutineStatement",
                "ApplicationDriverMacro",
                "AddLabwareDataV1",
                "CgaGetFingersScriptCommandDataV1",
                "CgaDropFingersScriptCommandDataV1",
            )
        ):
            continue
        command_id = object_type.rsplit(".", 1)[-1] if object_type else ""
        records.append(
            {
                "command_id": command_id,
                "object_type": object_type,
                "xml": ET.tostring(element, encoding="unicode"),
            }
        )
    return records


def _failure_for_record(
    record: CompiledCommandRecord | None,
    command_type: str,
    reason: str,
    message: str,
    extra: Mapping[str, Any] | None = None,
) -> CommandValidationFailure:
    return CommandValidationFailure(
        command_index=record.index if record else -1,
        command_type=record.command_id if record else command_type,
        reason=reason,
        message=message,
        line_number=record.line_number if record else None,
        object_type=record.object_type if record else "",
        extra=dict(extra or {}),
    )


def _record_for_element(
    commands: list[CompiledCommandRecord],
    element: ET.Element | None,
    command_id: str,
) -> CompiledCommandRecord | None:
    if element is None:
        for record in commands:
            if record.command_id == command_id:
                return record
        return None
    line_number = _first_text(element, "LineNumber") or None
    for record in commands:
        if record.command_id == command_id and record.line_number == line_number:
            return record
    for record in commands:
        if record.command_id == command_id:
            return record
    return None


def _extract_line_number_from_xml(xml_text: str) -> str:
    match = re.search(r"<LineNumber>\s*(\d+)\s*</LineNumber>", xml_text)
    return match.group(1) if match else ""


def _compiled_xsi_type_namespace_findings(text: str) -> list[dict[str, Any]]:
    if "VariableDefinitionHelper" not in text:
        return []
    declared = {
        match.group(1): match.group(2)
        for match in re.finditer(r'\bxmlns:([A-Za-z_][\w.-]*)="([^"]+)"', text)
    }
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for match in re.finditer(r'\b((?:xsi|i):type)="([^"]*VariableDefinitionHelper)"', text):
        attr_name = match.group(1)
        type_value = match.group(2)
        prefix = type_value.split(":", 1)[0] if ":" in type_value else ""
        uri = declared.get(prefix, "") if prefix else ""
        if prefix and uri == VARIABLE_HANDLING_NAMESPACE:
            continue
        key = (attr_name, prefix, type_value)
        if key in seen:
            continue
        seen.add(key)
        reason = "variable_declaration_type_namespace_missing" if prefix else "variable_declaration_type_namespace_empty"
        findings.append(
            {
                "reason": reason,
                "message": (
                    "VariableDeclarations use a VariableDefinitionHelper xsi:type whose namespace is not "
                    "bound to Tecan.VisionX.VariableHandling.Shared; FluentControl Script Editor will fail "
                    "to deserialize startup variables."
                ),
                "attribute": attr_name,
                "type_value": type_value,
                "prefix": prefix,
                "namespace": uri,
                "expected_namespace": VARIABLE_HANDLING_NAMESPACE,
            }
        )
    for match in re.finditer(
        r'<(?:[A-Za-z_][\w.-]*:)?anyType\b(?=[^>]*\b(?:xsi|i):type="([^"]*VariableDefinitionHelper)")[^>]*>',
        text,
    ):
        tag = match.group(0)
        type_match = re.search(r'\b(?:xsi|i):type="([^"]*VariableDefinitionHelper)"', tag)
        type_value = type_match.group(1) if type_match else ""
        prefix = type_value.split(":", 1)[0] if ":" in type_value else ""
        if not prefix:
            continue
        if re.search(rf'\bxmlns:{re.escape(prefix)}="{re.escape(VARIABLE_HANDLING_NAMESPACE)}"', tag):
            continue
        key = ("local", prefix, type_value)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            {
                "reason": "variable_declaration_type_namespace_not_local",
                "message": (
                    "VariableDefinitionHelper xsi:type must declare its namespace on the anyType element; "
                    "FluentControl Script Editor deserializes variable declarations from inner XML and loses "
                    "ancestor namespace declarations."
                ),
                "type_value": type_value,
                "prefix": prefix,
                "namespace": declared.get(prefix, ""),
                "expected_namespace": VARIABLE_HANDLING_NAMESPACE,
            }
        )
    return findings


def _compiled_element_for_labware_name(root: ET.Element, labware_name: str) -> ET.Element | None:
    for element in root.iter():
        for field_name in ("LabwareName", "LabwareLable", "LabwareLabel"):
            if _first_text(element, field_name) == labware_name:
                return element
    return None


def _compiled_variable_declarations(root: ET.Element) -> set[str]:
    names: set[str] = set()
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "anyType":
            type_hint = " ".join(str(value) for value in element.attrib.values())
            if "VariableDefinitionHelper" not in type_hint:
                continue
        elif local != "VariableDefinitionHelper":
            continue
        name = _first_text(element, "Name")
        if name:
            names.add(name)
    return names


def _compiled_script_reference_names(root: ET.Element) -> set[str]:
    names: set[str] = set()
    for ref in _elements_by_local_name(root, "Reference"):
        type_id = _first_text(ref, "TypeId")
        object_name = _first_text(ref, "ObjectName")
        if type_id == "Script" and object_name:
            names.add(_clean_subroutine_name(object_name))
    return names


def _compiled_labware_types_by_label(root: ET.Element) -> dict[str, str]:
    labels: dict[str, str] = {}
    for add_labware in _elements_by_local_name(root, "AddLabwareDataV1"):
        if _compiled_element_disabled(add_labware):
            continue
        label = _first_text(add_labware, "LabwareLable") or _first_text(add_labware, "LabwareLabel")
        labware_type = _first_text(add_labware, "LabwareType")
        if label and labware_type:
            labels[_norm_name(label)] = labware_type
    return labels


def _compiled_labware_name_fields(root: ET.Element) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for element in root.iter():
        local = _local_name(element.tag)
        if local not in {"LabwareName", "LabwareLable", "LabwareLabel"}:
            continue
        value = (element.text or "").strip()
        if value and value not in seen:
            seen.add(value)
            names.append(value)
    return names


def _first_text(root: ET.Element, name: str, default: str = "") -> str:
    for element in root.iter():
        if _local_name(element.tag) == name:
            text = (element.text or "").strip()
            return text if text else default
    return default


def _compiled_element_disabled(element: ET.Element) -> bool:
    return _first_text(element, "IsDisabledForExecution").casefold() == "true"


def _elements_by_local_name(root: ET.Element, name: str) -> list[ET.Element]:
    return [element for element in root.iter() if _local_name(element.tag) == name]


def _bracket_variable_names(value: Any) -> set[str]:
    return {match.group(1) for match in re.finditer(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", str(value or ""))}


def _clean_subroutine_name(value: Any) -> str:
    return str(value or "").strip().strip('"').replace("/", "\\")


def _subroutine_reference_matches(subroutine: str, references: set[str]) -> bool:
    subroutine_norm = _norm_name(subroutine.rsplit("\\", 1)[-1])
    return any(
        _norm_name(reference) == _norm_name(subroutine)
        or _norm_name(reference.rsplit("\\", 1)[-1]) == subroutine_norm
        for reference in references
    )


def _norm_name(value: Any) -> str:
    return str(value or "").strip().casefold()


def _number(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _local_name(tag: Any) -> str:
    text = str(tag)
    if "}" in text:
        return text.rsplit("}", 1)[-1]
    return text
