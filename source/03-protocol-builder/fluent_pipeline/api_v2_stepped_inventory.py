"""Offline API V2 stepped command inventory and validation runner (api-v2-001, api-v2-004).

Maps compiled XSCR (or protocol IR) to ``ICommand`` instances and executes them
one at a time through ``ExecutionChannel.ExecuteCommand(ICommand)`` instead of a
offline registry / recording channels without a live VisionX session.

``approved_passthrough`` registry entries without a typed API V2 command are
routed through ``GenericCommand.ToXML()`` raw XML (api-v2-004).
"""

from __future__ import annotations

import time
from . import xml_compat as ET
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .api_v2_add_labware_validate import (
    extract_add_labware_fields,
    record_successful_add_labware,
    runtime_error_for_validate_failure as add_labware_runtime_error_for_validate_failure,
    validate_add_labware_before_execute,
)
from .api_v2_transfer_labware_validate import (
    extract_transfer_labware_fields,
    record_successful_transfer,
    runtime_error_for_validate_failure as transfer_runtime_error_for_validate_failure,
    validate_transfer_labware_before_execute,
)
from .api_v2_subroutine_validate import (
    runtime_error_for_validate_failure as subroutine_runtime_error_for_validate_failure,
    validate_subroutine_before_execute,
    validate_subroutines_after_load,
)
from .api_v2_user_prompt_validate import (
    runtime_error_for_validate_failure,
    validate_user_prompt_before_execute,
)
from .api_v2.command_tracing import (
    command_trace_for_stepped,
    merge_remove_labware_traces_into_details,
    merge_set_location_traces_into_details,
    stepped_command_trace,
)
from .api_v2.generic_passthrough import (
    stepped_command_from_xscr,
    uses_generic_command_passthrough,
    validate_generic_passthrough_execute_xml,
)
from .api_v2.run_control import MethodTeardown, RunControlOptions, SemiAutomatedResumeMonitor
from .api_v2_execution import (
    ABORT_REASON_BLOCKED_USER_PROMPT,
    ABORT_REASON_RUNTIME_ERROR,
    EXECUTION_ABORT_DETAIL_KEY,
    ExecutionAbortContext,
    SteppedExecutionTracker,
    abort_execution_channel,
    execution_abort_from_blocked_user_prompt,
    execution_abort_from_runtime_error,
    perform_runtime_teardown,
)
from .command_registry import registry_command_operation, registry_command_support_status
EXECUTION_STEPS_KEY = "execution_steps"
EXECUTION_SUMMARY_KEY = "execution_summary"


API_V2_STEPPED_PROVIDER_NAME = "api-v2"
API_V2_STEPPED_SCHEMA_VERSION = "tecan.fluent_api_v2_stepped.v1"

_OPERATION_TO_API_COMMAND: dict[str, str] = {
    "prompt_user": "UserPrompt",
    "move_plate": "TransferLabware",
    "call_subroutine": "Subroutine",
    "add_labware": "AddLabware",
    "remove_labware": "RemoveLabware",
    "get_tips": "GetTips",
    "drop_tips": "DropTips",
    "aspirate": "Aspirate",
    "dispense": "Dispense",
    "mix": "Mix",
    "initialize_device": "InitializeDevice",
    "application_driver_macro": "GenericCommand",
    "comment": "Comment",
}


@dataclass(frozen=True)
class ICommand:
    """Minimal ``ICommand`` stand-in for ``ExecutionChannel.ExecuteCommand``."""

    type_name: str
    index: int
    group: str
    line_number: str | None = None
    operation: str | None = None
    ir_step_id: str = ""
    payload_xml: str = ""
    source: str = "xscr"
    api_v2_type: str = ""
    execute_xml: str = ""

    @property
    def command_type(self) -> str:
        if self.api_v2_type:
            return self.api_v2_type
        if self.operation:
            return _OPERATION_TO_API_COMMAND.get(self.operation, self.type_name)
        return self.type_name

    def as_dict(self) -> dict[str, Any]:
        return {
            "type_name": self.type_name,
            "command_type": self.command_type,
            "index": self.index,
            "ir_step_id": self.ir_step_id or f"step_{self.index:03d}",
            "group": self.group,
            "line_number": self.line_number,
            "operation": self.operation,
            "source": self.source,
            "api_v2_type": self.api_v2_type,
            "generic_passthrough": uses_generic_command_passthrough(
                api_v2_type=self.api_v2_type,
                command_id=self.type_name,
            ),
            "payload_bytes": len(self.payload_xml.encode("utf-8")),
            "execute_xml_bytes": len(self.execute_xml.encode("utf-8")),
        }


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    error: str = ""
    state: str = ""
    messages: tuple[str, ...] = ()


@dataclass
class SteppedRunResult:
    ok: bool
    status: str
    summary: str
    commands_total: int = 0
    commands_executed: int = 0
    failed_index: int | None = None
    failed_command: ICommand | None = None
    errors: list[str] = field(default_factory=list)
    runtime_errors: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    state: str = ""
    last_error: str = ""
    command_log: list[dict[str, Any]] = field(default_factory=list)
    execution_steps: list[dict[str, Any]] = field(default_factory=list)
    resume_run_events: list[dict[str, Any]] = field(default_factory=list)
    duration_seconds: float = 0.0

    def as_details(self) -> dict[str, Any]:
        return {
            "schema_version": API_V2_STEPPED_SCHEMA_VERSION,
            "runner": "stepped_execute_command",
            "commands_total": self.commands_total,
            "commands_executed": self.commands_executed,
            "failed_index": self.failed_index,
            "failed_command": self.failed_command.as_dict() if self.failed_command else None,
            "command_log": self.command_log,
            EXECUTION_STEPS_KEY: self.execution_steps,
            "duration_seconds": round(self.duration_seconds, 3),
            "resume_run_events": self.resume_run_events or None,
        }


class ExecutionChannel(Protocol):
    """FluentControl ``ExecutionChannel`` surface used by the stepped runner."""

    name: str

    def prepare_method(self, method: str, *, simulation_mode: bool = True) -> ExecutionResult:
        ...

    def execute_command(self, command: ICommand) -> ExecutionResult:
        ...

    def finish_execution(self) -> ExecutionResult:
        ...

    def wait_for_channel_close(self, timeout_seconds: float) -> ExecutionResult:
        ...

    def close_method(self) -> None:
        ...

    def abort_execution(self) -> None:
        ...


@dataclass
class ChannelEventSink:
    """Records RuntimeControllerEvents.Error and ChannelCloses callbacks (api-v2-002)."""

    errors: list[str] = field(default_factory=list)
    channel_closed: bool = False

    def on_error(self, message: str) -> None:
        text = str(message).strip()
        if text:
            self.errors.append(text)

    def on_channel_closes(self) -> None:
        self.channel_closed = True


def extract_commands_from_xscr(path: Path) -> list[ICommand]:
    """Walk compiled XSCR XML and emit one ``ICommand`` per statement ``Object``."""
    root = ET.parse(path).getroot()
    commands: list[ICommand] = []
    index = 0
    for group_object in _iter_local(root, "Object"):
        object_type = str(group_object.attrib.get("Type") or "")
        if not object_type.endswith("ScriptGroupDataV1"):
            continue
        group_data = _direct_child(group_object, "ScriptGroupDataV1")
        group_name = _direct_text(group_data, "Name") if group_data is not None else "Ungrouped"
        statements = _first_descendant(group_object, "Statements")
        for command_object in _direct_children(statements, "Object"):
            type_name = _command_type_name(command_object)
            if not type_name:
                continue
            line_number = _first_text(command_object, "LineNumber")
            payload_xml = ET.tostring(command_object, encoding="unicode")
            api_v2_type, execute_xml, mapped_operation = stepped_command_from_xscr(
                command_object,
                command_id=type_name,
            )
            operation = mapped_operation or registry_command_operation(type_name)
            commands.append(
                ICommand(
                    type_name=type_name,
                    index=index,
                    group=group_name or "Ungrouped",
                    line_number=line_number,
                    operation=operation,
                    ir_step_id=f"xscr_{line_number or index}",
                    payload_xml=payload_xml,
                    source="xscr",
                    api_v2_type=api_v2_type,
                    execute_xml=execute_xml,
                )
            )
            index += 1
    return commands


def map_ir_steps_to_commands(ir: Mapping[str, Any]) -> list[ICommand]:
    """Map reviewed protocol IR steps to ``ICommand`` instances."""
    commands: list[ICommand] = []
    for index, step in enumerate(ir.get("steps") or []):
        if not isinstance(step, dict):
            continue
        command_id = str(step.get("command_id") or step.get("operation") or "").strip()
        if not command_id:
            continue
        params = step.get("parameters") or {}
        payload_xml = ""
        if isinstance(params, dict):
            payload_xml = str(params.get("raw_xml") or "")
        commands.append(
            ICommand(
                type_name=command_id,
                index=index,
                group=str(step.get("group") or "Ungrouped"),
                line_number=str(step.get("line_number") or "") or None,
                operation=str(step.get("operation") or "") or None,
                ir_step_id=str(step.get("id") or f"step_{index:03d}"),
                payload_xml=payload_xml,
                source="ir",
            )
        )
    return commands


def resolve_commands(
    *,
    xscr_path: Path | None,
    ir: Mapping[str, Any] | None = None,
) -> tuple[list[ICommand], str]:
    """Prefer compiled XSCR commands; fall back to IR when XSCR is absent."""
    if xscr_path is not None:
        if not xscr_path.exists():
            return [], f"Compiled XSCR was not found: {xscr_path}"
        try:
            commands = extract_commands_from_xscr(xscr_path)
        except ET.ParseError as exc:
            return [], f"Could not parse compiled XSCR: {exc}"
        if commands:
            return commands, ""
        return [], f"No executable commands were found in {xscr_path}"
    if ir is not None:
        commands = map_ir_steps_to_commands(ir)
        if commands:
            return commands, ""
        return [], "Protocol IR contained no mappable command steps."
    return [], "No compiled XSCR or protocol IR was provided for stepped execution."


def _append_execution_step(
    result: SteppedRunResult,
    command: ICommand,
    *,
    ok: bool,
    status: str,
    runtime_error: str = "",
    executed: bool = False,
    trace: str | None = None,
) -> None:
    """Record per-step pass/fail for runtime-report JSON (api-v2-002)."""
    step: dict[str, Any] = {
        "index": command.index,
        "ir_step_id": command.ir_step_id or f"step_{command.index:03d}",
        "command_type": command.command_type,
        "type_name": command.type_name,
        "ok": bool(ok),
        "status": status,
        "runtime_error": runtime_error,
        "executed": executed,
    }
    if trace:
        step["trace"] = trace
    result.execution_steps.append(step)


def _deck_layout_trace(command: ICommand) -> str | None:
    from .api_v2.command_tracing import set_location_trace_for_stepped_command

    return set_location_trace_for_stepped_command(command)


def _enrich_log_entry(log_entry: dict[str, Any], command: ICommand) -> None:
    trace = _deck_layout_trace(command) or command_trace_for_stepped(command)
    if trace:
        log_entry["trace"] = trace
        log_entry["ir_step_id"] = command.ir_step_id or f"step_{command.index:03d}"
    if command.api_v2_type == "RemoveLabware" or command.type_name == "RemoveLabwareDataV1":
        _, extra = stepped_command_trace(command)
        labware_name = str(extra.get("labware_name") or "").strip()
        if labware_name:
            log_entry["labware_name"] = labware_name


class SteppedRunner:
    """Execute mapped ``ICommand`` steps one at a time via ``ExecutionChannel``."""

    def __init__(self, channel: ExecutionChannel):
        self.channel = channel

    def run(
        self,
        *,
        method: str,
        commands: Sequence[ICommand],
        simulation_mode: bool = True,
        close_method: bool = True,
    ) -> SteppedRunResult:
        started = time.time()
        result = SteppedRunResult(
            ok=False,
            status="failed",
            summary="Stepped execution did not run.",
            commands_total=len(commands),
        )
        if not commands:
            result.summary = "No commands were available for stepped execution."
            result.errors.append(result.summary)
            result.duration_seconds = time.time() - started
            return result

        prepare = self.channel.prepare_method(method, simulation_mode=simulation_mode)
        result.messages.extend(prepare.messages)
        result.state = prepare.state
        if not prepare.ok:
            result.summary = prepare.error or "FluentControl prepare-method failed before stepped execution."
            result.errors.append(result.summary)
            result.last_error = prepare.error
            result.duration_seconds = time.time() - started
            return result

        executed = 0
        tracker = SteppedExecutionTracker()
        resume_monitor = getattr(self.channel, "resume_run_monitor", None)
        prior_add_labels: set[str] = set()
        prior_add_slots: set[tuple[str, str]] = set()
        deck_labels: set[str] = set()
        deck_slots: dict[str, tuple[str, str]] = {}
        occupied_slots: set[tuple[str, str]] = set()
        for command in commands:
            tracker.begin_command(command.index, command.command_type, step_id=command.ir_step_id)
            native_validator = getattr(self.channel, "native_user_prompt_validate", None)
            native_validate = (
                (lambda: native_validator(command))
                if callable(native_validator)
                else None
            )
            validation = validate_user_prompt_before_execute(
                command,
                native_validate=native_validate,
            )
            if not validation.ok:
                log_entry = {
                    "index": command.index,
                    "type_name": command.type_name,
                    "group": command.group,
                    "operation": command.operation,
                    "ok": False,
                    "error": validation.message,
                    "validate": validation.as_dict(),
                    "executed": False,
                    "trace": command_trace_for_stepped(command),
                }
                _enrich_log_entry(log_entry, command)
                result.command_log.append(log_entry)
                result.failed_index = command.index
                result.failed_command = command
                result.last_error = validation.message
                message = runtime_error_for_validate_failure(validation, command)
                result.runtime_errors.append(message)
                result.summary = message
                result.status = "failed"
                result.commands_executed = executed
                result.duration_seconds = time.time() - started
                _append_execution_step(
                    result,
                    command,
                    ok=False,
                    status="failed",
                    runtime_error=validation.message,
                    executed=False,
                    trace=log_entry.get("trace"),
                )
                if close_method:
                    self._abort_and_teardown(
                        result,
                        tracker=tracker,
                        reason=ABORT_REASON_BLOCKED_USER_PROMPT,
                        message=message,
                        close_method=close_method,
                    )
                return result

            runtime_inventory = getattr(self.channel, "runtime_script_inventory", None)
            native_sub_validator = getattr(self.channel, "native_subroutine_validate", None)
            native_sub_validate = (
                (lambda: native_sub_validator(command))
                if callable(native_sub_validator)
                else None
            )
            sub_validation = validate_subroutine_before_execute(
                command,
                native_validate=native_sub_validate,
                runtime_inventory=runtime_inventory,
            )
            if not sub_validation.ok:
                log_entry = {
                    "index": command.index,
                    "type_name": command.type_name,
                    "group": command.group,
                    "operation": command.operation,
                    "ok": False,
                    "error": sub_validation.message,
                    "validate": sub_validation.as_dict(),
                    "executed": False,
                }
                result.command_log.append(log_entry)
                result.failed_index = command.index
                result.failed_command = command
                result.last_error = sub_validation.message
                message = subroutine_runtime_error_for_validate_failure(sub_validation, command)
                result.runtime_errors.append(message)
                result.summary = message
                result.status = "failed"
                result.commands_executed = executed
                result.duration_seconds = time.time() - started
                _append_execution_step(
                    result,
                    command,
                    ok=False,
                    status="failed",
                    runtime_error=sub_validation.message,
                    executed=False,
                )
                if close_method:
                    self._abort_and_teardown(
                        result,
                        tracker=tracker,
                        reason=ABORT_REASON_BLOCKED_USER_PROMPT,
                        message=message,
                        close_method=close_method,
                    )
                return result

            native_add_validator = getattr(self.channel, "native_add_labware_validate", None)
            native_add_validate = (
                (lambda: native_add_validator(command))
                if callable(native_add_validator)
                else None
            )
            add_validation = validate_add_labware_before_execute(
                command,
                native_validate=native_add_validate,
                prior_labels=prior_add_labels,
                prior_slots=prior_add_slots,
            )
            if not add_validation.ok:
                log_entry = {
                    "index": command.index,
                    "type_name": command.type_name,
                    "group": command.group,
                    "operation": command.operation,
                    "ok": False,
                    "error": add_validation.message,
                    "validate": add_validation.as_dict(),
                    "executed": False,
                }
                result.command_log.append(log_entry)
                result.failed_index = command.index
                result.failed_command = command
                result.last_error = add_validation.message
                message = add_labware_runtime_error_for_validate_failure(add_validation, command)
                result.runtime_errors.append(message)
                result.summary = message
                result.status = "failed"
                result.commands_executed = executed
                result.duration_seconds = time.time() - started
                _append_execution_step(
                    result,
                    command,
                    ok=False,
                    status="failed",
                    runtime_error=add_validation.message,
                    executed=False,
                )
                if close_method:
                    self._abort_and_teardown(
                        result,
                        tracker=tracker,
                        reason=ABORT_REASON_RUNTIME_ERROR,
                        message=message,
                        close_method=close_method,
                    )
                return result

            native_transfer_validator = getattr(self.channel, "native_transfer_labware_validate", None)
            native_transfer_validate = (
                (lambda: native_transfer_validator(command))
                if callable(native_transfer_validator)
                else None
            )
            transfer_validation = validate_transfer_labware_before_execute(
                command,
                native_validate=native_transfer_validate,
                deck_labels=deck_labels,
                deck_slots=deck_slots,
                occupied_slots=occupied_slots,
            )
            if not transfer_validation.ok:
                log_entry = {
                    "index": command.index,
                    "type_name": command.type_name,
                    "group": command.group,
                    "operation": command.operation,
                    "ok": False,
                    "error": transfer_validation.message,
                    "validate": transfer_validation.as_dict(),
                    "executed": False,
                }
                result.command_log.append(log_entry)
                result.failed_index = command.index
                result.failed_command = command
                result.last_error = transfer_validation.message
                message = transfer_runtime_error_for_validate_failure(transfer_validation, command)
                result.runtime_errors.append(message)
                result.summary = message
                result.status = "failed"
                result.commands_executed = executed
                result.duration_seconds = time.time() - started
                _append_execution_step(
                    result,
                    command,
                    ok=False,
                    status="failed",
                    runtime_error=transfer_validation.message,
                    executed=False,
                )
                if close_method:
                    self._abort_and_teardown(
                        result,
                        tracker=tracker,
                        reason=ABORT_REASON_RUNTIME_ERROR,
                        message=message,
                        close_method=close_method,
                    )
                return result

            step_result = self.channel.execute_command(command)
            finish_result = self.channel.finish_execution()
            close_result = self.channel.wait_for_channel_close(30.0)
            step_ok = step_result.ok and finish_result.ok and close_result.ok
            runtime_error = (
                step_result.error
                or finish_result.error
                or close_result.error
            )
            validate_payload: dict[str, Any] = {}
            if add_validation.source not in {"skipped_non_add_labware", "skipped_no_payload"}:
                validate_payload["add_labware"] = add_validation.as_dict()
            if transfer_validation.source not in {"skipped_non_transfer", "skipped_no_payload"}:
                validate_payload["transfer_labware"] = transfer_validation.as_dict()
            if validation.source != "skipped_non_prompt":
                validate_payload["user_prompt"] = validation.as_dict()
            if sub_validation.source not in {"skipped_non_subroutine", "skipped_no_inventory"}:
                validate_payload["subroutine"] = sub_validation.as_dict()
            log_entry = {
                "index": command.index,
                "ir_step_id": command.ir_step_id or f"step_{command.index:03d}",
                "command_type": command.command_type,
                "type_name": command.type_name,
                "group": command.group,
                "operation": command.operation,
                "api_v2_type": command.api_v2_type,
                "generic_passthrough": uses_generic_command_passthrough(
                    api_v2_type=command.api_v2_type,
                    command_id=command.type_name,
                ),
                "execute_xml_bytes": len(command.execute_xml.encode("utf-8")),
                "ok": step_ok,
                "error": runtime_error,
                "finish_execution_ok": finish_result.ok,
                "channel_closed_ok": close_result.ok,
                "validate": validate_payload or None,
            }
            _enrich_log_entry(log_entry, command)
            if command.api_v2_type == "RemoveLabware" and log_entry.get("trace"):
                result.messages.append(f"ExecuteCommand trace: {log_entry['trace']}")
            result.command_log.append(log_entry)
            _append_execution_step(
                result,
                command,
                ok=step_ok,
                status="passed" if step_ok else "failed",
                runtime_error=runtime_error,
                executed=True,
                trace=log_entry.get("trace"),
            )
            result.messages.extend(step_result.messages)
            result.messages.extend(finish_result.messages)
            result.messages.extend(close_result.messages)
            if step_result.state:
                result.state = step_result.state
            if not step_ok:
                result.failed_index = command.index
                result.failed_command = command
                result.last_error = runtime_error
                message = (
                    f"Step {command.index + 1}/{len(commands)} "
                    f"({command.command_type} / {command.type_name} in {command.group}) failed: {runtime_error}"
                )
                result.runtime_errors.append(message)
                result.summary = message
                result.status = "failed"
                result.commands_executed = executed
                result.duration_seconds = time.time() - started
                self._abort_and_teardown(
                    result,
                    tracker=tracker,
                    reason=ABORT_REASON_RUNTIME_ERROR,
                    message=message,
                    close_method=close_method,
                )
                return result
            if resume_monitor is not None:
                resume_result = resume_monitor.after_user_prompt_command(
                    command_index=command.index,
                    command_type=command.type_name,
                )
                if resume_result is not None and resume_result.attempted and not resume_result.resumed:
                    reason = resume_result.reason or "resume_failed"
                    message = (
                        f"Step {command.index + 1}/{len(commands)} "
                        f"({command.type_name}): semi-automated ResumeRun did not complete ({reason})."
                    )
                    if resume_result.error:
                        message = f"{message} {resume_result.error}"
                    result.failed_index = command.index
                    result.failed_command = command
                    result.last_error = message
                    result.runtime_errors.append(message)
                    result.summary = message
                    result.status = "failed"
                    result.commands_executed = executed
                    result.duration_seconds = time.time() - started
                    self._teardown_method(close_method)
                    return result
            add_fields = extract_add_labware_fields(command)
            if add_fields is not None and add_validation.ok:
                record_successful_add_labware(
                    add_fields,
                    prior_labels=prior_add_labels,
                    prior_slots=prior_add_slots,
                )
                label_key = add_fields.label_key()
                if label_key:
                    deck_labels.add(label_key)
                slot = add_fields.slot_key()
                if slot[0]:
                    deck_slots[label_key] = slot
                    occupied_slots.add(slot)
            transfer_fields = extract_transfer_labware_fields(command)
            if transfer_fields is not None and transfer_validation.ok:
                record_successful_transfer(
                    transfer_fields,
                    deck_labels=deck_labels,
                    deck_slots=deck_slots,
                    occupied_slots=occupied_slots,
                )
            executed += 1

        self._teardown_method(close_method)

        if resume_monitor is not None and resume_monitor.events:
            result.messages.append(
                f"Semi-automated ResumeRun handled {len(resume_monitor.events)} prompt boundary(ies)."
            )

        result.ok = True
        result.status = "passed"
        result.commands_executed = executed
        result.summary = (
            f"Executed {executed} command(s) via ExecutionChannel.ExecuteCommand in simulation mode."
        )
        result.duration_seconds = time.time() - started
        return result

    def _abort_and_teardown(
        self,
        result: SteppedRunResult,
        *,
        tracker: SteppedExecutionTracker,
        reason: str,
        message: str,
        close_method: bool,
    ) -> None:
        """AbortExecution, then StopMethod/CloseMethod; record metadata (api-v2-009)."""
        abort = tracker.abort_context(reason, message)
        abort_fn = getattr(self.channel, "abort_execution", None)
        if callable(abort_fn):
            try:
                abort_fn()
                abort = replace(abort, abort_execution_called=True)
            except Exception as exc:
                abort = replace(abort, errors=(*abort.errors, f"AbortExecution failed: {exc}"))
        runtime = getattr(self.channel, "runtime_controller", None)
        native_channel = getattr(self.channel, "native_execution_channel", None)
        if runtime is not None or native_channel is not None:
            abort = perform_runtime_teardown(
                channel=native_channel,
                runtime=runtime,
                abort_context=abort,
                close_method=close_method,
            )
        elif close_method:
            self._teardown_method(close_method)
            abort = replace(
                abort,
                stop_method_called=True,
                close_method_called=True,
            )
        result.execution_abort = abort

    def _teardown_method(self, close_method: bool) -> None:
        """StopMethod before CloseMethod so hung runs do not block the next check (api-v2-066)."""
        if not close_method:
            return
        stop_fn = getattr(self.channel, "stop_method", None)
        close_fn = getattr(self.channel, "close_method", None)
        if callable(stop_fn) or callable(close_fn):
            if callable(stop_fn):
                stop_fn()
            if callable(close_fn):
                close_fn()
            return
        with MethodTeardown(
            self.channel,
            close_method=True,
            options=RunControlOptions(stop_before_close=True),
        ):
            pass


class RecordingExecutionChannel:
    """Test double that records ``ExecuteCommand`` calls without FluentControl."""

    name = "recording"

    def __init__(
        self,
        *,
        fail_at: int | None = None,
        fail_error: str = "simulated execution failure",
        unsupported_types: frozenset[str] = frozenset(),
    ):
        self.fail_at = fail_at
        self.fail_error = fail_error
        self.unsupported_types = unsupported_types
        self.prepare_calls: list[tuple[str, bool]] = []
        self.execute_calls: list[ICommand] = []
        self.finish_calls: list[ICommand] = []
        self.stopped = False
        self.aborted = False
        self.closed = False

    def prepare_method(self, method: str, *, simulation_mode: bool = True) -> ExecutionResult:
        self.prepare_calls.append((method, simulation_mode))
        return ExecutionResult(ok=True, state="prepared", messages=(f"prepared {method}",))

    def execute_command(self, command: ICommand) -> ExecutionResult:
        self.execute_calls.append(command)
        if command.type_name in self.unsupported_types:
            return ExecutionResult(
                ok=False,
                error=f"Unsupported runtime command type {command.type_name!r}.",
            )
        if self.fail_at is not None and command.index == self.fail_at:
            return ExecutionResult(ok=False, error=self.fail_error)
        return ExecutionResult(ok=True, messages=(f"executed {command.type_name}",))

    def finish_execution(self) -> ExecutionResult:
        if self.execute_calls:
            self.finish_calls.append(self.execute_calls[-1])
        return ExecutionResult(ok=True)

    def wait_for_channel_close(self, timeout_seconds: float) -> ExecutionResult:
        return ExecutionResult(ok=True)

    def stop_method(self) -> None:
        self.stopped = True

    def abort_execution(self) -> None:
        self.aborted = True

    def close_method(self) -> None:
        self.closed = True


class RegistryValidationExecutionChannel:
    """Offline channel that validates command types against the command registry."""

    name = "registry-validation"

    def prepare_method(self, method: str, *, simulation_mode: bool = True) -> ExecutionResult:
        return ExecutionResult(ok=True, state="simulation", messages=(f"prepared {method}",))

    def execute_command(self, command: ICommand) -> ExecutionResult:
        passthrough_error = validate_generic_passthrough_execute_xml(
            type_name=command.type_name,
            api_v2_type=command.api_v2_type,
            execute_xml=command.execute_xml,
            payload_xml=command.payload_xml,
            line_number=command.line_number,
        )
        if passthrough_error:
            return ExecutionResult(
                ok=False,
                error=f"GenericCommand passthrough failed: {passthrough_error}",
            )

        status = registry_command_support_status(command.type_name)
        if status is None and command.operation is None:
            return ExecutionResult(
                ok=False,
                error=(
                    f"Unknown FluentControl command type {command.type_name!r}; "
                    "not present in the packaged command registry."
                ),
            )
        label = command.api_v2_type or command.type_name
        if command.api_v2_type == "GenericCommand":
            return ExecutionResult(
                ok=True,
                messages=(f"validated GenericCommand.ToXML() passthrough for {command.type_name}",),
            )
        return ExecutionResult(ok=True, messages=(f"validated {label}",))

    def finish_execution(self) -> ExecutionResult:
        return ExecutionResult(ok=True, messages=("finish_execution",))

    def wait_for_channel_close(self, timeout_seconds: float) -> ExecutionResult:
        del timeout_seconds
        return ExecutionResult(ok=True, messages=("channel_closed",))

    def close_method(self) -> None:
        return None


def _command_type_name(command_object: ET.Element) -> str:
    for child in list(command_object):
        return _local_name(child.tag)
    object_type = str(command_object.attrib.get("Type") or "")
    return object_type.rsplit(".", 1)[-1]


def _direct_child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in list(parent):
        if _local_name(child.tag) == name:
            return child
    return None


def _direct_children(parent: ET.Element | None, name: str) -> list[ET.Element]:
    if parent is None:
        return []
    return [child for child in list(parent) if _local_name(child.tag) == name]


def _first_descendant(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in parent.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def _direct_text(parent: ET.Element | None, name: str) -> str | None:
    child = _direct_child(parent, name)
    if child is None or child.text is None:
        return None
    value = child.text.strip()
    return value or None


def _first_text(parent: ET.Element, name: str) -> str | None:
    node = _first_descendant(parent, name)
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _iter_local(parent: ET.Element, name: str):
    for child in parent.iter():
        if _local_name(child.tag) == name:
            yield child


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]
