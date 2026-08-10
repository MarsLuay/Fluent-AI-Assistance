"""Automatic protocol IR synthesis from selected scripts and pattern windows.

The planner is intentionally conservative: it only emits executable operations
when selected source data contains the fields required by the canonical IR
schema. Incomplete windows are preserved through warnings instead of being
filled with guessed labware, volumes, or liquid classes.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .command_registry import registry_command_operation, registry_field_value, registry_requires
from .inference import (
    InferenceCandidate,
    InferenceOrigin,
    infer_value,
    records_to_candidates,
)
from .protocol_ir_schema import (
    LABWARE_TARGET_OPERATIONS,
    LIQUID_CLASS_OPERATIONS,
    VOLUME_OPERATIONS,
    Operation,
    operation_name,
)


LABWARE_FIELDS = (
    "LabwareName",
    "RackLabel",
    "LabwareLabel",
    "LabwareLable",
    "RackName",
    "Labware",
    "TargetLabware",
    "SourceLabware",
    "DestinationLabware",
    "SourceRackLabel",
    "DestinationRackLabel",
    "TipBox",
    "TipBoxName",
    "TipBoxLabel",
    "TipRack",
    "TipRackName",
    "TipRackLabel",
    "TipLabware",
    "TipLabwareName",
)
LABWARE_TYPE_FIELDS = ("RackType", "LabwareType", "LabwareTypeName", "RackID", "CarrierType")
LIQUID_CLASS_FIELDS = (
    "LiquidClassNameBySelection",
    "LiquidClassName",
    "LiquidClass",
    "AspirationLiquidClass",
    "DispenseLiquidClass",
)
VOLUME_FIELDS = (
    "Volume",
    "VolumeUl",
    "VolumeUL",
    "VolumeInul",
    "VolumeInUl",
    "AspirationVolume",
    "DispenseVolume",
    "MixVolume",
)
WORKLIST_FIELDS = (
    "Worklist",
    "WorklistPath",
    "WorklistFile",
    "GWL",
    "GwlFile",
    "FileName",
    "Filename",
)
MOVE_DESTINATION_LOCATION_FIELDS = ("DestinationLocation", "ToLocation", "TargetLocation", "Location")
MOVE_DESTINATION_SITE_FIELDS = ("DestinationSite", "ToSite", "TargetSite", "Position")
MOVE_ONTO_LABWARE_FIELDS = ("OntoLabware", "OnLabware", "DestinationLabware", "ToLabware")
VALID_OPERATION_VALUES = frozenset(item.value for item in Operation)


def synthesize_seed_ir(
    ir: dict[str, Any],
    *,
    selected_scripts: list[dict[str, Any]],
    pattern_windows: list[dict[str, Any]],
    context_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Populate a seed IR with ordered steps and inventory where possible.

    The supplied ``ir`` is mutated in place and returned in the synthesis report
    as ``ir["source"]["ir_synthesis"]``.
    """

    warnings: list[dict[str, Any]] = []
    before_steps = len(ir.get("steps") or [])
    before_labware = len(ir.get("labware") or [])
    before_liquid_classes = len(ir.get("liquid_classes") or [])
    before_worklists = len(ir.get("worklists") or [])

    _ensure_collections(ir)
    _merge_context_inventory(ir, context_manifest)
    for script in selected_scripts:
        _merge_selected_script_inventory(ir, script)

    copied_source_step_count = 0
    for script in selected_scripts:
        source_ir = _load_selected_source_ir(script, warnings)
        if not source_ir:
            continue
        _merge_ir_inventory(ir, source_ir)
        if not pattern_windows:
            copied_source_step_count += _append_source_steps(ir, source_ir, script)

    synthesized_runtime_step_count = 0
    for window in pattern_windows:
        synthesized_runtime_step_count += _append_pattern_window_steps(ir, window, warnings)

    setup_step_count = _ensure_setup_steps_for_targets(ir)
    _renumber_steps(ir)

    planned_step_count = len(ir.get("steps") or []) - before_steps
    if planned_step_count:
        _mark_synthesis_review_required(ir)
    synthesis = {
        "kind": "ir_synthesis",
        "planned": planned_step_count > 0,
        "planned_step_count": planned_step_count,
        "synthesized_runtime_step_count": synthesized_runtime_step_count,
        "copied_source_step_count": copied_source_step_count,
        "setup_step_count": setup_step_count,
        "planned_labware_count": len(ir.get("labware") or []) - before_labware,
        "planned_liquid_class_count": len(ir.get("liquid_classes") or []) - before_liquid_classes,
        "planned_worklist_count": len(ir.get("worklists") or []) - before_worklists,
        "source_script_count": len(selected_scripts),
        "pattern_window_count": len(pattern_windows),
        "warnings": warnings,
        "inferred_field_count": sum(
            len(item.get("decisions") or [])
            for item in warnings
            if item.get("kind") == "inferred_pattern_step"
        ),
    }
    ir.setdefault("source", {})["ir_synthesis"] = synthesis
    return synthesis


def _mark_synthesis_review_required(ir: dict[str, Any]) -> None:
    assumptions = [
        assumption
        for assumption in ir.get("safety_assumptions") or []
        if not (isinstance(assumption, dict) and assumption.get("id") == "codex_must_fill_steps")
    ]
    if not any(
        isinstance(assumption, dict) and assumption.get("id") == "auto_ir_synthesis_review_required"
        for assumption in assumptions
    ):
        assumptions.append(
            {
                "id": "auto_ir_synthesis_review_required",
                "text": "IR steps were synthesized from selected scripts/patterns and must be reviewed against deck layout and method intent before instrument use.",
            }
        )
    ir["safety_assumptions"] = assumptions


def _ensure_collections(ir: dict[str, Any]) -> None:
    ir.setdefault("source", {})
    ir.setdefault("dependencies", [])
    ir.setdefault("labware", [])
    ir.setdefault("liquid_classes", [])
    ir.setdefault("worklists", [])
    ir.setdefault("steps", [])
    ir.setdefault("safety", {})


def _merge_context_inventory(ir: dict[str, Any], context_manifest: dict[str, Any] | None) -> None:
    # Keep seed IRs scoped to selected scripts/patterns. Pulling every liquid
    # class from a full-system export makes prompt-only verification scripts
    # depend on unrelated/stale liquid classes and can block readiness.
    return


def _merge_selected_script_inventory(ir: dict[str, Any], script: dict[str, Any]) -> None:
    dependencies = script.get("dependencies") or {}
    source_name = script.get("name") or script.get("resolved_name") or "selected_script"

    for label in _string_values(dependencies.get("labware_names")):
        _upsert_labware(ir, label, source=source_name)
    for label in _string_values(dependencies.get("rack_labels")):
        _upsert_labware(ir, label, source=source_name)
    for labware_type in _string_values(dependencies.get("rack_types")):
        _upsert_dependency(
            ir,
            {
                "kind": "rack_type",
                "name": labware_type,
                "required": True,
                "source": source_name,
            },
        )
    for liquid_class in _string_values(dependencies.get("liquid_classes")):
        _upsert_liquid_class(ir, liquid_class, role="script_dependency", source=source_name)
    for ref in _string_values(dependencies.get("external_or_worklist_refs")):
        if _looks_like_worklist(ref):
            _upsert_worklist(ir, ref, source=source_name)


def _load_selected_source_ir(script: dict[str, Any], warnings: list[dict[str, Any]]) -> dict[str, Any] | None:
    path_value = script.get("resolved_path") or script.get("extracted_path")
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.exists() or path.is_dir():
        return None

    try:
        from .protocol_ir import protocol_ir_from_path

        return protocol_ir_from_path(path)
    except Exception as exc:  # pragma: no cover - exact parser failures vary by source file
        warnings.append(
            {
                "kind": "source_ir_parse_failed",
                "source_script": script.get("name") or script.get("resolved_name"),
                "path": str(path),
                "message": str(exc),
            }
        )
    return None


def _merge_ir_inventory(ir: dict[str, Any], source_ir: dict[str, Any]) -> None:
    for labware in source_ir.get("labware") or []:
        if isinstance(labware, dict):
            _upsert_labware(
                ir,
                _first_present(labware, ("label", "name", "id")),
                catalog=_first_present(labware, ("catalog", "type", "labware_type")),
                role=labware.get("role"),
                source=_first_present(labware, ("source", "source_path")),
                extra=labware,
            )
    for liquid_class in source_ir.get("liquid_classes") or []:
        if isinstance(liquid_class, dict):
            _upsert_liquid_class(
                ir,
                _first_present(liquid_class, ("name", "label", "id")),
                role=liquid_class.get("role") or "source_ir",
                source=_first_present(liquid_class, ("source", "source_path")),
                extra=liquid_class,
            )
    for worklist in source_ir.get("worklists") or []:
        if isinstance(worklist, dict):
            _upsert_worklist(
                ir,
                _first_present(worklist, ("path", "source", "name", "id")),
                source=_first_present(worklist, ("source", "source_path")),
                extra=worklist,
            )
    for dependency in source_ir.get("dependencies") or []:
        if isinstance(dependency, dict):
            _upsert_dependency(ir, dependency)


def _append_source_steps(ir: dict[str, Any], source_ir: dict[str, Any], script: dict[str, Any]) -> int:
    count = 0
    source_label = script.get("name") or script.get("resolved_name") or script.get("resolved_path")
    for step in source_ir.get("steps") or []:
        if not isinstance(step, dict) or not step.get("operation"):
            continue
        copied = deepcopy(step)
        copied.setdefault("source_path", f"script:{source_label}")
        copied.setdefault("parameters", {})
        copied["parameters"].setdefault("selected_source_script", source_label)
        ir["steps"].append(copied)
        count += 1
    return count


def _append_pattern_window_steps(
    ir: dict[str, Any], window: dict[str, Any], warnings: list[dict[str, Any]]
) -> int:
    window_id = window.get("id")
    source_script = window.get("source_script") or window.get("source_path")
    source_path = f"pattern:{window_id} {source_script}".strip()
    count = 0

    _upsert_dependency(
        ir,
        {
            "kind": "source_pattern",
            "pattern_id": window_id,
            "pattern_type": window.get("pattern_type"),
            "name": window.get("name") or window.get("pattern_type"),
            "source_script": window.get("source_script"),
            "source_path": window.get("source_path"),
            "zeia_file": window.get("zeia_file"),
            "command_window": [
                window.get("start_command_index"),
                window.get("end_command_index"),
            ],
        },
    )

    window_fields = _normalize_fields(window.get("specifications"))
    for step in window.get("steps") or []:
        if not isinstance(step, dict):
            continue
        fields = {**window_fields, **_normalize_fields(step.get("fields"))}
        for worklist in _worklist_refs(fields):
            _upsert_worklist(ir, worklist, source=source_script)
        operation = _infer_operation(window, step, fields)
        if operation is None:
            warnings.append(
                {
                    "kind": "unsupported_command",
                    "pattern_id": window_id,
                    "command_index": step.get("command_index"),
                    "command_name": step.get("command_name"),
                }
            )
            continue

        planned = _build_planned_step(operation, window, step, fields, source_path, warnings, ir=ir)
        if not planned:
            continue

        ir["steps"].append(planned)
        _merge_step_inventory(ir, planned, fields, source_script)
        count += 1

    return count


def _build_planned_step(
    operation: str,
    window: dict[str, Any],
    step: dict[str, Any],
    fields: dict[str, Any],
    source_path: str,
    warnings: list[dict[str, Any]],
    *,
    ir: dict[str, Any],
) -> dict[str, Any] | None:
    command_name = step.get("command_name")
    target_labware = _clean_string(registry_field_value(command_name, "labware", fields)) or _labware_from_fields(fields)
    liquid_class = registry_field_value(command_name, "liquid_class", fields) or _first_field(fields, LIQUID_CLASS_FIELDS)
    volume = _number_value(registry_field_value(command_name, "volume_ul", fields) or _first_field(fields, VOLUME_FIELDS))
    worklist = _clean_string(registry_field_value(command_name, "worklist", fields)) or _first_worklist(fields)
    inference_decisions = []
    field_path = f"$.patterns[{window.get('id')}].commands[{step.get('command_index')}]"

    if not target_labware and (operation in LABWARE_TARGET_OPERATIONS or "labware" in set(registry_requires(command_name))):
        decision = infer_value(
            f"{field_path}.target_labware",
            candidates=records_to_candidates(
                [item for item in ir.get("labware") or [] if isinstance(item, dict)],
                value_keys=("label", "name", "id"),
                origin=InferenceOrigin.EXACT_SOURCE,
                source_prefix="selected_source:labware",
                reason="Reuse labware inventory mined from the selected source script.",
                intent=f"{operation} {step.get('summary') or ''}",
                priority=400,
            ),
        )
        if not decision.unresolved:
            target_labware = str(decision.value)
            inference_decisions.append(decision)

    if not liquid_class and (operation in LIQUID_CLASS_OPERATIONS or "liquid_class" in set(registry_requires(command_name))):
        decision = infer_value(
            f"{field_path}.liquid_class",
            candidates=records_to_candidates(
                [item for item in ir.get("liquid_classes") or [] if isinstance(item, dict)],
                value_keys=("name", "label", "id"),
                origin=InferenceOrigin.EXACT_SOURCE,
                source_prefix="selected_source:liquid_class",
                reason="Reuse a liquid class mined from the selected source script.",
                intent=f"{operation} {step.get('summary') or ''}",
                priority=400,
            ),
        )
        if not decision.unresolved:
            liquid_class = str(decision.value)
            inference_decisions.append(decision)

    if volume is None and (operation in VOLUME_OPERATIONS or "volume_ul" in set(registry_requires(command_name))):
        volume_candidates = [
            InferenceCandidate(
                value=item.get("volume_ul"),
                origin=InferenceOrigin.EXACT_SOURCE,
                source=str(item.get("source_path") or "selected_source:step"),
                reason="Reuse a volume from a previously mined source step.",
                priority=400,
            )
            for item in ir.get("steps") or []
            if isinstance(item, dict)
            and item.get("operation") == operation
            and _number_value(item.get("volume_ul")) is not None
        ]
        decision = infer_value(
            f"{field_path}.volume_ul",
            candidates=volume_candidates,
            fallback=InferenceCandidate(
                value=1,
                origin=InferenceOrigin.TEMPLATE_DEFAULT,
                source="protocol_ir:site_agnostic_shape",
                reason="Use the minimum positive site-agnostic draft volume.",
                priority=50,
            ),
        )
        if not decision.unresolved:
            volume = _number_value(decision.value)
            inference_decisions.append(decision)

    if not worklist and "worklist" in set(registry_requires(command_name)):
        decision = infer_value(
            f"{field_path}.worklist",
            candidates=records_to_candidates(
                [item for item in ir.get("worklists") or [] if isinstance(item, dict)],
                value_keys=("path", "source", "name", "id"),
                origin=InferenceOrigin.EXACT_SOURCE,
                source_prefix="selected_source:worklist",
                reason="Reuse a worklist mined from the selected source script.",
                priority=400,
            ),
        )
        if not decision.unresolved:
            worklist = str(decision.value)
            inference_decisions.append(decision)

    missing: list[str] = []
    required_fields = set(registry_requires(command_name))
    if (operation in LABWARE_TARGET_OPERATIONS or "labware" in required_fields) and not target_labware:
        missing.append("target_labware")
    if (operation in VOLUME_OPERATIONS or "volume_ul" in required_fields) and volume is None:
        missing.append("volume_ul")
    if (operation in LIQUID_CLASS_OPERATIONS or "liquid_class" in required_fields) and not liquid_class:
        missing.append("liquid_class")
    if "worklist" in required_fields and not worklist:
        missing.append("worklist")

    if missing:
        warnings.append(
            {
                "kind": "incomplete_pattern_step",
                "pattern_id": window.get("id"),
                "command_index": step.get("command_index"),
                "command_name": step.get("command_name"),
                "operation": operation,
                "missing": missing,
            }
        )
        return None

    parameters = {
        "source_pattern_id": window.get("id"),
        "source_pattern_type": window.get("pattern_type"),
        "source_script": window.get("source_script"),
        "zeia_file": window.get("zeia_file"),
        "command_index": step.get("command_index"),
        "command_family": step.get("command_family"),
        "summary": step.get("summary"),
        "line": step.get("line"),
        "fields": fields,
        "inference": [decision.to_dict() for decision in inference_decisions],
    }
    if worklist:
        parameters["worklist"] = worklist
    if inference_decisions:
        warnings.append(
            {
                "kind": "inferred_pattern_step",
                "pattern_id": window.get("id"),
                "command_index": step.get("command_index"),
                "operation": operation,
                "decisions": [decision.to_dict() for decision in inference_decisions],
            }
        )

    planned: dict[str, Any] = {
        "operation": operation,
        "group": "Synthesized Pattern Steps",
        "name": operation_name(operation),
        "source_path": f"{source_path} -> command {step.get('command_index')}",
        "parameters": {key: value for key, value in parameters.items() if value not in (None, "", [])},
    }
    if target_labware:
        planned["target_labware"] = target_labware
    if volume is not None:
        planned["volume_ul"] = volume
    if liquid_class:
        planned["liquid_class"] = str(liquid_class)
    if operation == Operation.READ_WORKLIST.value and worklist:
        planned["source"] = str(worklist)
        if target_labware:
            planned["destination"] = target_labware
    if operation == Operation.MOVE_PLATE.value:
        planned["parameters"]["labware"] = target_labware
        destination_location = (
            registry_field_value(command_name, "destination_location", fields)
            or _first_field(fields, MOVE_DESTINATION_LOCATION_FIELDS)
        )
        destination_site = _first_field(fields, MOVE_DESTINATION_SITE_FIELDS)
        onto_labware = _first_field(fields, MOVE_ONTO_LABWARE_FIELDS)
        if destination_location:
            planned["parameters"]["destination_location"] = str(destination_location)
        if destination_site:
            planned["parameters"]["destination_site"] = destination_site
        if onto_labware:
            planned["parameters"]["onto_labware"] = str(onto_labware)

    return planned


def _merge_step_inventory(
    ir: dict[str, Any], step: dict[str, Any], fields: dict[str, Any], source_script: str | None
) -> None:
    target = step.get("target_labware")
    if target:
        _upsert_labware(
            ir,
            target,
            catalog=_first_field(fields, LABWARE_TYPE_FIELDS),
            role=_labware_role_for_operation(str(step.get("operation"))),
            source=source_script,
        )
    liquid_class = step.get("liquid_class")
    if liquid_class:
        _upsert_liquid_class(
            ir,
            liquid_class,
            role=_liquid_class_role_for_operation(str(step.get("operation"))),
            source=source_script,
        )


def _ensure_setup_steps_for_targets(ir: dict[str, Any]) -> int:
    existing_setup = {
        step.get("target_labware")
        for step in ir.get("steps") or []
        if isinstance(step, dict)
        and step.get("operation") in {Operation.ADD_LABWARE.value, Operation.LOAD_LABWARE.value}
        and step.get("target_labware")
    }
    runtime_targets: list[str] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation = str(step.get("operation") or "")
        target = step.get("target_labware")
        if (
            target
            and operation in LABWARE_TARGET_OPERATIONS
            and operation not in {Operation.ADD_LABWARE.value, Operation.LOAD_LABWARE.value}
            and target not in runtime_targets
        ):
            runtime_targets.append(str(target))

    setup_steps: list[dict[str, Any]] = []
    for index, label in enumerate(runtime_targets, start=1):
        if label in existing_setup:
            continue
        labware = _find_labware(ir, label) or {}
        setup_steps.append(
            {
                "operation": Operation.ADD_LABWARE.value,
                "group": "Deck Setup",
                "name": operation_name(Operation.ADD_LABWARE.value),
                "target_labware": label,
                "source_path": "ir_synthesis:setup",
                "parameters": {
                    "label": label,
                    "catalog": labware.get("catalog") or label,
                    "location": labware.get("location") or "Site",
                    "position": labware.get("position") or index,
                    "source": "ir_synthesis",
                },
            }
        )
        _upsert_labware(
            ir,
            label,
            catalog=labware.get("catalog") or label,
            role=labware.get("role") or "protocol_target",
            source="ir_synthesis",
        )

    if setup_steps:
        ir["steps"] = setup_steps + list(ir.get("steps") or [])
    return len(setup_steps)


def _renumber_steps(ir: dict[str, Any]) -> None:
    for index, step in enumerate(ir.get("steps") or [], start=1):
        if isinstance(step, dict):
            operation = str(step.get("operation") or "")
            step["id"] = f"step_{index:03d}"
            step["index"] = index
            step.setdefault("group", "Steps")
            step.setdefault("name", operation_name(operation) if operation else f"Step {index}")
            step.setdefault("parameters", {})


def _infer_operation(window: dict[str, Any], step: dict[str, Any], fields: dict[str, Any]) -> str | None:
    if str(window.get("pattern_type") or "").strip().lower() == "gripper":
        return None
    registry_operation = registry_command_operation(step.get("command_name"))
    if registry_operation in VALID_OPERATION_VALUES:
        return registry_operation
    if registry_operation:
        return None
    step_text = " ".join(
        str(value)
        for value in (
            step.get("command_name"),
            step.get("command_family"),
            step.get("summary"),
            " ".join(str(key) for key in fields.keys()),
        )
        if value
    ).lower()
    operation = _infer_operation_from_text(step_text)
    if operation:
        return operation

    text = " ".join(
        str(value)
        for value in (
            window.get("pattern_type"),
            window.get("name"),
            step_text,
        )
        if value
    ).lower()
    return _infer_operation_from_text(text)


def _infer_operation_from_text(text: str) -> str | None:
    if "addlabware" in text or "add labware" in text:
        return Operation.ADD_LABWARE.value
    if "loadlabware" in text or "load labware" in text:
        return Operation.LOAD_LABWARE.value
    if any(token in text for token in ("initialize", "initialise", "init device", "home", "reset", "prime")):
        return Operation.INITIALIZE_DEVICE.value
    if "pick_up_tips" in text or "pickup" in text or "pick up" in text or "get tips" in text:
        return Operation.PICK_UP_TIPS.value
    if "settipsback" in text or "set tips back" in text or "return tips" in text:
        return Operation.SET_TIPS_BACK.value
    if "droptip" in text or "drop tip" in text or "eject tip" in text or "discard tip" in text:
        if "mca" in text:
            return Operation.MCA384_DROP_TIPS.value
        if "liha" in text:
            return Operation.LIHA_DROP_TIPS.value
        return Operation.DROP_TIPS.value
    if "aspirate" in text:
        if "liha" in text:
            return Operation.LIHA_ASPIRATE.value
        return Operation.ASPIRATE.value
    if "dispense" in text:
        if "liha" in text:
            return Operation.LIHA_DISPENSE.value
        return Operation.DISPENSE.value
    if "mix" in text:
        if "liha" in text:
            return Operation.LIHA_MIX.value
        return Operation.MCA384_MIX.value
    if "worklist" in text or ".gwl" in text:
        return Operation.READ_WORKLIST.value
    if any(token in text for token in ("wash", "flush", "clean", "decontam")):
        return Operation.WASH.value
    if "gripper" in text or "move plate" in text or "move_plate" in text:
        return Operation.MOVE_PLATE.value
    if "prompt" in text or "queryvariable" in text or "confirm" in text:
        return Operation.PROMPT_USER.value
    if "comment" in text:
        return Operation.COMMENT.value
    if "loop" in text or "foreach" in text:
        return Operation.LOOP_OVER_WELLS.value
    return None


def _upsert_labware(
    ir: dict[str, Any],
    label: Any,
    *,
    catalog: Any | None = None,
    role: Any | None = None,
    source: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    label_text = _clean_string(label)
    if not label_text:
        return
    catalog_text = _clean_string(catalog)
    role_text = _clean_string(role)
    source_text = _clean_string(source)
    current = _find_labware(ir, label_text)
    if current is None:
        current = {"label": label_text}
        ir.setdefault("labware", []).append(current)
    if extra:
        for key, value in extra.items():
            if value not in (None, "", []) and key not in current:
                current[key] = value
    current.setdefault("label", label_text)
    if catalog_text:
        current.setdefault("catalog", catalog_text)
    if role_text:
        current.setdefault("role", role_text)
    if source_text:
        current.setdefault("source", source_text)
    current.setdefault("role", "protocol_target")


def _upsert_liquid_class(
    ir: dict[str, Any],
    name: Any,
    *,
    role: Any | None = None,
    source: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    name_text = _clean_string(name)
    if not name_text:
        return
    current = _find_named(ir.setdefault("liquid_classes", []), "name", name_text)
    if current is None:
        current = {"name": name_text}
        ir["liquid_classes"].append(current)
    if extra:
        for key, value in extra.items():
            if value not in (None, "", []) and key not in current:
                current[key] = value
    role_text = _clean_string(role)
    source_text = _clean_string(source)
    if role_text:
        current.setdefault("role", role_text)
    if source_text:
        current.setdefault("source", source_text)
    current.setdefault("role", "pipetting")


def _upsert_worklist(
    ir: dict[str, Any],
    ref: Any,
    *,
    source: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    ref_text = _clean_string(ref)
    if not ref_text:
        return
    current = _find_worklist(ir, ref_text)
    if current is None:
        current = {"name": Path(ref_text).stem or ref_text, "source": ref_text}
        ir.setdefault("worklists", []).append(current)
    if extra:
        for key, value in extra.items():
            if value not in (None, "", []) and key not in current:
                current[key] = value
    source_text = _clean_string(source)
    if source_text:
        current.setdefault("referenced_by", source_text)


def _upsert_dependency(ir: dict[str, Any], dependency: dict[str, Any]) -> None:
    clean = {key: value for key, value in dependency.items() if value not in (None, "", [])}
    if not clean:
        return
    key = (
        clean.get("kind"),
        clean.get("name"),
        clean.get("pattern_id"),
        clean.get("source_path"),
        clean.get("guid"),
    )
    for current in ir.setdefault("dependencies", []):
        if not isinstance(current, dict):
            continue
        current_key = (
            current.get("kind"),
            current.get("name"),
            current.get("pattern_id"),
            current.get("source_path"),
            current.get("guid"),
        )
        if current_key == key:
            current.update({k: v for k, v in clean.items() if k not in current or current[k] in (None, "", [])})
            return
    ir["dependencies"].append(clean)


def _find_labware(ir: dict[str, Any], label: str) -> dict[str, Any] | None:
    for item in ir.get("labware") or []:
        if isinstance(item, dict) and _clean_string(_first_present(item, ("label", "name", "id"))) == label:
            return item
    return None


def _find_named(items: list[Any], key: str, value: str) -> dict[str, Any] | None:
    for item in items:
        if isinstance(item, dict) and _clean_string(item.get(key)) == value:
            return item
    return None


def _find_worklist(ir: dict[str, Any], ref: str) -> dict[str, Any] | None:
    for item in ir.get("worklists") or []:
        if not isinstance(item, dict):
            continue
        if _clean_string(item.get("source")) == ref or _clean_string(item.get("path")) == ref:
            return item
    return None


def _normalize_fields(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _scalar_or_copy(item) for key, item in value.items() if item not in (None, "", [])}


def _scalar_or_copy(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        values = [_scalar_or_copy(item) for item in value]
        return [item for item in values if item not in (None, "", [])]
    if isinstance(value, dict):
        return {str(key): _scalar_or_copy(item) for key, item in value.items() if item not in (None, "", [])}
    return str(value)


def _first_field(fields: dict[str, Any], names: tuple[str, ...]) -> Any:
    by_lower = {key.lower(): value for key, value in fields.items()}
    for name in names:
        value = by_lower.get(name.lower())
        if value not in (None, "", []):
            if isinstance(value, list):
                return next((item for item in value if item not in (None, "", [])), None)
            return value
    return None


def _labware_from_fields(fields: dict[str, Any]) -> str | None:
    return _clean_string(_first_field(fields, LABWARE_FIELDS))


def _number_value(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return None
    if number.is_integer():
        return int(number)
    return number


def _worklist_refs(fields: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for value in (_first_field(fields, (field,)) for field in WORKLIST_FIELDS):
        for item in _string_values(value):
            if item and item not in refs:
                refs.append(item)
    for value in fields.values():
        for item in _string_values(value):
            if _looks_like_worklist(item) and item not in refs:
                refs.append(item)
    return refs


def _first_worklist(fields: dict[str, Any]) -> str | None:
    refs = _worklist_refs(fields)
    return refs[0] if refs else None


def _looks_like_worklist(value: str) -> bool:
    return str(value).strip().lower().endswith((".gwl", ".csv", ".tsv"))


def _string_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_string_values(item))
        return values
    if isinstance(value, dict):
        values = []
        for item in value.values():
            values.extend(_string_values(item))
        return values
    text = _clean_string(value)
    return [text] if text else []


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", []):
            return value
    return None


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        return _clean_string(next((item for item in value if item not in (None, "", [])), None))
    text = str(value).strip()
    return text or None


def _labware_role_for_operation(operation: str) -> str:
    if "aspirate" in operation:
        return "source"
    if "dispense" in operation:
        return "destination"
    if "tip" in operation:
        return "tips"
    return "protocol_target"


def _liquid_class_role_for_operation(operation: str) -> str:
    if "aspirate" in operation:
        return "aspiration"
    if "dispense" in operation:
        return "dispense"
    if "mix" in operation:
        return "mix"
    return "pipetting"
