"""Deterministic offline analysis for asynchronous subroutine lifecycles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from ..ir.subroutine_semantics import KNOWN_SUBROUTINE_EXECUTION_MODES


@dataclass(frozen=True)
class LifecycleFinding:
    """Actionable lifecycle issue that does not claim runtime or hardware state."""

    code: str
    message: str
    step_index: int
    target: str | None = None
    candidates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "step_index": self.step_index,
            "target": self.target,
            "candidates": list(self.candidates),
        }


@dataclass
class SubroutineLifecycleReport:
    """Logical epochs for launches, joins, and detached invocations."""

    events: list[dict[str, Any]] = field(default_factory=list)
    findings: list[LifecycleFinding] = field(default_factory=list)
    outstanding: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.findings:
            return "review"
        if self.outstanding:
            return "incomplete"
        return "passed"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "events": [dict(event) for event in self.events],
            "findings": [finding.to_dict() for finding in self.findings],
            "outstanding": list(self.outstanding),
        }


def analyze_subroutine_lifecycle(steps: Iterable[Any]) -> SubroutineLifecycleReport:
    """Analyze a reachable linear path without launching threads or real time.

    ``JoinSubroutine`` only consumes an existing asynchronous invocation; it
    never creates a fresh copy.  Control-flow containers are intentionally not
    expanded here, because an unverified loop/branch outcome must remain a
    reviewable finding rather than an invented schedule.
    """
    report = SubroutineLifecycleReport()
    outstanding: dict[str, list[str]] = {}
    detached_targets: set[str] = set()
    invocation_number = 0

    for index, step in enumerate(_reachable_steps(steps)):
        if not _is_subroutine(step):
            continue
        target = str(getattr(step, "subroutine", "") or "").strip().strip('"') or None
        mode = str(getattr(step, "execution_mode", "") or "").strip()
        if mode not in KNOWN_SUBROUTINE_EXECUTION_MODES:
            report.findings.append(
                LifecycleFinding(
                    code="unknown_execution_mode",
                    message="Subroutine lifecycle mode is not verified; review before simulation.",
                    step_index=index,
                    target=target,
                )
            )
            continue

        if mode == "Asynchronous":
            invocation_number += 1
            invocation_id = f"{target or '<unresolved>'}#{invocation_number}"
            outstanding.setdefault(target or "", []).append(invocation_id)
            report.events.append(
                {
                    "step_index": index,
                    "action": "launch",
                    "mode": mode,
                    "target": target,
                    "invocation_id": invocation_id,
                    "epoch": len(report.events),
                    "mapping_timing": "snapshot_at_launch",
                }
            )
        elif mode == "JoinSubroutine":
            candidates = tuple(outstanding.get(target or "", ()))
            if not candidates:
                available = tuple(sorted(key for key, values in outstanding.items() if values))
                detached = (target or "") in detached_targets
                report.findings.append(
                    LifecycleFinding(
                        code="detached_join" if detached else "unmatched_join",
                        message=(
                            "JoinSubroutine cannot synchronize a FireAndForget invocation."
                            if detached
                            else "JoinSubroutine has no matching outstanding asynchronous invocation."
                        ),
                        step_index=index,
                        target=target,
                        candidates=available,
                    )
                )
                continue
            invocation_id = outstanding[target].pop(0)
            report.events.append(
                {
                    "step_index": index,
                    "action": "join",
                    "mode": mode,
                    "target": target,
                    "invocation_id": invocation_id,
                    "epoch": len(report.events),
                    "happens_before": True,
                }
            )
        elif mode == "FireAndForget":
            invocation_number += 1
            detached_targets.add(target or "")
            report.events.append(
                {
                    "step_index": index,
                    "action": "detach",
                    "mode": mode,
                    "target": target,
                    "invocation_id": f"{target or '<unresolved>'}#{invocation_number}",
                    "epoch": len(report.events),
                }
            )
        else:
            report.events.append(
                {
                    "step_index": index,
                    "action": "complete",
                    "mode": mode,
                    "target": target,
                    "epoch": len(report.events),
                }
            )

    report.outstanding = [
        invocation_id
        for target in sorted(outstanding)
        for invocation_id in outstanding[target]
    ]
    for invocation_id in report.outstanding:
        report.findings.append(
            LifecycleFinding(
                code="unjoined_async",
                message="Asynchronous subroutine remains outstanding at the modeled boundary.",
                step_index=-1,
                target=invocation_id.rsplit("#", 1)[0],
            )
        )
    return report


def _is_subroutine(step: Any) -> bool:
    return hasattr(step, "subroutine") and hasattr(step, "execution_mode")


def _reachable_steps(steps: Iterable[Any]) -> list[Any]:
    """Flatten only explicit script groups; keep loops/branches conservative."""
    result: list[Any] = []
    for step in steps:
        children = getattr(step, "steps", None)
        if children is not None and type(step).__name__ == "ScriptGroupStep":
            result.extend(_reachable_steps(children))
        else:
            result.append(step)
    return result
