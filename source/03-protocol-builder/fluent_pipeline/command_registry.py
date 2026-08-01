"""Command-registry helpers for fluent_pipeline."""

from __future__ import annotations

from typing import Any

from tecan_common.command_registry import (  # noqa: F401
    APPROVED_SUPPORT_STATUSES,
    LEGACY_REGISTRY_VERSION,
    REGISTRY_VERSION,
    SUPPORTED_REGISTRY_VERSIONS,
    command_registry_path,
    command_registry_resource,
    command_registry_sha256,
    load_command_registry,
    lookup_command,
    lookup_keys,
    registry_command_approved_passthrough,
    registry_command_family,
    registry_command_operation,
    registry_command_support_status,
    registry_command_supported,
    registry_field_value,
    registry_manual_step,
    registry_pattern_type,
    registry_requires,
    source_command_registry_path,
)

FLUENTCONTROL_NAME_SOURCES = frozenset(
    {
        "fluentcontrol_control_bar",
        "fluentcontrol_script_palette",
        "source_script_observed",
        "connector_display_name",
        "manual_curated",
        "ir_operation_label",
    }
)
NAME_CONFIDENCE_LEVELS = frozenset({"verified", "high", "medium", "low"})


def validate_command_registry_name_provenance(payload: dict[str, Any]) -> None:
    """Validate FluentControl name provenance metadata for registry v2 payloads."""
    commands = payload.get("commands") if isinstance(payload, dict) else None
    if not isinstance(commands, dict):
        raise ValueError("command_registry.json must contain a commands object")

    missing: list[str] = []
    invalid: list[str] = []
    for command_id, entry in commands.items():
        if not isinstance(entry, dict) or not entry.get("fluentcontrol_name"):
            continue
        source = entry.get("fluentcontrol_name_source")
        confidence = entry.get("confidence")
        if not source or not confidence:
            missing.append(command_id)
            continue
        if source not in FLUENTCONTROL_NAME_SOURCES:
            invalid.append(f"{command_id}:source={source!r}")
        if confidence not in NAME_CONFIDENCE_LEVELS:
            invalid.append(f"{command_id}:confidence={confidence!r}")
    if missing:
        raise ValueError(
            "command_registry.json v2 entries with fluentcontrol_name must declare "
            f"fluentcontrol_name_source and confidence: {', '.join(sorted(missing))}"
        )
    if invalid:
        raise ValueError(
            "command_registry.json v2 has invalid name provenance values: "
            + ", ".join(sorted(invalid))
        )


def load_command_registry_with_provenance() -> dict[str, Any]:
    """Load the shared registry and enforce v2 FluentControl name provenance."""
    payload = load_command_registry()
    validate_command_registry_name_provenance(payload)
    return payload


def lookup_command_by_operation(operation: Any) -> dict[str, Any] | None:
    """Return the default registry entry for a canonical protocol IR operation."""
    operation_text = str(operation or "").strip()
    if not operation_text:
        return None
    fallback: dict[str, Any] | None = None
    for command_id, raw_entry in load_command_registry().get("commands", {}).items():
        if not isinstance(raw_entry, dict) or raw_entry.get("operation") != operation_text:
            continue
        entry = {"id": command_id, **raw_entry}
        if raw_entry.get("default_for_operation"):
            return entry
        if fallback is None:
            fallback = entry
    return fallback


def registry_fluentcontrol_name(command_name: Any) -> str | None:
    """Return the FluentControl command label shown in the UI, when known."""
    return _registry_entry_fluentcontrol_name(lookup_command(command_name))


def registry_fluentcontrol_name_for_operation(operation: Any) -> str | None:
    """Return the default FluentControl UI command label for an IR operation."""
    return _registry_entry_fluentcontrol_name(lookup_command_by_operation(operation))


def registry_fluentcontrol_name_source(command_name: Any) -> str | None:
    """Return how the registry fluentcontrol_name was sourced."""
    return _registry_entry_name_provenance_field(lookup_command(command_name), "fluentcontrol_name_source")


def registry_fluentcontrol_name_confidence(command_name: Any) -> str | None:
    """Return confidence for the registry fluentcontrol_name."""
    return _registry_entry_name_provenance_field(lookup_command(command_name), "confidence")


def registry_fluentcontrol_name_metadata(command_name: Any) -> dict[str, str] | None:
    """Return fluentcontrol_name plus provenance metadata when known."""
    entry = lookup_command(command_name)
    name = _registry_entry_fluentcontrol_name(entry)
    if not name:
        return None
    metadata = {"fluentcontrol_name": name}
    source = _registry_entry_name_provenance_field(entry, "fluentcontrol_name_source")
    confidence = _registry_entry_name_provenance_field(entry, "confidence")
    if source:
        metadata["fluentcontrol_name_source"] = source
    if confidence:
        metadata["confidence"] = confidence
    return metadata


def _registry_entry_fluentcontrol_name(entry: dict[str, Any] | None) -> str | None:
    if not entry:
        return None
    for field in ("fluentcontrol_name", "display_name", "label", "name"):
        value = entry.get(field)
        if value not in (None, "", []):
            return str(value)
    return None


def _registry_entry_name_provenance_field(entry: dict[str, Any] | None, field: str) -> str | None:
    if not entry:
        return None
    value = entry.get(field)
    if value in (None, "", []):
        return None
    return str(value)
