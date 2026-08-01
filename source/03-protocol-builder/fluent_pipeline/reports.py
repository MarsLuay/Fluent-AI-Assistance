"""Markdown reporting helpers for the protocol builder."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .command_registry import registry_command_support_status
from .api_v2.command_summary import enrich_simulation_subroutine_traces
from .runner import CommandResult


def compact_simulation(data: dict[str, Any], *, protocol_ir: dict[str, Any] | None = None) -> dict[str, Any]:
    enrich_simulation_subroutine_traces(data, protocol_ir)
    state = data.get("state_summary") or {}
    final_labware = data.get("final_labware") or []
    unsupported, approved_opaque, approved_statuses = _classify_opaque_commands(
        data.get("unsupported_command_ids") or {}
    )
    return {
        "status": data.get("status"),
        "total_executed_steps": data.get("total_executed_steps"),
        "fully_simulated_steps": data.get("fully_simulated_steps"),
        "validation_only_steps": data.get("validation_only_steps"),
        "opaque_noop_steps": data.get("opaque_noop_steps"),
        "raw_xml_generic_steps": data.get("raw_xml_generic_steps"),
        "modeled_coverage": data.get("modeled_coverage"),
        "warnings": data.get("warnings") or [],
        "failure": data.get("failure"),
        "unsupported_command_ids": unsupported,
        "approved_opaque_command_ids": approved_opaque,
        "approved_opaque_support_statuses": approved_statuses,
        "effect_counts": data.get("effect_counts") or {},
        "final_labware_count": len(final_labware),
        "labware_volumes": state.get("labware_volumes"),
        "reagent_source_sufficiency": state.get("reagent_source_sufficiency"),
        "tip_state": state.get("tip_state"),
    }


def render_simulation_markdown(
    protocol: Path,
    data: dict[str, Any] | None,
    result: CommandResult,
    *,
    protocol_ir: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Fluent Simulation Report",
        "",
        f"- Generated at: `{now}`",
        f"- Protocol: `{protocol}`",
        f"- Exit code: `{result.returncode}`",
        f"- Command: `{result.command_line()}`",
        "",
    ]

    if data is None:
        lines.extend(
            [
                "## Result",
                "",
                "No JSON simulation payload was produced. The protocol probably failed while loading.",
                "",
            ]
        )
    else:
        enrich_simulation_subroutine_traces(data, protocol_ir)
        summary = compact_simulation(data, protocol_ir=protocol_ir)
        lines.extend(
            [
                "## Summary",
                "",
                f"- Status: `{summary['status']}`",
                f"- Executed steps: `{summary['total_executed_steps']}`",
                f"- Fully simulated steps: `{summary['fully_simulated_steps']}`",
                f"- Modeled coverage: `{summary['modeled_coverage']}`",
                f"- Raw XML / GenericStep count: `{summary['raw_xml_generic_steps']}`",
                f"- Final labware count: `{summary['final_labware_count']}`",
                "",
            ]
        )
        if summary["warnings"]:
            lines.extend(["## Warnings", ""])
            lines.extend(f"- {warning}" for warning in summary["warnings"])
            lines.append("")
        if summary["failure"]:
            lines.extend(["## Failure", "", "```json"])
            lines.append(json.dumps(summary["failure"], indent=2, sort_keys=True))
            lines.extend(["```", ""])
        if summary["unsupported_command_ids"]:
            lines.extend(["## Unsupported Commands", "", "```json"])
            lines.append(json.dumps(summary["unsupported_command_ids"], indent=2, sort_keys=True))
            lines.extend(["```", ""])
        opaque_subroutines = [
            item
            for item in (data.get("opaque_events") or [])
            if isinstance(item, dict) and item.get("call_label")
        ]
        if opaque_subroutines:
            lines.extend(["## Opaque Subroutine Calls", ""])
            for item in opaque_subroutines:
                message = str(item.get("message") or "").strip()
                label = str(item.get("call_label") or "")
                step_index = item.get("step_index")
                prefix = f"- Step `{step_index}`: " if step_index is not None else "- "
                if message:
                    lines.append(f"{prefix}`{label}` — {message}")
                else:
                    lines.append(f"{prefix}`{label}`")
            lines.append("")
        if summary["approved_opaque_command_ids"]:
            approved = {
                name: {
                    "count": count,
                    "support_status": summary["approved_opaque_support_statuses"].get(name),
                }
                for name, count in summary["approved_opaque_command_ids"].items()
            }
            lines.extend(["## Approved Opaque Commands", "", "```json"])
            lines.append(json.dumps(approved, indent=2, sort_keys=True))
            lines.extend(["```", ""])
        if summary["effect_counts"]:
            lines.extend(["## Effect Counts", "", "```json"])
            lines.append(json.dumps(summary["effect_counts"], indent=2, sort_keys=True))
            lines.extend(["```", ""])
        if summary["labware_volumes"] is not None:
            lines.extend(["## Labware Volumes", "", "```json"])
            lines.append(json.dumps(summary["labware_volumes"], indent=2, sort_keys=True))
            lines.extend(["```", ""])

    if data is None:
        _append_process_output(lines, result)
    elif result.stderr.strip():
        _append_process_output(lines, result, stdout_limit=0, stderr_limit=4000)
    return "\n".join(lines).rstrip() + "\n"


def render_roundtrip_markdown(source: Path, stages: list[dict[str, Any]]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# Fluent Roundtrip Report",
        "",
        f"- Generated at: `{now}`",
        f"- Source XSCR: `{source}`",
        "",
        "## Stages",
        "",
    ]
    for stage in stages:
        result: CommandResult = stage["result"]
        status = "passed" if result.ok else "failed"
        lines.extend(
            [
                f"### {stage['name']}",
                "",
                f"- Status: `{status}`",
                f"- Exit code: `{result.returncode}`",
                f"- Command: `{result.command_line()}`",
            ]
        )
        output = stage.get("output")
        if output:
            lines.append(f"- Output: `{output}`")
        data = stage.get("simulation")
        if data is not None:
            summary = compact_simulation(data)
            lines.extend(
                [
                    f"- Simulation status: `{summary['status']}`",
                    f"- Modeled coverage: `{summary['modeled_coverage']}`",
                    f"- Raw XML / GenericStep count: `{summary['raw_xml_generic_steps']}`",
                ]
            )
        lines.append("")
        _append_process_output(lines, result, stdout_limit=2500, stderr_limit=5000)
    return "\n".join(lines).rstrip() + "\n"


def render_compile_markdown(protocol: Path, output: Path, result: CommandResult) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    status = "passed" if result.ok else "failed"
    lines = [
        "# Fluent Compile Report",
        "",
        f"- Generated at: `{now}`",
        f"- Protocol draft: `{protocol}`",
        f"- Output XSCR: `{output}`",
        f"- Status: `{status}`",
        f"- Exit code: `{result.returncode}`",
        f"- Command: `{result.command_line()}`",
        "",
    ]
    _append_process_output(lines, result, stdout_limit=5000, stderr_limit=8000)
    return "\n".join(lines).rstrip() + "\n"


def _classify_opaque_commands(command_counts: dict[str, Any]) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
    unsupported: dict[str, int] = {}
    approved: dict[str, int] = {}
    statuses: dict[str, str] = {}
    for command, raw_count in sorted(command_counts.items()):
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            continue
        status = registry_command_support_status(command)
        if status:
            approved[str(command)] = count
            statuses[str(command)] = status
        else:
            unsupported[str(command)] = count
    return unsupported, approved, statuses


def render_doctor_markdown(checks: list[dict[str, Any]]) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = ["# Protocol Builder Doctor", "", f"- Generated at: `{now}`", ""]
    for check in checks:
        status = "passed" if check["ok"] else "failed"
        lines.extend([f"## {check['name']}", "", f"- Status: `{status}`"])
        detail = check.get("detail")
        if detail:
            lines.append(f"- Detail: {detail}")
        result = check.get("result")
        if result is not None:
            lines.append(f"- Command: `{result.command_line()}`")
            lines.append(f"- Exit code: `{result.returncode}`")
            lines.append("")
            _append_process_output(lines, result, stdout_limit=5000, stderr_limit=5000)
        else:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _append_process_output(
    lines: list[str],
    result: CommandResult,
    *,
    stdout_limit: int = 4000,
    stderr_limit: int = 4000,
) -> None:
    stdout = _trim(result.stdout.strip(), stdout_limit)
    stderr = _trim(result.stderr.strip(), stderr_limit)
    if stdout:
        lines.extend(["Stdout:", "", "```text", stdout, "```", ""])
    if stderr:
        lines.extend(["Stderr:", "", "```text", stderr, "```", ""])


def _trim(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... trimmed {len(text) - limit} characters ..."
