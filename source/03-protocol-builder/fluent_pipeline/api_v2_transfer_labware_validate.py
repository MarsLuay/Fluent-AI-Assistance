"""TransferLabware.Validate() preflight for API V2 stepped Gate 27 (api-v2-044).

Pre-flight labware / location / site checks in simulation before RGA
``ApplicationDriverMacro`` (``RGA1_TransferLabware``) steps execute. Surfaces
structured ``runtime_errors`` instead of unsafe gripper motion when deck state
is invalid. Mirrors ``fluentcoder/simulator/walk.py`` strict ``_on_gripper_move``.
"""

from __future__ import annotations

import html
import re
from . import xml_compat as ET
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from .api_v2.types import ApiV2ValidationError
from .api_v2.validate_runtime import (
    runtime_error_for_validate_failure as _shared_runtime_error_for_validate_failure,
)

API_V2_ISSUE_ID = "api-v2-044"
API_V2_METHOD = "TransferLabware.Validate()"
RGA_TRANSFER_MACRO = "RGA1_TransferLabware"
TRANSFER_LABWARE_TYPE_NAMES = frozenset({"ApplicationDriverMacro"})


@dataclass(frozen=True)
class TransferLabwareFields:
    labware: str
    location: str
    site: str
    move_to_base: bool = False
    fixed_site: bool = True
    macro_name: str = RGA_TRANSFER_MACRO

    def as_dict(self) -> dict[str, Any]:
        return {
            "labware": self.labware,
            "location": self.location,
            "site": self.site,
            "move_to_base": self.move_to_base,
            "fixed_site": self.fixed_site,
            "macro_name": self.macro_name,
        }

    def destination_slot(self) -> tuple[str, str] | None:
        if self.move_to_base:
            return None
        if _is_dynamic_expression(self.location) or _is_dynamic_expression(self.site):
            return None
        location = _normalize_base_token(self.location)
        site = str(self.site).strip()
        if not location or not site:
            return None
        return (location, site)


@dataclass(frozen=True)
class TransferLabwareValidateResult:
    ok: bool
    message: str = ""
    reason: str = ""
    field: str = ""
    source: str = "offline_deck_simulation"
    api_v2_method: str = API_V2_METHOD
    api_v2_issue: str = API_V2_ISSUE_ID
    fields: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "source": self.source,
            "api_v2_method": self.api_v2_method,
            "api_v2_issue": self.api_v2_issue,
        }
        if self.message:
            payload["message"] = self.message
        if self.reason:
            payload["reason"] = self.reason
        if self.field:
            payload["field"] = self.field
        if self.fields:
            payload["fields"] = dict(self.fields)
        return payload


class TransferLabwareCommandLike(Protocol):
    type_name: str
    index: int
    group: str
    payload_xml: str


def is_transfer_labware_command(command: TransferLabwareCommandLike) -> bool:
    if str(command.type_name or "") not in TRANSFER_LABWARE_TYPE_NAMES:
        return False
    fields = extract_transfer_labware_fields(command)
    return fields is not None and fields.macro_name == RGA_TRANSFER_MACRO


def extract_transfer_labware_fields(command: TransferLabwareCommandLike) -> TransferLabwareFields | None:
    payload = str(command.payload_xml or "").strip()
    if not payload:
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    macro = _find_descendant(root, "ApplicationDriverMacro")
    if macro is None:
        return None
    macro_name = _child_text(macro, "Name") or RGA_TRANSFER_MACRO
    settings = _child_text(macro, "ExecutionSettings")
    return TransferLabwareFields(
        labware=_parse_transfer_setting(settings, "Labware"),
        location=_parse_transfer_setting(settings, "Location"),
        site=_parse_transfer_setting(settings, "Site", "1") or "1",
        move_to_base=_parse_transfer_setting(settings, "MoveToBase", "false").lower() == "true",
        fixed_site=_parse_transfer_setting(settings, "FixedSite", "true").lower() == "true",
        macro_name=macro_name,
    )


def transfer_labware_fields_from_ir_step(step: Mapping[str, Any]) -> TransferLabwareFields | None:
    if str(step.get("operation") or "").strip() != "move_plate":
        return None
    params = step.get("parameters") or {}
    if not isinstance(params, dict):
        return None
    onto = str(params.get("onto_labware") or params.get("onto") or "")
    if onto:
        location = f'GetCoverSiteName("{onto}")'
        site = f'GetCoverSiteIndex("{onto}")'
    else:
        location = str(params.get("destination_location") or params.get("to_location") or "")
        site = str(params.get("destination_site") or params.get("to_site") or "1")
    return TransferLabwareFields(
        labware=str(params.get("labware") or step.get("target_labware") or ""),
        location=location,
        site=site,
        move_to_base=bool(params.get("move_to_base")),
        fixed_site=bool(params.get("fixed_site", True)),
    )


def validate_transfer_labware_fields(
    fields: TransferLabwareFields,
    *,
    deck_labels: set[str] | None = None,
    deck_slots: dict[str, tuple[str, str]] | None = None,
    occupied_slots: set[tuple[str, str]] | None = None,
    step_index: int | None = None,
    step_label: str = "",
) -> TransferLabwareValidateResult:
    """Offline ``TransferLabware.Validate()`` with strict deck simulation."""
    labels = deck_labels or set()
    slots = deck_slots or {}
    occupied = occupied_slots or set()
    prefix = step_label or (f"step {step_index + 1}" if step_index is not None else "TransferLabware")

    try:
        _validate_fields_core(fields)
    except ApiV2ValidationError as exc:
        return TransferLabwareValidateResult(
            ok=False,
            reason="api_v2_validate_rejected",
            message=f"{prefix}: {exc}",
            field=str(getattr(exc, "field", "") or ""),
            fields=fields.as_dict(),
        )

    label_key = _norm(fields.labware)
    if label_key and label_key not in labels:
        return TransferLabwareValidateResult(
            ok=False,
            reason="labware_not_on_deck",
            message=(
                f"{prefix}: TransferLabware({fields.labware!r}) references labware that is not "
                "present on the simulated deck (AddLabware or an earlier transfer required)."
            ),
            field="labware",
            fields=fields.as_dict(),
        )

    destination = fields.destination_slot()
    if destination is not None:
        source = slots.get(label_key)
        if source == destination:
            return TransferLabwareValidateResult(
                ok=False,
                reason="noop_transfer",
                message=(
                    f"{prefix}: TransferLabware({fields.labware!r}) targets the same slot "
                    f"{destination[0]}[{destination[1]}] it already occupies."
                ),
                field="location",
                fields=fields.as_dict(),
            )
        if destination in occupied and destination != source:
            return TransferLabwareValidateResult(
                ok=False,
                reason="occupied_destination_slot",
                message=(
                    f"{prefix}: destination slot {destination[0]}[{destination[1]}] is already "
                    "occupied in the simulated deck."
                ),
                field="site",
                fields=fields.as_dict(),
            )

    return TransferLabwareValidateResult(ok=True, fields=fields.as_dict())


def validate_transfer_labware_offline(
    command: TransferLabwareCommandLike,
    *,
    deck_labels: set[str] | None = None,
    deck_slots: dict[str, tuple[str, str]] | None = None,
    occupied_slots: set[tuple[str, str]] | None = None,
) -> TransferLabwareValidateResult:
    if not is_transfer_labware_command(command):
        return TransferLabwareValidateResult(ok=True, source="skipped_non_transfer")

    fields = extract_transfer_labware_fields(command)
    if fields is None:
        return TransferLabwareValidateResult(
            ok=True,
            source="skipped_no_payload",
            message="No RGA1_TransferLabware payload available for offline validation.",
        )

    return validate_transfer_labware_fields(
        fields,
        deck_labels=deck_labels,
        deck_slots=deck_slots,
        occupied_slots=occupied_slots,
        step_index=command.index,
        step_label=(
            f"TransferLabware at step {command.index + 1} "
            f"({command.type_name} in {command.group})"
        ),
    )


def validate_transfer_labware_before_execute(
    command: TransferLabwareCommandLike,
    *,
    native_validate: Callable[[], None] | None = None,
    deck_labels: set[str] | None = None,
    deck_slots: dict[str, tuple[str, str]] | None = None,
    occupied_slots: set[tuple[str, str]] | None = None,
) -> TransferLabwareValidateResult:
    offline = validate_transfer_labware_offline(
        command,
        deck_labels=deck_labels,
        deck_slots=deck_slots,
        occupied_slots=occupied_slots,
    )
    if not offline.ok:
        return offline

    if native_validate is not None and offline.source != "skipped_non_transfer":
        try:
            native_validate()
        except Exception as exc:
            message = str(exc).strip() or "TransferLabware.Validate() failed."
            return TransferLabwareValidateResult(
                ok=False,
                reason="native_validate_failed",
                message=message,
                fields=offline.fields,
                source="native",
            )
        return TransferLabwareValidateResult(ok=True, fields=offline.fields, source="native")

    return offline


def record_successful_transfer(
    fields: TransferLabwareFields,
    *,
    deck_labels: set[str],
    deck_slots: dict[str, tuple[str, str]],
    occupied_slots: set[tuple[str, str]],
) -> None:
    """Update simulated deck state after a validated transfer."""
    label_key = _norm(fields.labware)
    if not label_key:
        return
    source = deck_slots.get(label_key)
    if source is not None and source in occupied_slots:
        occupied_slots.discard(source)
    if fields.move_to_base:
        deck_slots.pop(label_key, None)
        return
    destination = fields.destination_slot()
    if destination is None:
        return
    deck_labels.add(label_key)
    deck_slots[label_key] = destination
    occupied_slots.add(destination)


def runtime_error_for_validate_failure(
    result: TransferLabwareValidateResult,
    command: TransferLabwareCommandLike,
) -> str:
    return _shared_runtime_error_for_validate_failure(result, command, kind="TransferLabware")


def _validate_fields_core(fields: TransferLabwareFields) -> None:
    if fields.macro_name != RGA_TRANSFER_MACRO:
        raise ApiV2ValidationError(
            f"unsupported ApplicationDriverMacro {fields.macro_name!r}; expected {RGA_TRANSFER_MACRO!r}.",
            field="macro_name",
            command="TransferLabware",
        )
    if not str(fields.labware or "").strip():
        raise ApiV2ValidationError("labware is required.", field="labware", command="TransferLabware")
    if fields.move_to_base:
        return
    if not str(fields.location or "").strip():
        raise ApiV2ValidationError("location is required.", field="location", command="TransferLabware")
    if not str(fields.site or "").strip() and not _is_dynamic_expression(fields.location):
        raise ApiV2ValidationError("site is required.", field="site", command="TransferLabware")


def _parse_transfer_setting(settings: str, tag: str, default: str = "") -> str:
    if not settings:
        return default
    text = html.unescape(settings)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return default
    return match.group(1).strip()


def _is_dynamic_expression(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return "(" in text or "+" in text or text.startswith("Get")


def _normalize_base_token(location: str) -> str:
    text = str(location or "").strip()
    if text in {"<BASE>", "&lt;BASE&gt;", "BASE"}:
        return "<BASE>"
    return text


def _find_descendant(element: ET.Element, name: str) -> ET.Element | None:
    for node in element.iter():
        if _local_name(node.tag) == name:
            return node
    return None


def _child_text(element: ET.Element | None, name: str, default: str = "") -> str:
    if element is None:
        return default
    for child in element:
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return default


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _norm(value: str) -> str:
    return str(value or "").strip().casefold()
