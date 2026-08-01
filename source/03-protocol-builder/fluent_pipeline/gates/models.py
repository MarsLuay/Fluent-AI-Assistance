"""Typed inputs shared by readiness-gate evaluators."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


GateRecord = dict[str, Any]
GateFactory = Callable[[str, str, str, dict[str, Any] | None], GateRecord]


@dataclass(frozen=True, slots=True)
class ValidationContext:
    """Artifacts computed once and supplied to registered readiness evaluators.

    Keep this context data-only.  Evaluators declare the artifact inputs they
    consume in the checked-in readiness registry, which makes evaluation
    dependencies reviewable without dynamic discovery.
    """

    make_gate: GateFactory
    compiled_xscr: Path | None = None
    draft_path: Path | None = None
    protocol_ir_path: Path | None = None
    protocol_ir: dict[str, Any] | None = None
    protocol_ir_error: str = ""
    compiled_ir: dict[str, Any] | None = None
    compiled_ir_error: str = ""
    compiled_inventory: Mapping[str, Any] = field(default_factory=dict)
    worklist: Path | None = None
    source_projects: tuple[Path, ...] = ()
    source_scripts: tuple[Path, ...] = ()
    source_xscr: Path | None = None
    source_irs: tuple[dict[str, Any], ...] = ()
    source_manifest: Mapping[str, Any] | None = None
    recreate_guide: Path | None = None
    worktable_diff: Mapping[str, Any] | None = None
    validation_options: Mapping[str, Any] = field(default_factory=dict)
    domain_ir: dict[str, Any] | None = None
