"""Parse FluentControl audit import events for conservative log correlation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Iterable


AUDIT_IMPORT_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?);.*?"
    r"Items imported from '(?P<archive>[^']+)': (?P<items>.+?)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AuditImportEvent:
    timestamp: str
    archive: str
    script: str


def read_audit_import_events(paths: Iterable[Path]) -> list[AuditImportEvent]:
    """Return imports with one unambiguous likely main-script name per event."""
    events: list[AuditImportEvent] = []
    for path in paths:
        try:
            lines = Path(path).read_text(encoding="utf-8-sig", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = AUDIT_IMPORT_RE.match(line)
            if not match:
                continue
            script = _primary_script(match.group("items").split(","))
            if script:
                events.append(
                    AuditImportEvent(
                        timestamp=match.group("timestamp"),
                        archive=match.group("archive"),
                        script=script,
                    )
                )
    return sorted(events, key=lambda event: _timestamp_to_epoch(event.timestamp))


def import_for_error(
    timestamp: str,
    events: Iterable[AuditImportEvent],
    *,
    max_age_seconds: float = 15 * 60,
) -> AuditImportEvent | None:
    """Return the most recent audit import preceding an error within the causal window."""
    error_time = _timestamp_to_epoch(timestamp)
    if error_time <= 0:
        return None
    candidates = [
        event
        for event in events
        if 0 <= error_time - _timestamp_to_epoch(event.timestamp) <= max_age_seconds
    ]
    return max(candidates, key=lambda event: _timestamp_to_epoch(event.timestamp), default=None)


def _primary_script(items: Iterable[str]) -> str:
    candidates: list[tuple[int, str]] = []
    for raw_item in items:
        item = raw_item.strip().rstrip(";").strip()
        if not item or "\\" in item or item.lower().endswith((".png", ".gif", ".jpg", ".vb", ".exe", ".dll")):
            continue
        lower = item.lower()
        score = 0
        if any(token in lower for token in ("script", "corpus", "verification", "method")):
            score += 100
        if lower.startswith("sub_") or lower in {"getfingers"}:
            score -= 100
        if "worktable" in lower or "_wt_" in lower:
            score -= 80
        if score > 0:
            candidates.append((score, item))
    if not candidates:
        return ""
    best_score = max(score for score, _ in candidates)
    best = sorted({item for score, item in candidates if score == best_score})
    return best[0] if len(best) == 1 else ""


def _timestamp_to_epoch(value: str) -> float:
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S.%f").timestamp()
    except ValueError:
        try:
            return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").timestamp()
        except ValueError:
            return 0.0
