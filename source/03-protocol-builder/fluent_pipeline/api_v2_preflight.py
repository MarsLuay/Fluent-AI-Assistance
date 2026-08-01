"""Offline ICommand.Validate() preflight helpers (api-v2-006)."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def preflight_command_validation(xscr_path: Path) -> dict[str, Any]:
    """Offline ``ICommand.Validate()`` preflight before compile/package checks."""
    from .api_v2.command_validate import validate_compiled_xscr_commands

    report = validate_compiled_xscr_commands(xscr_path)
    return report.as_dict()
