"""Compatibility shim for the optional FluentControl import/load diagnostic."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


FLUENT_CONTEXT_CHECK_VERSION = "tecan.fluent_context_check.v1"


@dataclass(frozen=True)
class FluentContextCheckConfig:
    method: str
    xscr_path: Path | None = None
    zeia_path: Path | None = None
    provider: str = "auto"
    host: str = "127.0.0.1"
    port: int = 50052
    insecure: bool = False
    timeout_seconds: float = 180.0
    command: str | None = None
    username: str | None = None
    password: str | None = None
    close_method: bool = True


def run_fluent_context_check(config: FluentContextCheckConfig) -> dict[str, Any]:
    """Return a deterministic offline report when no live provider is wired."""
    summary = "FluentControl import/load diagnostic is unavailable in the offline compatibility shim."
    return {
        "version": FLUENT_CONTEXT_CHECK_VERSION,
        "ok": False,
        "status": "unavailable",
        "provider": config.provider or "auto",
        "method": config.method,
        "xscr_path": str(config.xscr_path) if config.xscr_path else None,
        "zeia_path": str(config.zeia_path) if config.zeia_path else None,
        "summary": summary,
        "simulation_mode": True,
        "errors": [summary],
        "runtime_errors": [],
        "diagnostics": [],
        "state": "",
        "last_error": "",
        "messages": [],
        "details": {
            "host": config.host,
            "port": config.port,
            "command": config.command,
            "close_method": config.close_method,
        },
    }


def render_fluent_context_check_markdown(report: dict[str, Any]) -> str:
    """Render a compact Markdown summary for the compatibility report."""
    lines = [
        "# FluentControl Context Check",
        "",
        "- Type: `optional import/load diagnostic`",
        f"- Status: `{report.get('status') or 'unknown'}`",
        f"- Provider: `{report.get('provider') or 'unknown'}`",
        f"- Method: `{report.get('method') or ''}`",
        f"- Simulation mode: `{bool(report.get('simulation_mode', True))}`",
        f"- Summary: {report.get('summary') or ''}",
    ]
    errors = [str(item) for item in (report.get("errors") or []) if str(item).strip()]
    if errors:
        lines.extend(["", "## Errors", ""])
        for item in errors:
            lines.append(f"- {item}")
    return "\n".join(lines).rstrip() + "\n"
