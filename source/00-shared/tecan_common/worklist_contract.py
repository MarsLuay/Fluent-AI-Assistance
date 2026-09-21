"""Evidence-preserving Load/Execute Worklist contracts.

The repository does not yet have a sanitized XSCR fixture for every vendor
field.  This module therefore normalizes only fields explicitly supplied by a
caller and retains every other field in ``extra_fields`` instead of guessing
XML names or hardware semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


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


def parse_command_tip_mask(value: Any, *, supported_channels: int = 8) -> tuple[int | None, str, tuple[int, ...]]:
    """Parse a Load Worklist mask, where multiple selected channels are valid."""

    if value in (None, ""):
        return None, "unavailable", ()
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


def normalize_load_worklist(
    fields: Mapping[str, Any],
    *,
    command_id: str = "",
    command_version: str = "",
    provenance: Mapping[str, Any] | None = None,
) -> LoadWorklistContract:
    """Normalize observed fields while keeping unknown/additive fields lossless."""

    known_names = {
        "worklist",
        "WorklistName",
        "FileName",
        "Path",
        "selected_tip_mask",
        "selected_tip_indexes",
        "SelectedTipsIndexes",
        "SerializedTipsIndexes",
        "TipMask",
        "selected_arm",
        "DeviceAlias",
        "Arm",
        "ArmName",
        "default_liquid_class",
        "LiquidClass",
        "DefaultLiquidClass",
        "tip_system",
        "TipSystem",
        "options",
        "command_id",
        "command_version",
        "provenance",
        "LineNumber",
    }
    worklist = _first(fields, ("worklist", "WorklistName", "FileName", "Path"))
    selected_tip_mask = _first(fields, ("selected_tip_mask", "SelectedTipsIndexes", "SerializedTipsIndexes", "TipMask"))
    selected_arm = _first(fields, ("selected_arm", "DeviceAlias", "Arm", "ArmName"))
    default_liquid_class = _first(fields, ("default_liquid_class", "LiquidClass", "DefaultLiquidClass"))
    tip_system_value = _first(fields, ("tip_system", "TipSystem"))
    tip_system = _normalize_tip_system(tip_system_value)
    selected_tip_indexes = _first(fields, ("selected_tip_indexes", "SelectedTipIndexes"))
    if selected_tip_indexes is not None:
        selected_tip_mask_value, selected_tip_mask_status, selected_tip_channels = parse_selected_tip_indexes(
            selected_tip_indexes
        )
    else:
        selected_tip_mask_value, selected_tip_mask_status, selected_tip_channels = parse_command_tip_mask(
            selected_tip_mask
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


def build_execution_contract(commands: Iterable[Mapping[str, Any]]) -> list[LoadWorklistContract | ExecuteWorklistContract | Mapping[str, Any]]:
    """Keep load order and Execute boundaries explicit for later IR consumers."""

    loaded: list[LoadWorklistContract] = []
    result: list[LoadWorklistContract | ExecuteWorklistContract | Mapping[str, Any]] = []
    for command in commands:
        command_id = str(command.get("command_id") or command.get("id") or "")
        fields = command.get("parameters") if isinstance(command.get("parameters"), Mapping) else command
        if command_id in LOAD_COMMAND_IDS:
            contract = normalize_load_worklist(fields, command_id=command_id, provenance=command.get("provenance"))
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
