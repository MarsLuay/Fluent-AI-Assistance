"""Merge api-v2-076..087 scaffolds into runtime-report payloads."""

from __future__ import annotations

import os
from typing import Any, Callable, Mapping, MutableMapping

from .command_tracing import append_command_trace, trace_execution_command
from .commands import set_location_from_ir_step
from .legacy_sila import resolve_legacy_service_endpoint
from .progress_policy import ProgressSyncPolicy, progress_wait_guidance
from .runtime_session import (
    partition_method_inventory,
    record_session_identity,
    resume_run_policy,
    session_identity_errors,
    validate_method_in_inventory,
)
from .verification_helpers import (
    environmental_pre_run_template,
    interior_light_options_from_env,
    interior_light_policy,
)


def enrich_fluent_context_details(
    details: MutableMapping[str, Any],
    *,
    host: str,
    port: int,
    configured_username: str | None = None,
    observed_username: str | None = None,
    runnable_methods: list[str] | tuple[str, ...] | None = None,
    maintenance_methods: list[str] | tuple[str, ...] | None = None,
    resume_run_after_prompts: bool = False,
    operator_ack_path: str | None = None,
    interior_light_before_prompts: bool = False,
    include_environmental_template: bool = False,
) -> None:
    """Attach low-priority API V2 observability blocks to runtime-report details."""
    details["api_v2"] = {
        "legacy_service_endpoint": resolve_legacy_service_endpoint(host, port),
        "progress_policy": {
            "allow_async_invoke": ProgressSyncPolicy.ALLOW_ASYNC_INVOKE,
            "preferred_sources": list(ProgressSyncPolicy.PREFERRED_SOURCES),
            "guidance": progress_wait_guidance(),
        },
        "session": record_session_identity(
            configured_username=configured_username,
            observed_username=observed_username,
        ),
        "resume_run": resume_run_policy(
            enabled=resume_run_after_prompts,
            ack_path=operator_ack_path,
        ),
        "interior_light": interior_light_policy(enabled=interior_light_before_prompts),
    }
    if runnable_methods is not None or maintenance_methods is not None:
        details["api_v2"]["method_inventory"] = partition_method_inventory(
            runnable_methods,
            maintenance_methods,
        )
    if include_environmental_template:
        details["api_v2"]["environmental_pre_run"] = environmental_pre_run_template()


def merge_api_v2_context_into_report(
    report: dict[str, Any],
    *,
    host: str,
    port: int,
    method: str = "",
    configured_username: str | None = None,
    observed_username: str | None = None,
    runnable_methods: list[str] | tuple[str, ...] | None = None,
    maintenance_methods: list[str] | tuple[str, ...] | None = None,
    resume_run_after_prompts: bool | None = None,
    operator_ack_path: str | None = None,
    interior_light_before_prompts: bool | None = None,
    include_environmental_template: bool = False,
) -> dict[str, Any]:
    """Merge api-v2-081..087 observability blocks and apply preflight errors."""
    merged = dict(report)
    details = dict(merged.get("details") or {})
    if resume_run_after_prompts is None:
        resume_run_after_prompts = os.environ.get("TECAN_RESUME_RUN_AFTER_PROMPTS", "").strip().casefold() in {
            "1",
            "true",
            "yes",
        }
    if interior_light_before_prompts is None:
        interior_light_before_prompts = interior_light_options_from_env().interior_light_before_prompts
    if operator_ack_path is None:
        operator_ack_path = os.environ.get("TECAN_OPERATOR_ACK_FILE", "").strip() or None
    if configured_username is None:
        configured_username = os.environ.get("TECAN_FLUENT_USERNAME", "").strip() or None

    enrich_fluent_context_details(
        details,
        host=host,
        port=port,
        configured_username=configured_username,
        observed_username=observed_username,
        runnable_methods=runnable_methods,
        maintenance_methods=maintenance_methods,
        resume_run_after_prompts=bool(resume_run_after_prompts),
        operator_ack_path=operator_ack_path,
        interior_light_before_prompts=bool(interior_light_before_prompts),
        include_environmental_template=include_environmental_template,
    )
    merged["details"] = details

    errors = list(merged.get("errors") or [])
    inventory_errors = validate_method_in_inventory(
        method,
        runnable_methods=runnable_methods,
        maintenance_methods=maintenance_methods,
    )
    errors.extend(inventory_errors)
    session_block = (details.get("api_v2") or {}).get("session")
    identity_errors = session_identity_errors(session_block if isinstance(session_block, Mapping) else None)
    errors.extend(identity_errors)
    if inventory_errors or identity_errors:
        merged["ok"] = False
        merged["status"] = "failed"
        if not merged.get("summary"):
            merged["summary"] = (inventory_errors or identity_errors)[0]
    if errors:
        merged["errors"] = errors[:100]
    return merged


def render_api_v2_context_markdown_lines(details: Mapping[str, Any]) -> list[str]:
    """Human-readable api_v2 block for runtime-report markdown (081..085)."""
    api_v2 = details.get("api_v2")
    if not isinstance(api_v2, Mapping):
        return []
    lines = ["## API V2 context", ""]
    session = api_v2.get("session")
    if isinstance(session, Mapping):
        lines.append(f"- Session user: `{session.get('current_username') or '(none)'}`")
        if session.get("configured_username"):
            lines.append(f"- Configured user: `{session.get('configured_username')}`")
            lines.append(f"- Login verified: `{bool(session.get('login_verified'))}`")
    inventory = api_v2.get("method_inventory")
    if isinstance(inventory, Mapping):
        lines.append(
            f"- Method inventory: `{inventory.get('runnable_count', 0)}` runnable, "
            f"`{inventory.get('maintenance_count', 0)}` maintenance"
        )
    resume = api_v2.get("resume_run")
    if isinstance(resume, Mapping):
        lines.append(f"- ResumeRun after prompts: `{bool(resume.get('resume_run_after_prompts'))}`")
    interior = api_v2.get("interior_light")
    if isinstance(interior, Mapping):
        lines.append(f"- Interior light before prompts: `{bool(interior.get('interior_light_before_prompts'))}`")
    environmental = api_v2.get("environmental_pre_run")
    if isinstance(environmental, Mapping):
        lines.append("- Environmental pre-run template: present")
    traces = details.get("command_traces")
    if isinstance(traces, list) and traces:
        lines.extend(["", "### Command traces", ""])
        for item in traces[-10:]:
            if isinstance(item, Mapping):
                lines.append(f"- `{item.get('command_type')}`: {item.get('trace')}")
    lines.append("")
    return lines


def emit_ir_deck_step_events(
    ir: Mapping[str, Any],
    emit: Callable[[dict[str, Any]], None],
) -> None:
    """Emit SetLocation/add_labware trace events during generate --event-log (api-v2-086)."""
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation = str(step.get("operation") or "")
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        step_id = str(step.get("id") or "")
        if operation == "set_location":
            typed = set_location_from_ir_step(step)
            trace = typed.to_string()
            emit(
                {
                    "stage": "deck_step",
                    "status": "info",
                    "message": trace,
                    "ir_step_id": step_id,
                    "command_type": "SetLocation",
                }
            )
        elif operation == "add_labware":
            label = str(params.get("label") or params.get("labware") or "?")
            location = str(params.get("location") or "?")
            site = params.get("site") or params.get("position") or 1
            trace = f"AddLabware({label!r} @ {location}:{site})"
            emit(
                {
                    "stage": "deck_step",
                    "status": "info",
                    "message": trace,
                    "ir_step_id": step_id,
                    "command_type": "AddLabware",
                }
            )


def log_remove_labware_trace(
    details: MutableMapping[str, Any],
    *,
    labware: str,
    ir_step_id: str | None = None,
) -> str:
    """api-v2-079: record RemoveLabware ToString-style trace in runtime-report details."""
    from .command_tracing import format_remove_labware_trace

    trace = format_remove_labware_trace(labware=labware)
    append_command_trace(
        details,
        trace_execution_command("RemoveLabware", trace=trace, ir_step_id=ir_step_id),
    )
    return trace


def log_set_location_trace(
    details: MutableMapping[str, Any],
    *,
    labware: str,
    location: str,
    site: int | str,
    rotation: int | str | None = None,
    ir_step_id: str | None = None,
) -> str:
    """api-v2-086: record SetLocation ToString-style trace in runtime-report details."""
    from .command_tracing import format_set_location_trace

    trace = format_set_location_trace(
        labware=labware,
        location=location,
        site=site,
        rotation=rotation,
    )
    append_command_trace(
        details,
        trace_execution_command("SetLocation", trace=trace, ir_step_id=ir_step_id),
    )
    return trace


def subroutine_trace_for_call(call: Mapping[str, Any]) -> str:
    """api-v2-087: build Subroutine ToString-style label from an IR/manifest call record."""
    from .command_summary import subroutine_call_summary

    if call.get("operation") == "call_subroutine":
        return subroutine_call_summary(call)
    params = call.get("parameters") if isinstance(call.get("parameters"), dict) else {}
    return subroutine_call_summary(
        {
            "parameters": {
                "subroutine": call.get("ref") or call.get("subroutine") or params.get("subroutine"),
                "execution_mode": params.get("execution_mode") or call.get("execution_mode"),
                "variable_mappings_start": params.get("variable_mappings_start"),
                "variable_mappings_end": params.get("variable_mappings_end"),
            }
        }
    )
