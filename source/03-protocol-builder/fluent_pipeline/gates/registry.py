"""Explicit evaluator registry for the migrated readiness gates."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from .archive import evaluate_zeia_parsed
from .ir import evaluate_protocol_ir_schema
from .models import GateRecord, ValidationContext
from .worktable import (
    evaluate_carriers,
    evaluate_deck_layout,
    evaluate_device_aliases,
    evaluate_tip_boxes,
)


GateEvaluator = Callable[[ValidationContext], GateRecord]


@dataclass(frozen=True, slots=True)
class RegisteredGateEvaluator:
    """One statically registered evaluator and its reviewed artifact contract."""

    gate_id: str
    implementation: str
    artifact_inputs: tuple[str, ...]
    evaluate: GateEvaluator


# Deliberately static: no entry-point, filesystem, or import scanning is allowed
# in the readiness path.
_EVALUATORS = (
    RegisteredGateEvaluator(
        "zeia_parsed",
        "fluent_pipeline.gates.archive:evaluate_zeia_parsed",
        ("source manifest", "source project archives"),
        evaluate_zeia_parsed,
    ),
    RegisteredGateEvaluator(
        "protocol_ir_schema",
        "fluent_pipeline.gates.ir:evaluate_protocol_ir_schema",
        ("protocol.ir.json", "Python draft"),
        evaluate_protocol_ir_schema,
    ),
    RegisteredGateEvaluator(
        "tip_boxes_resolve",
        "fluent_pipeline.gates.worktable:evaluate_tip_boxes",
        ("protocol.ir.json", "source manifest"),
        evaluate_tip_boxes,
    ),
    RegisteredGateEvaluator(
        "carriers_resolve",
        "fluent_pipeline.gates.worktable:evaluate_carriers",
        ("protocol.ir.json", "source manifest"),
        evaluate_carriers,
    ),
    RegisteredGateEvaluator(
        "device_aliases_resolve",
        "fluent_pipeline.gates.worktable:evaluate_device_aliases",
        ("protocol.ir.json", "source manifest"),
        evaluate_device_aliases,
    ),
    RegisteredGateEvaluator(
        "deck_layout_consistent",
        "fluent_pipeline.gates.worktable:evaluate_deck_layout",
        ("protocol.ir.json", "source manifest"),
        evaluate_deck_layout,
    ),
)


@lru_cache(maxsize=1)
def readiness_evaluator_registry() -> tuple[RegisteredGateEvaluator, ...]:
    ids = [entry.gate_id for entry in _EVALUATORS]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate readiness evaluator registration.")
    return _EVALUATORS


def readiness_evaluator(gate_id: str) -> RegisteredGateEvaluator:
    for evaluator in readiness_evaluator_registry():
        if evaluator.gate_id == gate_id:
            return evaluator
    raise KeyError(f"No registered evaluator for readiness gate {gate_id!r}.")
