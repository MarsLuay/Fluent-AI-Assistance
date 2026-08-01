"""Offline workflow helpers for API V2 runtime-report planning."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


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

    selection = (config.initialize_selection or "workspace").strip().casefold()
    if selection not in {"workspace", "method"}:
        selection = "workspace"
    script = str(config.script_workspace or "").strip()
    init_only = str(config.initialize_workspace or "").strip()
    plan = build_initialization_worktable_plan(manifest, ir=ir, spec=spec)
    if not init_only and plan and plan.primary_init_worktable:
        init_only = plan.primary_init_worktable
    steps: list[FluentInitializeContext] = []
    if init_only and script and init_only.casefold() != script.casefold():
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
    if target and (not steps or steps[-1].name.casefold() != target.casefold()):
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
