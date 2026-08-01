"""FC-native command summaries for prompt_text_quality, prompt_coverage, and subroutine_load_review (api-v2-072, api-v2-073, api-v2-087)."""

from __future__ import annotations

import re
from typing import Any, Mapping

from ..subroutine_dependencies import clean_subroutine_reference
from ..policies.prompt_text import normalize_operator_prompt_text
from .commands import Subroutine, subroutine_from_ir_step

_SUBROUTINE_OPAQUE_PATH_RE = re.compile(
    r"""subroutine\s+(?:at\s+)?(?P<path>(?:'[^']*'|"[^"]*"|[^\s,]+))""",
    re.IGNORECASE,
)


def user_prompt_summary(text: str, *, strip_media: bool = True) -> str:
    """Canonical prompt text for prompt_text_quality and prompt_coverage matching (UserPrompt.ToString parity)."""
    prompt = str(text or "")
    if strip_media:
        return normalize_operator_prompt_text(prompt)
    return prompt.strip()


def ir_step_for_subroutine_call(
    ir: Mapping[str, Any] | None,
    call: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Locate the IR ``call_subroutine`` step backing a subroutine_load_review record."""
    if not isinstance(ir, dict):
        return None
    step_id = call.get("step_id")
    step_index = call.get("step_index")
    ref = clean_subroutine_reference(call.get("ref") or call.get("subroutine"))
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        if step_id and step.get("id") == step_id:
            return step
        if step_index is not None and step.get("index") == step_index:
            return step
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        if ref and clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine")) == ref:
            return step
    return None


def subroutine_call_summary(
    step: Mapping[str, Any] | None = None,
    *,
    path: str = "",
    execution_mode: str = "JoinSubroutine",
) -> str:
    """FC-native ``Subroutine.ToString()`` label for subroutine_load_review traces."""
    if step is not None:
        return subroutine_from_ir_step(step).to_string()
    clean_path = clean_subroutine_reference(path) or "?"
    return Subroutine(path=clean_path, execution_mode=execution_mode).to_string()


def subroutine_call_label(ir: Mapping[str, Any] | None, call: Mapping[str, Any]) -> str:
    """Build a Subroutine call label from an IR step when available."""
    step = ir_step_for_subroutine_call(ir, call)
    if step is not None:
        return subroutine_call_summary(step)
    return subroutine_call_summary(path=str(call.get("ref") or call.get("subroutine") or "?"))


def enrich_subroutine_load_review_record(
    record: Mapping[str, Any],
    ir: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Attach ``call_label`` to a subroutine_load_review finding record (api-v2-087)."""
    enriched = dict(record)
    if not enriched.get("call_label"):
        enriched["call_label"] = subroutine_call_label(ir, record)
    return enriched


def subroutine_path_from_opaque_message(message: str) -> str:
    """Best-effort subroutine path extraction from simulator opaque-call text."""
    match = _SUBROUTINE_OPAQUE_PATH_RE.search(str(message or ""))
    if not match:
        return ""
    return clean_subroutine_reference(match.group("path").strip("'"))


def enrich_simulation_subroutine_traces(
    data: dict[str, Any],
    ir: Mapping[str, Any] | None = None,
) -> None:
    """Attach ``call_label`` to opaque subroutine simulation steps/events for subroutine_load_review (api-v2-087)."""
    if not isinstance(data, dict):
        return

    def _label_for_record(record: dict[str, Any]) -> str | None:
        command_id = str(record.get("command_id") or record.get("step_type") or "")
        if command_id not in {"SubRoutineStatement", "SubRoutineStep"}:
            return None
        step = ir_step_for_subroutine_call(ir, record)
        if step is not None:
            return subroutine_call_summary(step)
        path = subroutine_path_from_opaque_message(str(record.get("message") or ""))
        if path:
            return subroutine_call_summary(path=path)
        return None

    for key in ("opaque_events", "steps"):
        items = data.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            label = _label_for_record(item)
            if label:
                item["call_label"] = label

    failure = data.get("failure")
    if isinstance(failure, dict):
        command_id = str(failure.get("command_id") or failure.get("step_type") or "")
        if command_id in {"SubRoutineStatement", "SubRoutineStep"}:
            label = _label_for_record(failure)
            if label:
                details = dict(failure.get("details") or {})
                details["call_label"] = label
                failure["details"] = details


def transfer_labware_summary(params: Mapping[str, Any] | None) -> str:
    """Human-readable TransferLabware summary for logs and XSCR diffs."""
    params = params or {}
    labware = str(params.get("labware") or params.get("labware_name") or "?")
    source_loc = str(params.get("from_location") or params.get("source_location") or "?")
    source_site = params.get("from_site", params.get("source_site", "?"))
    dest_loc = str(params.get("to_location") or params.get("destination_location") or "?")
    dest_site = params.get("to_site", params.get("destination_site", "?"))
    onto = params.get("onto_labware") or params.get("onto")
    onto_text = f" onto {onto}" if onto else ""
    return (
        f"TransferLabware {labware}: {source_loc}[{source_site}] -> "
        f"{dest_loc}[{dest_site}]{onto_text}"
    )
