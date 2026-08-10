"""Explicit evaluator registry for the migrated readiness gates."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from .archive import evaluate_zeia_parsed
from .evaluators import (
    evaluate_checksums,
    evaluate_command_inventory,
    evaluate_fluent_context_check,
    evaluate_generated_zeia,
    evaluate_labware,
    evaluate_liquid_class_compatibility,
    evaluate_liquid_classes,
    evaluate_liquid_state,
    evaluate_no_unapproved_raw_xml,
    evaluate_post_compile_xscr,
    evaluate_python_draft,
    evaluate_recreate,
    evaluate_repair_plan,
    evaluate_simulation,
    evaluate_subroutine_dependencies,
    evaluate_tip_capacity,
    evaluate_volume_bounds,
    evaluate_well_ranges,
    evaluate_worklists,
    evaluate_xscr,
    evaluate_xscr_ir_roundtrip,
)
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
        "labware_resolves",
        "fluent_pipeline.gates.evaluators:evaluate_labware",
        ("protocol.ir.json", "source manifest"),
        evaluate_labware,
    ),
    RegisteredGateEvaluator(
        "liquid_classes_resolve",
        "fluent_pipeline.gates.evaluators:evaluate_liquid_classes",
        ("protocol.ir.json", "source manifest"),
        evaluate_liquid_classes,
    ),
    RegisteredGateEvaluator(
        "worklist_paths_valid",
        "fluent_pipeline.gates.evaluators:evaluate_worklists",
        ("protocol.ir.json", "source manifest", "worklist"),
        evaluate_worklists,
    ),
    RegisteredGateEvaluator(
        "python_draft_generated",
        "fluent_pipeline.gates.evaluators:evaluate_python_draft",
        ("Python draft",),
        evaluate_python_draft,
    ),
    RegisteredGateEvaluator(
        "simulation_passes",
        "fluent_pipeline.gates.evaluators:evaluate_simulation",
        ("simulation report",),
        evaluate_simulation,
    ),
    RegisteredGateEvaluator(
        "repair_plan_clear",
        "fluent_pipeline.gates.evaluators:evaluate_repair_plan",
        ("repair plan",),
        evaluate_repair_plan,
    ),
    RegisteredGateEvaluator(
        "xscr_compiles",
        "fluent_pipeline.gates.evaluators:evaluate_xscr",
        ("compiled XSCR", "compile report"),
        evaluate_xscr,
    ),
    RegisteredGateEvaluator(
        "recreate_matches_ir",
        "fluent_pipeline.gates.evaluators:evaluate_recreate",
        ("protocol.ir.json", "RECREATE_SCRIPT.md"),
        evaluate_recreate,
    ),
    RegisteredGateEvaluator(
        "post_compile_xscr_reinspect",
        "fluent_pipeline.gates.evaluators:evaluate_post_compile_xscr",
        ("compiled XSCR", "protocol.ir.json", "source manifest"),
        evaluate_post_compile_xscr,
    ),
    RegisteredGateEvaluator(
        "xscr_ir_roundtrip_matches",
        "fluent_pipeline.gates.evaluators:evaluate_xscr_ir_roundtrip",
        ("protocol.ir.json", "compiled XSCR"),
        evaluate_xscr_ir_roundtrip,
    ),
    RegisteredGateEvaluator(
        "volume_bounds_valid",
        "fluent_pipeline.gates.evaluators:evaluate_volume_bounds",
        ("protocol.ir.json", "validation options"),
        evaluate_volume_bounds,
    ),
    RegisteredGateEvaluator(
        "well_ranges_valid",
        "fluent_pipeline.gates.evaluators:evaluate_well_ranges",
        ("protocol.ir.json",),
        evaluate_well_ranges,
    ),
    RegisteredGateEvaluator(
        "tip_capacity_valid",
        "fluent_pipeline.gates.evaluators:evaluate_tip_capacity",
        ("protocol.ir.json", "source manifest"),
        evaluate_tip_capacity,
    ),
    RegisteredGateEvaluator(
        "liquid_class_compatible",
        "fluent_pipeline.gates.evaluators:evaluate_liquid_class_compatibility",
        ("protocol.ir.json", "source manifest"),
        evaluate_liquid_class_compatibility,
    ),
    RegisteredGateEvaluator(
        "no_unapproved_raw_xml",
        "fluent_pipeline.gates.evaluators:evaluate_no_unapproved_raw_xml",
        ("Python draft", "compiled XSCR", "validation options"),
        evaluate_no_unapproved_raw_xml,
    ),
    RegisteredGateEvaluator(
        "liquid_state_valid",
        "fluent_pipeline.gates.evaluators:evaluate_liquid_state",
        ("protocol.ir.json", "liquid-state report"),
        evaluate_liquid_state,
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
    RegisteredGateEvaluator(
        "checksums_valid",
        "fluent_pipeline.gates.evaluators:evaluate_checksums",
        ("compiled XSCR", "source project archives", "checksum audit"),
        evaluate_checksums,
    ),
    RegisteredGateEvaluator(
        "generated_zeia_valid",
        "fluent_pipeline.gates.evaluators:evaluate_generated_zeia",
        ("source project archives", "archive audit"),
        evaluate_generated_zeia,
    ),
    RegisteredGateEvaluator(
        "command_inventory_resolves",
        "fluent_pipeline.gates.evaluators:evaluate_command_inventory",
        ("compiled XSCR", "source manifest", "alias maps"),
        evaluate_command_inventory,
    ),
    RegisteredGateEvaluator(
        "subroutine_dependencies_valid",
        "fluent_pipeline.gates.evaluators:evaluate_subroutine_dependencies",
        ("protocol.ir.json", "compiled XSCR", "source manifest", "source project archives"),
        evaluate_subroutine_dependencies,
    ),
    RegisteredGateEvaluator(
        "fluent_context_check",
        "fluent_pipeline.gates.evaluators:evaluate_fluent_context_check",
        ("FluentControl diagnostic report",),
        evaluate_fluent_context_check,
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
