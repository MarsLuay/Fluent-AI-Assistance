"""Codegen — emit a fluentcoder Python protocol from a Pydantic Protocol IR.

The output is a self-contained ``.py`` with a ``build_worktable()``
factory that, when executed, produces an IR equivalent to the input
(modulo the random ``WorkspaceDelta`` GUID and the FC checksum).

Conventions:

- Each placed Plate / Trough is filled with a stand-in
  ``default_reagent`` so simulation runs cleanly. Reagent identity is
  not recovered from the .xscr (never serialized); the user replaces
  the stand-in with real ``Reagent`` objects to model real liquids.
- ``EVA[*]`` AddLabware steps are skipped — ``head.mount_adapter()``
  triggers them implicitly and the install-bundle's checksum rewrite
  re-injects the labware row.
- The ``CgaGet/RgaTransfer/CgaDrop`` triplet that ``gripper.move(...)``
  emits is collapsed back into a single ``wt.gripper.move(...)`` call.
- ``GetCoverSiteName("X")`` destination locations are recognised as
  ``onto=X`` instead of ``to=("Site", N)``.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional, Union

from ..catalog.catalog import index_exists, resolve_by_name
from ..expressions import (
    BooleanLiteral,
    NumberLiteral,
    ReviewedRawExpression,
    SourcePreservedExpression,
    StringLiteral,
    VariableReference,
    expression_python_value,
    is_expression,
    render_expression,
)
from ..fc_variables import decode_fc_variable
from ..ir.schema import (
    AddLabwareStep, ApplicationDriverMacroStep, AspirateStep, CgaDropFingersStep, CgaGetFingersStep,
    CommentStep, ConditionalStep, DispenseStep, DropHeadAdapterStep,
    ExecuteApplicationStep, ExportVariableStep, GenericStep, GetHeadAdapterStep,
    EndScriptStep, MoveAxisCommandStep, StartMoveCommandStep, WaitForAsyncResponseStep,
    ImportVariableStep, InitializeDeviceStep, LihaAspirateStep, LihaDispenseStep,
    LihaDetectLiquidStep, GenerateReportStep, LihaMixStep,
    LihaDropTipsStep, LihaEmptyTipsStep, LihaGetTipsStep,
    LoopStep, Mca384DropTipsStep, Mca384EmptyTipsStep, Mca384GetTipsStep,
    Mca384MixStep, Mca384MoveArmStep, PickUpTipsStep, Protocol,
    QueryVariableStep, RemoveLabwareStep, RgaTransferLabwareStep,
    ScriptGroupStep, SetLocationStep, SetTipsBackStep, SetVariableStep,
    StartTimerStep, Step, SubRoutineStep, UserPromptStep, WaitForTimerStep, WaitStep, ExecuteVbScriptStep, TeGioSetPwmOutputStep, LeaveStep,
)


# Default fill volumes (µL) per labware family — heuristic stand-ins.
_FILL_BY_CATEGORY: dict[str, float] = {
    "plate":       200.0,
    "trough":      20_000.0,
    # tip_box, magnet_rack, etc. don't carry liquid; no fill_all emitted.
}

# CATEGORY_TO_CLASS gives the family base class. Codegen refines plates
# by grid: 8×12 → Plate96, 16×24 → Plate384, otherwise the base.
_PLATE_GRID_TO_CLASS: dict[tuple[int, int], str] = {
    (8, 12): "Plate96",
    (16, 24): "Plate384",
}

_CATEGORY_TO_BASE_CLASS: dict[str, str] = {
    "plate":         "Plate",
    "trough":        "Trough",
    "tip_box":       "TipBox",
    "magnet_rack":   "MagnetRack",
    "tube_rack":     "TubeRack",
    "wash_station":  "WashStation",
    "waste_chute":   "WasteChute",
    "hotel":         "Hotel",
    "adapter":       "Adapter",
    "fixed_deck":    "FixedDeck",
}

_GET_COVER_SITE_RE = re.compile(r'GetCoverSiteName\("([^"]+)"\)')
_WORKSPACE_LABWARE_REF_RE = re.compile(
    r"<(?:LabwareName|Labware)>\s*([^<]+?)\s*</(?:LabwareName|Labware)>",
    re.IGNORECASE,
)


def emit_python(protocol: Protocol, *, source_xscr: Optional[str] = None) -> str:
    """Render a ``Protocol`` to a Python source string."""
    classes_used: set[str] = set()
    label_to_var: dict[str, str] = {}
    placed_labware: list[tuple[str, str]] = []  # (var_name, category)

    body_lines: list[str] = []

    worktable_ctor = _emit_worktable_ctor(protocol)
    body_lines.append(f"    {worktable_ctor}")
    device_alias, available_id, rga_module_name = _infer_worktable_bindings(protocol)
    if device_alias:
        body_lines.append(f"    wt.device_alias = {device_alias!r}")
    if available_id:
        body_lines.append(f"    wt.available_id = {available_id!r}")
    if rga_module_name:
        body_lines.append(f"    wt.rga_module_name = {rga_module_name!r}")
    body_lines.append("")
    body_lines.append('    default_reagent = Reagent("liquid")')
    body_lines.append("    # Stand-in reagent — replace with real Reagent(...) instances")
    body_lines.append("    # to model identity (e.g. beads with pinned_when_magnetized=True).")
    classes_used.update({"Worktable", "Reagent"})
    fc_token_vars: dict[str, str] = {}

    # Emit protocol variables. set_sim_value is also seeded with the default
    # so the simulator has a value for any loop/conditional that references
    # this variable; the user can override before calling simulate().
    if protocol.variables:
        body_lines.append("")
        for var_name in protocol.variables:
            default = expression_python_value(protocol.variable_defaults.get(var_name, 0))
            metadata = protocol.variable_metadata.get(var_name) or {}
            scope = str(metadata.get("scope") or "Script")
            type_name = str(metadata.get("type_name") or "")
            body_lines.append(
                f"    wt.declare_variable({var_name!r}, {default!r}, "
                f"scope={scope!r}, type_name={type_name!r})"
            )
            body_lines.append(f"    wt.set_sim_value({var_name!r}, {default!r})")

    # Walk groups; collapse RGA triplets; skip EVA AddLabware.
    for group in protocol.groups:
        body_lines.append("")
        body_lines.append(f'    wt.group({group.name!r})')
        _emit_steps(
            group.steps,
            indent="    ",
            out=body_lines,
            classes_used=classes_used,
            label_to_var=label_to_var,
            placed_labware=placed_labware,
            fc_token_vars=fc_token_vars,
        )

    body_lines.append("")
    body_lines.append("    return wt")

    # Header. Use only the basename in the docstring to keep it path-safe
    # on Windows (full paths embed unescaped backslash sequences which
    # Python parses as escape codes).
    if source_xscr:
        basename = source_xscr.replace("\\", "/").rsplit("/", 1)[-1]
    else:
        basename = ".xscr"
    header_lines: list[str] = []
    header_lines.append('"""Auto-decompiled from {} — DO NOT hand-edit."""'.format(basename))
    header_lines.append("")
    header_lines.append(_format_imports(classes_used))
    header_lines.append("")
    header_lines.append("")
    header_lines.append("def build_worktable() -> Worktable:")

    footer_lines = [
        "",
        "",
        'if __name__ == "__main__":',
        "    wt = build_worktable()",
        "    out = wt.compile({!r})".format(
            (source_xscr.rsplit("\\", 1)[-1].rsplit("/", 1)[-1].replace(".xscr", "_recompiled.xscr"))
            if source_xscr else "decompiled.xscr"
        ),
        "    print(f'Wrote {out}')",
    ]

    return "\n".join(header_lines + body_lines + footer_lines) + "\n"


def _emit_worktable_ctor(protocol: Protocol) -> str:
    if protocol.worktable_name or protocol.worktable_guid:
        # Match generate/IR default (auto_place=False). Full-deck auto_place
        # requires a complete FluentControl labware catalog; seed only labels
        # literally referenced in retained step XML instead.
        seed_labels = _source_workspace_seed_labels(protocol)
        return (
            f"wt = Worktable.from_workspace({(protocol.worktable_name or '')!r}, "
            f"workspace_guid={protocol.worktable_guid!r}, "
            f"auto_place=False, seed_simulation={bool(seed_labels)!r}, "
            f"simulation_seed_labels={seed_labels!r}, "
            f"protocol_name={protocol.name!r}, comment={protocol.comment!r})"
        )

    message = (
        "Decompiled protocol is missing its WorktableWorkspace reference. "
        "Bind a specific workspace before building, simulating, or compiling it."
    )
    return f"raise RuntimeError({message!r})"


def _infer_worktable_bindings(protocol: Protocol) -> tuple[str | None, str | None, str | None]:
    """Lift DeviceAlias / AvailableID / RGA ModuleName onto Worktable fields.

    Prefer Protocol-level fields, then first non-empty values found on steps
    (including device embedded in preserved raw XML).
    """
    device_alias = str(getattr(protocol, "device_alias", None) or "").strip() or None
    available_id = str(getattr(protocol, "available_id", None) or "").strip() or None
    rga_module_name = str(getattr(protocol, "rga_module_name", None) or "").strip() or None

    for group in protocol.groups:
        for step in _iter_steps(group.steps):
            if not device_alias:
                step_alias = str(getattr(step, "device_alias", None) or "").strip()
                if step_alias:
                    device_alias = step_alias
            if not available_id:
                step_available = str(getattr(step, "available_id", None) or "").strip()
                if step_available:
                    available_id = step_available
            if not rga_module_name:
                step_module = str(getattr(step, "module_name", None) or "").strip()
                if step_module:
                    rga_module_name = step_module
            raw_xml = str(getattr(step, "raw_xml", None) or "")
            if raw_xml and (not device_alias or not available_id):
                if not device_alias:
                    match = re.search(r"<DeviceAlias>([^<]*)</DeviceAlias>", raw_xml)
                    if match and match.group(1).strip():
                        device_alias = match.group(1).strip()
                if not available_id:
                    match = re.search(r"<AvailableID>([^<]*)</AvailableID>", raw_xml)
                    if match and match.group(1).strip():
                        available_id = match.group(1).strip()
            if device_alias and available_id and rga_module_name:
                break
        if device_alias and available_id and rga_module_name:
            break
    return device_alias, available_id, rga_module_name


def _source_workspace_seed_labels(protocol: Protocol) -> tuple[str, ...]:
    """Return literal workspace labels referenced by source-preserved steps.

    Decompiled scripts can include operator-loaded workspace resources that have
    no AddLabware statement.  Seed only exact labels present in their retained
    source XML; never synthesize deck occupants or expand to unrelated catalog
    resources.
    """
    labels: set[str] = set()
    for group in protocol.groups:
        for step in _iter_steps(group.steps):
            raw_xml = getattr(step, "raw_xml", None)
            if isinstance(step, GenericStep):
                raw_xml = step.parameters.get("raw_xml")
            if not isinstance(raw_xml, str):
                continue
            for match in _WORKSPACE_LABWARE_REF_RE.finditer(raw_xml):
                label = match.group(1).strip()
                if label and "[" not in label and "+" not in label:
                    labels.add(label)
                elif re.search(r"\[\d+\]$", label):
                    labels.add(label)
    return tuple(sorted(labels))


def _iter_steps(steps: Iterable[Step]) -> Iterable[Step]:
    for step in steps:
        yield step
        if isinstance(step, ScriptGroupStep):
            yield from _iter_steps(step.steps)
        elif isinstance(step, LoopStep):
            yield from _iter_steps(step.steps)
        elif isinstance(step, ConditionalStep):
            yield from _iter_steps(step.then_steps)
            yield from _iter_steps(step.else_steps)


def _emit_steps(
    steps: Iterable[Step],
    *,
    indent: str,
    out: list[str],
    classes_used: set[str],
    label_to_var: dict[str, str],
    placed_labware: list[tuple[str, str]],
    fc_token_vars: dict[str, str],
) -> None:
    head_var_emitted = False
    steps_list = list(steps)
    i = 0
    while i < len(steps_list):
        step = steps_list[i]

        if step.disabled and not isinstance(step, (ConditionalStep, LoopStep, ScriptGroupStep)):
            out.append(indent + "wt.disable_next_step()")

        # Collapse Cga(Get|Drop)Fingers + transfer macro triplet into
        # a single gripper.move(...) call.
        if isinstance(step, CgaGetFingersStep) and i + 2 < len(steps_list):
            rga = _as_rga_transfer_step(steps_list[i + 1])
            if rga is not None and isinstance(steps_list[i + 2], CgaDropFingersStep):
                out.append(_emit_gripper_move(rga, indent=indent, label_to_var=label_to_var, classes_used=classes_used))
                i += 3
                continue

        if isinstance(step, AddLabwareStep):
            if step.labware_type.startswith("EVA["):
                # EVA AddLabware is auto-injected by the install-bundle
                # checksum rewrite; mount_adapter() handles the IR side.
                i += 1
                continue
            emitted, resolved = _emit_add_labware(
                step, classes_used, label_to_var, placed_labware, fc_token_vars
            )
            for line in emitted:
                out.append(indent + line)
            if resolved:
                fill = _emit_fill_all(step, label_to_var, placed_labware)
                if fill is not None:
                    out.append(indent + fill)
            i += 1
            continue

        if isinstance(step, GetHeadAdapterStep):
            if not head_var_emitted:
                out.append(indent + "head = wt.mca96")
                head_var_emitted = True
            out.append(indent + "head.mount_adapter()")
            i += 1
            continue

        if isinstance(step, DropHeadAdapterStep):
            out.append(indent + "head.drop_adapter()")
            i += 1
            continue

        if isinstance(step, PickUpTipsStep):
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            out.append(indent + f"head.pick_up({target})")
            i += 1
            continue

        if isinstance(step, SetTipsBackStep):
            if step.labware_name:
                target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
                out.append(indent + f"head.return_tips({target})")
            else:
                out.append(indent + "head.return_tips(None)")
            i += 1
            continue

        if isinstance(step, AspirateStep):
            out.append(indent + _emit_aspirate(step, label_to_var, classes_used))
            i += 1
            continue

        if isinstance(step, DispenseStep):
            out.append(indent + _emit_dispense(step, label_to_var, classes_used))
            i += 1
            continue

        if isinstance(step, RgaTransferLabwareStep):
            # Stray RGA without surrounding Cga* — emit standalone gripper.move.
            out.append(_emit_gripper_move(step, indent=indent, label_to_var=label_to_var, classes_used=classes_used))
            i += 1
            continue

        if isinstance(step, CgaGetFingersStep):
            out.append(_emit_cga_get_fingers(step, indent=indent))
            i += 1
            continue

        if isinstance(step, CgaDropFingersStep):
            out.append(_emit_cga_drop_fingers(step, indent=indent))
            i += 1
            continue

        if isinstance(step, RemoveLabwareStep):
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            out.append(indent + f"wt.remove({target})")
            i += 1
            continue

        if isinstance(step, LoopStep):
            times_repr = _times_repr(step, classes_used)
            loop_variable_arg = (
                f", loop_variable={step.loop_variable!r}" if step.loop_variable else ""
            )
            disabled_arg = ", disabled=True" if step.disabled else ""
            out.append(
                indent
                + f"with wt.loop(times={times_repr}, name={step.name!r}{loop_variable_arg}{disabled_arg}):"
            )
            _emit_steps(
                step.steps,
                indent=indent + "    ",
                out=out,
                classes_used=classes_used,
                label_to_var=label_to_var,
                placed_labware=placed_labware,
                fc_token_vars=fc_token_vars,
            )
            _ensure_block_body(out, indent + "    ")
            i += 1
            continue

        if isinstance(step, ConditionalStep):
            cond_var = f"_cond_{i}"
            disabled_arg = ", disabled=True" if step.disabled else ""
            if step.condition is not None:
                condition_repr = _emit_expression_arg(step.condition, classes_used, source_field=True)
                out.append(
                    indent
                    + f"with wt.conditional(condition={condition_repr}, name={step.name!r}{disabled_arg}):"
                )
            else:
                right_repr = _emit_expression_arg(step.right_value, classes_used)
                out.append(
                    indent + f"with wt.conditional(left={step.left_variable!r}, "
                    f"op={step.operator!r}, right={right_repr}, "
                    f"right_is_variable={step.right_is_variable!r}, name={step.name!r}{disabled_arg}):"
                )
            if step.else_steps:
                out[-1] = out[-1].replace("):", f") as {cond_var}:")
            _emit_steps(
                step.then_steps,
                indent=indent + "    ",
                out=out,
                classes_used=classes_used,
                label_to_var=label_to_var,
                placed_labware=placed_labware,
                fc_token_vars=fc_token_vars,
            )
            _ensure_block_body(out, indent + "    ")
            if step.else_steps:
                out.append(indent + f"with wt.else_branch({cond_var}):")
                _emit_steps(
                    step.else_steps,
                    indent=indent + "    ",
                    out=out,
                    classes_used=classes_used,
                    label_to_var=label_to_var,
                    placed_labware=placed_labware,
                    fc_token_vars=fc_token_vars,
                )
                _ensure_block_body(out, indent + "    ")
            i += 1
            continue

        # Unsupported steps — emit as a comment so round-trip won't break
        # silently. The user (or v1.2) replaces with proper API.
        if isinstance(step, ScriptGroupStep):
            out.append(indent + f"with wt.nested_group({step.name!r}):")
            _emit_steps(
                step.steps,
                indent=indent + "    ",
                out=out,
                classes_used=classes_used,
                label_to_var=label_to_var,
                placed_labware=placed_labware,
                fc_token_vars=fc_token_vars,
            )
            _ensure_block_body(out, indent + "    ")
            i += 1
            continue

        if isinstance(step, LihaGetTipsStep):
            out.append(indent + "liha = wt.liha")
            arg = _label_arg(step.labware_name, label_to_var)
            out.append(indent + f"liha.get_tips({arg})" if arg else indent + "liha.get_tips()")
            i += 1
            continue

        if isinstance(step, LihaDropTipsStep):
            out.append(indent + "liha = wt.liha")
            arg = _label_arg(step.labware_name, label_to_var)
            out.append(indent + f"liha.drop_tips({arg})" if arg else indent + "liha.drop_tips()")
            i += 1
            continue

        if isinstance(step, LihaAspirateStep):
            out.append(indent + "liha = wt.liha")
            out.append(indent + _emit_liha_pipette("aspirate", step, label_to_var, classes_used))
            i += 1
            continue

        if isinstance(step, LihaDispenseStep):
            out.append(indent + "liha = wt.liha")
            out.append(indent + _emit_liha_pipette("dispense", step, label_to_var, classes_used))
            i += 1
            continue

        if isinstance(step, LihaMixStep):
            out.append(indent + "liha = wt.liha")
            out.append(indent + _emit_liha_pipette("mix", step, label_to_var, classes_used, cycles=step.cycles))
            i += 1
            continue

        if isinstance(step, LihaDetectLiquidStep):
            out.append(indent + _emit_liha_detect_liquid(step, label_to_var))
            i += 1
            continue

        if isinstance(step, GenerateReportStep):
            out.append(indent + _emit_generate_report(step))
            i += 1
            continue

        if isinstance(step, LihaEmptyTipsStep):
            out.append(indent + "liha = wt.liha")
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            parts = [target, _emit_expression_arg(step.volume, classes_used)]
            if step.liquid_class:
                parts.append(f"liquid_class={step.liquid_class!r}")
            out.append(indent + f"liha.empty_tips({', '.join(parts)})")
            i += 1
            continue

        if isinstance(step, Mca384MixStep):
            if not head_var_emitted:
                out.append(indent + "head = wt.mca96")
                head_var_emitted = True
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            parts = [target, _emit_expression_arg(step.volume, classes_used), f"cycles={_emit_expression_arg(step.cycles, classes_used)}"]
            parts.append(f"liquid_class={(step.liquid_class or 'Water Mix')!r}")
            out.append(indent + f"head.mix({', '.join(parts)})")
            i += 1
            continue

        if isinstance(step, Mca384EmptyTipsStep):
            if not head_var_emitted:
                out.append(indent + "head = wt.mca96")
                head_var_emitted = True
            target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
            parts = [target, _emit_expression_arg(step.volume, classes_used)]
            if step.liquid_class:
                parts.append(f"liquid_class={step.liquid_class!r}")
            out.append(indent + f"head.empty_tips({', '.join(parts)})")
            i += 1
            continue

        if isinstance(step, SetVariableStep):
            out.append(indent + _emit_set_variable(step, classes_used))
            i += 1
            continue

        if isinstance(step, WaitStep):
            out.append(indent + f"wt.wait({_emit_expression_arg(step.duration_seconds, classes_used)})")
            i += 1
            continue

        if isinstance(step, CommentStep):
            out.append(indent + f"wt.add_comment({step.comment!r})")
            i += 1
            continue

        if isinstance(step, UserPromptStep):
            out.append(indent + _emit_user_prompt(step))
            i += 1
            continue

        if isinstance(step, ExecuteVbScriptStep):
            out.append(indent + _emit_execute_vb_script(step))
            i += 1
            continue

        if isinstance(step, TeGioSetPwmOutputStep):
            out.append(indent + _emit_tegio_set_pwm_output(step, classes_used))
            i += 1
            continue

        if isinstance(step, LeaveStep):
            out.append(indent + _emit_leave(step))
            i += 1
            continue

        if isinstance(step, StartTimerStep):
            out.append(indent + f"wt.start_timer({step.timer!r})")
            i += 1
            continue

        if isinstance(step, WaitForTimerStep):
            out.append(indent + f"wt.wait_for_timer({step.timer!r}, {_emit_expression_arg(step.duration_seconds, classes_used)})")
            i += 1
            continue

        if isinstance(step, ExportVariableStep):
            out.append(indent + _emit_export_variables(step))
            i += 1
            continue

        if isinstance(step, ImportVariableStep):
            out.append(indent + _emit_import_variables(step))
            i += 1
            continue

        if isinstance(step, QueryVariableStep):
            out.append(indent + (
                f"wt.query_variable({step.variable_name!r}, {step.query_prompt!r}, "
                f"limit_range={step.limit_range!r})"
            ))
            i += 1
            continue

        if isinstance(step, ExecuteApplicationStep):
            out.append(indent + _emit_execute_application(step, classes_used))
            i += 1
            continue

        if isinstance(step, InitializeDeviceStep):
            out.append(indent + _emit_initialize_device(step))
            i += 1
            continue

        if isinstance(step, SubRoutineStep):
            out.append(indent + _emit_call_subroutine(step, classes_used))
            i += 1
            continue

        if isinstance(step, SetLocationStep):
            out.append(indent + (
                f"wt.set_location({step.labware!r}, {step.location!r}, "
                f"{_emit_expression_arg(step.site, classes_used)}, rotation={step.rotation!r})"
            ))
            i += 1
            continue

        if isinstance(step, (Mca384GetTipsStep, Mca384DropTipsStep, Mca384MoveArmStep)):
            out.append(indent + _emit_mca384_generic_step(step))
            i += 1
            continue

        if isinstance(step, GenericStep):
            out.append(indent + _emit_generic_step(step))
            i += 1
            continue

        if isinstance(step, ApplicationDriverMacroStep):
            out.append(indent + _emit_application_driver_macro(step))
            i += 1
            continue

        if isinstance(step, MoveAxisCommandStep):
            out.append(indent + _emit_move_axis_command(step, classes_used))
            i += 1
            continue

        if isinstance(step, StartMoveCommandStep):
            out.append(indent + _emit_start_move_command(step))
            i += 1
            continue

        if isinstance(step, WaitForAsyncResponseStep):
            out.append(indent + _emit_wait_for_async_response(step))
            i += 1
            continue

        if isinstance(step, EndScriptStep):
            out.append(indent + _emit_end_script(step))
            i += 1
            continue

        out.append(indent + f"# [decompiler] unsupported step: {type(step).__name__}")
        i += 1


def _emit_add_labware(
    step: AddLabwareStep,
    classes_used: set[str],
    label_to_var: dict[str, str],
    placed_labware: list[tuple[str, str]],
    fc_token_vars: dict[str, str],
) -> tuple[list[str], bool]:
    fc_name = decode_fc_variable(step.labware_type)
    if fc_name:
        cls_name = "Plate96"
        category = "plate"
        classes_used.add(cls_name)
        var_name = _allocate_var_name(step.label, label_to_var)
        placed_labware.append((var_name, category))
        lines: list[str] = []
        if fc_name not in fc_token_vars:
            token_var = _allocate_fc_token_var(fc_name, fc_token_vars, label_to_var)
            fc_token_vars[fc_name] = token_var
            lines.append(f"{token_var} = wt.declare_fc_variable({fc_name!r})")
        catalog_expr = fc_token_vars[fc_name]
        lines.append(
            f"{var_name} = wt.place("
            f"{cls_name}({step.label!r}, catalog={catalog_expr}), "
            f"{step.location!r}, {_emit_expression_arg(step.position, classes_used)}, "
            "allow_occupied=True, allow_invalid_slot=True)"
        )
        return lines, True

    cls_name, category, error = _resolve_class_for_catalog(step.labware_type)
    if error is not None:
        # Fail loudly at runtime, but keep a place sketch in the source so
        # unmapped locations and allow_invalid_slot survive text round-trips /
        # offline review when the catalog index is empty or incomplete.
        cls_name, category = _fallback_class_for_unresolved_catalog(step.labware_type)
        classes_used.add(cls_name)
        var_name = _allocate_var_name(step.label, label_to_var)
        placed_labware.append((var_name, category))
        message = (
            f"Decompiled labware {step.label!r} references catalog {step.labware_type!r}, "
            f"but {error} Replace it with an exact installed labware before "
            "simulating or compiling this protocol."
        )
        return (
            [
                f"raise RuntimeError({message!r})",
                f"# Recovery sketch (unreachable): preserve {step.location!r} via allow_invalid_slot",
                f"{var_name} = wt.place("
                f"{cls_name}({step.label!r}, catalog={step.labware_type!r}), "
                f"{step.location!r}, {_emit_expression_arg(step.position, classes_used)}, "
                "allow_occupied=True, allow_invalid_slot=True)",
            ],
            True,
        )
    classes_used.add(cls_name)
    var_name = _allocate_var_name(step.label, label_to_var)
    placed_labware.append((var_name, category))
    return (
        [
            f"{var_name} = wt.place("
            f"{cls_name}({step.label!r}, catalog={step.labware_type!r}), "
            f"{step.location!r}, {_emit_expression_arg(step.position, classes_used)}, "
            "allow_occupied=True, allow_invalid_slot=True)"
        ],
        True,
    )


def _emit_fill_all(
    step: AddLabwareStep,
    label_to_var: dict[str, str],
    placed_labware: list[tuple[str, str]],
) -> Optional[str]:
    var = label_to_var.get(step.label)
    if var is None:
        return None
    _, category = placed_labware[-1] if placed_labware else (var, "")
    fill = _FILL_BY_CATEGORY.get(category)
    if fill is None:
        return None
    return f"{var}.fill_all(default_reagent, {fill})"


def _emit_expression_arg(value, classes_used: set[str], *, source_field: bool = False) -> str:
    if isinstance(value, (NumberLiteral, BooleanLiteral)):
        return repr(expression_python_value(value))
    if isinstance(value, VariableReference):
        return repr(value.name)
    if isinstance(value, StringLiteral) and not source_field:
        return repr(value.value)
    if isinstance(value, ReviewedRawExpression):
        classes_used.add("ReviewedRawExpression")
        return (
            f"ReviewedRawExpression(source={value.source!r}, approval_id={value.approval_id!r}, "
            f"reviewer={value.reviewer!r}, reference_metadata_origin={value.reference_metadata_origin!r}, "
            f"referenced_variables={value.referenced_variables!r}, "
            f"referenced_functions={value.referenced_functions!r})"
        )
    if isinstance(value, SourcePreservedExpression):
        classes_used.add("SourcePreservedExpression")
        return (
            f"SourcePreservedExpression(source={value.source!r}, source_hash={value.source_hash!r}, "
            f"source_entry={value.source_entry!r}, provenance_id={value.provenance_id!r}, "
            f"byte_stable={value.byte_stable!r}, reference_metadata_origin={value.reference_metadata_origin!r}, "
            f"referenced_variables={value.referenced_variables!r}, referenced_functions={value.referenced_functions!r})"
        )
    if is_expression(value):
        classes_used.add("parse_expression")
        return f"parse_expression({render_expression(value)!r})"
    return repr(value)


def _emit_aspirate(step: AspirateStep, label_to_var: dict[str, str], classes_used: set[str]) -> str:
    target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
    parts = [target, _emit_expression_arg(step.volume, classes_used)]
    if step.liquid_class:
        parts.append(f"liquid_class={step.liquid_class!r}")
    return f"head.aspirate({', '.join(parts)})"


def _emit_dispense(step: DispenseStep, label_to_var: dict[str, str], classes_used: set[str]) -> str:
    target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
    parts = [target, _emit_expression_arg(step.volume, classes_used)]
    if step.liquid_class:
        parts.append(f"liquid_class={step.liquid_class!r}")
    return f"head.dispense({', '.join(parts)})"


def _emit_set_variable(step: SetVariableStep, classes_used: set[str]) -> str:
    value = step.value
    if isinstance(value, (StringLiteral, NumberLiteral, BooleanLiteral)):
        return f"wt.set_variable({step.variable_name!r}, {expression_python_value(value)!r})"
    if isinstance(value, ReviewedRawExpression):
        classes_used.add("ReviewedRawExpression")
        return (
            f"wt.set_variable({step.variable_name!r}, "
            f"ReviewedRawExpression(source={value.source!r}, approval_id={value.approval_id!r}, "
            f"reviewer={value.reviewer!r}, reference_metadata_origin={value.reference_metadata_origin!r}, "
            f"referenced_variables={value.referenced_variables!r}, "
            f"referenced_functions={value.referenced_functions!r}))"
        )
    if isinstance(value, SourcePreservedExpression):
        classes_used.add("SourcePreservedExpression")
        return (
            f"wt.set_variable({step.variable_name!r}, "
            f"SourcePreservedExpression(source={value.source!r}, source_hash={value.source_hash!r}, "
            f"source_entry={value.source_entry!r}, provenance_id={value.provenance_id!r}, "
            f"byte_stable={value.byte_stable!r}, reference_metadata_origin={value.reference_metadata_origin!r}, "
            f"referenced_variables={value.referenced_variables!r}, referenced_functions={value.referenced_functions!r}))"
        )
    classes_used.add("parse_expression")
    return f"wt.set_variable({step.variable_name!r}, parse_expression({render_expression(value)!r}))"


def _label_arg(label: Optional[str], label_to_var: dict[str, str]) -> Optional[str]:
    if not label:
        return None
    return label_to_var.get(label, repr(label))


def _ensure_block_body(out: list[str], indent: str) -> None:
    if not out or not out[-1].startswith(indent):
        out.append(indent + "pass")


def _emit_liha_pipette(
    method: str,
    step: Union[LihaAspirateStep, LihaDispenseStep, LihaMixStep],
    label_to_var: dict[str, str],
    classes_used: set[str],
    *,
    cycles: Optional[Union[int, str]] = None,
) -> str:
    if step.raw_xml:
        command_id = {
            "aspirate": "LihaAspirate",
            "dispense": "LihaDispense",
            "mix": "LihaMix",
        }[method]
        return f"wt.raw_xml_step({command_id!r}, raw_xml={step.raw_xml!r})"
    target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
    if step.volumes:
        volume_arg = "[" + ", ".join(_emit_expression_arg(item, classes_used) for item in step.volumes) + "]"
    else:
        volume_arg = _emit_expression_arg(step.volume, classes_used)
    parts = [target, volume_arg]
    if cycles is not None:
        parts.append(f"cycles={_emit_expression_arg(cycles, classes_used)}")
    if step.liquid_class:
        parts.append(f"liquid_class={step.liquid_class!r}")
    if step.well_offset is not None:
        parts.append(f"well_offset={_emit_expression_arg(step.well_offset, classes_used)}")
    return f"liha.{method}({', '.join(parts)})"


def _emit_liha_detect_liquid(step: LihaDetectLiquidStep, label_to_var: dict[str, str]) -> str:
    if step.raw_xml:
        return f"wt.raw_xml_step('LihaDetectLiquid', raw_xml={step.raw_xml!r})"
    target = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
    parts = [target]
    if step.sensitivity is not None:
        parts.append(f"sensitivity={step.sensitivity!r}")
    if step.detection_speed is not None:
        parts.append(f"detection_speed={step.detection_speed!r}")
    return f"wt.detect_liquid({', '.join(parts)})"


def _emit_generate_report(step: GenerateReportStep) -> str:
    if step.raw_xml:
        return f"wt.raw_xml_step('GenerateReportStatement', raw_xml={step.raw_xml!r})"
    parts = []
    if step.report_name:
        parts.append(f"report_name={step.report_name!r}")
    if step.write_csv:
        parts.append("write_csv=True")
    if step.print_report:
        parts.append("print_report=True")
    if not step.all_labware_selected:
        parts.append("all_labware_selected=False")
    inner = ", ".join(parts)
    return f"wt.generate_report({inner})" if inner else "wt.generate_report()"


def _emit_export_variables(step: ExportVariableStep) -> str:
    parts = [repr(step.variables), repr(step.export_file)]
    if step.write_header:
        parts.append(f"write_header={step.write_header!r}")
    if step.replace_existing_file:
        parts.append(f"replace_existing_file={step.replace_existing_file!r}")
    if step.export_strings_with_quotes:
        parts.append(f"export_strings_with_quotes={step.export_strings_with_quotes!r}")
    if step.delimiter_code != 59:
        parts.append(f"delimiter_code={step.delimiter_code!r}")
    return f"wt.export_variables({', '.join(parts)})"


def _emit_import_variables(step: ImportVariableStep) -> str:
    parts = [repr(step.variables), repr(step.import_file)]
    if step.read_line:
        parts.append(f"read_line={step.read_line!r}")
    if step.line != 1:
        parts.append(f"line={step.line!r}")
    if step.start_in_column:
        parts.append(f"start_in_column={step.start_in_column!r}")
    if step.column != 1:
        parts.append(f"column={step.column!r}")
    if step.has_header:
        parts.append(f"has_header={step.has_header!r}")
    if step.delimiter_code != 59:
        parts.append(f"delimiter_code={step.delimiter_code!r}")
    return f"wt.import_variables({', '.join(parts)})"


def _emit_call_subroutine(step: SubRoutineStep, classes_used: set[str]) -> str:
    parts = [repr(step.subroutine)]
    if step.execution_mode != "Synchronous":
        parts.append(f"execution_mode={step.execution_mode!r}")
    if step.variable_mappings_start:
        classes_used.add("VariableMapping")
        mappings = ", ".join(
            f"VariableMapping(target={mapping.target!r}, source={_emit_expression_arg(mapping.source, classes_used, source_field=True)})"
            for mapping in step.variable_mappings_start
        )
        parts.append(f"variable_mappings_start=[{mappings}]")
    if step.variable_mappings_end:
        classes_used.add("VariableMapping")
        mappings = ", ".join(
            f"VariableMapping(target={mapping.target!r}, source={_emit_expression_arg(mapping.source, classes_used, source_field=True)})"
            for mapping in step.variable_mappings_end
        )
        parts.append(f"variable_mappings_end=[{mappings}]")
    return f"wt.call_subroutine({', '.join(parts)})"


def _emit_execute_application(step: ExecuteApplicationStep, classes_used: set[str]) -> str:
    parts = [repr(step.application)]
    config_parts = []
    if step.arguments:
        config_parts.append(f"arguments={step.arguments!r}")
    if not step.wait:
        config_parts.append(f"wait={step.wait!r}")
    if step.store_return:
        config_parts.append(f"store_return={step.store_return!r}")
    if step.variable:
        config_parts.append(f"variable={step.variable!r}")

    if config_parts:
        classes_used.add("ExecuteApplicationConfig")
        parts.append(f"config=ExecuteApplicationConfig({', '.join(config_parts)})")

    return f"wt.execute_application({', '.join(parts)})"


def _emit_initialize_device(step: InitializeDeviceStep) -> str:
    parts: list[str] = []
    if step.device_alias is not None:
        parts.append(f"device_alias={step.device_alias!r}")
    if step.available_id is not None:
        parts.append(f"available_id={step.available_id!r}")
    if step.init_type != "Initialize":
        parts.append(f"init_type={step.init_type!r}")
    return f"wt.initialize_device({', '.join(parts)})"


def _emit_generic_step(step: GenericStep) -> str:
    raw_xml = step.parameters.get("raw_xml")
    if raw_xml:
        return f"wt.raw_xml_step({step.step_type!r}, {raw_xml!r})"
    params = {
        k: v for k, v in step.parameters.items()
        if k not in {"raw_type"} and isinstance(k, str)
    }
    args = ", ".join(f"{k}={v!r}" for k, v in sorted(params.items()))
    if args:
        return f"wt.generic_step({step.step_type!r}, {args})"
    return f"wt.generic_step({step.step_type!r})"


def _emit_mca384_generic_step(
    step: Union[Mca384GetTipsStep, Mca384DropTipsStep, Mca384MoveArmStep],
) -> str:
    if isinstance(step, Mca384GetTipsStep):
        params = {}
        if step.labware_name:
            params["labware_name"] = step.labware_name
        return _generic_call("Mca384GetTips", params)
    if isinstance(step, Mca384DropTipsStep):
        params = {}
        if step.labware_name:
            params["labware_name"] = step.labware_name
        return _generic_call("Mca384DropTips", params)
    params = {"movement_type": step.movement_type}
    if step.labware_name:
        params["labware_name"] = step.labware_name
    return _generic_call("Mca384MoveArm", params)


def _generic_call(step_type: str, params: dict[str, object]) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in sorted(params.items()))
    if args:
        return f"wt.generic_step({step_type!r}, {args})"
    return f"wt.generic_step({step_type!r})"


def _emit_gripper_move(
    step: RgaTransferLabwareStep,
    *,
    indent: str,
    label_to_var: dict[str, str],
    classes_used: set[str],
) -> str:
    src_var = _label_arg(step.labware_name, label_to_var) or repr(step.labware_name)
    cover_match = _GET_COVER_SITE_RE.search(step.destination_location or "")
    extras: list[str] = []
    if step.module_name:
        extras.append(f"module_name={step.module_name!r}")
    if step.available_id:
        extras.append(f"available_id={step.available_id!r}")
    extra = (", " + ", ".join(extras)) if extras else ""
    if cover_match:
        target_label = cover_match.group(1)
        target_var = label_to_var.get(target_label)
        if target_var:
            return indent + f"wt.gripper.move({src_var}, onto={target_var}{extra})"
    loc = step.destination_location
    pos = _emit_expression_arg(step.destination_site, classes_used)
    return indent + f"wt.gripper.move({src_var}, to=({loc!r}, {pos}){extra})"


def _emit_cga_get_fingers(step: CgaGetFingersStep, *, indent: str) -> str:
    args = [f"labware_name={step.labware_name!r}"]
    if step.device_alias is not None:
        args.append(f"device_alias={step.device_alias!r}")
    if step.available_id is not None:
        args.append(f"available_id={step.available_id!r}")
    return indent + f"wt.gripper.get_fingers({', '.join(args)})"


def _emit_cga_drop_fingers(step: CgaDropFingersStep, *, indent: str) -> str:
    args = [f"labware_name={step.labware_name!r}"]
    if step.device_alias is not None:
        args.append(f"device_alias={step.device_alias!r}")
    if step.available_id is not None:
        args.append(f"available_id={step.available_id!r}")
    args.append(f"use_source_as_back_position={step.use_source_as_back_position!r}")
    return indent + f"wt.gripper.drop_fingers({', '.join(args)})"


def _emit_user_prompt(step: UserPromptStep) -> str:
    if step.rup_kind == "worktable" and step.raw_xml:
        return f"wt.user_prompt_worktable(raw_xml={step.raw_xml!r})"
    if step.rup_kind == "worktable" or (
        str(step.image_path or "").strip() and step.rup_kind != "standard"
    ):
        prompt_args = [repr(step.prompt), f"timeout={step.timeout!r}"]
        if step.auto_close is not None:
            prompt_args.append(f"auto_close={step.auto_close!r}")
        if step.image_path is not None:
            prompt_args.append(f"image_path={step.image_path!r}")
        if step.screen_title:
            prompt_args.append(f"screen_title={step.screen_title!r}")
        return f"wt.user_prompt_worktable({', '.join(prompt_args)})"
    prompt_args = [repr(step.prompt), f"timeout={step.timeout!r}"]
    if step.auto_close is not None:
        prompt_args.append(f"auto_close={step.auto_close!r}")
    if step.image_path is not None:
        prompt_args.append(f"image_path={step.image_path!r}")
    if step.screen_title:
        prompt_args.append(f"screen_title={step.screen_title!r}")
    if step.rup_kind == "standard":
        prompt_args.append("rup_kind='standard'")
    return f"wt.user_prompt({', '.join(prompt_args)})"


def _emit_execute_vb_script(step: ExecuteVbScriptStep) -> str:
    if step.raw_xml:
        return f"wt.execute_vb_script(raw_xml={step.raw_xml!r})"
    return (
        f"wt.execute_vb_script({step.vb_script!r}, "
        f"execution_mode={step.execution_mode!r})"
    )


def _emit_tegio_set_pwm_output(step: TeGioSetPwmOutputStep, classes_used: set[str]) -> str:
    if step.raw_xml:
        return f"wt.tegio_set_pwm_output(raw_xml={step.raw_xml!r})"
    return (
        f"wt.tegio_set_pwm_output(pin_number={step.pin_number!r}, "
        f"duty_cycle={_emit_expression_arg(step.duty_cycle, classes_used)}, "
        f"duration={_emit_expression_arg(step.duration, classes_used)})"
    )


def _emit_leave(step: LeaveStep) -> str:
    if step.raw_xml:
        return f"wt.leave(raw_xml={step.raw_xml!r})"
    return "wt.leave()"


def _emit_move_axis_command(
    step: MoveAxisCommandStep,
    classes_used: set[str] | None = None,
) -> str:
    classes_used = classes_used if classes_used is not None else set()
    if step.raw_xml:
        return f"wt.move_axis_command(raw_xml={step.raw_xml!r})"
    parts: list[str] = []
    if step.available_id is not None:
        parts.append(f"available_id={step.available_id!r}")
    if step.id_label is not None:
        parts.append(f"id_label={step.id_label!r}")
    if step.position not in (0, "0"):
        parts.append(f"position={_emit_expression_arg(step.position, classes_used)}")
    if step.charge_condition is not None:
        parts.append(f"charge_condition={_emit_expression_arg(step.charge_condition, classes_used)}")
    if step.max_speed is not None:
        parts.append(f"max_speed={step.max_speed!r}")
    if step.acceleration is not None:
        parts.append(f"acceleration={step.acceleration!r}")
    if step.deceleration is not None:
        parts.append(f"deceleration={step.deceleration!r}")
    return f"wt.move_axis_command({', '.join(parts)})"


def _emit_start_move_command(step: StartMoveCommandStep) -> str:
    if step.raw_xml:
        return f"wt.start_move_command(raw_xml={step.raw_xml!r})"
    parts: list[str] = []
    if step.available_id is not None:
        parts.append(f"available_id={step.available_id!r}")
    if step.id_label is not None:
        parts.append(f"id_label={step.id_label!r}")
    return f"wt.start_move_command({', '.join(parts)})"


def _emit_wait_for_async_response(step: WaitForAsyncResponseStep) -> str:
    if step.raw_xml:
        return f"wt.wait_for_async_response(raw_xml={step.raw_xml!r})"
    return "wt.wait_for_async_response()"


def _emit_end_script(step: EndScriptStep) -> str:
    if step.raw_xml:
        return f"wt.end_script(raw_xml={step.raw_xml!r})"
    if step.return_code != "Success":
        return f"wt.end_script(return_code={step.return_code!r})"
    return "wt.end_script()"


def _emit_application_driver_macro(step: ApplicationDriverMacroStep) -> str:
    if step.raw_xml and "LegacyDriverMacro" in step.raw_xml:
        return f"wt.raw_xml_step('LegacyDriverMacro', raw_xml={step.raw_xml!r})"
    parts = [repr(step.macro_name)]
    if step.module_name:
        parts.append(f"module_name={step.module_name!r}")
    if step.available_id:
        parts.append(f"available_id={step.available_id!r}")
    if step.execution_settings:
        parts.append(f"execution_settings={step.execution_settings!r}")
    elif step.parameters:
        parts.append(f"parameters={step.parameters!r}")
    return f"wt.application_driver_macro({', '.join(parts)})"


def _as_rga_transfer_step(step: Step) -> Optional[RgaTransferLabwareStep]:
    if isinstance(step, RgaTransferLabwareStep):
        return step
    if not isinstance(step, ApplicationDriverMacroStep):
        return None
    if step.macro_name != "RGA1_TransferLabware":
        return None
    params = step.parameters
    labware_name = params.get("Labware", "")
    dest_loc = params.get("Location", "Site")
    dest_pos = 1
    site_raw = params.get("Site", "1")
    try:
        dest_pos = max(1, int(site_raw))
    except (ValueError, TypeError):
        dest_pos = 1
    return RgaTransferLabwareStep(
        labware_name=labware_name,
        destination_location=dest_loc,
        destination_site=dest_pos,
        module_name=str(step.module_name or ""),
        available_id=step.available_id,
    )


def _times_repr(step: LoopStep, classes_used: set[str]) -> str:
    """Best LoopStep → ``times=`` argument."""
    if step.number_of_loops is not None:
        return _emit_expression_arg(step.number_of_loops, classes_used)
    return repr(step.iterations)


def _resolve_class_for_catalog(catalog_name: str) -> tuple[str, str, str | None]:
    """Map a catalog name to (Python class name, category, error)."""
    if not index_exists():
        return "", "external", "the local fluentcoder catalog index is not built."
    entry = resolve_by_name(catalog_name)
    if entry is None:
        return "", "external", "that catalog name is not installed in the local fluentcoder catalog index."
    category = entry.category
    base = _CATEGORY_TO_BASE_CLASS.get(category, "FixedDeck")
    if category == "plate":
        grid = (entry.grid_y or 0, entry.grid_x or 0)
        if grid in _PLATE_GRID_TO_CLASS:
            return _PLATE_GRID_TO_CLASS[grid], category, None
    return base, category, None


def _fallback_class_for_unresolved_catalog(catalog_name: str) -> tuple[str, str]:
    """Best-effort class when the local catalog index cannot resolve a name."""
    lowered = catalog_name.lower()
    if "384" in lowered:
        return "Plate384", "plate"
    if any(token in lowered for token in ("96", "well", "plate", "dwp", "microplate")):
        return "Plate96", "plate"
    if "trough" in lowered or "reservoir" in lowered:
        return "Trough", "trough"
    if "tip" in lowered or "diti" in lowered:
        return "TipBox", "tip_box"
    return "FixedDeck", "fixed_deck"


def _allocate_fc_token_var(
    fc_name: str,
    fc_token_vars: dict[str, str],
    label_to_var: dict[str, str],
) -> str:
    base = _to_var_name(fc_name)
    var = base
    n = 1
    used = set(label_to_var.values()) | set(fc_token_vars.values())
    while var in used:
        n += 1
        var = f"{base}_{n}"
    return var


def _allocate_var_name(label: str, label_to_var: dict[str, str]) -> str:
    if label in label_to_var:
        return label_to_var[label]
    base = _to_var_name(label)
    var = base
    n = 1
    while var in label_to_var.values():
        n += 1
        var = f"{base}_{n}"
    label_to_var[label] = var
    return var


def _to_var_name(label: str) -> str:
    """Make a Python identifier from a labware label."""
    s = re.sub(r"[^A-Za-z0-9_]", "_", label)
    if s and s[0].isdigit():
        s = "_" + s
    if not s:
        s = "lw"
    return s.lower() if s[:1].isupper() else s


def _format_imports(classes_used: set[str]) -> str:
    """Emit a `from fluentcoder import (...)` block listing only what's used."""
    ordered = sorted(classes_used)
    if len(ordered) <= 5:
        return f"from fluentcoder import {', '.join(ordered)}"
    lines = ["from fluentcoder import ("]
    for cls in ordered:
        lines.append(f"    {cls},")
    lines.append(")")
    return "\n".join(lines)
