"""Source-backed subroutine execution-mode and target semantics."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class SubroutineExecutionMode(str, Enum):
    """Execution modes observed in the verified XSCR corpus."""

    SYNCHRONOUS = "Synchronous"
    ASYNCHRONOUS = "Asynchronous"
    JOIN_SUBROUTINE = "JoinSubroutine"
    FIRE_AND_FORGET = "FireAndForget"


KNOWN_SUBROUTINE_EXECUTION_MODES = frozenset(item.value for item in SubroutineExecutionMode)


def normalize_subroutine_execution_mode(
    value: Any,
    *,
    default: str = SubroutineExecutionMode.SYNCHRONOUS.value,
    source_preserved: bool = False,
) -> str:
    """Normalize a mode without discarding an unknown source value.

    Authored values are strict.  A decompiler may pass ``source_preserved`` so
    a future vendor mode remains visible instead of being silently rewritten.
    """
    mode = value.value if isinstance(value, SubroutineExecutionMode) else str(value or "").strip()
    if not mode:
        return default
    if mode in KNOWN_SUBROUTINE_EXECUTION_MODES or source_preserved:
        return mode
    raise ValueError(f"Unknown subroutine execution mode: {mode!r}")


def classify_subroutine_target(value: Any) -> dict[str, str | None]:
    """Classify only evidence-backed targets; never infer a dynamic target.

    Mapping values carrying an explicit expression/source marker are reported
    as expressions.  Plain path-like strings are static only when they carry a
    folder separator; everything else remains unresolved.
    """
    if isinstance(value, Mapping):
        source = value.get("source") or value.get("expression") or value.get("value")
        if source not in (None, ""):
            return {"kind": "expression", "value": str(source), "source": str(source)}
        return {"kind": "unresolved", "value": None, "source": None}
    text = str(value or "").strip().strip('"')
    if not text:
        return {"kind": "unresolved", "value": None, "source": text}
    if "\\" in text or "/" in text:
        return {"kind": "static", "value": text, "source": text}
    return {"kind": "unresolved", "value": text, "source": text}
