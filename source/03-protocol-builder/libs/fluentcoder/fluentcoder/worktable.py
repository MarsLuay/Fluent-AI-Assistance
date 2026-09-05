"""Worktable — the root of a fluentcoder protocol.

Collects IR steps, owns the pipetting heads + gripper, exposes sim-time
values for the simulator to consume. Snapshots are populated when
`simulate()` is called (lazy — the twin is *not* mutated by author method
calls; it is reconstructed by the Simulator from the IR list).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Collection, Iterator, Optional, Union

if TYPE_CHECKING:
    from .simulator.options import SimulationOptions

from .expressions import Expression, coerce_source_expression, expression_python_value, render_expression
from .fc_variables import (
    FCVariableToken, as_labware_type, is_valid_fc_variable_name,
)
from .ir.schema import (
    AddLabwareStep, ApplicationDriverMacroStep, CommentStep, ConditionalStep,
    EndScriptStep, ExecuteApplicationStep, ExportVariableStep, GenericStep, Group,
    ImportVariableStep, InitializeDeviceStep, LoopStep, MoveAxisCommandStep,
    LihaDetectLiquidStep, GenerateReportStep,
    Protocol, QueryVariableStep, RemoveLabwareStep, ScriptGroupStep, SetLocationStep,
    SetVariableStep, StartMoveCommandStep, StartTimerStep, Step, SubRoutineStep,
    UserPromptStep, VariableMapping, WaitForAsyncResponseStep, WaitForTimerStep, WaitStep,
    ExecuteVbScriptStep, TeGioSetPwmOutputStep, LeaveStep,
)
from .labware.base import Labware

if TYPE_CHECKING:
    from .simulator.snapshots import Snapshot
    from .simulator.report import SimulationReport



@dataclass(frozen=True)
class MoveAxisConfig:
    available_id: Optional[str] = None
    id_label: Optional[str] = None
    position: Union[float, int, str, Expression] = 0
    charge_condition: Union[str, Expression, None] = None
    max_speed: Optional[str] = None
    acceleration: Optional[str] = None
    deceleration: Optional[str] = None
    raw_xml: Optional[str] = None


class Worktable:
    """The Tecan worktable.

    Authoring API:
        - `place(labware, location, position)` — add labware to a slot.
        - `group(name)` — start a new protocol group (Setup / Transfer / …).
        - `set_sim_value(name, value)` — provide a concrete value the
          simulator should use for a runtime variable.
        - `simulate()` — replay the IR through the Simulator, populating
          `snapshots`.
        - `compile(out_path)` — render `.xscr` via the vendored renderer.
    """

    def __init__(self, *, name: str = "Untitled Protocol", comment: str = "") -> None:
        self.name: str = name
        self.comment: str = comment

        # Slot map: (location, position) → list[Labware] bottom→top.
        # Tracked at authoring time so `gripper.move(onto=...)` and
        # `place(...)`'s occupied-slot check have something to work with.
        self.slot_map: dict[tuple[str, int], list[Labware]] = {}

        # Optional valid-slot whitelist set by `from_workspace`. When non-empty,
        # `place()` raises `InvalidSlotError` for slots outside the set.
        self.valid_slots: Optional[set[tuple[str, int]]] = None
        self.workspace_name: Optional[str] = None
        self.workspace_guid: Optional[str] = None
        # Optional device bindings for compile/render (ZEIA/site). Empty → fail-closed
        # when a step template requires DeviceAlias/AvailableID.
        self.device_alias: Optional[str] = None
        self.available_id: Optional[str] = None
        # RGA ApplicationDriverMacro ModuleName from ZEIA/recipe. Empty → fail-closed
        # on RGA transfer (never invent "RGA 1" in the renderer).
        self.rga_module_name: Optional[str] = None

        # Groups + steps collected as the author calls methods.
        self._groups: list[Group] = []
        self._active_group: Optional[Group] = None
        self.protocol_variables: dict[str, Union[float, int, str]] = {}
        self.protocol_variable_metadata: dict[str, dict[str, str]] = {}
        # Workspace occupants that exist before the script begins. These are
        # simulation-only context, never emitted as AddLabware statements.
        self._simulation_seed_labware: list[Labware] = []
        self._simulation_context_warnings: list[str] = []
        self._simulation_unresolved_seed_labels: set[str] = set()

        # Sim-time values — required for any runtime variable the simulator
        # must resolve (loop counts, conditional predicates, imports).
        self.sim_values: dict[str, Any] = {}

        # Stack of step-list targets. While a `with wt.loop(...)` / `with
        # wt.conditional(...)` block is active, _emit() appends to the
        # topmost list (the loop/conditional body); otherwise it appends
        # to the active group.
        self._emit_target_stack: list[list[Step]] = []
        self._next_step_disabled = False

        # Filled by `simulate()`.
        self.snapshots: list["Snapshot"] = []
        self.simulation_report: Optional["SimulationReport"] = None

        # Devices.
        from .heads import FCAHead, LiHa, MCA96Head, MCA384Head
        from .gripper import Gripper
        self.mca96: MCA96Head = MCA96Head(self)
        self.mca384: MCA384Head = MCA384Head(self)
        self.liha: LiHa = LiHa(self)
        self.fca: FCAHead = FCAHead(self)
        self.gripper: Gripper = Gripper(self)

    def set_default_catalog(self, labware_class: type, catalog_name: str) -> None:
        """Register a default FluentControl catalog name for ``labware_class``.

        Applies to this protocol's authoring session via the shared defaults
        registry (same as ``fluentcoder.defaults.set_catalog_default``).
        """
        from .defaults import set_catalog_default
        set_catalog_default(labware_class, catalog_name)

    # ── Workspace loader ────────────────────────────────────────────

    @classmethod
    def from_workspace(
        cls,
        name: str,
        *,
        workspace_guid: Optional[str] = None,
        auto_place: bool = True,
        seed_simulation: bool = False,
        simulation_seed_labels: Optional[Collection[str]] = None,
        protocol_name: str = "",
        comment: str = "",
    ) -> "Worktable":
        """Build a Worktable from a FluentControl workspace (`.xwsp`).

        - Registers the valid `(location, position)` slots from the workspace.
          `place()` raises `InvalidSlotError` for slots outside this set.
        - If `auto_place=True` (default), each occupant the workspace already
          has placed is instantiated via the catalog's category-to-class
          dispatch and placed on its slot.
        - If `seed_simulation=True`, named occupants seed the simulator without
          emitting script AddLabware commands.  `simulation_seed_labels` keeps
          this to source-referenced resources only.

        The catalog index must be built; raises if not.
        """
        from .catalog.xcmp import load_xwsp

        ws_file_path = cls._resolve_workspace_file(name, workspace_guid)
        ws = load_xwsp(ws_file_path)

        wt = cls(name=protocol_name or name, comment=comment)
        wt.workspace_name = ws.name
        wt.workspace_guid = ws.guid

        default_loc, slot_position_by_site = wt._initialize_slots_from_workspace(ws)

        wt._place_workspace_occupants(
            ws,
            auto_place,
            seed_simulation,
            simulation_seed_labels,
            default_loc,
            slot_position_by_site,
        )

        return wt

    @classmethod
    def _resolve_workspace_file(cls, name: str, workspace_guid: Optional[str]) -> Any:
        from .catalog.catalog import (
            index_exists, resolve_workspace_by_guid,
            resolve_workspace_by_name,
        )
        from .simulator.invariants import MissingSimValueError

        if not index_exists():
            raise MissingSimValueError(
                "Catalog index is not built; cannot resolve workspace. "
                "Run `fluentcoder catalog refresh` first."
            )

        ws_by_guid = resolve_workspace_by_guid(workspace_guid) if workspace_guid else None
        ws_by_name = resolve_workspace_by_name(name) if name else None
        ws_entry = ws_by_guid or ws_by_name
        if ws_by_guid and ws_by_name:
            guid_path = ws_by_guid.file_path.resolve()
            name_path = ws_by_name.file_path.resolve()
            if guid_path == name_path:
                ws_entry = ws_by_guid
            else:
                raise ValueError(
                    "Workspace reference is ambiguous: "
                    f"GUID {workspace_guid!r} resolved to {ws_by_guid.name!r} at {guid_path!s}, "
                    f"but name {name!r} resolved to {ws_by_name.name!r} at {name_path!s}."
                )
        if ws_entry is None:
            if workspace_guid and name:
                raise ValueError(
                    "Workspace reference could not be resolved from the local catalog index: "
                    f"name={name!r}, guid={workspace_guid!r}. "
                    "Run `fluentcoder catalog refresh` if the workspace is installed locally."
                )
            lookup = workspace_guid or name
            raise ValueError(f"Workspace {lookup!r} not found in catalog index")

        return ws_entry.file_path

    def _initialize_slots_from_workspace(self, ws: Any) -> tuple[str, dict[tuple[tuple[int, ...], str], int]]:
        slot_position_by_site: dict[tuple[tuple[int, ...], str], int] = {}
        slot_counts_by_location: dict[str, int] = {}
        seen_sites: set[tuple[tuple[int, ...], str]] = set()
        generic_site_paths: list[tuple[int, ...]] = []
        seen_generic_site_paths: set[tuple[int, ...]] = set()

        # Build the valid-slots whitelist from EVERY visited site (occupied or
        # not). XWSP site indices are 0-based; FluentControl positions are
        # 1-based — translate at the boundary.
        default_loc = ws.location_names[0] if ws.location_names else "Site"
        valid: set[tuple[str, int]] = set()
        for site_path, base_loc in ws.available_sites:
            if not site_path:
                continue
            if ws.name == "780_Empty" and site_path not in seen_generic_site_paths:
                seen_generic_site_paths.add(site_path)
                generic_site_paths.append(site_path)
            if not base_loc:
                continue
            site_key = (site_path, base_loc)
            if site_key in seen_sites:
                continue
            seen_sites.add(site_key)
            position = slot_position_by_site.get(site_key)
            if position is None:
                position = slot_counts_by_location.get(base_loc, 0) + 1
                slot_counts_by_location[base_loc] = position
                slot_position_by_site[site_key] = position
            valid.add((base_loc, position))
        if ws.name == "780_Empty":
            for position, _ in enumerate(generic_site_paths, start=1):
                valid.add(("Site", position))
        self.valid_slots = valid

        return default_loc, slot_position_by_site

    def _place_workspace_occupants(
        self,
        ws: Any,
        auto_place: bool,
        seed_simulation: bool,
        simulation_seed_labels: Optional[Collection[str]],
        default_loc: str,
        slot_position_by_site: dict[tuple[tuple[int, ...], str], int],
    ) -> None:
        from .catalog.catalog import resolve_by_name
        from .labware import CATEGORY_TO_CLASS

        def _workspace_position(site_path: tuple[int, ...]) -> int:
            if len(site_path) >= 3 and site_path[-1] == 0:
                return site_path[-2] + 1
            return site_path[-1] + 1

        def _occupant_position(occ: Any) -> int:
            site_path = getattr(occ, "site_path", None)
            location_name = getattr(occ, "base_location_identifier", None)
            if site_path and location_name:
                mapped = slot_position_by_site.get((site_path, location_name))
                if mapped is not None:
                    return mapped
            if site_path:
                return _workspace_position(site_path)
            return int(getattr(occ, "site_index", 0)) + 1

        seed_labels = {
            str(label).strip()
            for label in (simulation_seed_labels or ())
            if str(label).strip()
        }

        if auto_place or seed_simulation:
            if auto_place:
                self.group("Worktable Setup")
            used_labels: set[str] = set()
            for occ in ws.occupants:
                if seed_simulation and not auto_place and str(occ.catalog_name) not in seed_labels:
                    continue
                catalog_entry = resolve_by_name(occ.catalog_name)
                loc = occ.base_location_identifier or default_loc
                position = _occupant_position(occ)
                if catalog_entry is None:
                    if seed_simulation and not auto_place:
                        self._simulation_unresolved_seed_labels.add(str(occ.catalog_name))
                        self._simulation_context_warnings.append(
                            f"Workspace seed {occ.catalog_name!r} was skipped because its catalog entry is unavailable."
                        )
                        continue
                    raise ValueError(
                        f"Workspace {ws.name!r} requires occupant {occ.catalog_name!r} "
                        f"at {(loc, position)!r}, but that catalog name is not installed "
                        "in the local fluentcoder catalog index."
                    )
                cls_for_category = CATEGORY_TO_CLASS.get(
                    catalog_entry.category, CATEGORY_TO_CLASS["fixed_deck"]
                )
                position = _occupant_position(occ)
                label = str(occ.catalog_name)
                if label in used_labels:
                    label = f"{label}@{position}"
                used_labels.add(label)
                lw = cls_for_category(label, catalog=occ.catalog_name)
                loc = occ.base_location_identifier or default_loc
                if auto_place:
                    self.place(lw, loc, position)
                else:
                    self.seed_simulation_labware(lw, loc, position)

    # ── Authoring API ───────────────────────────────────────────────

    def group(self, name: str) -> None:
        """Start a new step group (e.g. 'Setup', 'Transfer')."""
        self._active_group = Group(name=name, steps=[])
        self._groups.append(self._active_group)

    @contextmanager
    def nested_group(self, name: str) -> Iterator[ScriptGroupStep]:
        """Emit a nested FluentControl script group."""
        group_step = ScriptGroupStep(name=name, steps=[])
        self._emit_target_stack.append(group_step.steps)
        try:
            yield group_step
        finally:
            self._emit_target_stack.pop()
            self._emit(group_step)

    def declare_variable(
        self,
        name: str,
        default: Union[float, int, str],
        *,
        scope: str = "Script",
        type_name: str = "",
    ) -> None:
        """Declare a FluentControl protocol variable with a default value."""
        self.protocol_variables[name] = default
        metadata: dict[str, str] = {}
        scope_text = str(scope or "Script").strip() or "Script"
        type_name_text = str(type_name or "").strip()
        if scope_text:
            metadata["scope"] = scope_text
        if type_name_text:
            metadata["type_name"] = type_name_text
        if metadata:
            self.protocol_variable_metadata[name] = metadata
        else:
            self.protocol_variable_metadata.pop(name, None)

    def declare_fc_variable(self, name: str) -> FCVariableToken:
        """Return a token usable as ``catalog=`` / ``labware_type`` for FC variables."""
        if not is_valid_fc_variable_name(name):
            raise ValueError(
                f"Invalid FluentControl variable name {name!r}; "
                "use letters, digits, and underscores (must not start with a digit)."
            )
        return FCVariableToken(name)

    def add_labware(
        self,
        labware_type: Union[str, FCVariableToken],
        label: str,
        location: str,
        position: Union[int, str, Expression],
        *,
        rotation: int = 0,
        has_lid: bool = False,
    ) -> None:
        """Emit an ``AddLabwareStep`` with a literal or FC-variable labware type."""
        self._emit(AddLabwareStep(
            labware_type=as_labware_type(labware_type),
            label=label,
            location=location,
            position=position,
            rotation=rotation,
            has_lid=has_lid,
        ))

    def set_sim_value(self, name: str, value: Any) -> None:
        """Provide a concrete value the simulator uses for runtime variable
        references (loop counts, conditional predicates, imports)."""
        self.sim_values[name] = value

    def seed_simulation_labware(self, labware: Labware, location: str, position: int) -> None:
        """Seed known pre-run workspace occupancy without emitting a command.

        Use only for resources read from an existing FluentControl workspace.
        The seed is cloned into the simulator's initial state and cannot change
        the compiled protocol XML.
        """
        if self.has_labware(labware.label) or any(
            item.label == labware.label for item in self._simulation_seed_labware
        ):
            raise ValueError(f"Simulation seed label {labware.label!r} is not unique")
        labware.slot = (location, int(position))
        labware.stack_below = []
        self._simulation_seed_labware.append(labware)

    def set_variable(self, name: str, value: Union[float, int, str, Expression]) -> None:
        self._emit(SetVariableStep(variable_name=name, value=value))

    def wait(self, duration_seconds: Union[int, float, str, Expression]) -> None:
        self._emit(WaitStep(duration_seconds=duration_seconds))

    def add_comment(self, text: str) -> None:
        self._emit(CommentStep(comment=text))

    def user_prompt(
        self,
        prompt: str,
        *,
        timeout: int = 0,
        auto_close: Optional[bool] = None,
        image_path: Optional[str] = None,
        is_image_used: Optional[bool] = None,
        screen_title: Optional[str] = None,
        rup_kind: Optional[str] = None,
        sound_path: Optional[str] = None,
    ) -> None:
        """Emit an operator prompt.

        Plain text compiles to ``UserPromptStatement``. Media prompts compile to
        TouchTools ``RUPStandardStatement`` when ``rup_kind="standard"`` (default
        for generated verification scripts after instrument init), or to
        ``RUPWorktableStatement`` when ``rup_kind="worktable"``.
        """
        if str(rup_kind or "").lower() == "standard":
            self._emit(
                UserPromptStep(
                    prompt=prompt,
                    timeout=timeout,
                    auto_close=auto_close,
                    image_path=image_path,
                    is_image_used=is_image_used if is_image_used is not None else True,
                    screen_title=screen_title,
                    rup_kind="standard",
                    sound_path=sound_path,
                )
            )
            return
        if str(image_path or "").strip():
            self.user_prompt_worktable(
                prompt=prompt,
                timeout=timeout,
                auto_close=auto_close,
                image_path=image_path,
                screen_title=screen_title,
                sound_path=sound_path,
            )
            return
        self._emit(
            UserPromptStep(
                prompt=prompt,
                timeout=timeout,
                auto_close=auto_close,
                image_path=image_path,
                is_image_used=is_image_used,
                screen_title=screen_title,
                sound_path=sound_path,
            )
        )

    def user_prompt_worktable(
        self,
        *,
        prompt: str = "",
        timeout: int = 0,
        auto_close: Optional[bool] = None,
        image_path: Optional[str] = None,
        screen_title: Optional[str] = None,
        selected_labware_name: Optional[str] = None,
        selected_labware_type: Optional[str] = None,
        grid: Optional[int] = None,
        site: Optional[int] = None,
        sound_path: Optional[str] = None,
        raw_xml: Optional[str] = None,
    ) -> None:
        """Emit a TouchTools RUP worktable-bound operator prompt."""
        self._emit(
            UserPromptStep(
                prompt=prompt,
                timeout=timeout,
                auto_close=auto_close,
                image_path=image_path,
                screen_title=screen_title,
                rup_kind="worktable",
                selected_labware_name=selected_labware_name,
                selected_labware_type=selected_labware_type,
                grid=grid,
                site=site,
                sound_path=sound_path,
                raw_xml=raw_xml,
            )
        )

    def execute_vb_script(
        self,
        vb_script: str = "",
        *,
        execution_mode: str = "Synchronous",
        raw_xml: Optional[str] = None,
    ) -> None:
        """Execute an external VB script (runtime side effect; simulator no-op)."""
        self._emit(ExecuteVbScriptStep(
            vb_script=vb_script,
            execution_mode=execution_mode,
            raw_xml=raw_xml,
        ))

    def tegio_set_pwm_output(
        self,
        *,
        pin_number: str = "",
        duty_cycle: Union[int, str, Expression] = 0,
        duration: Union[int, str, Expression] = 0,
        raw_xml: Optional[str] = None,
    ) -> None:
        """Set a TeGIO PWM output pin (hardware IO passthrough)."""
        self._emit(TeGioSetPwmOutputStep(
            pin_number=pin_number,
            duty_cycle=duty_cycle,
            duration=duration,
            raw_xml=raw_xml,
        ))

    def leave(self, *, raw_xml: Optional[str] = None) -> None:
        """Exit the innermost loop/group (control-flow passthrough)."""
        self._emit(LeaveStep(raw_xml=raw_xml))

    def start_timer(self, timer: int = 1) -> None:
        self._emit(StartTimerStep(timer=timer))

    def wait_for_timer(self, timer: int, duration_seconds: Union[int, float, str, Expression]) -> None:
        self._emit(WaitForTimerStep(timer=timer, duration_seconds=duration_seconds))

    def export_variables(
        self,
        variables: list[str],
        export_file: str,
        *,
        write_header: bool = False,
        replace_existing_file: bool = False,
        export_strings_with_quotes: bool = False,
        delimiter_code: int = 59,
    ) -> None:
        self._emit(ExportVariableStep(
            variables=variables,
            export_file=export_file,
            write_header=write_header,
            replace_existing_file=replace_existing_file,
            export_strings_with_quotes=export_strings_with_quotes,
            delimiter_code=delimiter_code,
        ))

    def import_variables(
        self,
        variables: list[str],
        import_file: str,
        *,
        read_line: bool = False,
        line: int = 1,
        start_in_column: bool = False,
        column: int = 1,
        has_header: bool = False,
        delimiter_code: int = 59,
    ) -> None:
        self._emit(ImportVariableStep(
            variables=variables,
            import_file=import_file,
            read_line=read_line,
            line=line,
            start_in_column=start_in_column,
            column=column,
            has_header=has_header,
            delimiter_code=delimiter_code,
        ))

    def query_variable(
        self,
        variable_name: str,
        query_prompt: str,
        *,
        limit_range: bool = False,
    ) -> None:
        self._emit(QueryVariableStep(
            variable_name=variable_name,
            query_prompt=query_prompt,
            limit_range=limit_range,
        ))

    def execute_application(
        self,
        application: str,
        *,
        arguments: str = "",
        wait: bool = True,
        store_return: bool = False,
        variable: str = "",
    ) -> None:
        self._emit(ExecuteApplicationStep(
            application=application,
            arguments=arguments,
            wait=wait,
            store_return=store_return,
            variable=variable,
        ))

    def initialize_device(
        self,
        *,
        device_alias: Optional[str] = None,
        available_id: Optional[str] = None,
        init_type: str = "Initialize",
    ) -> None:
        """Initialize, home, reset, or prime a FluentControl device."""
        if not device_alias and not available_id:
            raise ValueError("initialize_device requires device_alias and/or available_id")
        self._emit(InitializeDeviceStep(
            device_alias=device_alias,
            available_id=available_id,
            init_type=init_type,
        ))

    def call_subroutine(
        self,
        path: str,
        *,
        execution_mode: str = "Synchronous",
        variable_mappings_start: Optional[list[VariableMapping]] = None,
        variable_mappings_end: Optional[list[VariableMapping]] = None,
    ) -> None:
        """Call a FluentControl subroutine by script path."""
        self._emit(SubRoutineStep(
            subroutine=path,
            execution_mode=execution_mode,
            variable_mappings_start=list(variable_mappings_start or []),
            variable_mappings_end=list(variable_mappings_end or []),
        ))

    def move_axis_command(self, config: MoveAxisConfig) -> None:
        """Queue a hardware driver axis move (non-deck motion; simulator no-op)."""
        self._emit(MoveAxisCommandStep(
            available_id=config.available_id,
            id_label=config.id_label,
            position=config.position,
            charge_condition=config.charge_condition,
            max_speed=config.max_speed,
            acceleration=config.acceleration,
            deceleration=config.deceleration,
            raw_xml=config.raw_xml,
        ))

    def start_move_command(
        self,
        *,
        available_id: Optional[str] = None,
        id_label: Optional[str] = None,
        raw_xml: Optional[str] = None,
    ) -> None:
        """Start queued hardware driver axis moves (simulator no-op)."""
        self._emit(StartMoveCommandStep(
            available_id=available_id,
            id_label=id_label,
            raw_xml=raw_xml,
        ))

    def wait_for_async_response(self, *, raw_xml: Optional[str] = None) -> None:
        """Wait for hardware driver async completion (simulator no-op)."""
        self._emit(WaitForAsyncResponseStep(raw_xml=raw_xml))

    def end_script(self, *, return_code: str = "Success", raw_xml: Optional[str] = None) -> None:
        """Terminate script execution early with a return code (simulator no-op)."""
        self._emit(EndScriptStep(return_code=return_code, raw_xml=raw_xml))

    def set_location(
        self,
        labware: Union[Labware, str],
        location: str,
        site: int,
        *,
        rotation: int = 0,
    ) -> None:
        label = labware.label if isinstance(labware, Labware) else labware
        self._emit(SetLocationStep(
            labware=label,
            location=location,
            site=site,
            rotation=rotation,
        ))

    def generic_step(self, step_type: str, **parameters: Any) -> None:
        """Emit a recognized but not yet modeled FluentControl command."""
        self._emit(GenericStep(step_type=step_type, parameters=parameters))

    def raw_xml_step(self, step_type: str, raw_xml: str) -> None:
        """Emit an opaque FluentControl command preserved from a decompiled script."""
        self._emit(GenericStep(step_type=step_type, parameters={"raw_xml": raw_xml}))

    def detect_liquid(
        self,
        labware: Union[Labware, str],
        *,
        sensitivity: Optional[float] = None,
        detection_speed: Optional[float] = None,
    ) -> None:
        """Emit a LiHa liquid-detection step (simulator no-op)."""
        label = labware.label if isinstance(labware, Labware) else labware
        self._emit(LihaDetectLiquidStep(
            labware_name=label or "",
            sensitivity=sensitivity,
            detection_speed=detection_speed,
        ))

    def generate_report(
        self,
        *,
        report_name: str = "",
        write_csv: bool = False,
        print_report: bool = False,
        all_labware_selected: bool = True,
    ) -> None:
        """Emit a sample-tracking report generation step (simulator no-op)."""
        self._emit(GenerateReportStep(
            report_name=report_name,
            write_csv=write_csv,
            print_report=print_report,
            all_labware_selected=all_labware_selected,
        ))

    def application_driver_macro(
        self,
        macro_name: str,
        *,
        module_name: str = "",
        available_id: Optional[str] = None,
        execution_settings: str = "",
        parameters: Optional[dict[str, str]] = None,
        raw_xml: Optional[str] = None,
    ) -> None:
        """Emit a VisionX application-driver macro step."""
        self._emit(ApplicationDriverMacroStep(
            macro_name=macro_name,
            module_name=module_name,
            available_id=available_id,
            execution_settings=execution_settings,
            parameters=dict(parameters or {}),
            raw_xml=raw_xml,
        ))

    @contextmanager
    def loop(
        self,
        *,
        times: Union[int, str],
        name: str = "Loop",
        loop_variable: Optional[str] = None,
        disabled: bool = False,
    ) -> Iterator[LoopStep]:
        """Emit a `LoopStep` whose body is everything authored inside the
        `with` block.

        ``times`` is either a literal `int` (loop runs that many times) or a
        `str` naming a runtime variable. The variable's value at simulation
        time is resolved through `set_sim_value(name, value)`. ``loop_variable``
        is the optional FluentControl per-iteration counter; if omitted, a
        string ``times`` value remains the compatibility default.
        """
        loop_step = LoopStep(
            name=name,
            iterations=times if isinstance(times, int) else 1,
            loop_variable=loop_variable if loop_variable is not None else (
                times if isinstance(times, str) else None
            ),
            number_of_loops=times,
            disabled=disabled,
            steps=[],
        )
        self._emit_target_stack.append(loop_step.steps)
        try:
            yield loop_step
        finally:
            self._emit_target_stack.pop()
            self._emit(loop_step)

    @contextmanager
    def conditional(
        self,
        *,
        left: str = "",
        op: str = "==",
        right: Union[int, float, str, bool, Expression] = True,
        right_is_variable: bool = False,
        name: str = "If",
        condition: Union[str, Expression, None] = None,
        disabled: bool = False,
    ) -> Iterator[ConditionalStep]:
        """Emit a `ConditionalStep` whose then-branch is everything authored
        inside the `with` block.

        ``left`` is a runtime variable name; ``op`` is one of the supported
        comparators (``==``, ``!=``, ``<``, ``<=``, ``>``, ``>=``); ``right``
        is a literal or, with ``right_is_variable=True``, another variable
        name. else-branches are not authored via this context manager in
        v1.1; populate ``cond_step.else_steps`` directly if needed.
        """
        cond_step = ConditionalStep(
            name=name,
            condition=condition,
            left_variable=left,
            operator=op,
            right_value=right,
            right_is_variable=right_is_variable,
            disabled=disabled,
            then_steps=[],
            else_steps=[],
        )
        self._emit_target_stack.append(cond_step.then_steps)
        try:
            yield cond_step
        finally:
            self._emit_target_stack.pop()
            self._emit(cond_step)

    @contextmanager
    def else_branch(self, conditional: ConditionalStep) -> Iterator[ConditionalStep]:
        """Append authored steps to an existing conditional's else branch."""
        self._emit_target_stack.append(conditional.else_steps)
        try:
            yield conditional
        finally:
            self._emit_target_stack.pop()

    def place(
        self,
        labware: Labware,
        location: str,
        position: Union[int, str, Expression],
        *,
        allow_occupied: bool = False,
        allow_invalid_slot: bool = False,
    ) -> Labware:
        """Place labware on the worktable at (location, position).

        Stacking is performed via `gripper.move(onto=...)`, not via place();
        place() refuses an occupied slot at authoring time so accidental
        double-placement surfaces immediately. If the worktable was built via
        `from_workspace`, only slots in `self.valid_slots` are accepted.
        """
        cover_slot = self._cover_site_slot(location, position)
        slot_position = self._slot_position_key(position)
        slot = cover_slot or (location, slot_position)
        dynamic_position = cover_slot is None and not isinstance(slot_position, int)
        if (
            self.valid_slots is not None
            and not dynamic_position
            and slot not in self.valid_slots
            and not allow_invalid_slot
        ):
            from .simulator.invariants import InvalidSlotError
            raise InvalidSlotError(
                f"Slot {slot!r} is not on workspace {self.workspace_name!r}. "
                f"Valid examples: {sorted(self.valid_slots)[:5]}…"
            )
        if (
            not dynamic_position
            and slot in self.slot_map
            and self.slot_map[slot]
            and not (allow_occupied or cover_slot is not None)
        ):
            occupied_by = self.slot_map[slot][-1]
            raise ValueError(
                f"Slot {slot} already occupied by {occupied_by.label!r}. "
                f"Use `gripper.move({labware.label!r}, onto={occupied_by.label!r})` to stack."
            )
        stack = self.slot_map.setdefault(slot, [])
        stack.append(labware)
        labware.slot = slot
        labware.stack_below = list(stack[:-1])
        self._register_child_valid_slots(labware)
        self._emit(AddLabwareStep(
            labware_type=labware.catalog_name,
            label=labware.label,
            location=location,
            position=position,
        ))
        return labware

    def _cover_site_slot(self, location: str, position: Union[int, str, Expression]) -> Optional[tuple[str, int]]:
        """Resolve GetCoverSiteName/Index("Label") helpers for simulation bookkeeping."""
        if not isinstance(location, str):
            return None
        location_match = re.fullmatch(r'GetCoverSiteName\("([^"]+)"\)', location.strip())
        if not location_match:
            return None
        if isinstance(position, str):
            position_match = re.fullmatch(r'GetCoverSiteIndex\("([^"]+)"\)', position.strip())
            if not position_match or location_match.group(1) != position_match.group(1):
                return None
        else:
            try:
                numeric_position = int(expression_python_value(coerce_source_expression(position)))
            except Exception:
                return None
            if numeric_position < 1:
                return None
        if not isinstance(position, str):
            position = str(expression_python_value(coerce_source_expression(position)))
        elif not position:
            return None
        cover_label = location_match.group(1)
        for stack in self.slot_map.values():
            for placed in reversed(stack):
                if placed.label == cover_label and placed.slot is not None:
                    return placed.slot
        return None

    @staticmethod
    def _slot_position_key(position: Union[int, str, Expression]) -> Union[int, str]:
        try:
            expression = coerce_source_expression(position)
            value = expression_python_value(expression)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
            return render_expression(expression)
        except Exception:
            return str(position)

    def remove(self, labware: Union[Labware, str]) -> None:
        """Remove labware from the worktable."""
        if isinstance(labware, str):
            self._emit(RemoveLabwareStep(labware_name=labware))
            return
        if labware.slot is None:
            raise ValueError(f"Cannot remove {labware.label!r}: not on worktable")
        stack = self.slot_map.get(labware.slot, [])
        if labware in stack:
            stack.remove(labware)
            if not stack:
                del self.slot_map[labware.slot]
        labware.slot = None
        labware.stack_below = []
        self._emit(RemoveLabwareStep(labware_name=labware.label))

    # ── Compile / simulate ──────────────────────────────────────────

    def to_protocol(self) -> Protocol:
        """Build a Protocol IR from the collected steps."""
        protocol = Protocol(
            name=self.name,
            comment=self.comment,
            variables=list(self.protocol_variables.keys()),
            variable_defaults=dict(self.protocol_variables),
            variable_metadata=dict(self.protocol_variable_metadata),
            groups=[Group(name=g.name, steps=list(g.steps)) for g in self._groups],
            worktable_guid=self.workspace_guid,
            worktable_name=self.workspace_name,
            device_alias=self.device_alias,
            available_id=self.available_id,
            rga_module_name=self.rga_module_name,
        )
        protocol.assign_line_numbers()
        return protocol

    def compile(
        self,
        out_path: Union[str, Path],
        *,
        deterministic: bool = False,
    ) -> Path:
        """Render the protocol to a `.xscr` file at `out_path`."""
        from .compiler import render_protocol
        from .catalog import rewrite_checksum_in_place

        self._require_bound_workspace()
        protocol = self.to_protocol()
        xml = render_protocol(protocol, deterministic=deterministic)
        path = Path(out_path)
        path.write_text(xml, encoding="utf-8")
        rewrite_checksum_in_place(path)
        return path

    def simulate(
        self,
        options: Optional["SimulationOptions"] = None,
    ) -> None:
        """Replay the IR through the Simulator, populating `self.snapshots`.

        ``record_snapshots=True`` (default) deep-copies twin state after every
        step. ``record_snapshots=False`` skips per-step copies and keeps one
        deep-copied snapshot of the final twin state (lower memory use on long
        protocols). ``record_snapshots="delta"`` stores a lightweight per-step
        diff of changed labware volumes and tip state vs. the previous step.
        ``snapshot_mode`` overrides ``record_snapshots`` when set (``"full"``,
        ``"final_only"``, or ``"delta"``).
        """
        from .simulator import Simulator
        from .simulator.options import SimulationOptions

        options = options or SimulationOptions()

        if options.snapshot_mode is not None:
            effective_snapshot_mode = options.snapshot_mode
        elif options.record_snapshots == "delta":
            effective_snapshot_mode = "delta"
        elif options.record_snapshots is False:
            effective_snapshot_mode = "final_only"
        else:
            effective_snapshot_mode = "full"
        sim = Simulator(
            self,
            subroutine_registry=options.subroutine_registry,
            snapshot_mode=effective_snapshot_mode,
        )
        sim.run(
            fail_on_opaque=options.fail_on_opaque,
            min_coverage=options.min_coverage,
            strict=options.strict,
        )

    # ── Internal helpers ────────────────────────────────────────────

    def disable_next_step(self) -> None:
        """Mark the next emitted command disabled for FluentControl execution."""
        self._next_step_disabled = True

    def _emit(self, step: Step) -> None:
        if self._next_step_disabled:
            step.disabled = True
            self._next_step_disabled = False
        if self._emit_target_stack:
            self._emit_target_stack[-1].append(step)
            return
        if self._active_group is None:
            self._active_group = Group(name="Steps", steps=[])
            self._groups.append(self._active_group)
        self._active_group.steps.append(step)

    def _register_child_valid_slots(self, labware: Labware) -> None:
        """Extend valid workspace slots from a placed carrier's child sites."""
        if self.valid_slots is None or getattr(labware, "category", None) != "fixed_deck":
            return

        from .catalog.catalog import resolve_by_name
        from .catalog.xcmp import load_component_site_location_names

        entry = resolve_by_name(labware.catalog_name)
        if entry is None:
            return

        child_locations = load_component_site_location_names(entry.file_path)
        if not child_locations:
            return

        next_position_by_location: dict[str, int] = {}
        for name in child_locations:
            if not name:
                continue
            if name not in next_position_by_location:
                next_position_by_location[name] = max(
                    (pos for loc, pos in self.valid_slots if loc == name),
                    default=0,
                )
            next_position_by_location[name] += 1
            self.valid_slots.add((name, next_position_by_location[name]))

    def _find_unique_labware(self, cls: type) -> Labware:
        matches = [lw for stack in self.slot_map.values() for lw in stack
                   if isinstance(lw, cls)]
        if not matches:
            raise ValueError(f"No {cls.__name__} on the worktable")
        if len(matches) > 1:
            labels = [lw.label for lw in matches]
            raise ValueError(
                f"Multiple {cls.__name__} on the worktable ({labels}); "
                f"pass one explicitly"
            )
        return matches[0]

    def _labels_on_deck(self) -> list[str]:
        """Return sorted labware labels currently on the deck."""
        labels: list[str] = []
        for stack in self.slot_map.values():
            for lw in stack:
                labels.append(lw.label)
        return sorted(labels)

    def has_labware(self, label: str) -> bool:
        """Return True if a placed labware uses ``label``."""
        for stack in self.slot_map.values():
            for lw in stack:
                if lw.label == label:
                    return True
        return False

    def labware_by_label(self, label: str) -> Labware:
        for stack in self.slot_map.values():
            for lw in stack:
                if lw.label == label:
                    return lw
        available = self._labels_on_deck()
        if available:
            raise KeyError(
                f"No labware with label {label!r} on the worktable. "
                f"Available labels: {available}"
            )
        raise KeyError(
            f"No labware with label {label!r} on the worktable (deck is empty)."
        )

    def labware(self, label: str) -> Labware:
        """Find placed labware by its author-given label."""
        return self.labware_by_label(label)

    def __getitem__(self, label: str) -> Labware:
        return self.labware_by_label(label)

    def _require_bound_workspace(self) -> None:
        if self.workspace_name and self.workspace_guid:
            return
        raise ValueError(
            "Worktable is not bound to a specific FluentControl workspace. "
            "Build it with Worktable.from_workspace(...) before compiling."
        )
