"""Audit and repair compiled TouchTools prompt media references."""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from .checksums import entry_checksum_state, recompute_checksum_bytes
from .runner import PipelineError


def prompt_media_step_records(ir: dict[str, Any]) -> list[dict[str, str]]:
    """Map each visual/audio media file to its media and operator step labels."""
    records: list[dict[str, str]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "prompt_user":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        prompt = str(params.get("prompt") or "").strip()
        operator_match = re.match(r"(\d+/\d+)\)", prompt)
        operator_step = operator_match.group(1) if operator_match else ""
        for slot in params.get("media_placeholders") or []:
            if not isinstance(slot, dict):
                continue
            path = str(slot.get("path") or "").strip()
            if path:
                records.append(
                    _media_step_record(
                        path,
                        kind=str(slot.get("kind") or "visual"),
                        operator_step=operator_step,
                        prompt=prompt,
                        step_id=str(step.get("id") or ""),
                    )
                )
        sound_file = str(params.get("sound_file") or "").strip()
        if sound_file:
            records.append(
                _media_step_record(
                    sound_file,
                    kind="audio",
                    operator_step=operator_step,
                    prompt=prompt,
                    step_id=str(step.get("id") or ""),
                )
            )
    return records


def _media_step_record(
    path: str,
    *,
    kind: str,
    operator_step: str,
    prompt: str,
    step_id: str,
) -> dict[str, str]:
    basename = PureWindowsPath(path.replace("/", "\\")).name
    match = re.match(r"(step[_-]?\d+)", basename, flags=re.IGNORECASE)
    media_step = (
        match.group(1).replace("_", "").replace("-", "").casefold()
        if match
        else ""
    )
    return {
        "file": basename,
        "kind": kind,
        "media_step": media_step,
        "operator_step": operator_step,
        "operator_prompt": prompt,
        "ir_step_id": step_id,
    }


def ensure_compiled_prompt_media_references(
    xscr_path: Path,
    ir: dict[str, Any],
    *,
    recompute_checksum: bool = True,
) -> list[dict[str, str]]:
    """Wire IR audio slots into matching compiled RUP Standard prompts."""
    data = xscr_path.read_bytes()
    text = data.decode("utf-8-sig", errors="strict")
    protocol = ir.get("protocol") if isinstance(ir.get("protocol"), dict) else {}
    protocol_name = str(protocol.get("name") or "").strip()
    if not protocol_name:
        return []

    fixups: list[dict[str, str]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "prompt_user":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        prompt = str(params.get("prompt") or "").strip()
        sound_file = str(params.get("sound_file") or "").strip()
        prompt_kind = str(params.get("rup_kind") or step.get("command_id") or "").strip().lower()
        if prompt_kind in {
            "worktable",
            "rupworktablestatement",
        }:
            continue
        has_visual_media = bool(str(params.get("image_path") or "").strip() or params.get("media_placeholders"))
        if prompt_kind not in {"standard", "rupstandardstatement"} and not has_visual_media:
            continue
        if not prompt or not sound_file:
            continue
        basename = PureWindowsPath(sound_file.replace("/", "\\")).name
        absolute_sound = (
            "C:\\ProgramData\\Tecan\\VisionX\\TouchToolsData\\Images\\"
            f"{protocol_name}_media\\{basename}"
        )
        prompt_xml = xml_escape(prompt)
        block_span = _rup_standard_object_span_for_prompt(text, prompt_xml)
        if block_span is None:
            raise PipelineError(
                f"Could not find compiled RUP Standard prompt for audio slot {sound_file}: {prompt}"
            )
        block_start, block_end = block_span
        block = text[block_start:block_end]
        current = re.search(
            r"<SelectedSoundPath\b[^>]*>(.*?)</SelectedSoundPath>|<SelectedSoundPath\s*/>",
            block,
            flags=re.DOTALL,
        )
        if current is None:
            raise PipelineError(f"Compiled RUP Standard prompt has no SelectedSoundPath field: {prompt}")
        current_value = current.group(1).strip() if current.group(1) is not None else ""
        if PureWindowsPath(current_value).name.casefold() == basename.casefold():
            continue
        replacement = f"<SelectedSoundPath>{xml_escape(absolute_sound)}</SelectedSoundPath>"
        updated_block = re.sub(
            r"<SelectedSoundPath\b[^>]*>.*?</SelectedSoundPath>|<SelectedSoundPath\s*/>",
            lambda _match: replacement,
            block,
            count=1,
            flags=re.DOTALL,
        )
        text = text[:block_start] + updated_block + text[block_end:]
        fixups.append(
            {
                "step_id": str(step.get("id") or ""),
                "prompt": prompt,
                "sound_file": basename,
                "sound_path": absolute_sound,
            }
        )

    if not fixups:
        return []
    if not recompute_checksum:
        xscr_path.write_text(text, encoding="utf-8")
        return fixups

    encoding = "utf-8-sig" if data.startswith(b"\xef\xbb\xbf") else "utf-8"
    rewritten = recompute_checksum_bytes(text.encode(encoding))
    if rewritten is None or entry_checksum_state(rewritten) != "valid":
        raise PipelineError("Prompt audio fixup could not produce a valid XSCR checksum.")
    xscr_path.write_bytes(rewritten)
    return fixups


def _rup_standard_object_span_for_prompt(text: str, prompt_xml: str) -> tuple[int, int] | None:
    """Return the exact RUP Standard ``Object`` containing ``prompt_xml``."""
    message = f"<MessageText>{prompt_xml}</MessageText>"
    message_start = text.find(message)
    if message_start < 0:
        return None

    opening_pattern = re.compile(
        r'<Object\b[^>]*Type="[^"]*RUPStandardStatement"[^>]*>',
        re.DOTALL,
    )
    openings = [match for match in opening_pattern.finditer(text, 0, message_start)]
    if not openings:
        return None

    object_tokens = re.compile(r"<Object\b[^>]*>|</Object>", re.DOTALL)
    for opening in reversed(openings):
        depth = 0
        for token in object_tokens.finditer(text, opening.start()):
            if token.group(0).startswith("</"):
                depth -= 1
                if depth == 0:
                    end = token.end()
                    return (opening.start(), end) if message_start < end else None
            else:
                depth += 1
    return None
