"""FluentControl naming helpers shared across export/alias surfaces."""

from __future__ import annotations

from typing import Any

from tecan_common.native_acceleration import (
    strip_fluent_instance_suffix as _strip_fluent_instance_suffix,
)


def strip_fluent_instance_suffix(value: Any) -> str:
    """Strip a terminal Fluent instance suffix using the Rust implementation."""
    return _strip_fluent_instance_suffix(value)
