"""Offline workflow helpers for API V2 runtime-report planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from tecan_common.native_acceleration import (
    ascii_casefold_equals as _native_ascii_casefold_equals,
    ascii_casefold_trim as _native_ascii_casefold_trim,
)


EXECUTION_STEPS_KEY = "execution_steps"
EXECUTION_SUMMARY_KEY = "execution_summary"


@dataclass(frozen=True)
class FluentInitializeContext:
    selection: str
    name: str
    phase: str = "script_workspace"
    fallback_names: tuple[str, ...] = ()


@dataclass
class FluentContextCheckConfig:
    """Configuration shape retained for initialization planning and tests."""

    method: str = ""
    provider: str = "offline"
    xscr_path: Path | None = None
    zeia_path: Path | None = None
    initialize_workspace: str = ""
    script_workspace: str = ""
    initialize_selection: str = "workspace"
    variable_seeds: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _normalize_initialize_selection(value: Any) -> str:
    raw = value or "workspace"
    native = _native_ascii_casefold_trim(raw)
    selection = native if native is not None else raw.strip().casefold()
    return selection if selection in {"workspace", "method"} else "workspace"


def _casefold_equal(left: str, right: str) -> bool:
    native = _native_ascii_casefold_equals(left, right)
    return native if native is not None else left.casefold() == right.casefold()


def build_initialize_steps(
    config: FluentContextCheckConfig,
    *,
    manifest: Mapping[str, Any] | None = None,
    ir: Mapping[str, Any] | None = None,
    spec: Mapping[str, Any] | None = None,
) -> tuple[FluentInitializeContext, ...]:
    """Build ordered initialization worktable steps before method preparation."""
    from .initialization_worktables import (
        build_initialization_worktable_plan,
        fallback_names_for_initialize_step,
    )

    selection = _normalize_initialize_selection(config.initialize_selection)
    script = str(config.script_workspace or "").strip()
    init_only = str(config.initialize_workspace or "").strip()
    plan = build_initialization_worktable_plan(manifest, ir=ir, spec=spec)
    if not init_only and plan and plan.primary_init_worktable:
        init_only = plan.primary_init_worktable
    steps: list[FluentInitializeContext] = []
    if init_only and script and not _casefold_equal(init_only, script):
        steps.append(
            FluentInitializeContext(
                selection=selection,
                name=init_only,
                phase="pre_initialize",
                fallback_names=fallback_names_for_initialize_step(
                    plan,
                    phase="pre_initialize",
                    step_name=init_only,
                ),
            )
        )
    target = script or init_only
    if target and (not steps or not _casefold_equal(steps[-1].name, target)):
        steps.append(
            FluentInitializeContext(
                selection=selection,
                name=target,
                phase="script_workspace",
                fallback_names=fallback_names_for_initialize_step(
                    plan,
                    phase="script_workspace",
                    step_name=target,
                ),
            )
        )
    return tuple(steps)


def execution_steps_from_report(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    from .worktable_ir import execution_steps_from_report as _from_worktable_ir

    return _from_worktable_ir(report)
