"""GenericCommand.Validate() scaffold (api-v2-016).

Offline path: structural checks on approved_passthrough compiled command payloads
before ready packaging or stepped Gate 27 ``ExecuteCommand``.

Runtime path: ``NativeGenericCommandValidateProvider`` is reserved for VisionX API V2
``Tecan.VisionX.API.V2.Commands.GenericCommand.Validate()`` once a native provider
is wired (set ``TECAN_FC_NATIVE_VALIDATE=1``).
"""

from __future__ import annotations

import os
from .. import xml_compat as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol

from ..command_registry import registry_command_approved_passthrough, registry_command_support_status
from .commands import GenericCommand
from .types import ApiV2ValidationError

API_V2_GENERIC_VALIDATE_VERSION = "tecan.api_v2_generic_command_validate.v1"
API_V2_ISSUE_ID = "api-v2-016"
API_V2_METHOD = "GenericCommand.Validate()"


@dataclass(frozen=True)
class GenericCommandValidationFailure:
    index: int
    command_id: str
    reason: str
    message: str
    line_number: int | None = None
    object_type: str = ""
    source: str = "offline_schema"
    api_v2_method: str = API_V2_METHOD

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "index": self.index,
            "command_id": self.command_id,
            "reason": self.reason,
            "message": self.message,
            "source": self.source,
            "api_v2_method": self.api_v2_method,
            "api_v2_issue": API_V2_ISSUE_ID,
        }
        if self.line_number is not None:
            payload["line_number"] = self.line_number
        if self.object_type:
            payload["object_type"] = self.object_type
        return payload

    def as_finding(self) -> dict[str, Any]:
        finding = {"reason": self.reason, "message": self.message}
        if self.line_number is not None:
            finding["line_number"] = str(self.line_number)
        if self.command_id:
            finding["command_id"] = self.command_id
        return finding


@dataclass(frozen=True)
class GenericCommandValidationReport:
    ok: bool
    provider: str
    passthrough_count: int
    failure_count: int
    failures: tuple[GenericCommandValidationFailure, ...] = ()
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": API_V2_GENERIC_VALIDATE_VERSION,
            "ok": self.ok,
            "provider": self.provider,
            "passthrough_count": self.passthrough_count,
            "failure_count": self.failure_count,
            "summary": self.summary,
            "failures": [item.as_dict() for item in self.failures],
            "api_v2_issue": API_V2_ISSUE_ID,
            "api_v2_method": API_V2_METHOD,
        }

    def fluentcontrol_findings(self) -> list[dict[str, Any]]:
        return [item.as_finding() for item in self.failures]


class GenericCommandValidateProvider(Protocol):
    name: str

    def validate_passthrough_commands(self, path: Path) -> GenericCommandValidationReport:
        ...


class OfflineGenericCommandValidateProvider:
    name = "offline"

    def validate_passthrough_commands(self, path: Path) -> GenericCommandValidationReport:
        return validate_passthrough_commands_from_xscr(path, provider=self.name)


class NativeGenericCommandValidateProvider:
    """Reserved native VisionX API V2 GenericCommand.Validate() provider."""

    name = "api-v2-native"

    def validate_passthrough_commands(self, path: Path) -> GenericCommandValidationReport:
        if not native_generic_validate_available():
            return GenericCommandValidationReport(
                ok=True,
                provider=self.name,
                passthrough_count=0,
                failure_count=0,
                summary=(
                    "Native GenericCommand.Validate() is not enabled "
                    f"({API_V2_ISSUE_ID}); offline schema checks only."
                ),
            )
        return GenericCommandValidationReport(
            ok=False,
            provider=self.name,
            passthrough_count=0,
            failure_count=0,
            summary=(
                "Native GenericCommand.Validate() provider is not wired yet "
                f"({API_V2_ISSUE_ID})."
            ),
        )


def native_generic_validate_available() -> bool:
    try:
        import clr  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("TECAN_FC_NATIVE_VALIDATE"))


def default_generic_command_validate_provider() -> GenericCommandValidateProvider:
    if native_generic_validate_available():
        return NativeGenericCommandValidateProvider()
    return OfflineGenericCommandValidateProvider()


def validate_passthrough_commands_from_xscr(
    path: Path,
    *,
    provider: str = "offline",
) -> GenericCommandValidationReport:
    if not path.exists():
        return GenericCommandValidationReport(
            ok=True,
            provider=provider,
            passthrough_count=0,
            failure_count=0,
            summary="No compiled XSCR to validate for GenericCommand passthrough payloads.",
        )
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        failure = GenericCommandValidationFailure(
            index=-1,
            command_id="Script",
            reason="xscr_parse_error",
            message=f"Compiled XSCR could not be parsed for GenericCommand validation: {exc}",
        )
        return GenericCommandValidationReport(
            ok=False,
            provider=provider,
            passthrough_count=0,
            failure_count=1,
            failures=(failure,),
            summary="Compiled XSCR parse failed during GenericCommand validation.",
        )

    commands = extract_passthrough_generic_commands(root)
    failures: list[GenericCommandValidationFailure] = []
    for index, command in enumerate(commands):
        try:
            validate_generic_command_payload(command)
        except ApiV2ValidationError as exc:
            failures.append(
                GenericCommandValidationFailure(
                    index=index,
                    command_id=command.command_id,
                    reason=_reason_from_validation_error(exc),
                    message=str(exc),
                    line_number=command.line_number or None,
                    object_type=command.object_type,
                )
            )
    failures = failures[:50]
    ok = not failures
    summary = (
        f"All {len(commands)} approved passthrough command(s) passed GenericCommand.Validate() heuristics."
        if ok
        else f"{len(failures)} GenericCommand validation failure(s) in compiled XSCR."
    )
    return GenericCommandValidationReport(
        ok=ok,
        provider=provider,
        passthrough_count=len(commands),
        failure_count=len(failures),
        failures=tuple(failures),
        summary=summary,
    )


def _resolved_passthrough_command_id(element: ET.Element) -> str:
    object_type = str(element.attrib.get("Type") or "")
    type_id = object_type.rsplit(".", 1)[-1] if object_type else ""
    inner_id = _compiled_command_id(element)
    if inner_id and registry_command_approved_passthrough(inner_id):
        return inner_id
    if type_id and registry_command_approved_passthrough(type_id):
        return type_id
    return inner_id or type_id


def extract_passthrough_generic_commands(root: ET.Element) -> list[GenericCommand]:
    commands: list[GenericCommand] = []
    for element in root.iter():
        if _local_name(element.tag) != "Object":
            continue
        object_type = str(element.attrib.get("Type") or "")
        command_id = _resolved_passthrough_command_id(element)
        if not command_id or not registry_command_approved_passthrough(command_id):
            continue
        commands.append(
            GenericCommand(
                object_type=object_type,
                payload_xml=ET.tostring(element, encoding="unicode"),
                command_id=command_id,
                line_number=_extract_line_number(element),
            )
        )
    return commands


def validate_generic_command_payload(command: GenericCommand) -> None:
    """Offline ``GenericCommand.Validate()`` — raises ``ApiV2ValidationError`` on failure."""
    command_id = str(command.command_id or "").strip()
    object_type = str(command.object_type or "").strip()
    payload = str(command.payload_xml or "").strip()

    if not payload and not object_type:
        raise ApiV2ValidationError(
            "GenericCommand requires payload_xml or object_type.",
            command="GenericCommand",
        )

    if command_id and not registry_command_approved_passthrough(command_id):
        status = registry_command_support_status(command_id)
        if status is None:
            raise ApiV2ValidationError(
                f"Command {command_id!r} is not an approved passthrough; "
                "Gate 17 requires explicit raw XML approval.",
                field="command_id",
                command="GenericCommand",
            )

    if not payload:
        if not object_type:
            raise ApiV2ValidationError("payload_xml is empty.", field="payload_xml", command="GenericCommand")
        return

    try:
        element = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ApiV2ValidationError(
            f"GenericCommand payload is not well-formed XML: {exc}",
            field="payload_xml",
            command="GenericCommand",
        ) from exc

    if _local_name(element.tag) != "Object":
        raise ApiV2ValidationError(
            "GenericCommand payload must be a compiled Object element.",
            field="payload_xml",
            command="GenericCommand",
        )

    payload_type = str(element.attrib.get("Type") or "").strip()
    if object_type and payload_type and payload_type != object_type:
        raise ApiV2ValidationError(
            f"Object Type mismatch: payload has {payload_type!r}, expected {object_type!r}.",
            field="object_type",
            command="GenericCommand",
        )

    inner_id = _compiled_command_id(element)
    if command_id and inner_id and inner_id != command_id:
        raise ApiV2ValidationError(
            f"Inner command node {inner_id!r} does not match command_id {command_id!r}.",
            field="command_id",
            command="GenericCommand",
        )

    if command_id and not _object_has_command_body(element, command_id):
        raise ApiV2ValidationError(
            f"Approved passthrough {command_id!r} has no matching command body in payload XML.",
            field="payload_xml",
            command="GenericCommand",
        )


def validate_generic_command_before_execute(
    *,
    command_id: str,
    payload_xml: str,
    object_type: str = "",
    line_number: int = 0,
) -> str | None:
    """Pre-ExecuteCommand check for stepped runner; returns error text or None."""
    command = GenericCommand(
        object_type=object_type,
        payload_xml=payload_xml,
        command_id=command_id,
        line_number=line_number,
    )
    try:
        validate_generic_command_payload(command)
    except ApiV2ValidationError as exc:
        return str(exc)
    return None


def _object_has_command_body(element: ET.Element, command_id: str) -> bool:
    for child in element:
        if _local_name(child.tag) == command_id:
            return True
    for node in element.iter():
        if _local_name(node.tag) == command_id:
            return True
    return False


def _reason_from_validation_error(exc: ApiV2ValidationError) -> str:
    field = str(exc.field or "").strip()
    if field == "payload_xml":
        return "generic_command_payload_invalid"
    if field == "command_id":
        return "generic_command_unapproved_or_mismatch"
    if field == "object_type":
        return "generic_command_type_mismatch"
    return "generic_command_validate_failed"


def _compiled_command_id(element: ET.Element) -> str:
    for child in list(element):
        tag = _local_name(child.tag)
        if tag and tag != "Object":
            return tag
    object_type = str(element.attrib.get("Type") or "")
    return object_type.rsplit(".", 1)[-1] if object_type else ""


def _extract_line_number(element: ET.Element) -> int:
    for node in element.iter():
        if _local_name(node.tag) == "LineNumber":
            try:
                return int((node.text or "0").strip())
            except ValueError:
                return 0
    return 0


def _local_name(tag: Any) -> str:
    text = str(tag)
    if "}" in text:
        return text.rsplit("}", 1)[-1]
    return text
