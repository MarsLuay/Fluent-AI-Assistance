"""IR walker — reconstructs twin state and snapshots from a Protocol IR list."""

from __future__ import annotations

import copy
import math
import re
from .. import xml_compat as ET
from typing import TYPE_CHECKING, Any, Literal, Optional, Union

from ..fc_variables import decode_fc_variable
from ..expressions import (
    BinaryExpression,
    BooleanLiteral,
    Expression,
    FunctionCall,
    NumberLiteral,
    ReviewedRawExpression,
    SourcePreservedExpression,
    StringLiteral,
    UnaryExpression,
    VariableReference,
    coerce_source_expression,
    evaluate_binary_operator,
    expression_python_value,
    render_expression,
)
from ..heads.mca96 import Tip
from ..ir.schema import (
    AddLabwareStep, RemoveLabwareStep,
    GetHeadAdapterStep, DropHeadAdapterStep,
    PickUpTipsStep, SetTipsBackStep,
    AspirateStep, DispenseStep,
    RgaTransferLabwareStep, CgaGetFingersStep, CgaDropFingersStep,
    LoopStep, ConditionalStep, Mca384EmptyTipsStep, Mca384MixStep,
    SetLocationStep, SetVariableStep, SubRoutineStep, VariableMapping,
    CommentStep, DelayStep, ExecuteApplicationStep, ExportVariableStep,
    GenericStep, ImportVariableStep, InitializeDeviceStep, LihaAspirateStep, LihaDispenseStep,
    LihaDetectLiquidStep, GenerateReportStep,
    ApplicationDriverMacroStep,
    MoveAxisCommandStep,
    StartMoveCommandStep,
    WaitForAsyncResponseStep,
    EndScriptStep, ExecuteVbScriptStep, TeGioSetPwmOutputStep, LeaveStep,
    LihaDropTipsStep, LihaEmptyTipsStep, LihaGetTipsStep, LihaMixStep,
    Mca384DropTipsStep, Mca384GetTipsStep, Mca384MoveArmStep,
    QueryVariableStep, ScriptGroupStep, StartTimerStep, UserPromptStep,
    WaitForTimerStep, WaitStep,
)
from ..labware.base import Labware, Layer
from ..labware.tipboxes import TipBox
from .invariants import (
    CannotAspirateError, InsufficientVolumeError,
    InvalidSlotError, MissingAdapterError, MissingSimValueError,
    MissingTipsError, OccupiedSlotError, OverdrawError,
)
from .snapshots import (
    Snapshot,
    capture_state_fingerprint,
    compute_state_delta,
    take_delta_snapshot,
    take_snapshot,
)
from .report import EffectKind, SimulationFailure, SimulationReport, StepCoverage
from .invariants import SimulationError

if TYPE_CHECKING:
    from ..reagent import Reagent
    from ..subroutines.registry import SubroutineRegistry
    from ..worktable import Worktable


class Simulator:
    """Walks a Worktable's protocol IR and reconstructs the twin.

    Author method calls update the worktable's slot_map at authoring time
    (so subsequent author calls see consistent state); the simulator
    rebuilds an independent twin from the IR alone, decoupled from
    authoring-time mutations.
    """

    def __init__(
        self,
        worktable: "Worktable",
        *,
        subroutine_registry: "SubroutineRegistry | None" = None,
        max_subroutine_depth: int = 8,
        snapshot_mode: Literal["full", "final_only", "delta"] = "full",
    ) -> None:
        self._wt = worktable
        self._subroutine_registry = subroutine_registry
        self._max_subroutine_depth = max_subroutine_depth
        self._snapshot_mode = snapshot_mode
        self._subroutine_call_stack: list[str] = []
        # Per-subroutine sim variable scopes (innermost last).
        self._sim_scope_stack: list[dict[str, Any]] = []
        # Twin state — fresh slot map keyed on (loc, pos), bottom→top.
        self._slot_map: dict[tuple[str, int], list[Labware]] = {}
        # Maps catalog label → twin Labware copy.
        self._twin: dict[str, Labware] = {}
        # MCA-96 head state.
        self._mca_adapter_label: Optional[str] = None
        self._mca_tips: list[Tip] = []
        self._mca_tip_box_label: Optional[str] = None
        # LiHa/FCA state: eight independent channels.
        self._liha_tips: list[Tip | None] = [None] * 8
        self._liha_tip_box_label: Optional[str] = None
        # A raw source command can be structurally preserved while its deck
        # target is unavailable locally. Do not use the incomplete twin state
        # to model subsequent raw LiHa liquid actions as if they were proven.
        self._liha_state_uncertain = False
        # Running tally of total volume requested per source labware /
        # per-well across the whole sim. Always populated, regardless of
        # whether a source_volume_short failure occurs.
        self._source_requested_ul: dict[str, float] = {}
        self._source_requested_by_well_ul: dict[str, dict[str, float]] = {}
        self._report = SimulationReport()
        # Initial-state reagents declared via fill_all() etc. live on the
        # author-side Labware. We deep-copy them into the twin at place time.
        self._step_index = 0
        self._current_step = None
        self._strict = False
        self._last_snapshot_step = None
        self._prev_state_fingerprint: dict | None = None
        # Existing workspace occupancy is source context, not script behavior.
        # Clone it before replay so no simulation mutation leaks to authoring.
        for labware in getattr(worktable, "_simulation_seed_labware", ()):
            if labware.slot is None:
                continue
            twin = copy.deepcopy(labware)
            self._slot_map.setdefault(twin.slot, []).append(twin)
            self._twin[twin.label] = twin

    def run(
        self,
        *,
        fail_on_opaque: bool = False,
        min_coverage: float | None = None,
        strict: bool = False,
    ) -> None:
        self._strict = strict
        self._wt.snapshots.clear()
        self._wt.simulation_report = self._report
        protocol = self._wt.to_protocol()
        try:
            for warning in getattr(self._wt, "_simulation_context_warnings", ()):
                self._warn(str(warning))
            if strict:
                self._preflight_strict()
            for group in protocol.groups:
                for step in group.steps:
                    self._dispatch(step)
            if self._report.opaque_events:
                self._warn(
                    f"{len(self._report.opaque_events)} step(s) were opaque to the simulator"
                )
            if min_coverage is not None and self._report.modeled_coverage < min_coverage:
                self._warn(
                    f"simulation coverage {self._report.modeled_coverage:.3f} is below "
                    f"minimum {min_coverage:.3f}"
                )
            if fail_on_opaque and self._report.opaque_events:
                raise SimulationError("simulation encountered opaque GenericStep/raw XML commands")
            if min_coverage is not None and self._report.modeled_coverage < min_coverage:
                raise SimulationError(
                    f"simulation coverage {self._report.modeled_coverage:.3f} is below "
                    f"minimum {min_coverage:.3f}"
                )
        except Exception as exc:
            self._record_failure(exc)
            self._finalize_report()
            self._wt.simulation_report = self._report
            raise
        self._finalize_report()
        self._wt.simulation_report = self._report

    # ── Step dispatch ───────────────────────────────────────────────

    def _dispatch(self, step) -> None:
        self._current_step = step
        if getattr(step, "disabled", False):
            self._snapshot(step, EffectKind.VALIDATION_ONLY, "step disabled for execution")
            self._current_step = None
            return
        effect = EffectKind.OPAQUE
        message = ""
        if isinstance(step, AddLabwareStep):
            self._on_add_labware(step)
            effect = EffectKind.LABWARE_MOVEMENT
        elif isinstance(step, RemoveLabwareStep):
            self._on_remove_labware(step)
            effect = EffectKind.LABWARE_MOVEMENT
        elif isinstance(step, GetHeadAdapterStep):
            self._on_get_adapter(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, DropHeadAdapterStep):
            self._on_drop_adapter(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, PickUpTipsStep):
            self._on_pickup_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, SetTipsBackStep):
            self._on_return_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, AspirateStep):
            self._on_aspirate(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, DispenseStep):
            self._on_dispense(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, Mca384MixStep):
            self._on_mix(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, Mca384EmptyTipsStep):
            self._on_empty_tips(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, Mca384GetTipsStep):
            self._on_mca384_get_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, Mca384DropTipsStep):
            self._on_mca384_drop_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, Mca384MoveArmStep):
            self._on_mca384_move_arm(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, LihaGetTipsStep):
            self._on_liha_get_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, LihaDropTipsStep):
            self._on_liha_drop_tips(step)
            effect = EffectKind.TIP_STATE_CHANGE
        elif isinstance(step, LihaAspirateStep):
            self._on_liha_aspirate(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, LihaDispenseStep):
            self._on_liha_dispense(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, LihaMixStep):
            self._on_liha_mix(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, LihaDetectLiquidStep):
            effect = EffectKind.VALIDATION_ONLY
            message = "LiHa liquid detection is not modeled in the twin"
        elif isinstance(step, GenerateReportStep):
            effect = EffectKind.VALIDATION_ONLY
            message = "report generation is not modeled in the twin"
        elif isinstance(step, LihaEmptyTipsStep):
            self._on_liha_empty_tips(step)
            effect = EffectKind.LIQUID_TRANSFER
        elif isinstance(step, SetVariableStep):
            self._on_set_variable(step)
            effect = EffectKind.VARIABLE_CHANGE
        elif isinstance(step, SetLocationStep):
            self._on_set_location(step)
            effect = EffectKind.LABWARE_MOVEMENT
        elif isinstance(step, CgaGetFingersStep):
            effect = EffectKind.VALIDATION_ONLY
            pass  # gripper-finger-pickup is a no-op in the twin
        elif isinstance(step, CgaDropFingersStep):
            effect = EffectKind.VALIDATION_ONLY
            pass  # gripper-finger-drop is a no-op in the twin
        elif isinstance(step, RgaTransferLabwareStep):
            self._on_gripper_move(step)
            effect = EffectKind.LABWARE_MOVEMENT
        elif isinstance(step, ApplicationDriverMacroStep):
            effect, message = self._on_application_driver_macro(step)
        elif isinstance(step, (MoveAxisCommandStep, StartMoveCommandStep, WaitForAsyncResponseStep, EndScriptStep,
                              ExecuteVbScriptStep, TeGioSetPwmOutputStep, LeaveStep)):
            effect = EffectKind.VALIDATION_ONLY
            message = "hardware driver/runtime passthrough is not modeled in the twin"
        elif isinstance(step, LoopStep):
            self._on_loop(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, ConditionalStep):
            self._on_conditional(step)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, ScriptGroupStep):
            for child in step.steps:
                self._dispatch(child)
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, SubRoutineStep):
            effect, message = self._on_subroutine(step)
        elif isinstance(step, (WaitStep, DelayStep, CommentStep, UserPromptStep,
                              StartTimerStep, WaitForTimerStep, ExportVariableStep,
                              InitializeDeviceStep)):
            effect = EffectKind.VALIDATION_ONLY
        elif isinstance(step, (ImportVariableStep, QueryVariableStep, ExecuteApplicationStep)):
            message = "runtime/user/external side effect is not modeled"
        elif isinstance(step, GenericStep):
            effect, message = self._on_generic_step(step)
        # All other step types (waits, comments, timers, variables, externals)
        # are opaque to the twin. They still get a snapshot below.

        self._snapshot(step, effect, message)
        self._current_step = None

    def _snapshot(self, step, effect: EffectKind, message: str = "") -> None:
        command_id = getattr(step, "step_type", type(step).__name__)
        command_id = getattr(command_id, "value", command_id)
        raw_xml = isinstance(step, GenericStep) and bool(step.parameters.get("raw_xml"))
        coverage = StepCoverage(
            step_index=self._step_index,
            step_type=type(step).__name__,
            command_id=str(command_id),
            effect=effect,
            raw_xml=raw_xml,
            message=message,
        )
        self._report.add_step(coverage)
        self._last_snapshot_step = step
        if self._snapshot_mode == "full":
            self._wt.snapshots.append(take_snapshot(
                step_index=self._step_index,
                step=step,
                slot_map=self._slot_map,
                mca_adapter_label=self._mca_adapter_label,
                mca_tips=self._mca_tips,
                mca_tip_box_label=self._mca_tip_box_label,
                liha_tips=self._liha_tips,
                opaque_events=self._report.opaque_events,
                warnings=self._report.warnings,
            ))
        elif self._snapshot_mode == "delta":
            current = capture_state_fingerprint(
                slot_map=self._slot_map,
                mca_adapter_label=self._mca_adapter_label,
                mca_tips=self._mca_tips,
                mca_tip_box_label=self._mca_tip_box_label,
                liha_tips=self._liha_tips,
            )
            delta = compute_state_delta(self._prev_state_fingerprint, current)
            self._prev_state_fingerprint = current
            self._wt.snapshots.append(take_delta_snapshot(
                step_index=self._step_index,
                step=step,
                delta=delta,
                opaque_events=self._report.opaque_events,
                warnings=self._report.warnings,
            ))
        self._step_index += 1

    def _append_final_snapshot(self) -> None:
        if self._snapshot_mode != "final_only" or self._last_snapshot_step is None:
            return
        self._wt.snapshots.clear()
        self._wt.snapshots.append(take_snapshot(
            step_index=self._step_index - 1,
            step=self._last_snapshot_step,
            slot_map=self._slot_map,
            mca_adapter_label=self._mca_adapter_label,
            mca_tips=self._mca_tips,
            mca_tip_box_label=self._mca_tip_box_label,
            liha_tips=self._liha_tips,
            opaque_events=self._report.opaque_events,
            warnings=self._report.warnings,
        ))

    def _finalize_report(self) -> None:
        self._append_final_snapshot()
        self._report.final_labware = self._labware_summary()
        self._report.final_mca_tips = self._tip_summary(self._mca_tips)
        self._report.final_liha_tips = [
            None if tip is None else self._tip_summary([tip], start_index=i)[0]
            for i, tip in enumerate(self._liha_tips)
        ]
        self._report.state_summary = self._state_summary()

    def _record_failure(self, exc: Exception) -> None:
        if self._report.failure is not None:
            return
        step = self._current_step
        step_type = type(step).__name__ if step is not None else None
        command_id = None
        if step is not None:
            command_id = getattr(step, "step_type", type(step).__name__)
            command_id = getattr(command_id, "value", command_id)
            command_id = str(command_id)
        self._report.failure = SimulationFailure(
            category=self._classify_failure(exc),
            exception_type=type(exc).__name__,
            message=str(exc),
            step_index=self._step_index if step is not None else None,
            step_type=step_type,
            command_id=command_id,
            operation=_failure_operation(command_id),
            details=dict(getattr(exc, "sim_details", {}) or {}),
            repair_options=_repair_options(getattr(exc, "sim_category", None) or self._classify_failure(exc)),
        )

    def _classify_failure(self, exc: Exception) -> str:
        structured_category = getattr(exc, "sim_category", None)
        if structured_category:
            return str(structured_category)
        if isinstance(exc, InvalidSlotError):
            return "workspace_slot"
        if isinstance(exc, OccupiedSlotError):
            return "workspace_slot"
        if isinstance(exc, MissingSimValueError):
            return "runtime_variable"
        if isinstance(exc, MissingAdapterError):
            return "adapter_state"
        if isinstance(exc, MissingTipsError):
            return "tip_state"
        if isinstance(exc, (InsufficientVolumeError, OverdrawError, CannotAspirateError)):
            return "liquid_state"
        message = str(exc).lower()
        if "not bound to a specific fluentcontrol workspace" in message:
            return "workspace_binding"
        if "catalog-backed" in message or "catalog index" in message:
            return "catalog"
        if "opaque" in message:
            return "opaque_policy"
        if "coverage" in message:
            return "coverage_policy"
        return "simulation_state"

    def _preflight_strict(self) -> None:
        try:
            self._wt._require_bound_workspace()
        except ValueError as exc:
            raise SimulationError(str(exc)) from exc
        if self._wt.valid_slots is None:
            raise SimulationError(
                "Strict simulation requires workspace slot metadata to be loaded."
            )
        for stack in self._wt.slot_map.values():
            for labware in stack:
                if labware.slot is not None:
                    self._validate_workspace_slot(
                        labware.slot,
                        action=f"Labware {labware.label!r}",
                    )
                self._require_catalog_backed_labware(labware)

    def _validate_workspace_slot(self, slot: tuple[str, int], *, action: str) -> None:
        valid_slots = self._wt.valid_slots
        if valid_slots is None:
            return
        if slot in valid_slots:
            return
        raise InvalidSlotError(
            f"{action} references slot {slot!r}, which is not on workspace "
            f"{self._wt.workspace_name!r}."
        )

    def _require_catalog_backed_labware(self, labware: Labware) -> None:
        catalog_name = (labware.catalog_name or "").strip()
        if not catalog_name or catalog_name.startswith("<offline:"):
            raise SimulationError(
                f"Strict simulation requires installed catalog-backed labware semantics, "
                f"but {labware.label!r} is using unresolved catalog {catalog_name!r}."
            )
        if labware.category in {"plate", "trough", "tube_rack", "wash_station"} and not labware.wells:
            raise SimulationError(
                f"Strict simulation requires pipettable well geometry for {labware.label!r} "
                f"({catalog_name!r}), but no wells were resolved."
            )

    # ── Worktable / labware ─────────────────────────────────────────

    def _on_add_labware(self, step: AddLabwareStep) -> None:
        runtime_label = self._resolve_runtime_label(step.label)
        slot = (self._resolve_runtime_location(step.location), self._resolve_sim_int(step.position))
        if self._strict:
            self._validate_workspace_slot(slot, action=f"AddLabware({step.label!r})")
        cover_target = self._cover_site_target(step.location)
        cover = self._twin.get(cover_target) if cover_target else None
        is_cover_stack = cover is not None and cover.slot == slot
        existing_stack = self._slot_map.setdefault(slot, [])
        if existing_stack and not is_cover_stack:
            raise _with_sim_details(
                OccupiedSlotError(
                    f"Slot {slot} is already occupied by "
                    f"{existing_stack[-1].label!r}"
                ),
                category="slot_occupied",
                slot={"location": slot[0], "position": slot[1]},
                labware=step.label,
                occupied_by=existing_stack[-1].label,
            )
        # Clone author-side labware so twin mutations never leak back.
        try:
            original = self._wt.labware_by_label(step.label)
        except KeyError as exc:
            raise SimulationError(
                f"AddLabware({step.label!r}) has no resolved author-side labware instance."
            ) from exc
        twin = copy.deepcopy(original)
        fc_var_name = decode_fc_variable(step.labware_type)
        if fc_var_name:
            resolved_catalog = str(self._resolve_sim_value(fc_var_name))
            twin.catalog_name = resolved_catalog
        twin.label = runtime_label
        twin.slot = slot
        twin.stack_below = list(existing_stack)
        existing_stack.append(twin)
        self._twin[runtime_label] = twin

    def _on_remove_labware(self, step: RemoveLabwareStep) -> None:
        labware = self._twin.pop(step.labware_name, None)
        if self._strict and labware is None:
            raise SimulationError(
                f"RemoveLabware({step.labware_name!r}) references unknown labware."
            )
        if labware and labware.slot:
            stack = self._slot_map.get(labware.slot, [])
            if labware in stack:
                stack.remove(labware)
                if not stack:
                    del self._slot_map[labware.slot]
            labware.slot = None
            labware.stack_below = []

    def _on_gripper_move(self, step: RgaTransferLabwareStep) -> None:
        labware = self._twin.get(step.labware_name)
        if self._strict and (labware is None or labware.slot is None):
            raise SimulationError(
                f"RgaTransferLabware({step.labware_name!r}) references labware that is not present."
            )
        if labware is None or labware.slot is None:
            return
        dest = (step.destination_location, self._resolve_sim_int(step.destination_site))
        if self._strict:
            self._validate_workspace_slot(dest, action=f"RgaTransferLabware({step.labware_name!r})")
        # Pop from current stack.
        stack = self._slot_map.get(labware.slot, [])
        if labware in stack:
            stack.remove(labware)
            if not stack:
                del self._slot_map[labware.slot]
        # Push onto destination stack.
        dest_stack = self._slot_map.setdefault(dest, [])
        labware.stack_below = list(dest_stack)
        dest_stack.append(labware)
        labware.slot = dest

    # ── Head state ──────────────────────────────────────────────────

    def _on_get_adapter(self, step: GetHeadAdapterStep) -> None:
        self._mca_adapter_label = step.labware_name

    def _on_drop_adapter(self, step: DropHeadAdapterStep) -> None:
        self._mca_adapter_label = None

    def _on_pickup_tips(self, step: PickUpTipsStep) -> None:
        if self._mca_adapter_label is None:
            raise MissingAdapterError(
                f"PickUpTips({step.labware_name!r}) but no adapter is mounted on the MCA-96 head"
            )
        tip_box = self._twin.get(step.labware_name)
        if not isinstance(tip_box, TipBox):
            raise MissingTipsError(
                f"PickUpTips: {step.labware_name!r} is not a tip box"
            )
        try:
            addresses = tip_box.addresses_for_pickup(
                tip_columns=step.tip_columns,
                tip_count=step.tip_count,
                partial_columns=step.partial_columns,
                partial_rows=step.partial_rows,
            )
        except ValueError as exc:
            raise _with_sim_details(
                MissingTipsError(str(exc)),
                category="tip_box_empty",
                tip_box=step.labware_name,
            ) from exc
        requested_tips = len(addresses)
        if requested_tips == 0:
            raise MissingTipsError(
                f"PickUpTips: {step.labware_name!r} resolved to zero tips"
            )
        if tip_box.available_tip_count < requested_tips:
            raise _with_sim_details(
                MissingTipsError(
                    f"PickUpTips: {step.labware_name!r} has {tip_box.available_tip_count} tips available"
                ),
                category="tip_box_empty",
                tip_box=step.labware_name,
            )
        try:
            tip_box.consume_tips_at_addresses(addresses)
        except ValueError as exc:
            raise _with_sim_details(
                MissingTipsError(str(exc)),
                category="tip_box_empty",
                tip_box=step.labware_name,
            ) from exc
        capacity = tip_box.capacity_ul
        self._mca_tips = [Tip(capacity_ul=capacity) for _ in range(requested_tips)]
        self._mca_tip_box_label = step.labware_name

    def _on_return_tips(self, step: SetTipsBackStep) -> None:
        if not self._mca_tips:
            raise MissingTipsError("ReturnTips called but no tips on the head")
        target = step.labware_name or self._mca_tip_box_label
        if target is not None:
            tip_box = self._twin.get(target)
            if isinstance(tip_box, TipBox):
                tip_box.return_tips(len(self._mca_tips))
        self._mca_tips = []
        self._mca_tip_box_label = None

    def _on_mca384_get_tips(self, step: Mca384GetTipsStep) -> None:
        label = step.labware_name
        capacity = 50.0
        requested_tips = 384
        if label:
            tip_box = self._twin.get(label)
            if isinstance(tip_box, TipBox):
                if tip_box.available_tip_count < requested_tips:
                    raise _with_sim_details(
                        MissingTipsError(
                            f"Mca384GetTips: {label!r} has {tip_box.available_tip_count} tips available"
                        ),
                        category="tip_box_empty",
                        tip_box=label,
                    )
                capacity = tip_box.capacity_ul
                tip_box.consume_tips(requested_tips)
            elif tip_box is not None:
                self._warn(
                    f"Mca384GetTips({label!r}) is not a known TipBox; using {capacity:.0f} uL tips"
                )
        self._mca_tips = [Tip(capacity_ul=capacity) for _ in range(384)]
        self._mca_tip_box_label = label

    def _on_mca384_drop_tips(self, step: Mca384DropTipsStep) -> None:
        if not self._mca_tips:
            raise MissingTipsError("Mca384DropTips called but no tips on the head")
        target = step.labware_name or self._mca_tip_box_label
        if target is not None:
            tip_box = self._twin.get(target)
            if isinstance(tip_box, TipBox):
                tip_box.return_tips(len(self._mca_tips))
        self._mca_tips = []
        self._mca_tip_box_label = None

    def _on_mca384_move_arm(self, step: Mca384MoveArmStep) -> None:
        if step.labware_name and step.labware_name not in self._twin:
            if self._strict:
                raise SimulationError(
                    f"Mca384MoveArm references unknown labware {step.labware_name!r}."
                )
            self._warn(f"Mca384MoveArm references unknown labware {step.labware_name!r}")

    # ── Pipetting ───────────────────────────────────────────────────

    def _on_aspirate(self, step: AspirateStep) -> None:
        if self._mca_adapter_label is None:
            raise MissingAdapterError(
                f"Aspirate({step.labware_name!r}) without an adapter mounted"
            )
        if not self._mca_tips:
            raise MissingTipsError(
                f"Aspirate({step.labware_name!r}) without tips picked up"
            )
        target = self._twin.get(step.labware_name)
        if target is None:
            raise InsufficientVolumeError(
                f"Aspirate target {step.labware_name!r} is not on the worktable"
            )
        volume = self._resolve_sim_volume(step.volume)
        # Auto-parallel over wells: aspirate `volume` from each addressed well.
        wells = self._iter_aspirate_wells(target)
        if len(wells) == 1 and target.category == "trough":
            for tip in self._mca_tips:
                self._aspirate_one(target, wells[0], volume, tip)
            return
        for tip, well in zip(self._mca_tips, wells):
            self._aspirate_one(target, well, volume, tip)

    def _on_dispense(self, step: DispenseStep) -> None:
        if not self._mca_tips:
            raise MissingTipsError(
                f"Dispense({step.labware_name!r}) without tips picked up"
            )
        target = self._twin.get(step.labware_name)
        if target is None:
            raise InsufficientVolumeError(
                f"Dispense target {step.labware_name!r} is not on the worktable"
            )
        volume = self._resolve_sim_volume(step.volume)
        wells = self._iter_aspirate_wells(target)
        for tip, well in zip(self._mca_tips, wells):
            self._dispense_one(target, well, volume, tip)

    def _on_mix(self, step: Mca384MixStep) -> None:
        if self._mca_adapter_label is None:
            raise MissingAdapterError(
                f"Mix({step.labware_name!r}) without an adapter mounted"
            )
        if not self._mca_tips:
            raise MissingTipsError(
                f"Mix({step.labware_name!r}) without tips picked up"
            )
        if step.labware_name not in self._twin:
            raise InsufficientVolumeError(
                f"Mix target {step.labware_name!r} is not on the worktable"
            )
        target = self._twin[step.labware_name]
        volume = self._resolve_sim_volume(step.volume)
        cycles = self._resolve_sim_int(step.cycles)
        if cycles <= 0:
            return
        for tip, well in zip(self._mca_tips, self._iter_aspirate_wells(target)):
            self._validate_mix_one(target, well, volume, tip)

    def _on_empty_tips(self, step: Mca384EmptyTipsStep) -> None:
        if not self._mca_tips:
            raise MissingTipsError(
                f"EmptyTips({step.labware_name!r}) without tips picked up"
            )
        volume = self._resolve_sim_volume(step.volume)
        target = self._twin.get(step.labware_name)
        wells = self._iter_aspirate_wells(target) if target is not None else []
        for i, tip in enumerate(self._mca_tips):
            well = wells[i] if i < len(wells) else None
            self._empty_tip_one(tip, volume, well)

    def _on_liha_get_tips(self, step: LihaGetTipsStep) -> None:
        channels = self._liha_get_tips_channels(step)
        capacity = 1000.0
        if step.labware_name:
            tip_box = self._twin.get(step.labware_name)
            if isinstance(tip_box, TipBox):
                if tip_box.available_tip_count < len(channels):
                    raise _with_sim_details(
                        MissingTipsError(
                            f"LihaGetTips: {step.labware_name!r} has {tip_box.available_tip_count} tips available"
                        ),
                        category="tip_box_empty",
                        tip_box=step.labware_name,
                    )
                capacity = tip_box.capacity_ul
                tip_box.consume_tips(len(channels))
            elif tip_box is not None:
                capacity = self._infer_liha_tip_capacity(tip_box.catalog_name)
                self._warn(
                    f"LihaGetTips({step.labware_name!r}) is not a known TipBox; "
                    f"using {capacity:.0f} uL tips"
                )
        for ch in channels:
            self._liha_tips[ch] = Tip(capacity_ul=capacity)
        self._liha_tip_box_label = step.labware_name

    def _on_liha_drop_tips(self, step: LihaDropTipsStep) -> None:
        channels = [i for i, tip in enumerate(self._liha_tips) if tip is not None]
        if not channels:
            raise MissingTipsError("LihaDropTips called but no LiHa tips are mounted")
        target = step.labware_name or self._liha_tip_box_label
        if target is not None:
            tip_box = self._twin.get(target)
            if isinstance(tip_box, TipBox):
                tip_box.return_tips(len(channels))
        for ch in channels:
            self._liha_tips[ch] = None
        self._liha_tip_box_label = None

    def _on_liha_aspirate(self, step: LihaAspirateStep) -> None:
        target = self._require_labware(step.labware_name, "LiHa aspirate")
        wells = self._liha_wells(target, step.well_offset, step.selection, step.tip_channels)
        if step.volumes:
            if len(step.volumes) != len(wells):
                raise SimulationError(
                    f"LiHa aspirate volume count ({len(step.volumes)}) "
                    f"does not match selected channels ({len(wells)})."
                )
            for (ch, well), volume in zip(wells, step.volumes):
                tip = self._require_liha_tip(ch, "Aspirate")
                resolved = self._resolve_sim_volume(volume)
                self._aspirate_one(target, well, resolved, tip)
            return
        volume = self._resolve_sim_volume(step.volume)
        for ch, well in wells:
            tip = self._require_liha_tip(ch, "Aspirate")
            self._aspirate_one(target, well, volume, tip)

    def _on_liha_dispense(self, step: LihaDispenseStep) -> None:
        target = self._require_labware(step.labware_name, "LiHa dispense")
        wells = self._liha_wells(target, step.well_offset, step.selection, step.tip_channels)
        if step.volumes:
            if len(step.volumes) != len(wells):
                raise SimulationError(
                    f"LiHa dispense volume count ({len(step.volumes)}) "
                    f"does not match selected channels ({len(wells)})."
                )
            for (ch, well), volume in zip(wells, step.volumes):
                tip = self._require_liha_tip(ch, "Dispense")
                resolved = self._resolve_sim_volume(volume)
                self._dispense_one(target, well, resolved, tip)
            return
        volume = self._resolve_sim_volume(step.volume)
        for ch, well in wells:
            tip = self._require_liha_tip(ch, "Dispense")
            self._dispense_one(target, well, volume, tip)

    def _on_liha_mix(self, step: LihaMixStep) -> None:
        target = self._require_labware(step.labware_name, "LiHa mix")
        cycles = self._resolve_sim_int(step.cycles)
        if cycles <= 0:
            return
        wells = self._liha_wells(target, step.well_offset, step.selection, step.tip_channels)
        if step.volumes:
            if len(step.volumes) != len(wells):
                raise SimulationError(
                    f"LiHa mix volume count ({len(step.volumes)}) "
                    f"does not match selected channels ({len(wells)})."
                )
            for (ch, well), volume in zip(wells, step.volumes):
                tip = self._require_liha_tip(ch, "Mix")
                resolved = self._resolve_sim_volume(volume)
                self._validate_mix_one(target, well, resolved, tip)
            return
        volume = self._resolve_sim_volume(step.volume)
        for ch, well in wells:
            tip = self._require_liha_tip(ch, "Mix")
            self._validate_mix_one(target, well, volume, tip)

    def _on_liha_empty_tips(self, step: LihaEmptyTipsStep) -> None:
        target = self._twin.get(step.labware_name)
        wells = self._liha_wells(target, None, None) if target is not None else []
        volume = self._resolve_sim_volume(step.volume)
        channels = [i for i, tip in enumerate(self._liha_tips) if tip is not None]
        if not channels:
            raise MissingTipsError("LihaEmptyTips called but no LiHa tips are mounted")
        for ch in channels:
            well = next((w for c, w in wells if c == ch), None)
            self._empty_tip_one(self._liha_tips[ch], volume, well)

    def _on_set_variable(self, step: SetVariableStep) -> None:
        value = self._evaluate_sim_expression(step.value)
        self._wt.protocol_variables[step.variable_name] = value
        self._wt.sim_values[step.variable_name] = value

    def _on_set_location(self, step: SetLocationStep) -> None:
        labware = self._twin.get(step.labware)
        if self._strict and labware is None:
            raise SimulationError(
                f"SetLocation({step.labware!r}) references unknown labware."
            )
        if labware is None:
            return
        dest = (step.location, self._resolve_sim_int(step.site))
        if self._strict:
            self._validate_workspace_slot(dest, action=f"SetLocation({step.labware!r})")
            if dest in self._slot_map and self._slot_map[dest]:
                raise _with_sim_details(
                    OccupiedSlotError(
                        f"SetLocation({step.labware!r}) targets occupied slot {dest!r}."
                    ),
                    category="slot_occupied",
                    slot={"location": dest[0], "position": dest[1]},
                    labware=step.labware,
                    occupied_by=self._slot_map[dest][-1].label,
                )
        if labware.slot:
            stack = self._slot_map.get(labware.slot, [])
            if labware in stack:
                stack.remove(labware)
                if not stack:
                    del self._slot_map[labware.slot]
        dest_stack = self._slot_map.setdefault(dest, [])
        labware.stack_below = list(dest_stack)
        dest_stack.append(labware)
        labware.slot = dest

    def _on_subroutine(self, step: SubRoutineStep) -> tuple[EffectKind, str]:
        registry = self._subroutine_registry
        path = step.subroutine
        if registry is None:
            return EffectKind.OPAQUE, "external subroutine body is not available to the simulator"
        xscr_path = registry.resolve(path)
        if xscr_path is None:
            return EffectKind.OPAQUE, f"subroutine {path!r} not found in registry"
        normalized = registry.normalize_path(path)
        if normalized in self._subroutine_call_stack:
            return EffectKind.OPAQUE, f"subroutine cycle detected at {path!r}"
        if len(self._subroutine_call_stack) >= self._max_subroutine_depth:
            return (
                EffectKind.OPAQUE,
                f"subroutine call depth exceeds {self._max_subroutine_depth}",
            )
        from ..decompiler import parse_xscr

        proto = parse_xscr(xscr_path)
        self._subroutine_call_stack.append(normalized)
        self._sim_scope_stack.append(
            self._apply_variable_mappings_start(step.variable_mappings_start)
        )
        try:
            for group in proto.groups:
                for child in group.steps:
                    self._dispatch(child)
        finally:
            self._apply_variable_mappings_end(step.variable_mappings_end)
            self._sim_scope_stack.pop()
            self._subroutine_call_stack.pop()
        return EffectKind.VALIDATION_ONLY, f"inlined subroutine body from {path!r}"

    def _on_application_driver_macro(
        self,
        step: ApplicationDriverMacroStep,
    ) -> tuple[EffectKind, str]:
        """Application-driver macros are non-motion by default (gate 28 / verification safety).

        Even macros like ``RGA1_TransferLabware`` or ``RGA1_ExecuteSingleVector`` are
        not replayed as deck motion in the twin unless explicitly flagged prompt-only
        (operator-facing verification placeholder).
        """
        if step.parameters.get("prompt_only") in {True, "true", "True", "1"}:
            return EffectKind.VALIDATION_ONLY, "prompt-only application driver macro"
        return (
            EffectKind.VALIDATION_ONLY,
            "application driver macro not modeled (non-motion default for verification)",
        )

    def _on_generic_step(self, step: GenericStep) -> tuple[EffectKind, str]:
        raw_xml = step.parameters.get("raw_xml")
        command_id = step.step_type
        if raw_xml:
            adapted = self._adapt_raw_generic(step, raw_xml)
            if adapted is not None:
                target = getattr(adapted, "labware_name", None)
                if isinstance(adapted, LihaGetTipsStep):
                    self._liha_state_uncertain = False
                elif isinstance(adapted, LihaDropTipsStep) and self._liha_state_uncertain:
                    self._liha_state_uncertain = False
                    return (
                        EffectKind.OPAQUE,
                        "raw XML LiHa drop follows an opaque liquid-state command",
                    )
                elif self._liha_state_uncertain and isinstance(
                    adapted,
                    (LihaAspirateStep, LihaDispenseStep, LihaMixStep, LihaEmptyTipsStep),
                ):
                    return (
                        EffectKind.OPAQUE,
                        "raw XML LiHa liquid state follows an opaque command",
                    )
                requires_resolved_target = isinstance(
                    adapted,
                    (LihaAspirateStep, LihaDispenseStep, LihaMixStep, LihaDetectLiquidStep),
                )
                if requires_resolved_target and isinstance(target, str):
                    if self._is_unresolved_workspace_target(target):
                        self._liha_state_uncertain = True
                        return (
                            EffectKind.OPAQUE,
                            "raw XML command targets source workspace labware without local catalog geometry",
                        )
                    if self._lookup_labware(target) is None:
                        self._liha_state_uncertain = True
                        return (
                            EffectKind.OPAQUE,
                            "raw XML command targets labware not resolvable from the simulated deck",
                        )
                return self._apply_adapted_generic(adapted)
        if command_id in {"Wait", "Delay", "StartTimer", "WaitForTimer", "Comment"}:
            return EffectKind.VALIDATION_ONLY, "known no-liquid-effect command"
        if command_id in {"SetVariable"}:
            name = step.parameters.get("variable_name")
            if name is None:
                name = step.parameters.get("VariableName")
            value = step.parameters.get("value")
            if value is None:
                value = step.parameters.get("Value")
            if name:
                value = self._evaluate_sim_expression(value)
                self._wt.protocol_variables[str(name)] = value
                self._wt.sim_values[str(name)] = value
                return EffectKind.VARIABLE_CHANGE, "generic variable assignment modeled"
        if command_id in {"SetLocation"}:
            labware = step.parameters.get("labware") or step.parameters.get("LabwareName")
            location = step.parameters.get("location") or step.parameters.get("Location")
            site = step.parameters.get("site") or step.parameters.get("Site")
            if labware and location and site:
                self._on_set_location(SetLocationStep(labware=str(labware), location=str(location), site=int(site)))
                return EffectKind.LABWARE_MOVEMENT, "generic labware location modeled"
        return EffectKind.OPAQUE, "GenericStep/raw XML command is not modeled"

    def _is_unresolved_workspace_target(self, target: str) -> bool:
        unresolved = getattr(self._wt, "_simulation_unresolved_seed_labels", set())
        for label in unresolved:
            if target == label:
                return True
            if "[" in label and target.startswith(label.split("[", 1)[0]):
                return True
        return False

    def _apply_adapted_generic(self, adapted) -> tuple[EffectKind, str]:
        if isinstance(adapted, LihaGetTipsStep):
            self._on_liha_get_tips(adapted)
            return EffectKind.TIP_STATE_CHANGE, "raw XML LiHa get tips modeled"
        if isinstance(adapted, LihaDropTipsStep):
            self._on_liha_drop_tips(adapted)
            return EffectKind.TIP_STATE_CHANGE, "raw XML LiHa drop tips modeled"
        if isinstance(adapted, LihaAspirateStep):
            self._on_liha_aspirate(adapted)
            return EffectKind.LIQUID_TRANSFER, "raw XML LiHa aspirate modeled"
        if isinstance(adapted, LihaDispenseStep):
            self._on_liha_dispense(adapted)
            return EffectKind.LIQUID_TRANSFER, "raw XML LiHa dispense modeled"
        if isinstance(adapted, LihaMixStep):
            self._on_liha_mix(adapted)
            return EffectKind.VALIDATION_ONLY, "raw XML LiHa mix modeled"
        if isinstance(adapted, LihaEmptyTipsStep):
            self._on_liha_empty_tips(adapted)
            return EffectKind.LIQUID_TRANSFER, "raw XML LiHa empty tips modeled"
        if isinstance(adapted, Mca384GetTipsStep):
            self._on_mca384_get_tips(adapted)
            return EffectKind.TIP_STATE_CHANGE, "raw XML MCA384 get tips modeled"
        if isinstance(adapted, Mca384DropTipsStep):
            self._on_mca384_drop_tips(adapted)
            return EffectKind.TIP_STATE_CHANGE, "raw XML MCA384 drop tips modeled"
        if isinstance(adapted, Mca384MoveArmStep):
            self._on_mca384_move_arm(adapted)
            return EffectKind.VALIDATION_ONLY, "raw XML MCA384 move modeled"
        if isinstance(adapted, Mca384MixStep):
            self._on_mix(adapted)
            return EffectKind.VALIDATION_ONLY, "raw XML MCA384 mix modeled"
        if isinstance(adapted, Mca384EmptyTipsStep):
            self._on_empty_tips(adapted)
            return EffectKind.LIQUID_TRANSFER, "raw XML MCA384 empty tips modeled"
        return EffectKind.OPAQUE, "adapted raw XML command is not modeled"

    def _adapt_raw_generic(self, step: GenericStep, raw_xml: str):
        try:
            root = ET.fromstring(raw_xml)
        except ET.ParseError:
            return None
        command_id = step.step_type
        text = self._xml_text
        labware = text(root, "LabwareName")
        volume = self._xml_volume(root)
        liquid_class = text(root, "LiquidClassName")
        well_offset_text = text(root, "WellOffset")
        well_offset = int(well_offset_text) if well_offset_text and well_offset_text.lstrip("-").isdigit() else None
        selection = text(root, "SerializedWellIndexes") or text(root, "SelectedWellsString")
        if command_id in {"LihaGetTips", "LihaPickUp"}:
            return LihaGetTipsStep(labware_name=labware)
        if command_id == "LihaDropTips":
            return LihaDropTipsStep(labware_name=labware)
        if command_id == "LihaAspirate" and labware and volume is not None:
            return LihaAspirateStep(
                labware_name=labware,
                volume=volume,
                liquid_class=liquid_class,
                well_offset=well_offset,
                selection=selection,
            )
        if command_id == "LihaDispense" and labware and volume is not None:
            return LihaDispenseStep(
                labware_name=labware,
                volume=volume,
                liquid_class=liquid_class,
                well_offset=well_offset,
                selection=selection,
            )
        if command_id == "LihaMix" and labware and volume is not None:
            cycles_text = text(root, "Cycles")
            cycles = int(cycles_text) if cycles_text and cycles_text.isdigit() else 10
            return LihaMixStep(
                labware_name=labware,
                volume=volume,
                cycles=cycles,
                liquid_class=liquid_class,
                well_offset=well_offset,
                selection=selection,
            )
        if command_id == "LihaEmptyTips" and labware:
            return LihaEmptyTipsStep(
                labware_name=labware,
                volume=volume or 0,
                liquid_class=liquid_class,
            )
        if command_id == "Mca384GetTips":
            return Mca384GetTipsStep(labware_name=labware)
        if command_id == "Mca384DropTips":
            return Mca384DropTipsStep(labware_name=labware)
        if command_id == "Mca384MoveArm":
            movement_type = text(root, "MovementType") or "GlobalZTravel"
            return Mca384MoveArmStep(movement_type=movement_type, labware_name=labware)
        if command_id == "Mca384Mix" and labware and volume is not None:
            cycles_text = text(root, "Cycles")
            cycles = int(cycles_text) if cycles_text and cycles_text.isdigit() else 10
            return Mca384MixStep(
                labware_name=labware,
                volume=volume,
                cycles=cycles,
                liquid_class=liquid_class,
            )
        if command_id == "Mca384EmptyTips" and labware:
            return Mca384EmptyTipsStep(
                labware_name=labware,
                volume=volume or 0,
                liquid_class=liquid_class,
            )
        return None

    def _xml_text(self, root: ET.Element, tag: str) -> str | None:
        for elem in root.iter():
            if elem.tag.split("}")[-1] == tag and elem.text is not None:
                text = elem.text.strip()
                if text:
                    return text
        return None

    def _xml_volume(self, root: ET.Element) -> float | str | None:
        for tag in ("Volume", "Volumes"):
            holder = next((elem for elem in root.iter() if elem.tag.split("}")[-1] == tag), None)
            if holder is None:
                continue
            direct = (holder.text or "").strip()
            if direct:
                return self._parse_number_or_name(direct)
            for child in holder.iter():
                if child is holder:
                    continue
                child_text = (child.text or "").strip()
                if child_text:
                    return self._parse_number_or_name(child_text)
        return None

    def _parse_number_or_name(self, value: str) -> float | str:
        try:
            return float(value)
        except ValueError:
            return value

    def _aspirate_one(self, labware: Labware, well, volume_ul: float, tip: Tip) -> None:
        # Magnet-aware: top-down aspirate skips pinned layers when magnetized.
        # Strict: explicit aspirate of a pinned layer would be an explicit-layer
        # call (not yet wired in v1). Plain auto-parallel aspirate is treated
        # as "draw top-down across non-pinned layers."
        is_mag = labware.is_magnetized
        remaining = volume_ul
        if remaining <= 0:
            return
        if well.cap_closed:
            raise _with_sim_details(
                CannotAspirateError(
                    f"Aspirate: well {well.address!r} on {labware.label!r} — "
                    f"tube cap is closed"
                ),
                category="cannot_aspirate_cap_closed",
                operation="Aspirate",
                labware=labware.label,
                well=well.address,
                requested_volume_ul=volume_ul,
                current_volume_ul=well.volume_ul,
            )
        # Tally the request before drawing — counts intent, not delivery,
        # so the running total is meaningful even on failed aspirates.
        self._source_requested_ul[labware.label] = (
            self._source_requested_ul.get(labware.label, 0.0) + volume_ul
        )
        well_map = self._source_requested_by_well_ul.setdefault(labware.label, {})
        well_map[well.address] = well_map.get(well.address, 0.0) + volume_ul
        aspiratable_ul = sum(
            layer.volume_ul
            for layer in well.layers
            if not (is_mag and layer.reagent.pinned_when_magnetized)
        )
        # Walk layers top-down.
        i = len(well.layers) - 1
        while remaining > 0 and i >= 0:
            layer = well.layers[i]
            if is_mag and layer.reagent.pinned_when_magnetized:
                i -= 1
                continue
            take = min(layer.volume_ul, remaining)
            layer.volume_ul -= take
            remaining -= take
            tip.layers.append(Layer(reagent=layer.reagent, volume_ul=take))
            if layer.volume_ul <= 1e-9:
                # Remove the now-empty layer; index unchanged for next iter.
                del well.layers[i]
            i -= 1
        if remaining > 1e-6:
            if is_mag and well.volume_ul > 1e-9 and aspiratable_ul <= 1e-9:
                top = well.layers[-1] if well.layers else None
                pinned_name = top.reagent.name if top is not None else "pinned reagent"
                raise _with_sim_details(
                    CannotAspirateError(
                        f"Aspirate: well {well.address!r} on {labware.label!r} — "
                        f"all {well.volume_ul:.2f} uL is pinned on magnet "
                        f"({pinned_name!r} has pinned_when_magnetized=True)"
                    ),
                    category="cannot_aspirate_pinned",
                    operation="Aspirate",
                    labware=labware.label,
                    well=well.address,
                    requested_volume_ul=volume_ul,
                    current_volume_ul=well.volume_ul,
                )
            raise _with_sim_details(
                InsufficientVolumeError(
                    f"Aspirate: well {well.address!r} on {labware.label!r} short by "
                    f"{remaining:.2f} uL (after skipping pinned layers if magnetized)"
                ),
                category="source_volume_short",
                operation="Aspirate",
                labware=labware.label,
                well=well.address,
                requested_volume_ul=volume_ul,
                short_by_ul=remaining,
                current_volume_ul=well.volume_ul,
            )
        if tip.volume_ul > tip.capacity_ul + 1e-6:
            raise _with_sim_details(
                OverdrawError(
                    f"Aspirate: tip would hold {tip.volume_ul:.2f} uL but capacity is "
                    f"{tip.capacity_ul:.2f} uL"
                ),
                category="tip_capacity",
                operation="Aspirate",
                requested_volume_ul=volume_ul,
                current_volume_ul=tip.volume_ul,
                capacity_ul=tip.capacity_ul,
            )

    def _dispense_one(self, labware: Labware, well, volume_ul: float, tip: Tip) -> None:
        if volume_ul <= 0:
            return
        if well.cap_closed:
            raise _with_sim_details(
                CannotAspirateError(
                    f"Dispense: well {well.address!r} on {labware.label!r} — "
                    f"tube cap is closed"
                ),
                category="cannot_dispense_cap_closed",
                operation="Dispense",
                labware=labware.label,
                well=well.address,
                attempted_delta_ul=volume_ul,
                current_volume_ul=well.volume_ul,
            )
        if tip.volume_ul + 1e-9 < volume_ul:
            raise OverdrawError(
                f"Dispense: tip holds {tip.volume_ul:.2f} µL but {volume_ul:.2f} µL requested"
            )
        if well.volume_ul + volume_ul > well.max_volume_ul + 1e-6:
            raise _with_sim_details(
                OverdrawError(
                    f"Dispense: well {well.address!r} would overflow "
                    f"({well.volume_ul + volume_ul:.2f} > {well.max_volume_ul:.2f} uL; "
                    f"current {well.volume_ul:.2f} uL, dispense {volume_ul:.2f} uL)"
                ),
                category="well_overflow",
                operation="Dispense",
                labware=labware.label,
                well=well.address,
                current_volume_ul=well.volume_ul,
                attempted_delta_ul=volume_ul,
                capacity_ul=well.max_volume_ul,
            )
        # Tip dispenses FIFO (bottom of tip's layer stack first).
        remaining = volume_ul
        while remaining > 0 and tip.layers:
            layer = tip.layers[0]
            take = min(layer.volume_ul, remaining)
            layer.volume_ul -= take
            remaining -= take
            well.add_layer(layer.reagent, take)
            if layer.volume_ul <= 1e-9:
                del tip.layers[0]

    def _empty_tip_one(self, tip: Tip | None, volume_ul: float, well=None) -> None:
        if tip is None:
            return
        remaining = tip.volume_ul if volume_ul <= 0 else min(volume_ul, tip.volume_ul)
        while remaining > 0 and tip.layers:
            layer = tip.layers[0]
            take = min(layer.volume_ul, remaining)
            layer.volume_ul -= take
            remaining -= take
            if well is not None:
                well.add_layer(layer.reagent, take)
            if layer.volume_ul <= 1e-9:
                del tip.layers[0]

    def _validate_mix_one(self, labware: Labware, well, volume_ul: float, tip: Tip) -> None:
        if volume_ul <= 0:
            return
        if well.cap_closed:
            raise CannotAspirateError(
                f"Mix: well {well.address!r} on {labware.label!r} — tube cap is closed"
            )
        available = sum(
            layer.volume_ul
            for layer in well.layers
            if not (labware.is_magnetized and layer.reagent.pinned_when_magnetized)
        )
        if available + 1e-9 < volume_ul:
            raise InsufficientVolumeError(
                f"Mix: well {well.address!r} on {labware.label!r} holds "
                f"{available:.2f} uL available but {volume_ul:.2f} uL requested"
            )
        if tip.volume_ul + volume_ul > tip.capacity_ul + 1e-6:
            raise OverdrawError(
                f"Mix: tip would hold {tip.volume_ul + volume_ul:.2f} uL but "
                f"capacity is {tip.capacity_ul:.2f} uL"
            )

    def _iter_aspirate_wells(self, labware: Labware):
        if labware.wells:
            return list(labware.wells.values())
        return []

    def _liha_get_tips_channels(self, step: LihaGetTipsStep) -> list[int]:
        if step.tip_channels:
            channels = sorted(set(step.tip_channels))
            for ch in channels:
                if ch < 0 or ch > 7:
                    raise MissingTipsError(f"LiHa channel index {ch} is outside 0..7")
            return channels
        return self._liha_channels(step.tip_index)

    def _liha_channels(self, tip_index: int | None = None) -> list[int]:
        if tip_index is None:
            return list(range(8))
        if tip_index < 0 or tip_index > 7:
            raise MissingTipsError(f"LiHa channel index {tip_index} is outside 0..7")
        return [tip_index]

    def _liha_wells(
        self,
        labware: Labware,
        well_offset,
        selection: str | None,
        tip_channels: list[int] | None = None,
    ) -> list[tuple[int, object]]:
        wells = self._iter_aspirate_wells(labware)
        if not wells:
            raise InsufficientVolumeError(f"{labware.label!r} has no pipettable wells")
        if tip_channels:
            selected_channels = [
                channel for channel in tip_channels if self._liha_tips[channel] is not None
            ]
            if not selected_channels:
                raise MissingTipsError("LiHa pipetting requested without mounted tips on selected channels")
        else:
            selected_channels = [i for i, tip in enumerate(self._liha_tips) if tip is not None]
            if not selected_channels:
                raise MissingTipsError("LiHa pipetting requested without mounted tips")
        if len(wells) == 1 and labware.category == "trough" and selection is None and well_offset is None:
            return [(channel, wells[0]) for channel in selected_channels]
        indexes: list[int]
        if selection:
            indexes = self._parse_selection(selection, [well.address for well in wells])
        else:
            offset = 0
            if well_offset is not None:
                offset = self._resolve_sim_int(well_offset)
            indexes = [offset + i for i in range(len(selected_channels))]
        out: list[tuple[int, object]] = []
        for channel, index in zip(selected_channels, indexes):
            if index < 0 or index >= len(wells):
                raise InsufficientVolumeError(
                    f"LiHa well index {index} is outside {labware.label!r} well range"
                )
            out.append((channel, wells[index]))
        return out

    def _parse_selection(self, selection: str, well_addresses: list[str]) -> list[int]:
        address_to_index = {
            address.upper(): index for index, address in enumerate(well_addresses)
        }
        values: list[int] = []
        for token in selection.replace(",", ";").split(";"):
            token = token.strip()
            if not token:
                continue
            upper = token.upper()
            if upper in address_to_index:
                values.append(address_to_index[upper])
                continue
            if ">" in token:
                parts = [part.strip() for part in token.split(">") if part.strip()]
                if len(parts) == 3:
                    try:
                        start, stride, end = (int(part) for part in parts)
                    except ValueError:
                        pass
                    else:
                        if stride == 0:
                            raise MissingSimValueError(
                                f"LiHa serialized selection {selection!r} has stride 0"
                            )
                        stop = end + (1 if stride > 0 else -1)
                        values.extend(range(start, stop, stride))
                        continue
            try:
                values.append(int(token))
            except ValueError:
                if "-" in token:
                    left, right = (part.strip() for part in token.split("-", 1))
                    left_upper = left.upper()
                    right_upper = right.upper()
                    if left_upper in address_to_index and right_upper in address_to_index:
                        start = address_to_index[left_upper]
                        end = address_to_index[right_upper]
                        step = 1 if end >= start else -1
                        values.extend(range(start, end + step, step))
                        continue
                    values.extend(range(int(left), int(right) + 1))
        return values

    def _require_liha_tip(self, channel: int, action: str) -> Tip:
        tip = self._liha_tips[channel]
        if tip is None:
            raise MissingTipsError(f"{action}: LiHa channel {channel} has no tip")
        return tip

    def _require_labware(self, label: str, action: str) -> Labware:
        target = self._lookup_labware(label)
        if target is None:
            raise InsufficientVolumeError(f"{action} target {label!r} is not on the worktable")
        return target

    def _lookup_labware(self, label: str) -> Labware | None:
        """Resolve an exact or runtime-expanded FluentControl labware label.

        Source scripts commonly declare a labware as ``Plate[counter]`` and
        later address it through another counter that currently has the same
        value.  The authoring IR must retain those source strings exactly, but
        simulation needs their evaluated identity to reconstruct deck state.
        """
        exact = self._twin.get(label)
        if exact is not None or "[" not in label:
            return exact

        runtime_label = self._resolve_runtime_label(label)
        matches = [
            labware
            for known_label, labware in self._twin.items()
            if self._resolve_runtime_label(known_label) == runtime_label
        ]
        if len(matches) > 1:
            raise SimulationError(
                f"Runtime labware label {label!r} is ambiguous after evaluation "
                f"as {runtime_label!r}."
            )
        return matches[0] if matches else None

    def _resolve_runtime_label(self, label: str) -> str:
        def replace(match: re.Match[str]) -> str:
            source = match.group(1).strip()
            try:
                value = self._evaluate_sim_expression(source)
            except MissingSimValueError:
                return match.group(0)
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            return f"[{value}]"

        return re.sub(r"\[([^\[\]]+)\]", replace, label)

    def _resolve_runtime_location(self, location: str) -> str:
        try:
            expression = coerce_source_expression(location)
        except Exception:
            return location
        if not isinstance(expression, FunctionCall):
            return location
        return str(self._evaluate_sim_expression(expression))

    @staticmethod
    def _cover_site_target(location: str) -> str | None:
        match = re.fullmatch(r'\s*GetCoverSiteName\("([^"]+)"\)\s*', location or "")
        return match.group(1) if match else None

    def _infer_liha_tip_capacity(self, catalog_name: str) -> float:
        lowered = (catalog_name or "").lower()
        if "50ul" in lowered or "50 ul" in lowered:
            return 50.0
        if "200ul" in lowered or "200 ul" in lowered:
            return 200.0
        return 1000.0

    # ── Loops / conditionals ────────────────────────────────────────

    def _on_loop(self, step: LoopStep) -> None:
        count = step.number_of_loops if step.number_of_loops is not None else step.iterations
        for iteration in range(1, self._resolve_sim_int(count) + 1):
            scope = {step.loop_variable: iteration} if step.loop_variable else {}
            self._sim_scope_stack.append(scope)
            try:
                for child in step.steps:
                    self._dispatch(child)
            finally:
                self._sim_scope_stack.pop()

    def _on_conditional(self, step: ConditionalStep) -> None:
        if step.condition is not None:
            truthy = bool(self._evaluate_sim_expression(step.condition))
        else:
            left = self._resolve_sim_value(step.left_variable)
            right = self._resolve_sim_value(step.right_value) if step.right_is_variable else self._evaluate_sim_expression(step.right_value)
            op = step.operator
            # Coerce to common type for comparison.
            try:
                l, r = float(left), float(right)
            except (TypeError, ValueError):
                l, r = left, right
            truth_table = {
                "==": l == r, "!=": l != r,
                ">": l > r, "<": l < r, ">=": l >= r, "<=": l <= r,
            }
            truthy = truth_table.get(op, False)
        branch = step.then_steps if truthy else step.else_steps
        for child in branch:
            self._dispatch(child)

    # ── Reporting helpers ───────────────────────────────────────────

    def _warn(self, message: str) -> None:
        if message not in self._report.warnings:
            self._report.warnings.append(message)

    def _labware_summary(self) -> dict:
        out = {}
        for stack in self._slot_map.values():
            for labware in stack:
                wells = {}
                for address, well in labware.wells.items():
                    if well.volume_ul <= 1e-9 and not well.layers:
                        continue
                    wells[address] = {
                        "volume_ul": well.volume_ul,
                        "layers": [
                            {
                                "reagent": layer.reagent.name,
                                "volume_ul": layer.volume_ul,
                            }
                            for layer in well.layers
                        ],
                    }
                out[labware.label] = {
                    "catalog_name": labware.catalog_name,
                    "slot": list(labware.slot) if labware.slot else None,
                    "body_geometry": labware.body_geometry(),
                    "wells": wells,
                    "total_volume_ul": sum(
                        well.volume_ul for well in labware.wells.values()
                    ),
                }
        return out

    def _tip_summary(self, tips: list[Tip], *, start_index: int = 0) -> list[dict]:
        return [
            {
                "index": start_index + i,
                "capacity_ul": tip.capacity_ul,
                "volume_ul": tip.volume_ul,
                "layers": [
                    {
                        "reagent": layer.reagent.name,
                        "volume_ul": layer.volume_ul,
                    }
                    for layer in tip.layers
                ],
            }
            for i, tip in enumerate(tips)
        ]

    def _state_summary(self) -> dict:
        failure = self._report.failure.to_dict() if self._report.failure else None
        failure_details = (failure or {}).get("details") or {}
        failing_labware = failure_details.get("labware")
        failing_well = failure_details.get("well")
        return {
            "deck_occupancy": self._deck_occupancy_summary(),
            "tip_state": self._tip_state_summary(),
            "labware_volumes": self._labware_volume_summary(failing_labware, failing_well),
            "reagent_source_sufficiency": self._source_sufficiency_summary(failure),
        }

    def _deck_occupancy_summary(self) -> dict:
        occupied_slots = []
        for slot, stack in sorted(self._slot_map.items()):
            if not stack:
                continue
            occupied_slots.append(
                {
                    "location": slot[0],
                    "position": slot[1],
                    "stack": [labware.label for labware in stack],
                }
            )
        return {
            "occupied_slots": occupied_slots,
            "occupied_count": len(occupied_slots),
        }

    def _tip_state_summary(self) -> dict:
        tip_boxes = []
        for stack in self._slot_map.values():
            for labware in stack:
                if isinstance(labware, TipBox):
                    tip_boxes.append(
                        {
                            "label": labware.label,
                            "slot": list(labware.slot) if labware.slot else None,
                            "capacity_ul": labware.capacity_ul,
                            "is_full": bool(labware.is_full),
                            "consumed": not bool(labware.is_full),
                            "tip_count": labware.tip_count,
                            "occupied_tip_count": labware.occupied_tip_count,
                            "available_tip_count": labware.available_tip_count,
                            "used_tip_count": labware.used_tip_count,
                            "locations": {
                                address: tip.to_geometry()
                                for address, tip in labware.tip_locations.items()
                            },
                        }
                    )
        mounted_mca = self._tip_summary(self._mca_tips)
        mounted_liha = [
            None if tip is None else self._tip_summary([tip], start_index=i)[0]
            for i, tip in enumerate(self._liha_tips)
        ]
        return {
            "mca96": {
                "mounted_count": len(mounted_mca),
                "capacity_ul": _unique_values(tip["capacity_ul"] for tip in mounted_mca),
                "total_volume_ul": sum(float(tip["volume_ul"]) for tip in mounted_mca),
                "tip_box_label": self._mca_tip_box_label,
            },
            "liha": {
                "mounted_count": sum(1 for tip in mounted_liha if tip is not None),
                "capacity_ul": _unique_values(tip["capacity_ul"] for tip in mounted_liha if tip is not None),
                "total_volume_ul": sum(float(tip["volume_ul"]) for tip in mounted_liha if tip is not None),
                "tip_box_label": self._liha_tip_box_label,
            },
            "tip_boxes": sorted(tip_boxes, key=lambda item: item["label"]),
        }

    def _labware_volume_summary(self, failing_labware=None, failing_well=None) -> dict:
        labware_summaries = {}
        for stack in self._slot_map.values():
            for labware in stack:
                if isinstance(labware, TipBox):
                    continue
                volumes = [float(well.volume_ul) for well in labware.wells.values()]
                nonempty = [volume for volume in volumes if volume > 1e-9]
                failing_wells = {}
                if labware.label == failing_labware and failing_well in labware.wells:
                    well = labware.wells[failing_well]
                    failing_wells[failing_well] = {
                        "volume_ul": well.volume_ul,
                        "max_volume_ul": well.max_volume_ul,
                    }
                labware_summaries[labware.label] = {
                    "catalog_name": labware.catalog_name,
                    "slot": list(labware.slot) if labware.slot else None,
                    "well_count": len(volumes),
                    "nonempty_well_count": len(nonempty),
                    "total_volume_ul": sum(volumes),
                    "min_volume_ul": min(volumes) if volumes else 0.0,
                    "max_volume_ul": max(volumes) if volumes else 0.0,
                    "failing_wells": failing_wells,
                }
        return labware_summaries

    def _source_sufficiency_summary(self, failure: dict | None) -> dict:
        # Running tally of total requested volume across the sim, keyed by
        # source labware. Populated on every run regardless of failure state.
        sources: dict[str, dict] = {}
        for label, total in sorted(self._source_requested_ul.items()):
            twin = self._twin.get(label)
            remaining = (
                sum(float(well.volume_ul) for well in twin.wells.values())
                if twin is not None and twin.wells
                else None
            )
            wells_drawn_full = dict(self._source_requested_by_well_ul.get(label, {}))
            sample_wells_drawn = dict(list(sorted(wells_drawn_full.items()))[:12])
            max_per_well = max(wells_drawn_full.values()) if wells_drawn_full else 0.0
            sources[label] = {
                "total_requested_ul": total,
                "remaining_volume_ul": remaining,
                "well_count": len(wells_drawn_full),
                "max_per_well_requested_ul": max_per_well,
                "sample_wells_drawn": sample_wells_drawn,
                "wells_drawn": sample_wells_drawn,
                "wells_drawn_truncated": len(wells_drawn_full) > len(sample_wells_drawn),
            }
        summary: dict[str, Any] = {"sources": sources}
        # Backwards-compatible top-level fields populate only on the
        # specific source_volume_short failure case (existing tests rely
        # on this shape).
        if failure and failure.get("category") == "source_volume_short":
            details = failure.get("details") or {}
            label = details.get("labware")
            if label:
                summary.update(
                    {
                        "source_label": label,
                        "well": details.get("well"),
                        "requested_volume_ul": details.get("requested_volume_ul"),
                        "remaining_volume_ul": details.get("current_volume_ul"),
                        "short_by_ul": details.get("short_by_ul"),
                    }
                )
        return summary

    # ── Sim-time variable helpers ───────────────────────────────────

    def _apply_variable_mappings_start(
        self, mappings: list[VariableMapping]
    ) -> dict[str, Any]:
        scope: dict[str, Any] = {}
        for mapping in mappings:
            scope[mapping.target] = self._resolve_mapping_source(
                mapping.source, from_subroutine=False
            )
        return scope

    def _apply_variable_mappings_end(self, mappings: list[VariableMapping]) -> None:
        if not mappings:
            return
        for mapping in mappings:
            value = self._resolve_mapping_source(mapping.source, from_subroutine=True)
            self._wt.sim_values[mapping.target] = value

    def _resolve_mapping_source(self, source: Any, *, from_subroutine: bool) -> Any:
        raw_source = source
        try:
            expression = coerce_source_expression(source)
        except Exception:
            expression = StringLiteral(value=str(source or ""))
        if isinstance(expression, VariableReference):
            source = expression.name
        elif not isinstance(expression, StringLiteral):
            return self._evaluate_sim_expression(expression)
        else:
            source = expression.value
        if from_subroutine:
            for scope in reversed(self._sim_scope_stack):
                if source in scope:
                    return scope[source]
        else:
            if source in self._wt.sim_values:
                return self._wt.sim_values[source]
            if source in self._wt.protocol_variables:
                return self._wt.protocol_variables[source]
        literal = self._coerce_mapping_literal(source)
        if literal is not None:
            return literal
        raise MissingSimValueError(
            f"No sim-time value for subroutine mapping source {render_expression(expression) if expression else raw_source!r}."
        )

    @staticmethod
    def _coerce_mapping_literal(source: str) -> Any | None:
        try:
            if any(ch in source for ch in (".", "e", "E")):
                return float(source)
            return int(source)
        except ValueError:
            if source:
                return source
            return None

    def _resolve_sim_volume(self, volume: Any) -> float:
        return self._resolve_sim_number(volume)

    def _resolve_sim_int(self, value: Any) -> int:
        return int(self._resolve_sim_number(value))

    def _resolve_sim_number(self, value: Any) -> float:
        v = self._evaluate_sim_expression(value)
        try:
            return float(v)
        except (TypeError, ValueError) as exc:
            raise MissingSimValueError(
                f"Sim-time value for {render_expression(coerce_source_expression(value))!r} is not numeric: {v!r}"
            ) from exc

    def _evaluate_sim_expression(self, value: Any) -> Any:
        expression = coerce_source_expression(value)
        if isinstance(expression, NumberLiteral):
            return expression.value
        if isinstance(expression, StringLiteral):
            return expression.value
        if isinstance(expression, BooleanLiteral):
            return expression.value
        if isinstance(expression, VariableReference):
            return self._resolve_sim_value(expression.name)
        if isinstance(expression, UnaryExpression):
            operand = self._resolve_sim_number(expression.operand)
            return operand if expression.operator == "+" else -operand
        if isinstance(expression, BinaryExpression):
            return self._evaluate_sim_binary_expression(expression)
        if isinstance(expression, FunctionCall):
            function_name = expression.name.casefold()
            if function_name in {"if", "iif"} and len(expression.arguments) == 3:
                condition, when_true, when_false = expression.arguments
                return self._evaluate_sim_expression(
                    when_true if bool(self._evaluate_sim_expression(condition)) else when_false
                )
            if function_name == "ceil" and len(expression.arguments) == 1:
                return math.ceil(self._resolve_sim_number(expression.arguments[0]))
            if function_name in {"getcoversitename", "getcoversiteindex"} and len(expression.arguments) == 1:
                cover_label = str(self._evaluate_sim_expression(expression.arguments[0]))
                cover = self._twin.get(cover_label)
                if cover is None or cover.slot is None:
                    raise MissingSimValueError(
                        f"Simulator cannot resolve {expression.name}({cover_label!r}): "
                        "the cover labware has no simulated deck slot."
                    )
                return cover.slot[0] if function_name == "getcoversitename" else cover.slot[1]
            raise MissingSimValueError(
                f"Simulator cannot evaluate function expression {render_expression(expression)!r}."
            )
        if isinstance(expression, (SourcePreservedExpression, ReviewedRawExpression)):
            return self._resolve_sim_value(expression.source)
        return expression_python_value(expression)

    def _evaluate_sim_binary_expression(self, expression: BinaryExpression) -> Any:
        op = expression.operator
        if op in {"AND", "OR"}:
            left = bool(self._evaluate_sim_expression(expression.left))
            if op == "AND" and not left:
                return evaluate_binary_operator(op, left, False)
            if op == "OR" and left:
                return evaluate_binary_operator(op, left, False)
            return evaluate_binary_operator(op, left, bool(self._evaluate_sim_expression(expression.right)))
        left = self._evaluate_sim_expression(expression.left)
        right = self._evaluate_sim_expression(expression.right)
        try:
            return evaluate_binary_operator(op, left, right)
        except (TypeError, ValueError) as exc:
            raise MissingSimValueError(
                f"Simulator cannot evaluate binary expression {render_expression(expression)!r}: {exc}"
            ) from exc

    def _resolve_sim_value(self, name: Any):
        if not isinstance(name, str):
            expression = coerce_source_expression(name)
            if isinstance(expression, VariableReference):
                name = expression.name
            elif isinstance(expression, StringLiteral):
                name = expression.value
            else:
                return self._evaluate_sim_expression(expression)
        for scope in reversed(self._sim_scope_stack):
            if name in scope:
                return scope[name]
        if name in self._wt.sim_values:
            return self._wt.sim_values[name]
        if name in self._wt.protocol_variables:
            return self._wt.protocol_variables[name]
        raise MissingSimValueError(
            f"No sim-time value for runtime variable {name!r}. "
            f"Call `wt.set_sim_value({name!r}, <value>)` before simulating."
        )


def _with_sim_details(exc: Exception, *, category: str, **details):
    setattr(exc, "sim_category", category)
    setattr(exc, "sim_details", {key: value for key, value in details.items() if value is not None})
    return exc


def _failure_operation(command_id: str | None) -> str | None:
    if not command_id:
        return None
    lowered = command_id.lower()
    if "aspirate" in lowered:
        return "Aspirate"
    if "dispense" in lowered:
        return "Dispense"
    if "gettips" in lowered or "pickup" in lowered:
        return "GetTips"
    if "addlabware" in lowered:
        return "AddLabware"
    if "setlocation" in lowered:
        return "SetLocation"
    return command_id


def _repair_options(category: str) -> list[str]:
    options = {
        "tip_capacity": ["use_higher_capacity_tips", "split_operation_volume"],
        "tip_box_empty": ["use_fresh_tip_box", "keep_mounted_tips", "return_tips_before_reuse"],
        "source_volume_short": ["increase_source_initial_volume", "reduce_requested_transfer_volume"],
        "well_overflow": ["aspirate_before_dispensing_more", "split_cycles", "use_higher_capacity_labware"],
        "slot_occupied": ["choose_another_valid_slot", "stack_intentionally_with_gripper_move"],
    }
    return options.get(category, [])


def _unique_values(values) -> list:
    return sorted({value for value in values})
