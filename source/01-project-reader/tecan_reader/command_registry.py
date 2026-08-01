"""Command-registry helpers shared via :mod:`tecan_common.command_registry`."""

from __future__ import annotations

from tecan_common.command_registry import (  # noqa: F401
    APPROVED_SUPPORT_STATUSES,
    LEGACY_REGISTRY_VERSION,
    REGISTRY_VERSION,
    SUPPORTED_REGISTRY_VERSIONS,
    command_registry_path,
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
)
