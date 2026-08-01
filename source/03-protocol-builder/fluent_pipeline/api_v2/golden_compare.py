"""Golden XML compare: compiled XSCR vs FC-native ``ICommand.ToXML()``."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from .. import xml_compat as ET

from .commands import API_V2_COMMAND_TYPES, XSCR_COMMAND_ID_TO_API_V2, command_from_xscr_object, command_to_xml
from .native_provider import NativeToXmlProvider
from .types import ApiV2ValidationError
from .xml_compare import compare_command_xml, extract_command_objects_from_xscr


def compare_xscr_commands_to_native_xml(
    xscr_path: Path | None = None,
    *,
    xscr_text: str | None = None,
    native_provider: NativeToXmlProvider | None = None,
) -> list[dict[str, Any]]:
    """Compare each compiled command Object against re-serialized ``ICommand.ToXML()``."""
    text = xscr_text
    if text is None and xscr_path is not None and xscr_path.exists():
        try:
            text = xscr_path.read_text(encoding="utf-8-sig")
        except OSError:
            text = ""
    records = extract_command_objects_from_xscr(text or "")
    provider = native_provider or NativeToXmlProvider()
    findings: list[dict[str, Any]] = []

    for index, record in enumerate(records):
        command_id = record.get("command_id") or ""
        compiled_xml = record.get("xml") or ""
        try:
            element = ET.fromstring(compiled_xml)
        except ET.ParseError as exc:
            findings.append(
                {
                    "index": index,
                    "command_id": command_id,
                    "status": "error",
                    "reason": "compiled_xml_parse_error",
                    "message": str(exc),
                }
            )
            continue

        command = command_from_xscr_object(element, command_id=command_id)
        if command is None:
            findings.append(
                {
                    "index": index,
                    "command_id": command_id,
                    "status": "skipped",
                    "reason": "unmapped_command",
                }
            )
            continue

        api_v2_type = XSCR_COMMAND_ID_TO_API_V2.get(command_id or "") or type(command).__name__
        try:
            native_xml = provider.to_xml(command)
            if not native_xml:
                native_xml = command_to_xml(command, validate=False)
        except ApiV2ValidationError as exc:
            findings.append(
                {
                    "index": index,
                    "command_id": command_id,
                    "command_type": api_v2_type,
                    "status": "error",
                    "reason": "command_validation_error",
                    "message": str(exc),
                }
            )
            continue

        result = compare_command_xml(compiled_xml, native_xml)
        if result.equal:
            findings.append(
                {
                    "index": index,
                    "command_id": command_id,
                    "command_type": api_v2_type,
                    "status": "matched",
                    "provider": provider.name,
                }
            )
            continue

        findings.append(
            {
                "index": index,
                "command_id": command_id,
                "command_type": api_v2_type,
                "status": "mismatch",
                "provider": provider.name,
                "diff_hint": result.diff_hint,
                "command_summary": _command_summary(command),
                "compiled_preview": compiled_xml[:240],
                "native_preview": native_xml[:240],
            }
        )
    return findings


def _command_summary(command: Any) -> str:
    """Human-readable ``ICommand.ToString()`` for Gate 11 mismatch logs (api-v2-072)."""
    to_string = getattr(command, "to_string", None)
    if callable(to_string):
        try:
            return str(to_string())
        except Exception:
            pass
    return type(command).__name__


def golden_compare_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Roll up golden-compare findings for validation gate details."""
    compared = [item for item in findings if item.get("status") in {"matched", "mismatch"}]
    matched = [item for item in findings if item.get("status") == "matched"]
    mismatches = [item for item in findings if item.get("status") == "mismatch"]
    skipped = [item for item in findings if item.get("status") == "skipped"]
    errors = [item for item in findings if item.get("status") == "error"]
    typed = [
        item
        for item in compared
        if item.get("command_type") in API_V2_COMMAND_TYPES
        or XSCR_COMMAND_ID_TO_API_V2.get(str(item.get("command_id") or ""))
    ]
    if not findings:
        status = "skipped"
        summary = "No compiled command objects found for FC-native XML compare."
    elif mismatches or errors:
        status = "needs_review"
        summary = (
            f"FC-native XML compare: {len(mismatches)} mismatch(es), "
            f"{len(errors)} error(s) across {len(compared)} compared command(s)."
        )
    elif compared and len(matched) == len(compared):
        status = "passed"
        summary = f"FC-native XML compare matched for all {len(matched)} typed command(s)."
    else:
        status = "needs_review"
        summary = (
            f"FC-native XML compare partial: {len(matched)} matched, "
            f"{len(skipped)} skipped, {len(compared)} compared."
        )
    return {
        "status": status,
        "summary": summary,
        "issue": "api-v2-005",
        "compared_count": len(compared),
        "matched_count": len(matched),
        "mismatch_count": len(mismatches),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "typed_command_count": len(typed),
        "findings": findings[:20],
    }
