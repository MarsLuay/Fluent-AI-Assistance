"""Subroutine.Validate() live resolution for Gate 27 and subroutine_load_review (api-v2-041).

After ZEIA/XSCR import and method prepare, resolve each ``call_subroutine`` against
the target FluentControl worktable script inventory. Offline manifest checks for subroutine_load_review
cannot see scripts that exist only on the target system—or miss ones that do not.

When a native VisionX API V2 session is available, build
``Tecan.VisionX.API.V2.Commands.Subroutine`` and call ``Validate()`` before
``ExecutionChannel.ExecuteCommand``; otherwise apply offline field checks plus
inventory resolution when ``TECAN_FLUENT_RUNTIME_SCRIPT_INVENTORY`` is set.
"""

from __future__ import annotations

import json
import os
from . import xml_compat as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .subroutine_dependencies import (
    clean_subroutine_reference,
    find_subroutine_record,
    norm_subroutine_key,
    subroutine_calls_from_ir,
)
from .api_v2.validate_runtime import (
    runtime_error_for_validate_failure as _shared_runtime_error_for_validate_failure,
)


API_V2_ISSUE_ID = "api-v2-041"
API_V2_METHOD = "Subroutine.Validate()"
SUBROUTINE_TYPE_NAMES = frozenset({"SubRoutineStatement"})


@dataclass(frozen=True)
class SubroutineValidateResult:
    ok: bool
    message: str = ""
    reason: str = ""
    subroutine: str = ""
    source: str = "offline"
    api_v2_method: str = API_V2_METHOD
    api_v2_issue: str = API_V2_ISSUE_ID
    alternatives: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.ok,
            "source": self.source,
            "api_v2_method": self.api_v2_method,
            "api_v2_issue": self.api_v2_issue,
        }
        if self.message:
            payload["message"] = self.message
        if self.reason:
            payload["reason"] = self.reason
        if self.subroutine:
            payload["subroutine"] = self.subroutine
        if self.alternatives:
            payload["alternatives"] = list(self.alternatives)
        return payload


@dataclass
class SubroutineInventoryReport:
    """Batch resolution of all subroutine calls against a runtime worktable inventory."""

    ok: bool
    provider: str
    call_count: int = 0
    missing: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: list[dict[str, Any]] = field(default_factory=list)
    resolved: list[dict[str, Any]] = field(default_factory=list)
    field_failures: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    needs_review: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "api_v2_issue": API_V2_ISSUE_ID,
            "api_v2_method": API_V2_METHOD,
            "ok": self.ok,
            "provider": self.provider,
            "call_count": self.call_count,
            "missing": self.missing,
            "ambiguous": self.ambiguous,
            "resolved": self.resolved,
            "field_failures": self.field_failures,
            "summary": self.summary,
            "needs_review": self.needs_review,
        }


class SubroutineCommandLike(Protocol):
    type_name: str
    index: int
    group: str
    payload_xml: str


def is_subroutine_command(command: SubroutineCommandLike) -> bool:
    return str(command.type_name or "") in SUBROUTINE_TYPE_NAMES


def extract_subroutine_path(command: SubroutineCommandLike) -> str:
    payload = str(command.payload_xml or "").strip()
    if not payload:
        return ""
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        return ""
    for node in root.iter():
        if _local_name(node.tag) == "SubRoutine":
            return clean_subroutine_reference(node.text)
    return ""


def subroutine_model_from_command(command: SubroutineCommandLike) -> tuple[str, str]:
    path = extract_subroutine_path(command)
    execution_mode = "JoinSubroutine"
    payload = str(command.payload_xml or "").strip()
    if payload:
        try:
            root = ET.fromstring(payload)
            mode_node = _find_descendant(root, "ExecutionMode")
            if mode_node is not None and (mode_node.text or "").strip():
                execution_mode = (mode_node.text or "").strip()
        except ET.ParseError:
            pass
    return path, execution_mode


def validate_subroutine_fields(path: str, execution_mode: str = "JoinSubroutine") -> SubroutineValidateResult:
    clean = clean_subroutine_reference(path)
    if not clean:
        return SubroutineValidateResult(
            ok=False,
            reason="field_validate_failed",
            message="path is required.",
            subroutine=path,
            source="offline_fields",
        )
    if "\\" not in clean and "/" not in clean:
        return SubroutineValidateResult(
            ok=False,
            reason="field_validate_failed",
            message="subroutine path should use a folder prefix (for example Folder\\SUB_Name).",
            subroutine=clean,
            source="offline_fields",
        )
    mode = str(execution_mode or "").strip()
    allowed = {"JoinSubroutine", "Synchronous", "Asynchronous", "FireAndForget"}
    if mode and mode not in allowed:
        return SubroutineValidateResult(
            ok=False,
            reason="field_validate_failed",
            message=f"execution_mode {mode!r} is not a known Subroutine mode.",
            subroutine=clean,
            source="offline_fields",
        )
    return SubroutineValidateResult(ok=True, subroutine=clean, source="offline_fields")


def load_runtime_script_inventory(path: Path | None = None) -> list[dict[str, Any]]:
    """Load target worktable script inventory from env or an explicit JSON file."""
    raw_path = path or _inventory_path_from_env()
    if raw_path is None or not raw_path.exists():
        return []
    try:
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(payload, dict):
        scripts = payload.get("scripts")
        if isinstance(scripts, list):
            return [item for item in scripts if isinstance(item, dict)]
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def resolve_subroutine_against_inventory(
    ref: str,
    inventory: Sequence[Mapping[str, Any]],
    *,
    source_manifest: Mapping[str, Any] | None = None,
) -> SubroutineValidateResult:
    """Resolve one subroutine path against a live worktable script inventory."""
    clean = clean_subroutine_reference(ref)
    if not clean:
        return SubroutineValidateResult(
            ok=False,
            reason="empty_subroutine",
            message="Subroutine path is empty.",
            subroutine=ref,
            source="inventory",
        )
    manifest = {"scripts": list(inventory), **dict(source_manifest or {})}
    match, alternatives = find_subroutine_record(manifest, list(inventory), clean, {})
    if match is None:
        return SubroutineValidateResult(
            ok=False,
            reason="subroutine_missing_on_worktable",
            message=(
                f"Subroutine {clean!r} does not resolve in the loaded worktable script inventory "
                "(the subroutine_load_review manifest can still pass while the target system lacks the script)."
            ),
            subroutine=clean,
            source="inventory",
        )
    alt_names = tuple(
        str(item.get("object_name") or item.get("qualified_name") or "")
        for item in alternatives
        if isinstance(item, dict)
    )
    if alternatives:
        return SubroutineValidateResult(
            ok=True,
            reason="subroutine_ambiguous_on_worktable",
            message=(
                f"Subroutine {clean!r} matched multiple scripts on the loaded worktable; "
                f"confirm the intended target ({alt_names})."
            ),
            subroutine=clean,
            source="inventory",
            alternatives=alt_names,
        )
    return SubroutineValidateResult(
        ok=True,
        subroutine=clean,
        source="inventory",
    )


def validate_subroutine_offline(
    command: SubroutineCommandLike,
    *,
    runtime_inventory: Sequence[Mapping[str, Any]] | None = None,
) -> SubroutineValidateResult:
    if not is_subroutine_command(command):
        return SubroutineValidateResult(ok=True, source="skipped_non_subroutine")

    model = subroutine_model_from_command(command)
    field_result = validate_subroutine_fields(model[0], model[1])
    if not field_result.ok:
        field_result = SubroutineValidateResult(
            ok=False,
            reason=field_result.reason,
            message=(
                f"Subroutine at step {command.index + 1} ({command.type_name} in {command.group}): "
                f"{field_result.message}"
            ),
            subroutine=field_result.subroutine,
            source=field_result.source,
        )
        return field_result

    inventory = list(runtime_inventory or ())
    if not inventory:
        inventory = load_runtime_script_inventory()
    if not inventory:
        return SubroutineValidateResult(
            ok=True,
            source="skipped_no_inventory",
            message=(
                "Subroutine field checks passed; runtime worktable inventory was not available "
                "for live resolution (set TECAN_FLUENT_RUNTIME_SCRIPT_INVENTORY)."
            ),
            subroutine=model[0],
        )

    inventory_result = resolve_subroutine_against_inventory(model[0], inventory)
    if not inventory_result.ok:
        return SubroutineValidateResult(
            ok=False,
            reason=inventory_result.reason,
            message=(
                f"Subroutine at step {command.index + 1} ({command.type_name} in {command.group}): "
                f"{inventory_result.message}"
            ),
            subroutine=inventory_result.subroutine,
            source=inventory_result.source,
            alternatives=inventory_result.alternatives,
        )
    if inventory_result.reason == "subroutine_ambiguous_on_worktable":
        return SubroutineValidateResult(
            ok=True,
            reason=inventory_result.reason,
            message=(
                f"Subroutine at step {command.index + 1} ({command.type_name} in {command.group}): "
                f"{inventory_result.message}"
            ),
            subroutine=inventory_result.subroutine,
            source=inventory_result.source,
            alternatives=inventory_result.alternatives,
        )
    return SubroutineValidateResult(ok=True, subroutine=model[0], source="inventory")


def validate_subroutine_before_execute(
    command: SubroutineCommandLike,
    *,
    native_validate: Callable[[], None] | None = None,
    runtime_inventory: Sequence[Mapping[str, Any]] | None = None,
) -> SubroutineValidateResult:
    """Validate a Subroutine command before ``ExecuteCommand`` (native first, then offline)."""
    if not is_subroutine_command(command):
        return SubroutineValidateResult(ok=True, source="skipped_non_subroutine")

    if native_validate is not None:
        try:
            native_validate()
        except Exception as exc:
            offline = validate_subroutine_offline(command, runtime_inventory=runtime_inventory)
            message = str(exc).strip() or "Subroutine.Validate() failed."
            if not offline.ok:
                message = f"{message}; offline check: {offline.message}"
            return SubroutineValidateResult(
                ok=False,
                reason="native_validate_failed",
                message=message,
                subroutine=offline.subroutine or extract_subroutine_path(command),
                source="native",
            )
        inventory_check = validate_subroutine_offline(command, runtime_inventory=runtime_inventory)
        if not inventory_check.ok:
            return inventory_check
        if inventory_check.reason == "subroutine_ambiguous_on_worktable":
            return inventory_check
        return SubroutineValidateResult(
            ok=True,
            subroutine=inventory_check.subroutine or extract_subroutine_path(command),
            source="native",
        )

    return validate_subroutine_offline(command, runtime_inventory=runtime_inventory)


def validate_subroutines_after_load(
    *,
    ir: Mapping[str, Any] | None = None,
    commands: Sequence[SubroutineCommandLike] | None = None,
    runtime_inventory: Sequence[Mapping[str, Any]] | None = None,
    provider: str = "offline",
) -> SubroutineInventoryReport:
    """Batch live resolution after ZEIA/XSCR import (Gate 27 post-load hook for subroutine_load_review)."""
    refs: list[tuple[str, int | None]] = []
    if commands:
        for command in commands:
            if not is_subroutine_command(command):
                continue
            path = extract_subroutine_path(command)
            if path:
                refs.append((path, command.index))
    elif ir is not None:
        for call in subroutine_calls_from_ir(dict(ir)):
            ref = str(call.get("ref") or "")
            if ref:
                refs.append((ref, call.get("step_index")))

    inventory = list(runtime_inventory or ())
    if not inventory:
        inventory = load_runtime_script_inventory()

    missing: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    resolved: list[dict[str, Any]] = []
    field_failures: list[dict[str, Any]] = []

    seen: set[str] = set()
    for ref, step_index in refs:
        key = norm_subroutine_key(ref)
        if key in seen:
            continue
        seen.add(key)

        field_result = validate_subroutine_fields(ref)
        if not field_result.ok:
            field_failures.append(
                {
                    "subroutine": ref,
                    "step_index": step_index,
                    **field_result.as_dict(),
                }
            )
            continue

        if not inventory:
            continue

        result = resolve_subroutine_against_inventory(ref, inventory)
        record = {"subroutine": ref, "step_index": step_index, **result.as_dict()}
        if not result.ok:
            missing.append(record)
        elif result.reason == "subroutine_ambiguous_on_worktable":
            ambiguous.append(record)
        else:
            resolved.append(record)

    call_count = len(seen)
    needs_review = bool(ambiguous) or (bool(refs) and not inventory)
    ok = not missing and not field_failures
    if not refs:
        summary = "Protocol calls no subroutines; nothing to validate on the loaded worktable."
    elif not inventory:
        summary = (
            f"{call_count} subroutine call(s) passed field checks; runtime worktable inventory "
            "was not available for live resolution (needs review)."
        )
    elif missing:
        summary = (
            f"{len(missing)} subroutine call(s) failed live worktable resolution after import."
        )
    elif ambiguous:
        summary = (
            f"All {call_count} subroutine call(s) resolved, but {len(ambiguous)} need review "
            "because the name is ambiguous on the loaded worktable."
        )
    else:
        summary = f"All {call_count} subroutine call(s) resolved on the loaded worktable."

    return SubroutineInventoryReport(
        ok=ok,
        provider=provider,
        call_count=call_count,
        missing=missing,
        ambiguous=ambiguous,
        resolved=resolved,
        field_failures=field_failures,
        summary=summary,
        needs_review=needs_review,
    )


def runtime_error_for_validate_failure(
    result: SubroutineValidateResult,
    command: SubroutineCommandLike,
) -> str:
    return _shared_runtime_error_for_validate_failure(result, command, kind="Subroutine")


def _inventory_path_from_env() -> Path | None:
    raw = os.environ.get("TECAN_FLUENT_RUNTIME_SCRIPT_INVENTORY", "").strip()
    return Path(raw) if raw else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find_descendant(root: ET.Element, name: str) -> ET.Element | None:
    for node in root.iter():
        if _local_name(node.tag) == name:
            return node
    return None
