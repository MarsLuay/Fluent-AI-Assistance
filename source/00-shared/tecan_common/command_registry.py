"""Shared FluentControl command-registry lookup helpers."""

from __future__ import annotations

import hashlib
from importlib import resources
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


REGISTRY_VERSION = "tecan.command_registry.v2"
LEGACY_REGISTRY_VERSION = "tecan.command_registry.v1"
SUPPORTED_REGISTRY_VERSIONS = frozenset({REGISTRY_VERSION, LEGACY_REGISTRY_VERSION})
APPROVED_SUPPORT_STATUSES = frozenset({"approved_passthrough", "approved_non_command"})
_REGISTRY_RESOURCE = ("data", "command_registry.json")


def command_registry_resource() -> Path:
    """Return the packaged command-registry resource path."""
    resource = Path(str(resources.files("tecan_common").joinpath(*_REGISTRY_RESOURCE)))
    if not resource.is_file():
        raise FileNotFoundError("Could not find tecan_common/data/command_registry.json")
    return resource


def source_command_registry_path() -> Path:
    """Return the editable repository source file for the command registry."""
    return Path(__file__).resolve().parent / "data" / "command_registry.json"


def command_registry_path() -> Path:
    """Backward-compatible alias for the packaged command-registry resource."""
    return command_registry_resource()


@lru_cache(maxsize=1)
def load_command_registry() -> dict[str, Any]:
    """Load and minimally validate the packaged command registry."""
    resource = command_registry_resource()
    payload = json.loads(resource.read_text(encoding="utf-8"))
    schema_version = payload.get("schema_version")
    if schema_version not in SUPPORTED_REGISTRY_VERSIONS:
        raise ValueError(f"Unsupported command registry version: {schema_version!r}")
    commands = payload.get("commands")
    if not isinstance(commands, dict):
        raise ValueError("command_registry.json must contain a commands object")
    return payload


def command_registry_sha256() -> str:
    """Return the SHA-256 digest of the packaged command registry."""
    digest = hashlib.sha256()
    digest.update(command_registry_resource().read_bytes())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _lookup_index() -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for command_id, raw_entry in load_command_registry().get("commands", {}).items():
        if not isinstance(raw_entry, dict):
            continue
        entry = {"id": command_id, **raw_entry}
        for alias in [command_id, *(raw_entry.get("aliases") or [])]:
            for key in lookup_keys(alias):
                index.setdefault(key, entry)
    return index


def lookup_command(command_name: Any) -> dict[str, Any] | None:
    """Return the registry entry for a command id, raw type, short name, or alias."""
    for key in lookup_keys(command_name):
        hit = _lookup_index().get(key)
        if hit:
            return dict(hit)
    return None


def registry_command_family(command_name: Any) -> str | None:
    hit = lookup_command(command_name)
    return str(hit.get("family")) if hit and hit.get("family") else None


def registry_command_operation(command_name: Any) -> str | None:
    hit = lookup_command(command_name)
    return str(hit.get("operation")) if hit and hit.get("operation") else None


def registry_command_support_status(command_name: Any) -> str | None:
    hit = lookup_command(command_name)
    if not hit:
        return None
    if hit.get("operation"):
        return "mapped"
    status = str(hit.get("support_status") or "").strip()
    if status in APPROVED_SUPPORT_STATUSES:
        return status
    if hit.get("approved_passthrough"):
        return "approved_passthrough"
    return None


def registry_command_supported(command_name: Any) -> bool:
    return registry_command_support_status(command_name) is not None


def registry_command_approved_passthrough(command_name: Any) -> bool:
    status = registry_command_support_status(command_name)
    return status in APPROVED_SUPPORT_STATUSES


def registry_pattern_type(command_name: Any) -> str | None:
    hit = lookup_command(command_name)
    return str(hit.get("pattern_type")) if hit and hit.get("pattern_type") else None


def registry_requires(command_name: Any) -> list[str]:
    hit = lookup_command(command_name)
    requires = hit.get("requires") if hit else None
    return [str(item) for item in requires] if isinstance(requires, list) else []


def registry_field_value(command_name: Any, canonical_field: str, fields: dict[str, Any]) -> Any:
    hit = lookup_command(command_name)
    aliases = hit.get("field_aliases") if hit else None
    if isinstance(aliases, dict) and isinstance(aliases.get(canonical_field), list):
        value = _first_field(fields, aliases[canonical_field])
        if value not in (None, "", []):
            return value
    fallbacks = {
        "labware": ["LabwareName", "LabwareLable", "RackLabel", "RackType"],
        "volume_ul": ["Volume", "AspirationVolume", "AspirateVolume", "DispenseVolume", "MixVolume"],
        "liquid_class": [
            "LiquidClassNameBySelection",
            "LiquidClassName",
            "AspirationLiquidClass",
            "DispenseLiquidClass",
            "LiquidClass",
        ],
        "worklist": ["WorklistName", "FileName", "Path"],
        "prompt": ["QueryPrompt", "Comment", "Name"],
        "comment": ["Comment", "Text", "Name"],
        "variable": ["VariableName", "Name"],
        "value": ["Value", "VariableValue", "DefaultValue"],
        "minimum": ["MinimumText", "MinValue", "UnresolvedMinValue"],
        "maximum": ["MaximumText", "MaxValue", "UnresolvedMaxValue"],
        "timeout": ["Timeout", "TimeOut", "RUPTimeOut"],
        "device_alias": ["DeviceAlias", "AvailableID", "Name"],
    }
    return _first_field(fields, fallbacks.get(canonical_field, []))


def registry_manual_step(command_name: Any, fields: dict[str, Any] | None = None) -> str | None:
    hit = lookup_command(command_name)
    if not hit:
        return None
    template = str(hit.get("manual_step") or "")
    if not template:
        return None
    values = _template_values(hit, fields or {})
    try:
        return template.format(**values)
    except KeyError:
        return template


def lookup_keys(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    local = text.rsplit(".", 1)[-1].strip()
    candidates = {
        text,
        local,
        re.sub(r"ScriptCommandDataV\d+$", "ScriptCommand", local),
        re.sub(r"CommandDataV\d+$", "Command", local),
        re.sub(r"DataV\d+$", "", local),
        re.sub(r"DataV\d+$", "Command", local),
    }
    return [_normalize_key(candidate) for candidate in candidates if candidate]


def _template_values(entry: dict[str, Any], fields: dict[str, Any]) -> dict[str, str]:
    values: dict[str, str] = {}
    aliases = entry.get("field_aliases") or {}
    if isinstance(aliases, dict):
        for canonical, names in aliases.items():
            if isinstance(names, list):
                values[str(canonical)] = _first_field(fields, names)
    values.setdefault("labware", _first_field(fields, ["LabwareName", "LabwareLable", "RackLabel", "RackType"]))
    values.setdefault("volume_ul", _first_field(fields, ["Volume"]))
    values.setdefault("liquid_class", _first_field(fields, ["LiquidClassNameBySelection", "LiquidClassName"]))
    values.setdefault("worklist", _first_field(fields, ["WorklistName", "FileName", "Path"]))
    values.setdefault("prompt", _first_field(fields, ["QueryPrompt", "Comment", "Name"]))
    values.setdefault("variable", _first_field(fields, ["VariableName", "Name"]))
    values.setdefault("value", _first_field(fields, ["Value", "VariableValue", "DefaultValue"]))
    values.setdefault("minimum", _first_field(fields, ["MinimumText", "MinValue", "UnresolvedMinValue"]))
    values.setdefault("maximum", _first_field(fields, ["MaximumText", "MaxValue", "UnresolvedMaxValue"]))
    values.setdefault("timeout", _first_field(fields, ["Timeout", "TimeOut", "RUPTimeOut"]))
    values.setdefault("device_alias", _first_field(fields, ["DeviceAlias", "AvailableID", "Name"]))
    values.setdefault("comment", _first_field(fields, ["Comment", "Name"]))
    return values


def _first_field(fields: dict[str, Any], names: list[str]) -> Any:
    by_lower = {str(key).lower(): value for key, value in fields.items()}
    for name in names:
        value = by_lower.get(str(name).lower())
        if isinstance(value, list):
            value = next((item for item in value if item not in (None, "", [])), "")
        if value not in (None, "", []):
            return value
    return None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())
