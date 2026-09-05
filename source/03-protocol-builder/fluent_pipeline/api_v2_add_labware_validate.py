"""AddLabware.Validate() preflight for API V2 and offline gates (api-v2-008).

Validates catalog name, deck location, site, rotation, duplicate labels/slots,
and bracket-variable references for ``add_labware`` steps before compile packaging
or ``ExecutionChannel.ExecuteCommand``. Complements Gate 3/22 manifest checks.
"""

from __future__ import annotations

import re
from . import xml_compat as ET
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from .api_v2.types import ApiV2ValidationError
from .api_v2.validate_runtime import (
    runtime_error_for_validate_failure as _shared_runtime_error_for_validate_failure,
)


API_V2_ISSUE_ID = "api-v2-008"
API_V2_METHOD = "AddLabware.Validate()"

ADD_LABWARE_TYPE_NAMES = frozenset({"AddLabwareDataV1"})

_PLACEHOLDER_TOKENS = frozenset(
    {
        "todo",
        "tbd",
        "tba",
        "fixme",
        "placeholder",
        "fill in",
        "fill me in",
        "xxx",
        "n/a",
        "na",
        "...",
        "??",
        "???",
    }
)

_FC_VAR_PREFIX = "fc_var:"


@dataclass(frozen=True)
class AddLabwareFields:
    labware_type: str
    labware_label: str
    location: str
    site: int | str = 1
    rotation: int | str = 0
    has_lid: bool = False

    def slot_key(self) -> tuple[str, str]:
        return (_norm(self.location), str(self.site).strip())

    def label_key(self) -> str:
        return _norm(self.labware_label)

    def as_dict(self) -> dict[str, Any]:
        return {
            "labware_type": self.labware_type,
            "labware_label": self.labware_label,
            "location": self.location,
            "site": self.site,
            "rotation": self.rotation,
            "has_lid": self.has_lid,
        }


@dataclass(frozen=True)
class AddLabwareValidateResult:
    ok: bool
    message: str = ""
    reason: str = ""
    field: str = ""
    source: str = "offline"
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


class AddLabwareCommandLike(Protocol):
    type_name: str
    index: int
    group: str
    payload_xml: str


def is_add_labware_command(command: AddLabwareCommandLike) -> bool:
    return str(command.type_name or "") in ADD_LABWARE_TYPE_NAMES


def extract_add_labware_fields(command: AddLabwareCommandLike) -> AddLabwareFields | None:
    payload = str(command.payload_xml or "").strip()
    if not payload:
        return None
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return None
    node = _find_descendant(root, "AddLabwareDataV1")
    if node is None:
        return None
    return AddLabwareFields(
        labware_type=_child_text(node, "LabwareType"),
        labware_label=_child_text(node, "LabwareLable") or _child_text(node, "LabwareLabel"),
        location=_child_text(node, "Location"),
        site=_child_text(node, "Position", "1") or 1,
        rotation=_child_text(node, "Rotation", "0") or 0,
        has_lid=_child_text(node, "HasLid", "False").lower() == "true",
    )


def add_labware_fields_from_ir_step(step: Mapping[str, Any]) -> AddLabwareFields | None:
    if str(step.get("operation") or "").strip() != "add_labware":
        return None
    params = step.get("parameters") or {}
    if not isinstance(params, dict):
        return None
    return AddLabwareFields(
        labware_type=str(params.get("catalog") or params.get("labware_type") or ""),
        labware_label=str(params.get("label") or params.get("labware") or step.get("target_labware") or ""),
        location=str(params.get("location") or ""),
        site=params.get("site") or params.get("position") or 1,
        rotation=params.get("rotation") or 0,
        has_lid=bool(params.get("has_lid")),
    )


def validate_add_labware_fields(
    fields: AddLabwareFields,
    *,
    declared_variables: set[str] | None = None,
    prior_labels: set[str] | None = None,
    prior_slots: set[tuple[str, str]] | None = None,
    step_index: int | None = None,
    step_label: str = "",
    check_bracket_variables: bool = False,
) -> AddLabwareValidateResult:
    """Offline ``AddLabware.Validate()`` aligned with typed ``AddLabware.validate()``."""
    declared = declared_variables if declared_variables is not None else set()
    labels = prior_labels or set()
    slots = prior_slots or set()
    prefix = step_label or (f"step {step_index + 1}" if step_index is not None else "AddLabware")

    try:
        _validate_fields_core(
            fields,
            declared_variables=declared,
            check_bracket_variables=check_bracket_variables,
        )
    except ApiV2ValidationError as exc:
        return AddLabwareValidateResult(
            ok=False,
            reason="api_v2_validate_rejected",
            message=f"{prefix}: {exc}",
            field=str(getattr(exc, "field", "") or ""),
            fields=fields.as_dict(),
            source="offline_typed_validate",
        )

    label_key = fields.label_key()
    if label_key and label_key in labels:
        return AddLabwareValidateResult(
            ok=False,
            reason="duplicate_labware_label",
            message=(
                f"{prefix}: labware label {fields.labware_label!r} is already placed "
                "by an earlier AddLabware command."
            ),
            field="labware_label",
            fields=fields.as_dict(),
            source="offline_typed_validate",
        )

    slot = fields.slot_key()
    if slot[0] and slot in slots:
        return AddLabwareValidateResult(
            ok=False,
            reason="occupied_slot",
            message=(
                f"{prefix}: deck slot {fields.location}[{fields.site}] is already occupied "
                "by an earlier AddLabware command."
            ),
            field="site",
            fields=fields.as_dict(),
            source="offline_typed_validate",
        )

    if check_bracket_variables or declared_variables is not None:
        for variable in _bracket_variable_names(fields.labware_label):
            if variable not in declared:
                return AddLabwareValidateResult(
                    ok=False,
                    reason="undeclared_variable",
                    message=(
                        f"{prefix}: labware label {fields.labware_label!r} references undeclared "
                        f"variable {variable!r}."
                    ),
                    field="labware_label",
                    fields=fields.as_dict(),
                    source="offline_typed_validate",
                )

    return AddLabwareValidateResult(ok=True, fields=fields.as_dict(), source="offline_typed_validate")


def validate_add_labware_offline(
    command: AddLabwareCommandLike,
    *,
    declared_variables: set[str] | None = None,
    prior_labels: set[str] | None = None,
    prior_slots: set[tuple[str, str]] | None = None,
) -> AddLabwareValidateResult:
    if not is_add_labware_command(command):
        return AddLabwareValidateResult(ok=True, source="skipped_non_add_labware")

    fields = extract_add_labware_fields(command)
    if fields is None:
        return AddLabwareValidateResult(
            ok=True,
            source="skipped_no_payload",
            message="No AddLabware payload available for offline validation.",
        )

    return validate_add_labware_fields(
        fields,
        declared_variables=declared_variables,
        prior_labels=prior_labels,
        prior_slots=prior_slots,
        step_index=command.index,
        step_label=f"AddLabware at step {command.index + 1} ({command.type_name} in {command.group})",
    )


def validate_add_labware_before_execute(
    command: AddLabwareCommandLike,
    *,
    native_validate: Callable[[], None] | None = None,
    declared_variables: set[str] | None = None,
    prior_labels: set[str] | None = None,
    prior_slots: set[tuple[str, str]] | None = None,
) -> AddLabwareValidateResult:
    if not is_add_labware_command(command):
        return AddLabwareValidateResult(ok=True, source="skipped_non_add_labware")

    offline = validate_add_labware_offline(
        command,
        declared_variables=declared_variables,
        prior_labels=prior_labels,
        prior_slots=prior_slots,
    )
    if not offline.ok:
        return offline

    if native_validate is not None:
        try:
            native_validate()
        except Exception as exc:
            message = str(exc).strip() or "AddLabware.Validate() failed."
            return AddLabwareValidateResult(
                ok=False,
                reason="native_validate_failed",
                message=message,
                fields=offline.fields,
                source="native",
            )
        return AddLabwareValidateResult(ok=True, fields=offline.fields, source="native")

    return offline


def validate_add_labware_ir_steps(
    ir: Mapping[str, Any],
    *,
    declared_variables: set[str] | None = None,
) -> list[AddLabwareValidateResult]:
    """Pre-compile validation of all ``add_labware`` IR steps (api-v2-008)."""
    declared = set(declared_variables or ())
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for var in (step.get("parameters") or {}).get("declared_variables") or ():
            declared.add(str(var))
    for item in ir.get("variables") or []:
        if isinstance(item, dict) and item.get("name"):
            declared.add(str(item["name"]))

    failures: list[AddLabwareValidateResult] = []
    prior_labels: set[str] = set()
    prior_slots: set[tuple[str, str]] = set()
    add_index = 0
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        fields = add_labware_fields_from_ir_step(step)
        if fields is None:
            continue
        result = validate_add_labware_fields(
            fields,
            declared_variables=declared,
            prior_labels=prior_labels,
            prior_slots=prior_slots,
            step_index=add_index,
            step_label=f"IR add_labware step {step.get('id') or add_index + 1}",
            check_bracket_variables=True,
        )
        if not result.ok:
            failures.append(result)
        else:
            if fields.label_key():
                prior_labels.add(fields.label_key())
            if fields.slot_key()[0]:
                prior_slots.add(fields.slot_key())
        add_index += 1
    return failures


def record_successful_add_labware(fields: AddLabwareFields, *, prior_labels: set[str], prior_slots: set[tuple[str, str]]) -> None:
    if fields.label_key():
        prior_labels.add(fields.label_key())
    if fields.slot_key()[0]:
        prior_slots.add(fields.slot_key())


def runtime_error_for_validate_failure(
    result: AddLabwareValidateResult,
    command: AddLabwareCommandLike,
) -> str:
    return _shared_runtime_error_for_validate_failure(result, command, kind="AddLabware")


def failures_to_dicts(failures: Sequence[AddLabwareValidateResult]) -> list[dict[str, Any]]:
    return [item.as_dict() for item in failures if not item.ok]


def _validate_fields_core(
    fields: AddLabwareFields,
    *,
    declared_variables: set[str],
    check_bracket_variables: bool = False,
) -> None:
    labware_type = str(fields.labware_type or "").strip()
    labware_label = str(fields.labware_label or "").strip()
    location = str(fields.location or "").strip()

    if not labware_type:
        raise ApiV2ValidationError("labware_type is required.", field="labware_type", command="AddLabware")
    if not labware_label:
        raise ApiV2ValidationError("labware_label is required.", field="labware_label", command="AddLabware")
    if not location:
        raise ApiV2ValidationError("location is required.", field="location", command="AddLabware")

    site_text = str(fields.site).strip()
    if not site_text:
        raise ApiV2ValidationError("site is required.", field="site", command="AddLabware")
    if site_text.isdigit() and int(site_text) < 1:
        raise ApiV2ValidationError("site must be >= 1.", field="site", command="AddLabware")

    rotation_text = str(fields.rotation).strip()
    if rotation_text.lstrip("-").isdigit():
        rotation = int(rotation_text)
        if rotation < 0 or rotation > 359:
            raise ApiV2ValidationError(
                "rotation must be between 0 and 359.",
                field="rotation",
                command="AddLabware",
            )

    if labware_type.startswith(_FC_VAR_PREFIX):
        var_name = labware_type[len(_FC_VAR_PREFIX) :].strip()
        _validate_fc_variable_name("labware_type", var_name)
    elif labware_type.startswith("[") and labware_type.endswith("]"):
        if check_bracket_variables:
            var_name = labware_type[1:-1].strip()
            if var_name and var_name not in declared_variables:
                raise ApiV2ValidationError(
                    f"labware_type references undeclared variable {var_name!r}.",
                    field="labware_type",
                    command="AddLabware",
                )
    elif _looks_like_placeholder(labware_type):
        raise ApiV2ValidationError(
            f"labware_type looks like a placeholder: {labware_type!r}.",
            field="labware_type",
            command="AddLabware",
        )

    if _looks_like_placeholder(labware_label):
        raise ApiV2ValidationError(
            f"labware_label looks like a placeholder: {labware_label!r}.",
            field="labware_label",
            command="AddLabware",
        )


def _validate_fc_variable_name(field: str, name: str) -> None:
    if not name:
        raise ApiV2ValidationError(f"{field} FC variable name is empty.", field=field, command="AddLabware")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ApiV2ValidationError(
            f"{field} FC variable name {name!r} is invalid.",
            field=field,
            command="AddLabware",
        )


def _looks_like_placeholder(value: str) -> bool:
    normalized = re.sub(r"\s+", " ", str(value or "").strip()).casefold()
    if not normalized:
        return True
    if normalized in _PLACEHOLDER_TOKENS:
        return True
    if re.fullmatch(r"[\W_]+", normalized):
        return True
    return False


def _bracket_variable_names(value: str) -> set[str]:
    return {match.group(1) for match in re.finditer(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", str(value or ""))}


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
