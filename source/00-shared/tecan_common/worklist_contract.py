"""Evidence-preserving Load/Execute Worklist contracts.

The repository does not yet have a sanitized XSCR fixture for every vendor
field.  This module therefore normalizes only fields explicitly supplied by a
caller and retains every other field in ``extra_fields`` instead of guessing
XML names or hardware semantics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from . import native_acceleration
from .performance import performance_marker


SCHEMA_VERSION = "tecan.worklist.contract.v2"
LOAD_COMMAND_IDS = frozenset(
    {
        "LoadWorklistStatementDataV4",
        "WorklistImportStatementDataV2",
        "LoadWorklist",
        "ReadWorklist",
        "ImportWorklist",
    }
)
EXECUTE_COMMAND_IDS = frozenset({"ExecuteWorklistStatementDataV1", "ExecuteWorklist"})
_IR_CONTEXT_FIELDS = frozenset(
    {
        "labware",
        "volume_ul",
        "liquid_class",
        "device_alias",
        "available_id",
        "head_position",
        "back_position",
        "raw_xml",
        "compiled_path",
        "source_entry",
        "command_index",
        "line_number",
    }
)


def _first(fields: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in fields and fields[name] not in (None, ""):
            return fields[name]
    return None


def _native_contract_integer_supported(value: Any) -> bool:
    if type(value) is int:
        parsed = value
    elif type(value) is str and value.isascii():
        try:
            parsed = int(value.strip(), 10)
        except ValueError:
            return False
    else:
        return False
    return -(1 << 127) <= parsed < (1 << 127)


def parse_command_tip_mask(value: Any, *, supported_channels: int = 8) -> tuple[int | None, str, tuple[int, ...]]:
    """Parse a Load Worklist mask, where multiple selected channels are valid."""

    if value in (None, ""):
        return None, "unavailable", ()
    if (
        type(supported_channels) is int
        and 0 <= supported_channels <= 128
        and _native_contract_integer_supported(value)
    ):
        native = native_acceleration.worklist_parse_contract_tip_mask(
            value, False, supported_channels
        )
        if native is not None:
            return native
    try:
        mask = int(str(value).strip(), 10)
    except (TypeError, ValueError):
        return None, "malformed", ()
    maximum = (1 << supported_channels) - 1
    if mask <= 0 or mask & ~maximum:
        return mask, "out_of_range", ()
    return mask, "valid", tuple(channel for channel in range(1, supported_channels + 1) if mask & (1 << (channel - 1)))


def parse_selected_tip_indexes(
    value: Any,
    *,
    supported_channels: int = 8,
) -> tuple[int | None, str, tuple[int, ...]]:
    """Normalize source ``SelectedTipsIndexes`` values without treating them as a mask.

    FluentControl XML represents this field as zero-based ``int`` entries.  The
    normalized contract exposes one-based channel numbers, matching the GWL
    record-level TipMask representation, while retaining the original values
    in ``selected_tip_mask`` for provenance/round-trip consumers.
    """

    if value in (None, ""):
        return None, "unavailable", ()
    values = value if isinstance(value, (list, tuple, set)) else [value]
    native_values = list(value) if type(value) is tuple else value
    if (
        type(supported_channels) is int
        and 0 <= supported_channels < 128
        and type(value) in (str, int, list, tuple)
        and all(_native_contract_integer_supported(item) for item in values)
    ):
        native = native_acceleration.worklist_parse_contract_tip_mask(
            native_values, True, supported_channels
        )
        if native is not None:
            return native
    indexes: list[int] = []
    for item in values:
        try:
            index = int(str(item).strip(), 10)
        except (TypeError, ValueError):
            return None, "malformed", ()
        if index < 0 or index >= supported_channels:
            return None, "out_of_range", ()
        if index not in indexes:
            indexes.append(index)
    if not indexes:
        return None, "out_of_range", ()
    mask = sum(1 << index for index in indexes)
    return mask, "valid", tuple(index + 1 for index in sorted(indexes))


def _normalize_tip_system(value: Any) -> str:
    native = native_acceleration.worklist_normalize_tip_system(value)
    if native is not None:
        return native
    normalized = str(value or "").strip().lower().replace(" ", "_")
    if normalized in {"fixed", "fixed_tip", "fixedtips", "fixed_tips"}:
        return "fixed_tip"
    if normalized in {"diti", "disposable_tip", "disposable_tips", "disposable_tip_adapter"}:
        return "diti"
    return "unknown"


@dataclass(frozen=True)
class LoadWorklistContract:
    worklist: Any = None
    selected_tip_mask: Any = None
    selected_arm: Any = None
    default_liquid_class: Any = None
    tip_system: str = "unknown"
    selected_tip_mask_value: int | None = None
    selected_tip_mask_status: str = "unavailable"
    selected_tip_channels: tuple[int, ...] = ()
    options: dict[str, Any] = field(default_factory=dict)
    extra_fields: dict[str, Any] = field(default_factory=dict)
    command_id: str = ""
    command_version: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "load_worklist",
            "worklist": self.worklist,
            "selected_tip_mask": self.selected_tip_mask,
            "selected_arm": self.selected_arm,
            "default_liquid_class": self.default_liquid_class,
            "tip_system": self.tip_system,
            "selected_tip_mask_value": self.selected_tip_mask_value,
            "selected_tip_mask_status": self.selected_tip_mask_status,
            "selected_tip_channels": list(self.selected_tip_channels),
            "options": dict(self.options),
            "extra_fields": dict(self.extra_fields),
            "command_id": self.command_id,
            "command_version": self.command_version,
            "provenance": dict(self.provenance),
        }


@dataclass(frozen=True)
class ExecuteWorklistContract:
    loads_before_execute: tuple[str, ...] = ()
    command_id: str = "ExecuteWorklistStatementDataV1"
    extra_fields: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "execute_worklist",
            "loads_before_execute": list(self.loads_before_execute),
            "command_id": self.command_id,
            "extra_fields": dict(self.extra_fields),
            "provenance": dict(self.provenance),
        }


def _native_json_compatible(value: Any, active: set[int] | None = None) -> bool:
    if value is None or type(value) in (str, bool, int):
        return True
    if type(value) is float:
        return math.isfinite(value)
    if type(value) not in (dict, list):
        return False
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        return False
    active.add(identity)
    try:
        if type(value) is dict:
            return all(
                type(key) is str and _native_json_compatible(item, active)
                for key, item in value.items()
            )
        return all(_native_json_compatible(item, active) for item in value)
    finally:
        active.remove(identity)


def _supports_native(value: Any) -> bool:
    try:
        return _native_json_compatible(value)
    except RecursionError:
        return False


def _load_contract_from_native(
    payload: Mapping[str, Any],
    fields: Mapping[str, Any],
    command_id: str,
    command_version: str,
    provenance: Mapping[str, Any] | None,
) -> LoadWorklistContract:
    known_names = {
        "worklist", "WorklistName", "FileName", "Path", "selected_tip_mask",
        "selected_tip_indexes", "SelectedTipsIndexes", "SerializedTipsIndexes", "TipMask",
        "selected_arm", "DeviceAlias", "Arm", "ArmName", "default_liquid_class",
        "LiquidClass", "DefaultLiquidClass", "tip_system", "TipSystem", "options",
        "command_id", "command_version", "provenance", "LineNumber",
    }
    options = fields.get("options") if isinstance(fields.get("options"), dict) else {}
    extra = {str(key): value for key, value in fields.items() if str(key) not in known_names}
    return LoadWorklistContract(
        worklist=_first(fields, ("worklist", "WorklistName", "FileName", "Path")),
        selected_tip_mask=_first(
            fields, ("selected_tip_mask", "SelectedTipsIndexes", "SerializedTipsIndexes", "TipMask")
        ),
        selected_arm=_first(fields, ("selected_arm", "DeviceAlias", "Arm", "ArmName")),
        default_liquid_class=_first(fields, ("default_liquid_class", "LiquidClass", "DefaultLiquidClass")),
        tip_system=payload.get("tip_system", "unknown"),
        selected_tip_mask_value=payload.get("selected_tip_mask_value"),
        selected_tip_mask_status=payload.get("selected_tip_mask_status", "unavailable"),
        selected_tip_channels=tuple(payload.get("selected_tip_channels") or ()),
        options=dict(options),
        extra_fields=extra,
        command_id=command_id or str(fields.get("command_id") or ""),
        command_version=command_version or str(fields.get("command_version") or ""),
        provenance=dict(provenance or fields.get("provenance") or {}),
    )


def _normalize_load_worklist_python(
    fields: Mapping[str, Any],
    *,
    command_id: str = "",
    command_version: str = "",
    provenance: Mapping[str, Any] | None = None,
) -> LoadWorklistContract:
    known_names = {
        "worklist", "WorklistName", "FileName", "Path", "selected_tip_mask",
        "selected_tip_indexes", "SelectedTipsIndexes", "SerializedTipsIndexes", "TipMask",
        "selected_arm", "DeviceAlias", "Arm", "ArmName", "default_liquid_class",
        "LiquidClass", "DefaultLiquidClass", "tip_system", "TipSystem", "options",
        "command_id", "command_version", "provenance", "LineNumber",
    }
    worklist = _first(fields, ("worklist", "WorklistName", "FileName", "Path"))
    selected_tip_mask = _first(
        fields, ("selected_tip_mask", "SelectedTipsIndexes", "SerializedTipsIndexes", "TipMask")
    )
    selected_arm = _first(fields, ("selected_arm", "DeviceAlias", "Arm", "ArmName"))
    default_liquid_class = _first(fields, ("default_liquid_class", "LiquidClass", "DefaultLiquidClass"))
    tip_system = _normalize_tip_system(_first(fields, ("tip_system", "TipSystem")))
    selected_tip_indexes = _first(fields, ("selected_tip_indexes", "SelectedTipIndexes"))
    if selected_tip_indexes is not None:
        selected_tip_mask_value, selected_tip_mask_status, selected_tip_channels = (
            parse_selected_tip_indexes(selected_tip_indexes)
        )
    else:
        selected_tip_mask_value, selected_tip_mask_status, selected_tip_channels = (
            parse_command_tip_mask(selected_tip_mask)
        )
    options = fields.get("options") if isinstance(fields.get("options"), dict) else {}
    extra = {str(key): value for key, value in fields.items() if str(key) not in known_names}
    return LoadWorklistContract(
        worklist=worklist,
        selected_tip_mask=selected_tip_mask,
        selected_arm=selected_arm,
        default_liquid_class=default_liquid_class,
        tip_system=tip_system,
        selected_tip_mask_value=selected_tip_mask_value,
        selected_tip_mask_status=selected_tip_mask_status,
        selected_tip_channels=selected_tip_channels,
        options=dict(options),
        extra_fields=extra,
        command_id=command_id or str(fields.get("command_id") or ""),
        command_version=command_version or str(fields.get("command_version") or ""),
        provenance=dict(provenance or fields.get("provenance") or {}),
    )


def normalize_load_worklist(
    fields: Mapping[str, Any],
    *,
    command_id: str = "",
    command_version: str = "",
    provenance: Mapping[str, Any] | None = None,
) -> LoadWorklistContract:
    """Normalize observed fields while keeping unknown/additive fields lossless."""

    field_count = len(fields) if type(fields) is dict else None
    with performance_marker(
        "worklist.load_contract_normalization", field_count=field_count
    ):
        if type(fields) is dict and _supports_native(fields):
            native = native_acceleration.worklist_load_contract(
                fields, command_id, command_version, provenance
            )
            if native is not None:
                try:
                    return _load_contract_from_native(
                        native, fields, command_id, command_version, provenance
                    )
                except (AttributeError, KeyError, TypeError, ValueError):
                    pass
        return _normalize_load_worklist_python(
            fields,
            command_id=command_id,
            command_version=command_version,
            provenance=provenance,
        )


def _contracts_from_native(
    payload: list[dict[str, Any]], commands: list[Mapping[str, Any]]
) -> list[LoadWorklistContract | ExecuteWorklistContract | Mapping[str, Any]] | None:
    if len(payload) != len(commands):
        return None
    loaded: list[LoadWorklistContract] = []
    result: list[LoadWorklistContract | ExecuteWorklistContract | Mapping[str, Any]] = []
    for command, contract in zip(commands, payload):
        command_id = str(command.get("command_id") or command.get("id") or "")
        fields = (
            command.get("parameters")
            if isinstance(command.get("parameters"), Mapping)
            else command
        )
        if command_id in LOAD_COMMAND_IDS:
            if contract.get("kind") != "load_worklist":
                return None
            load_contract = _load_contract_from_native(
                contract, fields, command_id, "", command.get("provenance")
            )
            loaded.append(load_contract)
            result.append(load_contract)
        elif command_id in EXECUTE_COMMAND_IDS:
            if contract.get("kind") != "execute_worklist":
                return None
            expected_loads = tuple(str(item.worklist or "") for item in loaded)
            native_loads = tuple(contract.get("loads_before_execute") or ())
            if native_loads != expected_loads:
                return None
            extra_fields = {
                str(key): value
                for key, value in fields.items()
                if str(key) not in {"command_id", "provenance", *_IR_CONTEXT_FIELDS}
                and value not in (None, "", [], {})
            }
            result.append(
                ExecuteWorklistContract(
                    loads_before_execute=expected_loads,
                    command_id=command_id,
                    extra_fields=extra_fields,
                    provenance=dict(command.get("provenance") or {}),
                )
            )
            loaded.clear()
        else:
            result.append(command)
    return result


def build_execution_contract(
    commands: Iterable[Mapping[str, Any]],
) -> list[LoadWorklistContract | ExecuteWorklistContract | Mapping[str, Any]]:
    """Keep load order and Execute boundaries explicit for later IR consumers."""

    with performance_marker("worklist.execution_contract") as metrics:
        if type(commands) is list and all(type(command) is dict for command in commands):
            if _supports_native(commands):
                native = native_acceleration.worklist_execution_contract(commands)
                if native is not None:
                    try:
                        converted = _contracts_from_native(native, commands)
                    except (AttributeError, KeyError, TypeError, ValueError):
                        converted = None
                    if converted is not None:
                        metrics["command_count"] = len(commands)
                        return converted

        loaded: list[LoadWorklistContract] = []
        result: list[LoadWorklistContract | ExecuteWorklistContract | Mapping[str, Any]] = []
        command_count = 0
        for command in commands:
            command_count += 1
            command_id = str(command.get("command_id") or command.get("id") or "")
            fields = (
                command.get("parameters")
                if isinstance(command.get("parameters"), Mapping)
                else command
            )
            if command_id in LOAD_COMMAND_IDS:
                contract = _normalize_load_worklist_python(
                    fields, command_id=command_id, provenance=command.get("provenance")
                )
                loaded.append(contract)
                result.append(contract)
            elif command_id in EXECUTE_COMMAND_IDS:
                extra_fields = {
                    str(key): value
                    for key, value in fields.items()
                    if str(key) not in {"command_id", "provenance", *_IR_CONTEXT_FIELDS}
                    and value not in (None, "", [], {})
                }
                result.append(
                    ExecuteWorklistContract(
                        loads_before_execute=tuple(str(item.worklist or "") for item in loaded),
                        command_id=command_id,
                        extra_fields=extra_fields,
                        provenance=dict(command.get("provenance") or {}),
                    )
                )
                loaded.clear()
            else:
                result.append(command)
        metrics["command_count"] = command_count
        return result


__all__ = [
    "EXECUTE_COMMAND_IDS",
    "LOAD_COMMAND_IDS",
    "ExecuteWorklistContract",
    "LoadWorklistContract",
    "SCHEMA_VERSION",
    "build_execution_contract",
    "normalize_load_worklist",
    "parse_command_tip_mask",
    "parse_selected_tip_indexes",
]
