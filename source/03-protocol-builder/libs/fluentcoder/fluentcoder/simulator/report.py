"""Simulation coverage reporting."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class EffectKind(str, Enum):
    NO_EFFECT = "no_effect"
    VALIDATION_ONLY = "validation_only"
    LIQUID_TRANSFER = "liquid_transfer"
    TIP_STATE_CHANGE = "tip_state_change"
    LABWARE_MOVEMENT = "labware_movement"
    VARIABLE_CHANGE = "variable_change"
    OPAQUE = "opaque"


FULLY_SIMULATED_EFFECTS = {
    EffectKind.LIQUID_TRANSFER,
    EffectKind.TIP_STATE_CHANGE,
    EffectKind.LABWARE_MOVEMENT,
    EffectKind.VARIABLE_CHANGE,
}


@dataclass
class StepCoverage:
    step_index: int
    step_type: str
    command_id: str
    effect: EffectKind
    raw_xml: bool = False
    message: str = ""

    @property
    def is_fully_simulated(self) -> bool:
        return self.effect in FULLY_SIMULATED_EFFECTS

    @property
    def is_validation_only(self) -> bool:
        return self.effect == EffectKind.VALIDATION_ONLY

    @property
    def is_opaque(self) -> bool:
        return self.effect in {EffectKind.OPAQUE, EffectKind.NO_EFFECT}

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index": self.step_index,
            "step_type": self.step_type,
            "command_id": self.command_id,
            "effect": self.effect.value,
            "raw_xml": self.raw_xml,
            "message": self.message,
        }


@dataclass
class SimulationFailure:
    category: str
    exception_type: str
    message: str
    step_index: int | None = None
    step_type: str | None = None
    command_id: str | None = None
    operation: str | None = None
    details: dict[str, Any] = field(default_factory=dict)
    repair_options: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "exception_type": self.exception_type,
            "message": self.message,
            "step_index": self.step_index,
            "step_type": self.step_type,
            "command_id": self.command_id,
            "operation": self.operation,
            "details": dict(self.details),
            "repair_options": list(self.repair_options),
        }


@dataclass
class SimulationReport:
    steps: list[StepCoverage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    opaque_events: list[dict[str, Any]] = field(default_factory=list)
    failure: SimulationFailure | None = None
    final_labware: dict[str, Any] = field(default_factory=dict)
    final_mca_tips: list[dict[str, Any]] = field(default_factory=list)
    final_liha_tips: list[dict[str, Any] | None] = field(default_factory=list)
    state_summary: dict[str, Any] = field(default_factory=dict)

    def add_step(self, coverage: StepCoverage) -> None:
        self.steps.append(coverage)
        if coverage.effect == EffectKind.OPAQUE:
            event = {
                "step_index": coverage.step_index,
                "command_id": coverage.command_id,
                "step_type": coverage.step_type,
                "raw_xml": coverage.raw_xml,
                "message": coverage.message,
            }
            self.opaque_events.append(event)

    @property
    def status(self) -> str:
        if self.failure is not None:
            return "failed"
        if self.opaque_events:
            return "passed_with_opaque"
        return "passed"

    @property
    def total_executed_steps(self) -> int:
        return len(self.steps)

    @property
    def fully_simulated_steps(self) -> int:
        return sum(1 for step in self.steps if step.is_fully_simulated)

    @property
    def validation_only_steps(self) -> int:
        return sum(1 for step in self.steps if step.is_validation_only)

    @property
    def opaque_noop_steps(self) -> int:
        return sum(1 for step in self.steps if step.is_opaque)

    @property
    def raw_xml_generic_steps(self) -> int:
        return sum(1 for step in self.steps if step.raw_xml)

    @property
    def modeled_steps(self) -> int:
        return self.fully_simulated_steps + self.validation_only_steps

    @property
    def modeled_coverage(self) -> float:
        if not self.steps:
            return 1.0
        return self.modeled_steps / len(self.steps)

    @property
    def unsupported_command_ids(self) -> dict[str, int]:
        counts = Counter(
            step.command_id
            for step in self.steps
            if step.effect == EffectKind.OPAQUE
        )
        return dict(sorted(counts.items()))

    @property
    def effect_counts(self) -> dict[str, int]:
        counts = Counter(step.effect.value for step in self.steps)
        return {
            effect.value: counts.get(effect.value, 0)
            for effect in EffectKind
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "total_executed_steps": self.total_executed_steps,
            "fully_simulated_steps": self.fully_simulated_steps,
            "validation_only_steps": self.validation_only_steps,
            "opaque_noop_steps": self.opaque_noop_steps,
            "raw_xml_generic_steps": self.raw_xml_generic_steps,
            "modeled_coverage": self.modeled_coverage,
            "effect_counts": self.effect_counts,
            "unsupported_command_ids": self.unsupported_command_ids,
            "steps": [step.to_dict() for step in self.steps],
            "opaque_events": list(self.opaque_events),
            "warnings": list(self.warnings),
            "failure": None if self.failure is None else self.failure.to_dict(),
            "final_labware": self.final_labware,
            "final_mca_tips": self.final_mca_tips,
            "final_liha_tips": self.final_liha_tips,
            "state_summary": self.state_summary,
        }
