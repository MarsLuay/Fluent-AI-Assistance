"""Shared safeguards for generated Tecan bundle setup scripts."""

from __future__ import annotations

from pathlib import Path


def repair_powershell_pipelines(setup_bat: Path) -> list[str]:
    """Remove cmd escape carets that become literal tokens inside PowerShell commands."""
    text = setup_bat.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    repaired: list[str] = []
    changes: list[str] = []
    for line_number, line in enumerate(lines, start=1):
        updated = line
        if "powershell" in line.casefold() and "^|" in line:
            updated = line.replace("^|", "|")
        if updated != line:
            changes.append(f"line {line_number}: replaced literal PowerShell ^| pipeline token")
        repaired.append(updated)
    if changes:
        setup_bat.write_text("".join(repaired), encoding="utf-8")
    return changes


def setup_bat_findings(setup_bat: Path) -> list[dict[str, str | int]]:
    """Return blocking diagnostics-script defects caught before handoff."""
    findings: list[dict[str, str | int]] = []
    for line_number, line in enumerate(
        setup_bat.read_text(encoding="utf-8", errors="replace").splitlines(),
        start=1,
    ):
        if "powershell" in line.casefold() and "^|" in line:
            findings.append(
                {
                    "reason": "powershell_pipeline_cmd_escape",
                    "line": line_number,
                    "message": (
                        "PowerShell pipelines inside a quoted -Command must use plain |. "
                        "The cmd escape ^ is passed literally and causes a parser error."
                    ),
                }
            )
    return findings
