"""Shared helpers for Tecan XML readers."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any, Iterable
import re

from tecan_common import xml_compat as ET

from .command_registry import registry_command_family

TECAN_EXTENSIONS = {
    ".zeia",
    ".xscr",
    ".xcmp",
    ".xwsp",
    ".xlqc",
    ".xlcp",
    ".xsit",
    ".gwl",
    ".xml",
}


def read_text(path: str | Path) -> str:
    p = Path(path)
    data = p.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def parse_xml_text(
    text: str,
    *,
    max_bytes: int = ET.MAX_XML_BYTES,
    max_depth: int = ET.MAX_XML_DEPTH,
) -> ET.Element:
    return ET.fromstring(text, max_bytes=max_bytes, max_depth=max_depth)


def extension_counts(names: Iterable[str]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for name in names:
        suffix = Path(name).suffix.lower() or "<none>"
        counts[suffix] += 1
    return dict(sorted(counts.items()))


def command_short_name(type_name: str) -> str:
    short = type_name.split(".")[-1]
    short = re.sub(r"DataV\d+$", "", short)
    short = re.sub(r"CommandDataV\d+$", "Command", short)
    short = re.sub(r"ScriptCommandDataV\d+$", "Command", short)
    return short


def command_family(type_name: str) -> str:
    registry_family = registry_command_family(type_name)
    if registry_family:
        return registry_family
    t = type_name.lower()
    if "touchtools" in t or ".rup." in t:
        return "TouchTools/RUP"
    if "liha" in t or "fca" in t:
        return "LiHa/FCA"
    if "mca384" in t:
        return "MCA384"
    if "mca96" in t or ".mca." in t:
        return "MCA96"
    if "rga" in t or "cga" in t or "gripper" in t:
        return "RGA/CGA"
    if "worklist" in t:
        return "Worklist"
    if "worktable" in t or "addlabware" in t or "removelabware" in t:
        return "Worktable"
    if "variable" in t:
        return "Variables"
    if (
        "loopgroup" in t
        or "conditionalgroup" in t
        or "alternategroup" in t
        or "scriptgroup" in t
    ):
        return "Control flow"
    if (
        "commentstatement" in t
        or "userprompt" in t
        or "delay" in t
        or "waitstatement" in t
        or "timer" in t
        or "leavestatement" in t
    ):
        return "User/script flow"
    if "subroutine" in t:
        return "Subroutine"
    if "applicationdriver" in t or "executeapplication" in t or "executevbscript" in t:
        return "External application"
    if "smartcommands" in t:
        return "Smart command"
    if t.startswith("system."):
        return "Internal value"
    return "Other"


def compact_list(values: Iterable[str], *, limit: int = 20) -> list[str]:
    out: list[str] = []
    for value in values:
        if value and value not in out:
            out.append(value)
        if len(out) >= limit:
            break
    return out


def to_jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Counter):
        return dict(value)
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value
