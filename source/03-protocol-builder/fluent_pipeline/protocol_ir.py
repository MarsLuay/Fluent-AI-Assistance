"""Canonical, tool-neutral Tecan protocol IR.

This module is intentionally independent from FluentControl XML and from the
first-party fluentcoder authoring objects.  It gives Codex one stable contract to
review and edit, then downstream renderers can produce Python drafts, GWL
worklists, compiled XSCR files, and handoff documentation from the same data.
"""

from __future__ import annotations

import ast
import copy
import html
import json
import re
import tempfile
import zipfile
from . import xml_compat as ET
from pathlib import Path
from typing import Any, Iterable
from tecan_common.zeia_limits import validate_zeia_archive_limits

from tecan_common.gwl import (
    Break,
    Comment,
    Pipette,
    Wash,
    Worklist,
    parse_gwl_line,
    serialize_gwl,
)
from fluentcoder.expressions import (
    canonical_expression_key,
    expression_fields_for_command,
    expression_to_mapping,
    parse_or_preserve_source_expression,
    walk_expression_values,
)

from .command_registry import registry_command_operation, registry_field_value
from .protocol_ir_compat import (
    CANONICAL_SETUP_GROUP_NAME,
    CATALOG_DEFAULT_CLASS_NAMES,
    DEFAULT_TOUCHTOOLS_IMAGES_DIR,
    MEDIA_PLACEHOLDER_BEGIN,
    MEDIA_PLACEHOLDER_END,
    PLACEHOLDER_IMAGE_TEMPLATE_PATH,
    RUP_VARIABLE_REVIEW_INSTRUCTIONS,
    RUP_VARIABLE_SELECTOR_INSTRUCTIONS,
    TOUCHTOOLS_IMAGE_ROOT,
    TOUCHTOOLS_SCRIPT_FILES_SUBDIR,
    _MINIMAL_PLACEHOLDER_PNG,
    annotate_verification_prompts_with_media,
    apply_default_verification_worktable_bindings,
    apply_deployed_touchtools_media_paths,
    build_media_path_map,
    build_media_path_map_from_placeholder_rows,
    build_media_path_map_from_specs,
    canonical_setup_group_name_for_steps,
    collect_media_placeholders,
    deployed_media_path,
    apply_touchtools_media_path_map_to_xscr,
    force_worktable_prompt_images,
    is_setup_group_name,
    media_slot_filename,
    media_slot_relative_path,
    media_slot_specs,
    normalize_group_hierarchy,
    normalize_operator_prompt_text,
    normalize_runtime_variable_prompt_instructions,
    normalize_setup_groups,
    prompt_has_media_boilerplate,
    prompt_image_media_slots,
    prompt_looks_like_external_initialization_check,
    prompt_step_image_path,
    prompt_step_is_deck_presence_check,
    prompt_step_media_path,
    prompt_step_worktable_binding,
    prompt_step_worktable_media_path,
    protocol_is_prompt_only,
    route_unbound_worktable_prompts_to_standard,
    required_media_slot_specs,
    resolve_touchtools_images_dir,
    resolve_touchtools_media_subfolder,
    resolve_verification_prompt_rup_kind,
    rewrite_flat_touchtools_media_paths_in_xscr,
    sanitize_worktable_prompt_variable_labware_bindings,
    sound_path_specs_from_ir,
    sync_verification_prompt_target_labware,
    touchtools_media_subfolder,
    worktable_pipeline_video_slots,
    render_media_path_map_markdown,
    write_placeholder_image_slot,
    write_placeholder_video_slot,
)  # noqa: F401
from .protocol_ir_schema import (
    CURRENT_PROTOCOL_IR_BUNDLE_VERSION,
    CURRENT_PROTOCOL_IR_VERSION,
    LABWARE_TARGET_OPERATIONS,
    Operation,
    assert_valid_protocol_ir,
    migrate_protocol_ir,
    migrate_protocol_ir_bundle,
    normalize_operation,
    operation_specs,
    operation_name,
    protocol_ir_bundle_json_schema,
    protocol_ir_json_schema,
    protocol_ir_schema_markdown,
    protocol_ir_schema_versions,
    register_protocol_ir_migration,
    protocol_ir_migration_path,
    validate_protocol_ir,
    validate_protocol_ir_bundle,
    validate_protocol_ir_document,
    validate_protocol_ir_bundle_document,
)
from .subroutine_dependencies import upsert_ir_subroutine_dependencies
from .ir.rga_move_policy import (
    _manual_rga_adapter_move_name,
    _rga_move_report_entry,
    _rga_source_pattern,
    apply_rga_move_pattern_policy,
    convert_unsafe_rga_adapter_moves_to_prompts,
)

CANONICAL_IR_VERSION = CURRENT_PROTOCOL_IR_VERSION
CANONICAL_IR_BUNDLE_VERSION = CURRENT_PROTOCOL_IR_BUNDLE_VERSION
if CANONICAL_IR_VERSION != CURRENT_PROTOCOL_IR_VERSION:
    raise RuntimeError("Protocol IR version constant mismatch")
if CANONICAL_IR_BUNDLE_VERSION != CURRENT_PROTOCOL_IR_BUNDLE_VERSION:
    raise RuntimeError("Protocol IR bundle version constant mismatch")
_PROSE_PARAMETER_KEYS = {
    "display_text",
    "instructions",
    "message",
    "prompt",
    "screen_title",
}

# Prompt/media helper implementations live in protocol_ir_compat.py and are
# re-exported here so callers can keep importing from protocol_ir.py.


def protocol_ir_from_path(path: Path) -> dict[str, Any]:
    """Load a Python draft, XSCR, GWL, or ZEIA archive into canonical IR."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".py":
        return protocol_ir_from_python(source)
    if suffix == ".xscr":
        return protocol_ir_from_xscr(source)
    if suffix == ".gwl":
        return protocol_ir_from_gwl(source)
    if suffix == ".zeia":
        return protocol_ir_bundle_from_zeia(source)
    raise ValueError(f"Unsupported IR input type: {source.suffix or source.name}")


def protocol_ir_from_python(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    build = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "build_worktable"
        ),
        None,
    )
    if build is None:
        raise ValueError("Python draft must define build_worktable()")

    ir = _empty_ir(path.stem, source_format="python", source_path=str(path))
    labware_by_var: dict[str, str] = {}
    reagent_by_var: dict[str, str] = {}
    current_group = "Ungrouped"

    for statement in build.body:
        target = _assignment_target_name(statement)
        value = statement.value if isinstance(statement, (ast.Assign, ast.Expr)) else statement
        call = value if isinstance(value, ast.Call) else None

        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Attribute):
            attr = statement.value.attr.lower()
            if attr in {"mca96", "liha", "fca"}:
                _add_dependency(
                    ir,
                    kind="device",
                    name=_friendly_device_name(attr),
                    required=True,
                    source_path=_source_path(path.name, current_group, source, statement.value),
                )
            continue

        if call is None:
            continue

        if _is_worktable_from_workspace(call):
            ir["protocol"].update(_python_protocol_metadata(call, fallback_name=path.stem))
            ir["worktable"].update(_python_worktable_metadata(call))
            continue

        if _call_name(call) == "Reagent" and target:
            reagent_name = _value_label(call.args[0], reagent_by_var, labware_by_var) if call.args else target
            reagent_by_var[target] = reagent_name
            _upsert_item(
                ir["reagents"],
                {"id": _safe_id(reagent_name), "name": reagent_name, "role": "reagent"},
                key="name",
            )
            continue

        if _is_method_call(call, "group"):
            current_group = _value_label(call.args[0], reagent_by_var, labware_by_var) if call.args else current_group
            continue

        if _is_method_call(call, "place"):
            item = _python_labware(call, path.name, current_group, source)
            if item:
                _upsert_item(ir["labware"], item, key="label")
                if target:
                    labware_by_var[target] = item["label"]
                _append_step(
                    ir,
                    group=current_group,
                    operation="add_labware",
                    name="Add Labware",
                    target_labware=item["label"],
                    parameters={
                        "labware_type": item.get("catalog") or item.get("python_class") or "",
                        "label": item["label"],
                        "location": item.get("location", ""),
                        "position": item.get("position"),
                        "rotation": item.get("rotation", 0),
                        "has_lid": item.get("has_lid", False),
                    },
                    source_path=item.get("source_path", ""),
                )
            continue

        if _is_method_call(call, "fill_all"):
            _apply_python_fill_all(ir, call, labware_by_var, reagent_by_var)
            continue

        if _is_method_call(call, "add_comment"):
            comment = _value_label(call.args[0], reagent_by_var, labware_by_var) if call.args else ""
            _append_step(
                ir,
                group=current_group,
                operation="comment",
                name="Comment",
                command_id="CommentStatement",
                parameters={"comment": comment, "method": "add_comment"},
                source_path=_source_path(path.name, current_group, source, call),
            )
            continue

        if _is_method_call(call, "user_prompt") or _is_method_call(call, "user_prompt_worktable"):
            prompt_step = _python_prompt_step(call, path.name, current_group, source)
            if prompt_step:
                _append_step(ir, **prompt_step)
            continue

        step = _python_runtime_step(call, path.name, current_group, source, reagent_by_var, labware_by_var)
        if step:
            _append_step(ir, **step)
            liquid_class = step.get("liquid_class")
            if liquid_class:
                _upsert_item(
                    ir["liquid_classes"],
                    {"name": liquid_class, "role": "pipetting"},
                    key="name",
                )

    _finalize_ir(ir)
    return ir


def protocol_ir_from_xscr(path: Path, *, source_name: str | None = None) -> dict[str, Any]:
    root = ET.parse(path).getroot()
    command_index_by_object = {
        id(command_object): command_index
        for command_index, command_object in enumerate(_iter_local(root, "Object"), start=1)
    }
    parent_by_child = {
        child: parent
        for parent in root.iter()
        for child in list(parent)
    }
    source_label = source_name or str(path)
    ir = _empty_ir(path.stem, source_format="xscr", source_path=source_label)

    payload = _first_descendant(root, "Payload")
    protocol_name = _direct_text(payload, "ObjectName") if payload is not None else ""
    comment = _direct_text(payload, "Comment") if payload is not None else ""
    if protocol_name:
        ir["protocol"]["name"] = protocol_name
    if comment:
        ir["protocol"]["comment"] = comment

    for variable in _xscr_variable_declarations(root, source_label):
        _upsert_item(ir["variables"], variable, key="name")

    for reference in _iter_local(root, "Reference"):
        kind = _direct_text(reference, "TypeId") or "reference"
        name = _direct_text(reference, "ObjectName") or ""
        guid = _direct_text(reference, "Guid") or ""
        if kind == "WorktableWorkspace":
            ir["worktable"]["name"] = name
            ir["worktable"]["guid"] = guid
        elif kind == "LiquidClass" and name:
            _upsert_item(ir["liquid_classes"], {"name": name, "guid": guid}, key="name")
        if name or guid:
            _add_dependency(ir, kind=kind, name=name or guid, guid=guid, required=True)

    def append_command(
        command_object: ET.Element,
        group_name: str,
        *,
        embedded_in_raw_command: str = "",
    ) -> None:
        step = _xscr_step(command_object, group_name or "Ungrouped", source_entry=source_label)
        if not step:
            return
        parameters = step.setdefault("parameters", {})
        parameters["command_index"] = command_index_by_object.get(id(command_object))
        parameters.setdefault(
            "line_number",
            _number_or_text(
                _first_text(command_object, "LineNumber")
                or _application_driver_macro_attr(command_object, "LineNumber")
                or ""
            ),
        )
        if embedded_in_raw_command:
            parameters["embedded_in_raw_command"] = embedded_in_raw_command
        _append_step(ir, **step)
        _record_xscr_variable_step(ir, ir["steps"][-1])
        if step["operation"] == "add_labware":
            params = step["parameters"]
            _upsert_item(
                ir["labware"],
                {
                    "id": _safe_id(str(params.get("label") or "")),
                    "label": params.get("label") or "",
                    "catalog": params.get("labware_type") or "",
                    "location": params.get("location") or "",
                    "position": params.get("position"),
                    "rotation": params.get("rotation", 0),
                    "has_lid": params.get("has_lid", False),
                    "role": _infer_labware_role(
                        str(params.get("label") or ""),
                        str(params.get("labware_type") or ""),
                    ),
                },
                key="label",
            )
        if step.get("liquid_class"):
            _upsert_item(
                ir["liquid_classes"],
                {"name": step["liquid_class"], "role": "pipetting"},
                key="name",
            )

    def append_statement_commands(
        statements: ET.Element | None,
        group_name: str,
        *,
        embedded_in_raw_command: str = "",
    ) -> None:
        for command_object in _xscr_leaf_command_objects(statements):
            append_command(
                command_object,
                group_name,
                embedded_in_raw_command=embedded_in_raw_command,
            )

    # VisionX source scripts may place executable objects directly under the
    # root ``Script/Commands/ScriptGroup``. Compiled fluentcoder output places
    # the same objects inside ``ScriptGroupDataV1``. Read both layouts in
    # document order so canonical roundtrips do not lose root loops or RUPs.
    processed_group_objects: set[int] = set()
    for root_group in _root_script_groups(root, parent_by_child):
        root_group_name = _direct_text(root_group, "Name") or "Ungrouped"
        root_objects = _direct_child(root_group, "Objects")
        for root_object in _direct_children(root_objects, "Object"):
            object_type = str(root_object.attrib.get("Type") or "")
            if object_type.endswith("ScriptGroupDataV1"):
                processed_group_objects.add(id(root_object))
                group_data = _direct_child(root_object, "ScriptGroupDataV1")
                group_name = _direct_text(group_data, "Name") if group_data is not None else root_group_name
                append_statement_commands(
                    _first_descendant(root_object, "Statements"),
                    group_name or "Ungrouped",
                    embedded_in_raw_command=_enclosing_raw_command_id(root_object, parent_by_child),
                )
            else:
                append_command(root_object, root_group_name)

    for group_object in _iter_local(root, "Object"):
        object_type = str(group_object.attrib.get("Type") or "")
        if not object_type.endswith("ScriptGroupDataV1") or id(group_object) in processed_group_objects:
            continue
        group_data = _direct_child(group_object, "ScriptGroupDataV1")
        group_name = _direct_text(group_data, "Name") if group_data is not None else "Ungrouped"
        append_statement_commands(
            _first_descendant(group_object, "Statements"),
            group_name or "Ungrouped",
            embedded_in_raw_command=_enclosing_raw_command_id(group_object, parent_by_child),
        )

    _finalize_ir(ir)
    return ir


def _xscr_nested_leaf_supported(command_object: ET.Element) -> bool:
    """True when a nested XSCR Object can become a protocol-IR step.

    ``ApplicationDriverMacro`` / ``LegacyDriverMacro`` are approved passthrough
    commands. Only ``RGA1_TransferLabware`` has a typed IR representation;
    every other application-driver macro remains a native raw-XML step. Nested
    toggle groups must retain both kinds or canonical roundtrips silently drop
    hardware commands.
    """
    command_id = _command_id(command_object)
    if _operation_from_command_id(command_id):
        return True
    if command_id == "LegacyDriverMacro":
        return True
    if command_id == "ApplicationDriverMacro":
        return True
    return False


def _xscr_leaf_command_objects(statements: ET.Element | None) -> Iterable[ET.Element]:
    """Yield executable XSCR commands inside conditional/default wrappers."""
    for command_object in _direct_children(statements, "Object"):
        command_id = _command_id(command_object)
        if command_id in {"ConditionalGroup", "AlternateGroup"}:
            payload = _direct_child(command_object, command_id)
            objects = _direct_child(payload, "Objects") if payload is not None else None
            nested = list(_xscr_leaf_command_objects(objects))
            supported_nested = [
                item
                for item in nested
                if _xscr_nested_leaf_supported(item)
            ]
            if supported_nested:
                yield from supported_nested
            else:
                yield command_object
            continue
        yield command_object


def _root_script_groups(
    root: ET.Element,
    parent_by_child: dict[ET.Element, ET.Element],
) -> Iterable[ET.Element]:
    """Yield native root ScriptGroup containers in document order."""
    for group in _iter_local(root, "ScriptGroup"):
        parent = parent_by_child.get(group)
        if parent is not None and _local_name(parent.tag) == "Commands":
            yield group


def protocol_ir_from_gwl(path: Path) -> dict[str, Any]:
    ir = _empty_ir(path.stem, source_format="gwl", source_path=str(path))
    ir["protocol"]["name"] = path.stem
    records: list[dict[str, Any]] = []

    for line_number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        record = parse_gwl_line(raw, line_no=line_number, permissive=True)
        if record is None:
            continue
        if isinstance(record, Pipette):
            record_data = {
                "operation": "aspirate" if record.operation == "A" else "dispense",
                "rack_label": record.rack_label,
                "rack_id": record.rack_id,
                "rack_type": record.rack_type,
                "position": record.position,
                "tube_id": record.tube_id,
                "volume_ul": _number_or_text(record.volume),
                "liquid_class": record.liquid_class,
                "tip_mask": record.tip_mask,
                "forced_rack_type": record.forced_rack_type,
                "line_number": line_number,
            }
            records.append(record_data)
            _upsert_item(
                ir["labware"],
                {
                    "id": _safe_id(record.rack_label),
                    "label": record.rack_label,
                    "catalog": record.rack_type,
                    "role": "source" if record.operation == "A" else "destination",
                },
                key="label",
            )
            if record.liquid_class:
                _upsert_item(ir["liquid_classes"], {"name": record.liquid_class}, key="name")
            _append_step(
                ir,
                group="Worklist",
                operation=record_data["operation"],
                name=record_data["operation"].replace("_", " ").title(),
                command_id=record.operation,
                target_labware=record.rack_label,
                volume_ul=record_data["volume_ul"],
                liquid_class=record.liquid_class or None,
                parameters=record_data,
                source_path=f"{path.name} -> line {line_number}",
            )
            continue
        if isinstance(record, Comment):
            records.append({"operation": "comment", "text": record.text, "line_number": line_number})
            continue
        if isinstance(record, Wash):
            wash_record = {"operation": "wash", "record_type": record.type_character, "line_number": line_number}
            if record.scheme is not None:
                wash_record["scheme"] = record.type_character[1:]
            records.append(wash_record)
            continue
        if isinstance(record, Break):
            records.append({"operation": "break", "line_number": line_number})
            continue
    ir["worklists"].append({"name": path.stem, "source": str(path), "records": records})
    _finalize_ir(ir)
    return ir


def protocol_ir_bundle_from_zeia(path: Path) -> dict[str, Any]:
    protocols: list[dict[str, Any]] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf, tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            infos = validate_zeia_archive_limits(zf)
            for info in infos:
                entry = info.filename
                if Path(entry).suffix.lower() != ".xscr":
                    continue
                target = tmp_dir / _safe_filename(Path(entry).name or f"script_{len(protocols) + 1}.xscr")
                target.write_bytes(zf.read(entry))
                protocol = protocol_ir_from_xscr(target, source_name=f"{path.name}!{entry}")
                protocol["source"]["archive_entry"] = entry
                protocols.append(protocol)
    else:
        protocols = _protocols_from_imported_project_fallback(path)
        if not protocols:
            raise zipfile.BadZipFile("File is not a zip file")

    return migrate_protocol_ir_bundle({
        "ir_version": CANONICAL_IR_BUNDLE_VERSION,
        "source": {"format": "zeia", "path": str(path)},
        "protocol_count": len(protocols),
        "protocols": protocols,
    })


def _protocols_from_imported_project_fallback(path: Path) -> list[dict[str, Any]]:
    project_root = path.parents[1] if path.parent.name == "source" and len(path.parents) >= 2 else path.parent
    manifest_path = project_root / "manifest.json"
    extracted_dir = project_root / "extracted"
    if not manifest_path.exists() or not extracted_dir.is_dir():
        return []

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}

    candidate_paths: list[tuple[str, Path]] = []
    for script in manifest.get("scripts") or []:
        if not isinstance(script, dict):
            continue
        relative = str(script.get("extracted_path") or "").strip()
        entry = str(script.get("entry") or relative or "")
        if not relative:
            continue
        script_path = project_root / relative
        if script_path.suffix.lower() == ".xscr" and script_path.is_file():
            candidate_paths.append((entry, script_path))

    if not candidate_paths:
        candidate_paths = [(item.name, item) for item in sorted(extracted_dir.rglob("*.xscr")) if item.is_file()]

    protocols: list[dict[str, Any]] = []
    for entry, script_path in candidate_paths:
        protocol = protocol_ir_from_xscr(script_path, source_name=f"{path.name}!{entry}")
        protocol["source"]["archive_entry"] = entry
        protocols.append(protocol)
    return protocols


def load_protocol_ir(path: Path) -> dict[str, Any]:
    payload = load_ir_payload(path)
    return migrate_protocol_ir(payload)


def write_protocol_ir(ir: dict[str, Any], path: Path) -> None:
    write_ir_payload(migrate_protocol_ir(ir), path)


def load_ir_payload(path: Path) -> dict[str, Any]:
    if path.suffix.lower() in {".yaml", ".yml"}:
        yaml = _yaml_module()
        with path.open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
        return payload or {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_ir_payload(payload: dict[str, Any], path: Path) -> None:
    version = payload.get("ir_version")
    if (version in (None, "") and isinstance(payload.get("steps"), list)) or (
        isinstance(payload.get("steps"), list) and not isinstance(payload.get("protocols"), list)
    ):
        payload = migrate_protocol_ir(payload)
    elif isinstance(payload.get("protocols"), list):
        payload = migrate_protocol_ir_bundle(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in {".yaml", ".yml"}:
        yaml = _yaml_module()
        with path.open("w", encoding="utf-8") as handle:
            yaml.safe_dump(payload, handle, sort_keys=False)
        return
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def render_python_draft(ir: dict[str, Any]) -> str:
    """Render a canonical protocol IR as a fluentcoder Python draft."""
    normalized = normalize_setup_groups(migrate_protocol_ir(ir))
    normalized = sanitize_worktable_prompt_variable_labware_bindings(normalized)
    return _render_python_draft_from_validated_ir(normalized)


def _render_python_draft_from_validated_ir(ir: dict[str, Any]) -> str:
    """Render an already validated canonical protocol IR."""
    protocol = ir.get("protocol", {})
    worktable = ir.get("worktable", {})
    labware = list(ir.get("labware") or [])
    steps = list(ir.get("steps") or [])

    classes = sorted(
        {
            str(item.get("python_class") or _python_class_for_labware(item))
            for item in labware
            if item.get("label")
        }
        | {"Reagent", "Worktable"}
        | _expression_imports_for_render(steps, ir.get("variables") or [])
        | (
            {"VariableMapping", "parse_expression"}
            if any(
                isinstance(step, dict)
                and str(step.get("operation") or "") == "call_subroutine"
                and any((step.get("parameters") or {}).get(section) for section in ("variable_mappings_start", "variable_mappings_end"))
                for step in steps
            )
            else set()
        )
    )
    lines = [
        '"""Generated from canonical Tecan Protocol IR."""',
        "",
        f"from fluentcoder import {', '.join(classes)}",
        "",
        "",
        "def build_worktable() -> Worktable:",
    ]

    protocol_name = protocol.get("name") or "Generated protocol"
    comment = protocol.get("comment") or ""
    worktable = worktable if isinstance(worktable, dict) else {}
    workspace_name = str(worktable.get("name") or "").strip()
    workspace_guid = str(worktable.get("guid") or "").strip()
    # Do not invent lab worktable names (e.g. 780_Empty). Empty name is allowed for
    # IR fragments; generation_workflow must bind name/guid before calling render.
    wt_args = [repr(workspace_name)]
    if workspace_guid:
        wt_args.append(f"workspace_guid={workspace_guid!r}")
    wt_args.append(f"auto_place={bool(worktable.get('auto_place', False))!r}")
    wt_args.append(f"protocol_name={protocol_name!r}")
    wt_args.append(f"comment={comment!r}")
    lines.append(f"    wt = Worktable.from_workspace({', '.join(wt_args)})")
    lines.append("")

    reagent_vars: dict[str, str] = {}
    for reagent in ir.get("reagents") or []:
        name = str(reagent.get("name") or "liquid")
        var = _safe_var(name, used=set(reagent_vars.values()))
        reagent_vars[name] = var
        lines.append(f"    {var} = Reagent({name!r})")
    if reagent_vars:
        lines.append("")

    declared_variables = _declared_variables_for_render(ir)
    if declared_variables:
        for name, default, scope, type_name in declared_variables:
            kwargs = []
            if scope and scope != "Script":
                kwargs.append(f"scope={scope!r}")
            if type_name:
                kwargs.append(f"type_name={type_name!r}")
            if kwargs:
                lines.append(f"    wt.declare_variable({name!r}, {default!r}, {', '.join(kwargs)})")
            else:
                lines.append(f"    wt.declare_variable({name!r}, {default!r})")
            lines.append(f"    wt.set_sim_value({name!r}, {default!r})")
        lines.append("")

    labware_vars: dict[str, str] = {}
    used_vars: set[str] = set(reagent_vars.values()) | {"wt", "head"}
    setup_steps = [step for step in steps if step.get("operation") == "add_labware"]
    setup_labels = {str(step.get("target_labware") or "") for step in setup_steps}
    rendered_setup_group = False

    def ensure_setup_group() -> None:
        nonlocal rendered_setup_group
        if not rendered_setup_group:
            lines.append(f"    wt.group({CANONICAL_SETUP_GROUP_NAME!r})")
            rendered_setup_group = True

    if setup_steps:
        ensure_setup_group()
        for step in setup_steps:
            lines.append(f"    {_step_trace_comment(step)}")
            params = step.get("parameters") or {}
            raw_xml = str(params.get("raw_xml") or "").strip()
            if raw_xml:
                command_id = str(step.get("command_id") or "AddLabwareDataV1")
                lines.append(f"    wt.raw_xml_step({command_id!r}, {raw_xml!r})")
                continue
            label = str(params.get("label") or step.get("target_labware") or "")
            item = _labware_by_label(labware, label)
            var = _safe_var(label, used=used_vars)
            used_vars.add(var)
            labware_vars[label] = var
            cls_name = str(item.get("python_class") or _python_class_for_labware(item))
            catalog = str(params.get("labware_type") or item.get("catalog") or label)
            location = str(params.get("location") or item.get("location") or "Site")
            position = params.get("position") or item.get("position") or 1
            position_expression = params.get("position_expression") or item.get("position_expression")
            position_arg = (
                _expression_python_arg(position_expression, fallback=position)
                if isinstance(position_expression, dict)
                else _python_position_arg(position)
            )
            lines.append(
                f"    {var} = wt.place({cls_name}({label!r}, catalog={catalog!r}), "
                f"{location!r}, {position_arg})"
            )
            initial = item.get("initial_contents") or {}
            if isinstance(initial, dict) and (
                initial.get("volume_ul_expression") is not None or initial.get("volume_ul")
            ):
                reagent_name = str(initial.get("reagent") or "liquid")
                reagent_var = reagent_vars.get(reagent_name)
                if reagent_var:
                    initial_volume = _expression_python_value(
                        initial.get("volume_ul_expression"),
                        fallback=initial.get("volume_ul"),
                    )
                    lines.append(f"    {var}.fill_all({reagent_var}, {initial_volume!r})")
        lines.append("")

    # Place only inventory labware referenced by executable labware-target steps.
    referenced_labels = _referenced_labware_labels(steps)
    missing_labware = [
        item
        for item in labware
        if str(item.get("label") or "") not in setup_labels
        and str(item.get("label") or "") in referenced_labels
        and item.get("catalog")
    ]
    if missing_labware:
        ensure_setup_group()
        for item in missing_labware:
            label = str(item.get("label") or "")
            if not label:
                continue
            var = _safe_var(label, used=used_vars)
            used_vars.add(var)
            labware_vars[label] = var
            cls_name = str(item.get("python_class") or _python_class_for_labware(item))
            catalog = str(item.get("catalog") or label)
            location = str(item.get("location") or "Site")
            position = (
                _expression_python_arg(
                    item.get("position_expression"),
                    fallback=item.get("position") or 1,
                )
                if isinstance(item.get("position_expression"), dict)
                else _python_position_arg(item.get("position") or 1)
            )
            lines.append(f"    {var} = wt.place({cls_name}({label!r}, catalog={catalog!r}), {location!r}, {position})")
        lines.append("")

    current_group = CANONICAL_SETUP_GROUP_NAME if rendered_setup_group else None
    current_condition: dict[str, Any] | None = None
    category_conditions = ir.get("category_conditions") if isinstance(ir.get("category_conditions"), dict) else {}
    head_declared = False
    for step in steps:
        operation = step.get("operation")
        if operation == "add_labware":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        if params.get("embedded_in_raw_command"):
            continue
        group = str(step.get("group") or "Steps")
        needs_head = _is_head_step(operation) and not head_declared
        if group != current_group:
            lines.append(f"    wt.group({group!r})")
            current_group = group
            condition = category_conditions.get(group) if isinstance(category_conditions, dict) else None
            current_condition = condition if isinstance(condition, dict) else None
            if needs_head:
                lines.append("    head = wt.mca96")
                head_declared = True
                needs_head = False
            if current_condition is not None:
                lines.append(
                    "    with wt.conditional("
                    f"left={str(current_condition.get('variable') or '')!r}, "
                    f"op={str(current_condition.get('op') or '==')!r}, "
                    f"right={str(current_condition.get('value') or '')!r}, "
                    f"name={group!r}):"
                )
        if needs_head:
            lines.append("    head = wt.mca96")
            head_declared = True
        indent = "        " if current_condition is not None else "    "
        rendered = _render_python_step(step, labware_vars)
        if rendered:
            lines.append(f"{indent}{_step_trace_comment(step)}")
        lines.extend(f"{indent}{line}" for line in rendered)

    lines.extend(["", "    return wt", "", "", 'if __name__ == "__main__":', "    wt = build_worktable()", "    wt.simulate()", f"    out = wt.compile({(str(protocol.get('name') or 'protocol').replace(' ', '_') + '.xscr')!r})", "    print(f'Wrote {out}')", ""])
    return "\n".join(lines)


def _declared_variables_for_render(ir: dict[str, Any]) -> list[tuple[str, Any, str, str]]:
    """Collect FluentControl variables that must exist before generated commands use them."""
    variables: dict[str, tuple[Any, str, str]] = {}

    def add(record: Any) -> None:
        if not isinstance(record, dict):
            return
        name = str(record.get("name") or "").strip()
        if not name or not _valid_variable_name(name) or name in variables:
            return
        scope = str(record.get("scope") or record.get("variable_scope") or "Script").strip() or "Script"
        variables[name] = (_variable_default(record), scope, _variable_type(record))

    for record in ir.get("variables") or []:
        add(record)
    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    for script in source.get("selected_source_scripts") or []:
        if not isinstance(script, dict):
            continue
        for record in script.get("startup_variables") or []:
            add(record)

    referenced = _referenced_variable_names(ir)
    if referenced:
        return [
            (name, default, scope, type_name)
            for name, (default, scope, type_name) in variables.items()
            if name in referenced
        ]
    return [(name, default, scope, type_name) for name, (default, scope, type_name) in variables.items()]


def _step_trace_comment(step: dict[str, Any]) -> str:
    index = step.get("index") or 0
    try:
        index = int(index)
    except (TypeError, ValueError):
        index = 0
    trace_id = str(step.get("id") or (f"step_{index:03d}" if index else "step_unknown"))
    operation = str(step.get("operation") or "unknown")
    name = str(step.get("name") or operation.replace("_", " ").title())
    return f"# TRACE step_id={trace_id} ir=steps[{index}] operation={operation} name={name!r}"


def _variable_default(record: dict[str, Any]) -> Any:
    if isinstance(record.get("default_expression"), dict):
        return _expression_python_value(record["default_expression"], fallback=record.get("default_value", 0))
    for key in ("default_value", "value"):
        if _has_value(record.get(key)):
            return record[key]
    for key in ("values", "default_values"):
        values = record.get(key)
        if isinstance(values, list) and values:
            return _number_or_text(str(values[0]))
    return 0


def _variable_type(record: dict[str, Any]) -> str:
    raw = str(record.get("type") or record.get("type_name") or record.get("variable_type") or "").strip()
    if not raw:
        return ""
    folded = raw.casefold()
    if folded in {"system.string", "string"}:
        return "String"
    if folded in {"system.int32", "system.integer", "integer", "int", "int32"}:
        return "Integer"
    if folded in {"system.double", "system.single", "double", "single", "float", "floating point"}:
        return "Floating Point"
    return raw


def _expression_imports_for_render(steps: list[dict[str, Any]], variables: list[Any]) -> set[str]:
    imports: set[str] = set()
    for record in walk_expression_values({"variables": variables, "steps": steps}):
        _collect_expression_imports(record.expression, imports)
    return imports


def _collect_expression_imports(expression: Any, imports: set[str]) -> None:
    if not isinstance(expression, dict):
        return
    kind = str(expression.get("kind") or "")
    if kind in {"string_literal", "number_literal", "boolean_literal"}:
        return
    if kind == "source_preserved_expression":
        imports.add("SourcePreservedExpression")
        return
    if kind == "reviewed_raw_expression":
        imports.add("ReviewedRawExpression")
        return
    if kind:
        imports.add("parse_expression")


def _expression_python_value(expression: Any, *, fallback: Any = None) -> Any:
    if not isinstance(expression, dict):
        return fallback
    kind = str(expression.get("kind") or "")
    if kind in {"string_literal", "number_literal", "boolean_literal"}:
        return expression.get("value")
    return _expression_source_text(expression, fallback=fallback)


def _expression_python_arg(expression: Any, *, fallback: Any = None) -> str:
    if not isinstance(expression, dict):
        return repr(fallback)
    kind = str(expression.get("kind") or "")
    if kind in {"string_literal", "number_literal", "boolean_literal"}:
        return repr(expression.get("value"))
    if kind == "source_preserved_expression":
        return (
            "SourcePreservedExpression("
            f"source={str(expression.get('source') or '')!r}, "
            f"source_hash={str(expression.get('source_hash') or '')!r}, "
            f"source_entry={str(expression.get('source_entry') or '')!r}, "
            f"provenance_id={str(expression.get('provenance_id') or '')!r}, "
            f"byte_stable={bool(expression.get('byte_stable', True))!r}, "
            f"reference_metadata_origin={str(expression.get('reference_metadata_origin') or '')!r}, "
            f"referenced_variables={tuple(expression.get('referenced_variables') or ())!r}, "
            f"referenced_functions={tuple(expression.get('referenced_functions') or ())!r})"
        )
    if kind == "reviewed_raw_expression":
        return (
            "ReviewedRawExpression("
            f"source={str(expression.get('source') or '')!r}, "
            f"approval_id={str(expression.get('approval_id') or '')!r}, "
            f"reviewer={str(expression.get('reviewer') or '')!r}, "
            f"reference_metadata_origin={str(expression.get('reference_metadata_origin') or '')!r}, "
            f"referenced_variables={tuple(expression.get('referenced_variables') or ())!r}, "
            f"referenced_functions={tuple(expression.get('referenced_functions') or ())!r})"
        )
    return f"parse_expression({_expression_source_text(expression, fallback=fallback)!r})"


def _expression_python_ast_arg(expression: Any, *, fallback: Any = None) -> str:
    if not isinstance(expression, dict):
        return f"parse_expression({str(fallback if fallback is not None else '')!r})"
    kind = str(expression.get("kind") or "")
    if kind in {"source_preserved_expression", "reviewed_raw_expression"}:
        return _expression_python_arg(expression, fallback=fallback)
    return f"parse_expression({_expression_source_text(expression, fallback=fallback)!r})"


def _expression_source_text(expression: Any, *, fallback: Any = None) -> str:
    if not isinstance(expression, dict):
        return str(fallback if fallback is not None else "")
    try:
        from fluentcoder.expressions import expression_from_mapping, render_expression

        return render_expression(expression_from_mapping(expression))
    except Exception:
        if "source" in expression:
            return str(expression.get("source") or "")
        return str(fallback if fallback is not None else "")


def _referenced_variable_names(ir: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    for record in walk_expression_values(ir):
        refs.update(_expression_variable_names_from_mapping(record.expression))
    category_conditions = ir.get("category_conditions")
    if isinstance(category_conditions, dict):
        for condition in category_conditions.values():
            if isinstance(condition, dict):
                _add_explicit_variable_name(refs, condition.get("variable"))
    for item in ir.get("labware") or []:
        if isinstance(item, dict):
            refs.update(_bracket_variable_names(item.get("label")))
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation = str(step.get("operation") or "")
        refs.update(_bracket_variable_names(step.get("target_labware")))
        params = step.get("parameters")
        if isinstance(params, dict):
            for key, value in params.items():
                if str(key) in _PROSE_PARAMETER_KEYS:
                    continue
                if str(key).endswith("_expression") or str(key).endswith("_expressions"):
                    continue
                if isinstance(value, (str, int, float)):
                    refs.update(_bracket_variable_names(value))
            if operation in {"query_variable", "set_variable"}:
                _add_explicit_variable_name(refs, params.get("variable"))
            elif operation == "execute_application":
                _add_explicit_variable_name(refs, params.get("variable"))
            elif operation == "runtime_variable_prompt":
                for item in params.get("variables") or []:
                    if isinstance(item, dict):
                        _add_explicit_variable_name(refs, item.get("name"))
            elif operation == "call_subroutine":
                for section in ("variable_mappings_start", "variable_mappings_end"):
                    for mapping in params.get(section) or []:
                        if isinstance(mapping, dict):
                            if "source_expression" not in mapping:
                                _add_explicit_variable_name(refs, mapping.get("source"))
            elif operation in {"conditional_branch", "default_branch"}:
                refs.update(_expression_variable_names(params.get("condition")))
    return refs


def _expression_variable_names(value: Any) -> set[str]:
    expression = re.sub(r'"[^"]*"|\'[^\']*\'', " ", str(value or ""))
    reserved = {"and", "or", "not", "true", "false", "none"}
    return {
        name
        for name in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression)
        if name.casefold() not in reserved and _valid_variable_name(name)
    }


def _expression_variable_names_from_mapping(expression: Any) -> set[str]:
    if not isinstance(expression, dict):
        return set()
    kind = str(expression.get("kind") or "")
    if kind == "variable_reference":
        name = str(expression.get("name") or "")
        return {name} if _valid_variable_name(name) else _expression_variable_names(name)
    if kind == "function_call":
        refs: set[str] = set()
        for arg in expression.get("arguments") or []:
            refs.update(_expression_variable_names_from_mapping(arg))
        return refs
    if kind == "unary_expression":
        return _expression_variable_names_from_mapping(expression.get("operand"))
    if kind == "binary_expression":
        refs = _expression_variable_names_from_mapping(expression.get("left"))
        refs.update(_expression_variable_names_from_mapping(expression.get("right")))
        return refs
    if kind in {"source_preserved_expression", "reviewed_raw_expression"}:
        return _opaque_expression_variable_names(expression)
    return set()


def _opaque_expression_variable_names(expression: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    values = expression.get("referenced_variables")
    if not isinstance(values, list):
        return refs
    for value in values:
        name = str(value or "").strip()
        if _valid_variable_name(name):
            refs.add(name)
    return refs


def _bracket_variable_names(value: Any) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(r"\[([A-Za-z_][A-Za-z0-9_]*)\]", str(value or ""))
    }


def _add_explicit_variable_name(refs: set[str], value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return
    if text.casefold() in {"true", "false", "none"}:
        return
    if _valid_variable_name(text):
        refs.add(text)


def _prompt_timeout(value: Any) -> int:
    number = _number_or_text(str(value if value is not None else ""))
    if not isinstance(number, (int, float)):
        return 0
    return max(0, min(7200, int(number)))


def _valid_variable_name(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value.strip()) is not None


def _referenced_labware_labels(steps: list[dict[str, Any]]) -> set[str]:
    labels: set[str] = set()
    for step in steps:
        if not isinstance(step, dict):
            continue
        operation = str(step.get("operation") or "")
        if operation not in LABWARE_TARGET_OPERATIONS:
            continue
        for key in ("target_labware", "source_labware", "destination_labware"):
            value = step.get(key)
            if value not in (None, "", []):
                labels.add(str(value))
        params = step.get("parameters")
        if isinstance(params, dict):
            for key in ("labware", "onto_labware", "onto"):
                value = params.get(key)
                if value not in (None, "", []):
                    labels.add(str(value))
    return labels


def render_gwl(ir: dict[str, Any]) -> str:
    """Render simple aspirate/dispense steps as a Gemini WorkList draft."""
    labware = list(ir.get("labware") or [])
    worklist = Worklist(name=str(ir.get("protocol", {}).get("name") or ir.get("id") or "worklist"))
    pending_aspirate: dict[str, Any] | None = None
    for step in ir.get("steps") or []:
        op = step.get("operation")
        if op == "aspirate":
            pending_aspirate = step
        elif op == "dispense" and pending_aspirate is not None:
            source_label = str(pending_aspirate.get("target_labware") or pending_aspirate.get("source_labware") or "")
            dest_label = str(step.get("target_labware") or step.get("destination_labware") or "")
            source_type = str(_labware_by_label(labware, source_label).get("catalog") or "")
            dest_type = str(_labware_by_label(labware, dest_label).get("catalog") or "")
            volume = step.get("volume_ul") or pending_aspirate.get("volume_ul") or ""
            liquid_class = step.get("liquid_class") or pending_aspirate.get("liquid_class") or ""
            worklist.add(
                Pipette(
                    operation="A",
                    rack_label=source_label,
                    rack_type=source_type,
                    position=1,
                    volume=volume,
                    liquid_class=liquid_class,
                )
            )
            worklist.add(
                Pipette(
                    operation="D",
                    rack_label=dest_label,
                    rack_type=dest_type,
                    position=1,
                    volume=volume,
                    liquid_class=liquid_class,
                )
            )
            pending_aspirate = None
    return serialize_gwl(worklist.records) if worklist.records else ""


def render_recreate_markdown(ir: dict[str, Any], *, generated_files: dict[str, str] | None = None) -> str:
    files = generated_files or {}
    protocol = ir.get("protocol", {})
    protocol_name = protocol.get("name") or "Generated protocol"
    script_filename = protocol_filename(ir, ".xscr")
    worktable = ir.get("worktable") or {}
    labware = list(ir.get("labware") or [])
    host_config = _host_config_report(ir)
    manual_steps = _manual_fluentcontrol_steps(ir, script_filename)
    lines = [
        f"# Recreate Script: {protocol_name}",
        "",
        "This guide is generated from the same canonical protocol IR as the generated script.",
        "",
        f"- Source of truth: `{files.get('ir', 'protocol.ir.json')}`",
        f"- Script name: `{protocol_name}`",
    ]
    if files.get("request_spec"):
        lines.append(f"- Request spec / prompt: `{files['request_spec']}`")
    if files.get("python"):
        lines.append(f"- Python draft: `{files['python']}`")
    if files.get("xscr"):
        lines.append(f"- Direct import file: `{files['xscr']}`")
    if files.get("traceability_report"):
        lines.append(f"- Traceability report: `{files['traceability_report']}`")
    if files.get("traceability_map"):
        lines.append(f"- Traceability map: `{files['traceability_map']}`")
    if files.get("zeia"):
        lines.append(f"- One-file project import: `{files['zeia']}`")
    if files.get("gwl"):
        lines.append(f"- Generated worklist: `{files['gwl']}`")

    source = ir.get("source") or {}
    original_request = _source_verbatim_prompt(source, protocol)
    lines.extend(["", "## Original Request", ""])
    if original_request:
        lines.append("This bundle was generated from the following user request:")
        lines.append("")
        opening_fence, closing_fence = _markdown_fences_for(original_request)
        lines.extend([opening_fence, original_request, closing_fence])
    else:
        lines.append("No original request prompt was recorded for this bundle.")

    lines.extend(["", "## Manual FluentControl Steps", ""])
    for index, step in enumerate(manual_steps, start=1):
        lines.append(f"{index}. {step['text']}")
        for detail in step.get("details", []):
            lines.append(f"   - {detail}")

    if host_config:
        lines.extend(["", "## Host Instrument Configuration", ""])
        lines.append(f"- Status: `{host_config.get('status') or 'unknown'}`")
        expected = host_config.get("expected") or {}
        if expected.get("exact_names"):
            lines.append(f"- Expected exact names: `{', '.join(expected['exact_names'])}`")
        if expected.get("patterns"):
            lines.append(f"- Expected name patterns: `{', '.join(expected['patterns'])}`")
        if host_config.get("installed_configs"):
            lines.append(f"- Installed configs detected: `{', '.join(host_config['installed_configs'])}`")
        if host_config.get("matches"):
            lines.append(f"- Matching configs: `{', '.join(host_config['matches'])}`")
        lines.append(f"- User action: {host_config.get('user_instruction') or _host_config_instruction(expected)}")

    lines.extend(["", "## Worktable Used", ""])
    for label, key in (("Base worktable", "name"), ("Worktable GUID", "guid"), ("Auto-place labware", "auto_place")):
        if _has_value(worktable.get(key)):
            lines.append(f"- {label}: `{worktable[key]}`")

    lines.extend(["", "## Chosen Items", ""])
    for item in labware:
        lines.append(f"- `{item.get('label')}`")
        for label, key in (
            ("Catalog / FluentControl type", "catalog"),
            ("Python class", "python_class"),
            ("Deck location", "deck_location"),
            ("Role", "role"),
        ):
            if _has_value(item.get(key)):
                lines.append(f"  - {label}: `{item[key]}`")
    for item in ir.get("liquid_classes") or []:
        lines.append(f"- `{item.get('name')}`")
        lines.append("  - Item kind: `liquid_class`")
    for item in ir.get("reagents") or []:
        lines.append(f"- `{item.get('name')}`")
        lines.append("  - Item kind: `reagent`")
    for item in ir.get("variables") or []:
        lines.append(f"- `{item.get('name')}`")
        lines.append("  - Item kind: `variable`")
        for label, key in (
            ("Type", "type"),
            ("Scope", "scope"),
            ("Default value", "default_value"),
            ("Query at startup", "query_at_startup"),
            ("Prompt", "query_prompt"),
        ):
            if _has_value(item.get(key)):
                lines.append(f"  - {label}: `{item[key]}`")

    lines.extend(["", "## IR Command Reference", ""])
    for step in ir.get("steps") or []:
        index = step.get("index")
        name = step.get("name") or str(step.get("operation", "")).replace("_", " ").title()
        lines.append(f"{index}. {name}")
        lines.append(f"   - Command name: `{name}`")
        if step.get("command_id"):
            lines.append(f"   - FluentControl command ID: `{step['command_id']}`")
        lines.append("   - Specifications:")
        for spec in _step_specs(step):
            lines.append(f"     - {spec}")
        lines.append("   - Path to find it:")
        source_path = step.get("source_path") or f"canonical IR -> steps[{index}]"
        lines.append(f"     - Canonical IR: `steps[{index}]`")
        lines.append(f"     - Source: `{source_path}`")

    lines.extend(
        [
            "",
            "## Safety Assumptions",
            "",
        ]
    )
    for assumption in ir.get("safety_assumptions") or []:
        text = assumption.get("text") if isinstance(assumption, dict) else str(assumption)
        lines.append(f"- {text}")
    lines.extend(["", "This folder is a handoff package, not an approval signal.", ""])
    return "\n".join(lines)


def _source_verbatim_prompt(source: dict[str, Any], protocol: dict[str, Any]) -> str:
    request_spec = source.get("request_spec") if isinstance(source.get("request_spec"), dict) else {}
    for container in (source, request_spec):
        for key in ("verbatim_prompt", "original_user_prompt", "source_prompt"):
            if isinstance(container, dict) and container.get(key) is not None:
                return str(container.get(key))
    return str(
        source.get("intent")
        or source.get("generation_intent")
        or (protocol.get("comment") if isinstance(protocol, dict) else "")
        or ""
    ).strip()


def _markdown_fences_for(text: str) -> tuple[str, str]:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text", fence


def is_ir_bundle(payload: dict[str, Any]) -> bool:
    return payload.get("ir_version") == CANONICAL_IR_BUNDLE_VERSION


def protocol_filename(ir: dict[str, Any], suffix: str) -> str:
    name = str(ir.get("protocol", {}).get("name") or ir.get("id") or "protocol")
    return f"{_safe_filename(_safe_id(name))}{suffix}"


def _empty_ir(name: str, *, source_format: str, source_path: str) -> dict[str, Any]:
    return {
        "ir_version": CANONICAL_IR_VERSION,
        "id": _safe_id(name),
        "protocol": {
            "name": name,
            "comment": "",
            "schema_intent": "canonical source of truth for generated Tecan artifacts",
        },
        "source": {"format": source_format, "path": source_path},
        "worktable": {"name": "", "guid": "", "auto_place": False},
        "labware": [],
        "reagents": [],
        "liquid_classes": [],
        "variables": [],
        "worklists": [],
        "dependencies": [],
        "safety_assumptions": [
            {
                "id": "manual_validation_required",
                "text": "Generated artifacts must be reviewed, simulated, and validated in FluentControl before instrument use.",
            }
        ],
        "steps": [],
    }


def _finalize_ir(ir: dict[str, Any]) -> None:
    for index, step in enumerate(ir.get("steps") or [], start=1):
        step["index"] = index
        step.setdefault("id", f"step_{index:03d}")
    normalize_setup_groups(ir)
    _infer_transfer_roles(ir)
    upsert_ir_subroutine_dependencies(ir)
    for item in ir.get("labware") or []:
        if item.get("location") and item.get("position") and not item.get("deck_location"):
            item["deck_location"] = f"{item['location']} {item['position']}"
        if item.get("catalog"):
            _add_dependency(ir, kind="labware", name=item["catalog"], required=True)
    for item in ir.get("liquid_classes") or []:
        if item.get("name"):
            _add_dependency(ir, kind="liquid_class", name=item["name"], guid=item.get("guid", ""), required=True)
    normalized = normalize_setup_groups(migrate_protocol_ir(ir))
    ir.clear()
    ir.update(normalized)


def _infer_transfer_roles(ir: dict[str, Any]) -> None:
    by_label = {item.get("label"): item for item in ir.get("labware") or []}
    for step in ir.get("steps") or []:
        if step.get("operation") == "aspirate" and step.get("target_labware") in by_label:
            by_label[step["target_labware"]].setdefault("role", "source")
        elif step.get("operation") == "dispense" and step.get("target_labware") in by_label:
            by_label[step["target_labware"]].setdefault("role", "destination")
        elif step.get("operation") in {"pick_up_tips", "return_tips"} and step.get("target_labware") in by_label:
            by_label[step["target_labware"]].setdefault("role", "tips")


def _xscr_variable_declarations(root: ET.Element, source_label: str) -> list[dict[str, Any]]:
    variables: list[dict[str, Any]] = []
    for element in root.iter():
        local = _local_name(element.tag)
        if local == "anyType":
            type_hint = " ".join(str(value) for value in element.attrib.values())
            if "VariableDefinitionHelper" not in type_hint:
                continue
        elif local != "VariableDefinitionHelper":
            continue
        name = _first_text(element, "Name")
        if not name:
            continue
        values = _xscr_variable_values(element)
        variable = {
            "name": name,
            "type": _first_text(element, "TypeName"),
            "scope": _first_text(element, "Scope"),
            "query_at_startup": _bool_text(_first_text(element, "QueryOnStartup")),
            "query_prompt": _first_text(element, "QueryOnStartupString"),
            "read_only": _bool_text(_first_text(element, "ReadOnly")),
            "values": values,
            "default_value": values[0] if values else None,
            "source_path": f"{source_label} -> VariableDeclarations -> {name}",
        }
        variables.append({key: value for key, value in variable.items() if _has_value(value)})
    return variables


def _xscr_variable_values(element: ET.Element) -> list[Any]:
    values = _first_descendant(element, "Values")
    if values is None:
        return []
    out: list[Any] = []
    for child in list(values):
        value = _text(child)
        if value not in (None, "", []):
            out.append(_number_or_text(str(value)))
    return out


def _record_xscr_variable_step(ir: dict[str, Any], step: dict[str, Any]) -> None:
    operation = str(step.get("operation") or "")
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    variable_name = str(params.get("variable") or "").strip()

    if operation in {"set_variable", "set_remaining_runtime"} and variable_name:
        variable = _ensure_variable(ir, variable_name)
        if operation == "set_remaining_runtime":
            variable.setdefault("kind", "internal")
            variable.setdefault("type", "duration")
            variable.setdefault("scope", "Runtime")
        assignment = {
            "operation": operation,
            "value": params.get("value"),
            "line": params.get("line_number"),
            "step_id": step.get("id"),
        }
        if isinstance(params.get("value_expression"), dict):
            assignment["value_expression"] = copy.deepcopy(
                params["value_expression"]
            )
        _append_unique_record(variable, "assignments", assignment)
        return

    if operation == "query_variable" and variable_name:
        variable = _ensure_variable(ir, variable_name)
        variable.setdefault("query_prompt", params.get("prompt") or "")
        if _has_value(params.get("minimum")):
            variable.setdefault("minimum", params.get("minimum"))
        if _has_value(params.get("maximum")):
            variable.setdefault("maximum", params.get("maximum"))
        _append_unique_record(
            variable,
            "queries",
            {
                "operation": operation,
                "prompt": params.get("prompt"),
                "line": params.get("line_number"),
                "step_id": step.get("id"),
            },
        )
        return

    if operation == "runtime_variable_prompt":
        for item in params.get("variables") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            variable = _ensure_variable(ir, name)
            for target_key, source_key in (
                ("type", "type"),
                ("query_prompt", "display_text"),
                ("display_type", "display_type"),
                ("default_value", "value"),
                ("allowed_values", "allowed_values"),
                ("minimum", "minimum"),
                ("maximum", "maximum"),
                ("enabled", "enabled"),
            ):
                value = item.get(source_key)
                if _has_value(value):
                    variable.setdefault(target_key, value)
            _append_unique_record(
                variable,
                "queries",
                {
                    "operation": operation,
                    "screen_title": params.get("screen_title"),
                    "prompt": item.get("display_text"),
                    "line": params.get("line_number"),
                    "step_id": step.get("id"),
                },
            )


def _ensure_variable(ir: dict[str, Any], name: str) -> dict[str, Any]:
    for variable in ir.setdefault("variables", []):
        if variable.get("name") == name:
            return variable
    variable = {"name": name}
    ir["variables"].append(variable)
    return variable


def _append_unique_record(item: dict[str, Any], key: str, record: dict[str, Any]) -> None:
    cleaned = {field: value for field, value in record.items() if _has_value(value)}
    if not cleaned:
        return
    records = item.setdefault(key, [])
    if cleaned not in records:
        records.append(cleaned)


def _append_step(
    ir: dict[str, Any],
    *,
    group: str,
    operation: str,
    name: str,
    command_id: str | None = None,
    target_labware: str | None = None,
    source_labware: str | None = None,
    destination_labware: str | None = None,
    volume_ul: float | int | str | None = None,
    liquid_class: str | None = None,
    parameters: dict[str, Any] | None = None,
    source_path: str = "",
    compiled_path: str = "",
) -> None:
    operation = normalize_operation(operation)
    payload = {
        "id": f"step_{len(ir['steps']) + 1:03d}",
        "index": len(ir["steps"]) + 1,
        "group": group,
        "operation": operation,
        "name": name,
        "command_id": command_id,
        "target_labware": target_labware,
        "source_labware": source_labware,
        "destination_labware": destination_labware,
        "volume_ul": volume_ul,
        "liquid_class": liquid_class,
        "parameters": parameters or {},
        "source_path": source_path,
        "compiled_path": compiled_path,
        "safety_flags": [],
    }
    ir["steps"].append({key: value for key, value in payload.items() if _has_value(value)})


def _python_protocol_metadata(call: ast.Call, *, fallback_name: str) -> dict[str, Any]:
    protocol_name = _keyword_value(call, "protocol_name") or fallback_name
    return {"name": protocol_name, "comment": _keyword_value(call, "comment") or ""}


def _python_worktable_metadata(call: ast.Call) -> dict[str, Any]:
    auto_place = _keyword_value(call, "auto_place")
    return {
        "name": _literal_text(call.args[0]) if call.args else "",
        "guid": _keyword_value(call, "workspace_guid") or "",
        "auto_place": True if auto_place is None else bool(auto_place),
    }


def _python_labware(call: ast.Call, draft_name: str, current_group: str, source: str) -> dict[str, Any] | None:
    if not call.args or not isinstance(call.args[0], ast.Call):
        return None
    labware_call = call.args[0]
    label = _literal_text(labware_call.args[0]) if labware_call.args else None
    if not label:
        return None
    catalog = _keyword_value(labware_call, "catalog") or ""
    location = _literal_text(call.args[1]) if len(call.args) > 1 else ""
    position = _literal_text(call.args[2]) if len(call.args) > 2 else None
    return {
        "id": _safe_id(str(label)),
        "label": str(label),
        "python_class": _call_name(labware_call),
        "catalog": catalog,
        "location": location,
        "position": position,
        "deck_location": f"{location} {position}" if location and position else "",
        "rotation": _keyword_value(call, "rotation") or 0,
        "has_lid": _keyword_value(call, "has_lid") or False,
        "role": _infer_labware_role(str(label), str(catalog)),
        "source_path": _source_path(draft_name, current_group, source, call),
    }


def _apply_python_fill_all(
    ir: dict[str, Any],
    call: ast.Call,
    labware_by_var: dict[str, str],
    reagent_by_var: dict[str, str],
) -> None:
    target = call.func.value
    if not isinstance(target, ast.Name):
        return
    label = labware_by_var.get(target.id)
    if not label:
        return
    reagent = _value_label(call.args[0], reagent_by_var, labware_by_var) if call.args else ""
    volume = _literal_text(call.args[1]) if len(call.args) > 1 else None
    for item in ir["labware"]:
        if item.get("label") == label:
            item["initial_contents"] = {"reagent": reagent, "volume_ul": volume, "scope": "all_wells"}


def _python_prompt_step(
    call: ast.Call,
    draft_name: str,
    current_group: str,
    source: str,
) -> dict[str, Any] | None:
    """Lift ``wt.user_prompt`` / ``wt.user_prompt_worktable`` into prompt_user IR."""
    if not isinstance(call.func, ast.Attribute):
        return None
    method = call.func.attr
    prompt = _keyword_value(call, "prompt")
    if prompt is None and call.args:
        prompt = _literal_text(call.args[0])
    prompt = "" if prompt is None else str(prompt)
    timeout = _keyword_value(call, "timeout")
    auto_close = _keyword_value(call, "auto_close")
    image_path = _keyword_value(call, "image_path") or ""
    screen_title = _keyword_value(call, "screen_title") or ""
    sound_path = _keyword_value(call, "sound_path") or _keyword_value(call, "sound_file") or ""
    parameters: dict[str, Any] = {
        "method": method,
        "prompt": prompt,
        "image_path": str(image_path or ""),
        "sound_file": str(sound_path or ""),
        "sound_path": str(sound_path or ""),
    }
    if timeout is not None:
        parameters["timeout"] = timeout
    if auto_close is not None:
        parameters["auto_close"] = bool(auto_close)
    if screen_title:
        parameters["screen_title"] = str(screen_title)
    if method == "user_prompt_worktable":
        worktable_labware = {
            "selected_labware_name": _keyword_value(call, "selected_labware_name") or "",
            "selected_labware_type": _keyword_value(call, "selected_labware_type") or "",
            "grid": _keyword_value(call, "grid"),
            "site": _keyword_value(call, "site"),
        }
        parameters["worktable_labware"] = {
            key: value for key, value in worktable_labware.items() if value not in (None, "")
        }
        command_id = "RUPWorktableStatement"
    else:
        command_id = "UserPromptStatement"
    return {
        "group": current_group,
        "operation": "prompt_user",
        "name": "Prompt User",
        "command_id": command_id,
        "parameters": parameters,
        "source_path": _source_path(draft_name, current_group, source, call),
    }


def _python_runtime_step(
    call: ast.Call,
    draft_name: str,
    current_group: str,
    source: str,
    reagent_by_var: dict[str, str],
    labware_by_var: dict[str, str],
) -> dict[str, Any] | None:
    if not isinstance(call.func, ast.Attribute):
        return None
    operation = _PYTHON_OPERATION_BY_METHOD.get(call.func.attr)
    if not operation:
        return None
    target = _value_label(call.args[0], reagent_by_var, labware_by_var) if call.args else None
    volume = _literal_text(call.args[1]) if len(call.args) > 1 else None
    liquid_class = _keyword_value(call, "liquid_class")
    return {
        "group": current_group,
        "operation": operation,
        "name": _operation_name(operation),
        "command_id": call.func.attr,
        "target_labware": target,
        "source_labware": target if operation == "aspirate" else None,
        "destination_labware": target if operation == "dispense" else None,
        "volume_ul": volume,
        "liquid_class": liquid_class,
        "parameters": {
            "method": call.func.attr,
            "target": target,
            "volume_ul": volume,
            "liquid_class": liquid_class,
        },
        "source_path": _source_path(draft_name, current_group, source, call),
    }


def _xscr_step(
    command_object: ET.Element,
    group_name: str,
    *,
    source_entry: str = "",
) -> dict[str, Any] | None:
    command_id = _command_id(command_object)
    operation = _operation_from_command_id(command_id)
    if command_id == "LegacyDriverMacro":
        operation = "application_driver_macro"
    macro_name = ""
    if command_id == "ApplicationDriverMacro":
        macro_name = _application_driver_macro_name(command_object)
        if macro_name == "RGA1_TransferLabware":
            operation = "move_plate"
        else:
            operation = "application_driver_macro"
    if not operation:
        return None
    line_number = _first_text(command_object, "LineNumber") or _application_driver_macro_attr(command_object, "LineNumber")
    compiled_path = f"Commands -> {group_name}"
    if line_number:
        compiled_path += f" -> Line {line_number}"
    compiled_path += f" -> {command_id}"

    if operation in {"conditional_branch", "default_branch"}:
        return _xscr_branch_step(
            command_object,
            group_name,
            operation,
            command_id,
            compiled_path,
            source_entry=source_entry,
        )

    if operation == "add_labware":
        location = _first_text(command_object, "Location") or ""
        position = _number_or_text(_first_text(command_object, "Position") or "")
        label = _first_text(command_object, "LabwareLable") or ""
        labware_type = _first_text(command_object, "LabwareType") or ""
        parameters = {
            "label": label,
            "labware_type": labware_type,
            "location": location,
            "position": position,
            "rotation": _number_or_text(_first_text(command_object, "Rotation") or "0"),
            "has_lid": _bool_text(_first_text(command_object, "HasLid")),
        }
        parameters.update(
            _registered_xscr_expression_parameters(
                command_object,
                command_id,
                source_entry=source_entry,
            )
        )
        return {
            "group": group_name,
            "operation": operation,
            "name": _operation_name(operation),
            "command_id": command_id,
            "target_labware": label,
            "parameters": parameters,
            "compiled_path": compiled_path,
        }

    fields = _command_field_map(command_object)
    if operation in {"query_variable", "set_variable", "runtime_variable_prompt"}:
        return _xscr_variable_step(
            command_object,
            group_name,
            operation,
            command_id,
            fields,
            compiled_path,
            source_entry=source_entry,
        )
    if operation == "call_subroutine":
        subroutine = (
            registry_field_value(command_id, "subroutine", fields)
            or _first_text(command_object, "SubRoutine")
            or ""
        )
        execution_mode = (
            registry_field_value(command_id, "execution_mode", fields)
            or _first_text(command_object, "ExecutionMode")
            or "Synchronous"
        )
        return {
            "group": group_name,
            "operation": operation,
            "name": _operation_name(operation),
            "command_id": command_id,
            "parameters": {
                "subroutine": str(subroutine).strip().strip('"'),
                "execution_mode": execution_mode,
                "variable_mappings_start": _xscr_variable_mapping_items(
                    command_object,
                    "VariableMappingsStart",
                    source_entry=source_entry,
                ),
                "variable_mappings_end": _xscr_variable_mapping_items(
                    command_object,
                    "VariableMappingsEnd",
                    source_entry=source_entry,
                ),
            },
            "compiled_path": compiled_path,
        }
    if operation == "execute_application":
        return _xscr_execute_application_step(command_object, group_name, command_id, fields, compiled_path)
    if operation == "execute_vb_script":
        return _xscr_execute_vb_script_step(command_object, group_name, command_id, fields, compiled_path)
    if operation == "move_plate" and macro_name == "RGA1_TransferLabware":
        return _xscr_rga_transfer_step(command_object, group_name, command_id, compiled_path)

    labware = registry_field_value(command_id, "labware", fields) or _first_text(command_object, "LabwareName")
    volume = _number_or_text(registry_field_value(command_id, "volume_ul", fields) or _first_text(command_object, "Volume") or "")
    liquid_class = (
        registry_field_value(command_id, "liquid_class", fields)
        or _first_nonempty_text(command_object, ["LiquidClassNameBySelection", "LiquidClassName"])
    )
    prompt = None
    if operation == "prompt_user":
        prompt = registry_field_value(command_id, "prompt", fields) or _first_nonempty_text(
            command_object,
            ["Prompt", "QueryPrompt", "Message"],
        )
        image_path = registry_field_value(command_id, "image_path", fields) or _first_text(
            command_object, "SelectedImagePath"
        )
        sound_file = registry_field_value(command_id, "sound_file", fields) or _first_nonempty_text(
            command_object,
            ["SelectedSoundPath", "SoundFile"],
        )
        repeat_sound = registry_field_value(command_id, "repeat_sound", fields) or _first_nonempty_text(
            command_object,
            ["RepeatValue", "RepeatSound"],
        )
        auto_close = _first_nonempty_text(command_object, ["AutoClose", "RUPAutoClose"])
    comment = None
    if operation == "comment":
        comment = registry_field_value(command_id, "comment", fields) or _first_nonempty_text(
            command_object,
            ["Comment", "Text"],
        )
    timeout = _number_or_text(
        str(registry_field_value(command_id, "timeout", fields) or _first_text(command_object, "Timeout") or "")
    )
    variable = registry_field_value(command_id, "variable", fields) or _first_nonempty_text(
        command_object,
        ["VariableName", "Name"],
    )
    parameters = {
        "labware": labware,
        "volume_ul": volume,
        "liquid_class": liquid_class,
        "device_alias": _first_text(command_object, "DeviceAlias"),
        "available_id": _first_text(command_object, "AvailableID"),
        "head_position": _first_text(command_object, "HeadPositions"),
        "back_position": _first_text(command_object, "Backs"),
    }
    parameters.update(
        _registered_xscr_expression_parameters(
            command_object,
            command_id,
            source_entry=source_entry,
        )
    )
    if operation == "prompt_user" and command_id == "RUPWorktableStatement":
        worktable_labware = {
            "selected_labware_name": str(labware or ""),
            "selected_labware_type": _first_text(command_object, "LabwareTypeName") or "",
            "grid": _number_or_text(_first_text(command_object, "Grid") or ""),
            "site": _number_or_text(_first_text(command_object, "Site") or ""),
        }
        parameters["worktable_labware"] = {
            key: value
            for key, value in worktable_labware.items()
            if value not in (None, "")
        }
    if prompt:
        parameters["prompt"] = prompt
        parameters["image_path"] = image_path or ""
        parameters["sound_file"] = sound_file or ""
        parameters["repeat_sound"] = repeat_sound or ""
        if auto_close is not None:
            parameters["auto_close"] = _bool_text(auto_close)
    if comment:
        parameters["comment"] = comment
    if timeout is not None:
        parameters["timeout"] = timeout
    if variable:
        parameters["variable"] = variable
    if _preserve_raw_xml_for_operation(operation):
        parameters["raw_xml"] = ET.tostring(command_object, encoding="unicode")
    return {
        "group": group_name,
        "operation": operation,
        "name": _operation_name(operation),
        "command_id": command_id,
        "target_labware": labware,
        "source_labware": labware if operation in {"aspirate", "liha_aspirate"} else None,
        "destination_labware": labware if operation in {"dispense", "liha_dispense"} else None,
        "volume_ul": volume,
        "liquid_class": liquid_class,
        "parameters": parameters,
        "compiled_path": compiled_path,
    }


def _xscr_branch_step(
    command_object: ET.Element,
    group_name: str,
    operation: str,
    command_id: str,
    compiled_path: str,
    *,
    source_entry: str = "",
) -> dict[str, Any]:
    payload = _direct_child(command_object, command_id)
    condition = _direct_text(payload, "Condition") or _first_text(command_object, "Condition")
    branch_name = _direct_text(payload, "Name") or ""
    is_disabled = _bool_text(_direct_text(payload, "IsDisabledForExecution"))
    parameters = {
        "condition": condition,
        "branch_name": branch_name,
        "is_default_branch": operation == "default_branch",
        "is_disabled_for_execution": is_disabled,
        "line_number": _number_or_text(_direct_text(payload, "LineNumber") or ""),
        "raw_xml": ET.tostring(command_object, encoding="unicode"),
    }
    parameters.update(
        _registered_xscr_expression_parameters(
            command_object,
            command_id,
            source_entry=source_entry,
        )
    )
    # A ConditionalGroup can contain nested ConditionalGroups.  The generic
    # registered-field reader intentionally walks descendants for compound
    # fields, but that would select a child ``Condition`` here and make the
    # authoritative expression disagree with this branch's own legacy
    # ``condition`` field.  Use the direct payload condition captured above.
    parameters.pop("condition_expression", None)
    if condition:
        condition_expression = expression_to_mapping(
            parse_or_preserve_source_expression(condition)
        )
        if (
            source_entry
            and condition_expression.get("kind") == "source_preserved_expression"
            and not condition_expression.get("source_entry")
        ):
            condition_expression["source_entry"] = source_entry
        parameters["condition_expression"] = condition_expression
    return {
        "group": group_name,
        "operation": operation,
        "name": branch_name or _operation_name(operation),
        "command_id": command_id,
        "parameters": parameters,
        "compiled_path": compiled_path,
    }


def _xscr_execute_application_step(
    command_object: ET.Element,
    group_name: str,
    command_id: str,
    fields: dict[str, Any],
    compiled_path: str,
) -> dict[str, Any]:
    path = registry_field_value(command_id, "path", fields) or _first_text(command_object, "Application") or ""
    arguments = registry_field_value(command_id, "arguments", fields) or _first_text(command_object, "Arguments") or ""
    wait = registry_field_value(command_id, "wait", fields)
    store_return = registry_field_value(command_id, "store_return", fields)
    variable = registry_field_value(command_id, "variable", fields) or _first_text(command_object, "Variable") or ""
    parameters = {
        "path": str(path).strip().strip('"'),
        "arguments": str(arguments),
        "wait": _bool_text(str(wait)) if wait not in (None, "") else _bool_text(_first_text(command_object, "Wait")),
        "store_return": (
            _bool_text(str(store_return))
            if store_return not in (None, "")
            else _bool_text(_first_text(command_object, "StoreReturn"))
        ),
        "variable": variable,
        "raw_xml": ET.tostring(command_object, encoding="unicode"),
    }
    return {
        "group": group_name,
        "operation": "execute_application",
        "name": _operation_name("execute_application"),
        "command_id": command_id,
        "parameters": parameters,
        "compiled_path": compiled_path,
    }


def _xscr_execute_vb_script_step(
    command_object: ET.Element,
    group_name: str,
    command_id: str,
    fields: dict[str, Any],
    compiled_path: str,
) -> dict[str, Any]:
    vb_script = registry_field_value(command_id, "vb_script", fields) or _first_text(command_object, "VbScript") or ""
    parameters = {
        "vb_script": str(vb_script).strip().strip('"'),
        "execution_mode": registry_field_value(command_id, "execution_mode", fields) or "Synchronous",
        "raw_xml": ET.tostring(command_object, encoding="unicode"),
    }
    return {
        "group": group_name,
        "operation": "execute_vb_script",
        "name": _operation_name("execute_vb_script"),
        "command_id": command_id,
        "parameters": parameters,
        "compiled_path": compiled_path,
    }


def _xscr_variable_step(
    command_object: ET.Element,
    group_name: str,
    operation: str,
    command_id: str,
    fields: dict[str, Any],
    compiled_path: str,
    *,
    source_entry: str = "",
) -> dict[str, Any]:
    if operation == "runtime_variable_prompt":
        params = {
            "screen_title": _first_text(command_object, "RUPScreenTitle"),
            "display_and_wait": _bool_text(_first_text(command_object, "RUPDisplayAndWait")),
            "auto_close": _bool_text(_first_text(command_object, "RUPAutoClose")),
            "timeout": _number_or_text(_first_text(command_object, "RUPTimeOut") or ""),
            "line_number": _number_or_text(_first_text(command_object, "LineNumber") or ""),
            "variables": _rup_variable_items(command_object),
            "raw_xml": ET.tostring(command_object, encoding="unicode"),
        }
        return {
            "group": group_name,
            "operation": operation,
            "name": _operation_name(operation),
            "command_id": command_id,
            "parameters": params,
            "compiled_path": compiled_path,
        }

    variable = registry_field_value(command_id, "variable", fields) or _first_nonempty_text(
        command_object,
        ["VariableName", "Name"],
    )
    value = registry_field_value(command_id, "value", fields) or _first_text(command_object, "Value")
    prompt = registry_field_value(command_id, "prompt", fields) or _first_nonempty_text(
        command_object,
        ["QueryPrompt", "Prompt", "Message", "Comment"],
    )
    minimum = registry_field_value(command_id, "minimum", fields) or _first_text(command_object, "MinimumText")
    maximum = registry_field_value(command_id, "maximum", fields) or _first_text(command_object, "MaximumText")
    timeout = registry_field_value(command_id, "timeout", fields) or _first_text(command_object, "Timeout")
    line_number = _number_or_text(_first_text(command_object, "LineNumber") or "")
    if operation == "set_variable" and str(variable).strip().casefold() == "remainingruntime":
        operation = "set_remaining_runtime"

    params = {
        "variable": variable,
        "value": value,
        "prompt": prompt,
        "minimum": minimum,
        "maximum": maximum,
        "timeout": _number_or_text(str(timeout or "")),
        "line_number": line_number,
        "raw_xml": ET.tostring(command_object, encoding="unicode"),
    }
    params.update(
        _registered_xscr_expression_parameters(
            command_object,
            command_id,
            source_entry=source_entry,
        )
    )
    return {
        "group": group_name,
        "operation": operation,
        "name": _operation_name(operation),
        "command_id": command_id,
        "parameters": {key: val for key, val in params.items() if _has_value(val)},
        "compiled_path": compiled_path,
    }


def _xscr_rga_transfer_step(
    command_object: ET.Element,
    group_name: str,
    command_id: str,
    compiled_path: str,
) -> dict[str, Any]:
    params = _rga_transfer_parameters(command_object)
    params["raw_xml"] = ET.tostring(command_object, encoding="unicode")
    labware = str(params.get("labware") or "")
    return {
        "group": group_name,
        "operation": "move_plate",
        "name": _operation_name("move_plate"),
        "command_id": command_id,
        "target_labware": labware,
        "parameters": params,
        "compiled_path": compiled_path,
    }


def _rga_transfer_parameters(command_object: ET.Element) -> dict[str, Any]:
    inner_text = _first_text(command_object, "ExecutionSettings") or ""
    params: dict[str, Any] = {}
    if not inner_text:
        return params
    try:
        inner = ET.fromstring(html.unescape(inner_text))
    except ET.ParseError:
        return params
    for element in inner.iter():
        if not isinstance(element.tag, str) or element.text is None:
            continue
        key = _local_name(element.tag)
        value = element.text.strip()
        if not value:
            continue
        if key == "Labware":
            params["labware"] = value
        elif key == "Location":
            params["destination_location"] = value
        elif key == "Site":
            params["destination_site"] = _number_or_text(value)
        elif key == "MoveToBase":
            params["move_to_base"] = _bool_text(value)
        elif key == "FixedSite":
            params["fixed_site"] = _bool_text(value)
    location = str(params.get("destination_location") or "")
    location_match = re.fullmatch(r'GetCoverSiteName\("([^"]+)"\)', location)
    raw_site = _first_nested_text(inner, "Site")
    site_match = re.fullmatch(r'GetCoverSiteIndex\("([^"]+)"\)', str(raw_site or ""))
    # Onto-moves compile as GetCoverSiteName(...). Site may be GetCoverSiteIndex with
    # the same labware, or a literal 1 after fluentcoder/FC rewrite — both mean onto.
    if location_match:
        cover_labware = location_match.group(1)
        if site_match and site_match.group(1) != cover_labware:
            return params
        params["onto_labware"] = cover_labware
        params.pop("destination_location", None)
        params.pop("destination_site", None)
    return params


def _first_nested_text(element: ET.Element, name: str) -> str:
    for child in element.iter():
        if _local_name(child.tag) == name:
            return str(child.text or "").strip()
    return ""


def _rup_variable_items(command_object: ET.Element) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _iter_local(command_object, "RupVariableItem"):
        allowed_values_raw = _direct_text(item, "AllowedValues") or ""
        variable = {
            "name": _direct_text(item, "VariableName"),
            "type": _direct_text(item, "VariableType"),
            "display_text": _direct_text(item, "DisplayText"),
            "display_type": _direct_text(item, "DisplayType"),
            "value": _direct_text(item, "VariableValue"),
            "allowed_values": _split_allowed_values(allowed_values_raw),
            "allowed_values_raw": allowed_values_raw,
            "disabled_when_value": _direct_text(item, "UnresolvedDisabledWhenValue"),
            "enabled": _bool_text(_direct_text(item, "IsEnabled")),
            "minimum": _number_or_text(_direct_text(item, "MinValue") or ""),
            "maximum": _number_or_text(_direct_text(item, "MaxValue") or ""),
            "uses_min_max": _bool_text(_direct_text(item, "IsMinMaxValueUsed")),
        }
        items.append({key: value for key, value in variable.items() if _has_value(value)})
    return items


def _split_allowed_values(value: str) -> list[str]:
    if not value:
        return []
    return [item.strip().strip('"') for item in value.split(";") if item.strip()]


def _render_python_step(step: dict[str, Any], labware_vars: dict[str, str]) -> list[str]:
    operation = step.get("operation")
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    target = str(step.get("target_labware") or "")
    target_expr = labware_vars.get(target, repr(target))
    volume = step.get("volume_ul")
    volume_expression = step.get("volume_ul_expression") or params.get("volume_ul_expression")
    volume_arg = _expression_python_arg(volume_expression, fallback=volume)
    liquid_class = step.get("liquid_class")
    if operation == "get_head_adapter":
        return ["head.mount_adapter()"]
    if operation == "drop_head_adapter":
        return ["head.drop_adapter()"]
    if operation in {"pick_up_tips", "mca384_get_tips"}:
        return [f"head.pick_up({target_expr})"]
    if operation in {"set_tips_back", "mca384_drop_tips"}:
        return [f"head.return_tips({target_expr})"]
    if operation == "aspirate":
        return [f"head.aspirate({target_expr}, {volume_arg}, liquid_class={liquid_class!r})"]
    if operation == "dispense":
        return [f"head.dispense({target_expr}, {volume_arg}, liquid_class={liquid_class!r})"]
    if operation == "mca384_mix":
        cycles = params.get("cycles", 10)
        cycles_arg = _expression_python_arg(params.get("cycles_expression"), fallback=cycles)
        return [f"head.mix({target_expr}, {volume_arg}, cycles={cycles_arg}, liquid_class={liquid_class!r})"]
    if operation == "comment":
        comment = (step.get("parameters") or {}).get("comment") or step.get("comment") or ""
        return [f"wt.add_comment({str(comment)!r})"]
    if operation == "prompt_user":
        params = step.get("parameters") or {}
        prompt = params.get("prompt") or step.get("prompt") or ""
        timeout = _prompt_timeout(params.get("timeout") if params.get("timeout") is not None else step.get("timeout"))
        image_path = prompt_step_worktable_media_path(params) or prompt_step_media_path(params) or str(
            params.get("image_path") or ""
        )
        screen_title = str(params.get("screen_title") or "")
        sound_path = str(params.get("sound_path") or params.get("sound_file") or "")
        if prompt_step_is_deck_presence_check(params) or str(step.get("command_id") or "") == "RUPWorktableStatement":
            binding = prompt_step_worktable_binding(params) or {}
            prompt_args = [f"prompt={str(prompt)!r}", f"timeout={timeout!r}"]
            auto_close = _optional_bool(params.get("auto_close"))
            if auto_close is not None:
                prompt_args.append(f"auto_close={auto_close!r}")
            if image_path:
                prompt_args.append(f"image_path={image_path!r}")
            if screen_title:
                prompt_args.append(f"screen_title={screen_title!r}")
            if binding.get("selected_labware_name"):
                prompt_args.append(f"selected_labware_name={binding['selected_labware_name']!r}")
            if binding.get("selected_labware_type"):
                prompt_args.append(f"selected_labware_type={binding['selected_labware_type']!r}")
            if binding.get("grid") is not None:
                prompt_args.append(f"grid={binding['grid']!r}")
            if binding.get("site") is not None:
                prompt_args.append(f"site={binding['site']!r}")
            if sound_path:
                prompt_args.append(f"sound_path={sound_path!r}")
            return [f"wt.user_prompt_worktable({', '.join(prompt_args)})"]
        prompt_args = [f"{str(prompt)!r}", f"timeout={timeout!r}"]
        auto_close = _optional_bool(params.get("auto_close"))
        if auto_close is not None:
            prompt_args.append(f"auto_close={auto_close!r}")
        if image_path:
            prompt_args.append(f"image_path={image_path!r}")
        if screen_title:
            prompt_args.append(f"screen_title={screen_title!r}")
        if str(params.get("rup_kind") or step.get("command_id") or "").strip().lower() in {
            "standard",
            "rupstandardstatement",
        } and image_path:
            prompt_args.append("rup_kind='standard'")
        if sound_path:
            prompt_args.append(f"sound_path={sound_path!r}")
        return [f"wt.user_prompt({', '.join(prompt_args)})"]
    if operation == "query_variable":
        from .api_v2.commands import command_from_ir_step, command_to_xml

        xml = command_to_xml(command_from_ir_step(step))
        return [f"wt.raw_xml_step('QueryVariableStatement', {xml!r})"]
    if operation == "runtime_variable_prompt":
        params = dict(step.get("parameters") or {})
        params.setdefault("line_number", step.get("line_number") or step.get("index") or 0)
        xml = render_rup_variable_statement_xml(params)
        return [f"wt.raw_xml_step('RUPVariableStatement', {xml!r})"]
    if operation == "execute_application":
        params = step.get("parameters") or {}
        xml = str(params.get("raw_xml") or "").strip() or render_execute_application_xml(params)
        return [f"wt.raw_xml_step('ExecuteApplicationStatement', {xml!r})"]
    if operation == "execute_vb_script":
        xml = render_execute_vb_script_xml(step.get("parameters") or {})
        return [f"wt.raw_xml_step('ExecuteVbScriptStatement', {xml!r})"]
    if operation == "set_variable":
        variable = str(params.get("variable") or "").strip()
        if variable:
            value_arg = _expression_python_arg(params.get("value_expression"), fallback=params.get("value"))
            return [f"wt.set_variable({variable!r}, {value_arg})"]
    if operation == "move_plate":
        labware = str(params.get("labware") or target or "")
        labware_expr = labware_vars.get(labware, repr(labware))
        site_expression = params.get("site_expression")
        destination_site_fallback = (
            params.get("destination_site")
            or params.get("to_site")
            or step.get("destination_site")
            or 1
        )
        if params.get("allow_automated_verification_motion") or params.get("verification_after_motion_required"):
            raw_xml = str(params.get("raw_xml") or "").strip()
            if raw_xml:
                return [f"wt.raw_xml_step('ApplicationDriverMacro', {raw_xml!r})"]
            onto_labware = str(params.get("onto_labware") or params.get("onto") or "")
            destination_location = str(
                params.get("destination_location")
                or params.get("to_location")
                or step.get("destination_location")
                or ""
            )
            destination_site = _expression_source_text(
                site_expression,
                fallback=destination_site_fallback,
            )
            if onto_labware:
                destination_location = f'GetCoverSiteName("{onto_labware}")'
                destination_site = f'GetCoverSiteIndex("{onto_labware}")'
            macro_params = {
                "Labware": labware,
                "Location": destination_location,
                "Site": destination_site,
                "FixedSite": bool(params.get("fixed_site", True)),
                "MoveToBase": bool(params.get("move_to_base", False)),
                "OnTheFlyTool": "",
                "UseOnTheFlyTool": False,
            }
            return [
                "wt.application_driver_macro("
                "'RGA1_TransferLabware', "
                f"parameters={macro_params!r})"
            ]
        onto_labware = str(params.get("onto_labware") or params.get("onto") or "")
        if onto_labware:
            onto_expr = labware_vars.get(onto_labware, repr(onto_labware))
            return [f"wt.gripper.move({labware_expr}, onto={onto_expr})"]
        destination_location = str(
            params.get("destination_location")
            or params.get("to_location")
            or step.get("destination_location")
            or ""
        )
        if isinstance(site_expression, dict):
            destination_site_arg = _expression_python_arg(site_expression)
        else:
            destination_site_arg = repr(destination_site_fallback)
        return [f"wt.gripper.move({labware_expr}, to=({destination_location!r}, {destination_site_arg}))"]
    if operation == "call_subroutine":
        params = step.get("parameters") or {}
        subroutine = str(params.get("subroutine") or params.get("SubRoutine") or "").strip().strip('"')
        execution_mode = str(params.get("execution_mode") or params.get("ExecutionMode") or "Synchronous")
        parts = [repr(subroutine)]
        if execution_mode != "Synchronous":
            parts.append(f"execution_mode={execution_mode!r}")
        for param_name, key in (
            ("variable_mappings_start", "variable_mappings_start"),
            ("variable_mappings_end", "variable_mappings_end"),
        ):
            mappings = params.get(key) or params.get("".join(part.capitalize() for part in key.split("_")))
            if not isinstance(mappings, list) or not mappings:
                continue
            rendered_items = []
            for item in mappings:
                if not isinstance(item, dict):
                    continue
                source_arg = _expression_python_ast_arg(
                    item.get("source_expression"),
                    fallback=item.get("source"),
                )
                rendered_items.append(
                    f"VariableMapping(target={str(item.get('target') or '')!r}, source={source_arg})"
                )
            rendered = ", ".join(rendered_items)
            if rendered:
                parts.append(f"{param_name}=[{rendered}]")
        return [f"wt.call_subroutine({', '.join(parts)})"]
    params = step.get("parameters") or {}
    raw_xml = params.get("raw_xml")
    if raw_xml:
        command_id = step.get("command_id") or operation
        return [f"wt.raw_xml_step({str(command_id)!r}, {str(raw_xml)!r})"]
    return [f"# TODO: render unsupported IR operation {operation!r}: {json.dumps(step, sort_keys=True)}"]


def _preserve_raw_xml_for_operation(operation: str) -> bool:
    rendered = {
        "add_labware",
        "get_head_adapter",
        "drop_head_adapter",
        "pick_up_tips",
        "mca384_get_tips",
        "set_tips_back",
        "mca384_drop_tips",
        "aspirate",
        "dispense",
        "mca384_mix",
        "comment",
        "prompt_user",
        "move_plate",
        "call_subroutine",
    }
    return operation not in rendered


def _step_specs(step: dict[str, Any]) -> list[str]:
    specs = []
    if step.get("target_labware"):
        specs.append(f"Target labware: `{step['target_labware']}`")
    if step.get("volume_ul"):
        specs.append(f"Volume: `{_format_volume(step['volume_ul'])} uL`")
    if step.get("liquid_class"):
        specs.append(f"Liquid class: `{step['liquid_class']}`")
    for key, value in sorted((step.get("parameters") or {}).items()):
        if _has_value(value) and key not in {"target", "volume_ul", "liquid_class"}:
            specs.append(f"{key}: `{value}`")
    return specs or ["No extra specifications captured."]


def _manual_fluentcontrol_steps(ir: dict[str, Any], script_filename: str) -> list[dict[str, Any]]:
    worktable = ir.get("worktable") or {}
    labware = list(ir.get("labware") or [])
    steps = list(ir.get("steps") or [])
    host_config = _host_config_report(ir)
    manual: list[dict[str, Any]] = [
        {"text": "Open FluentControl."},
    ]
    if host_config:
        manual.append(
            {
                "text": "Verify the FluentControl/VisionX instrument configuration dropdown before import/run.",
                "details": [_host_config_instruction((host_config.get("expected") or {}), host_config)],
            }
        )
    manual.append({"text": f"Load worktable: `{worktable.get('name') or 'selected project worktable'}`."})
    if worktable.get("guid"):
        manual[-1]["details"] = [f"Worktable GUID: `{worktable['guid']}`"]

    if labware:
        manual.append(
            {
                "text": "Confirm labware:",
                "details": [_manual_labware_detail(item) for item in labware if item.get("label")],
            }
        )

    for step in steps:
        if step.get("operation") == "add_labware" and labware:
            continue
        manual.append({"text": _manual_step_text(step)})

    manual.append({"text": f"Save the script as `{script_filename}`."})
    manual.append({"text": "Run the optional FluentControl import/load diagnostic or manually open the script in Script Editor."})
    manual.append({"text": "Simulate before real instrument use."})
    return manual


def _host_config_report(ir: dict[str, Any]) -> dict[str, Any]:
    source = ir.get("source") if isinstance(ir.get("source"), dict) else {}
    report = source.get("host_instrument_configuration") if isinstance(source, dict) else None
    return report if isinstance(report, dict) else {}


def _host_config_instruction(expected: dict[str, Any], report: dict[str, Any] | None = None) -> str:
    if report and report.get("user_instruction"):
        return str(report["user_instruction"])
    exact = expected.get("exact_names") or []
    patterns = expected.get("patterns") or []
    if exact or patterns:
        pieces = []
        if exact:
            pieces.append(f"one of `{', '.join(exact)}`")
        if patterns:
            pieces.append(f"a name matching `{', '.join(patterns)}`")
        return (
            "Set FluentControl to "
            + " or ".join(pieces)
            + "; if another configuration is active, switch via the configuration dropdown before import/run."
        )
    return (
        "Confirm the active configuration matches the target instrument, arm/head setup, RGA/finger hardware, "
        "carriers, and deck before import/run."
    )


def _manual_labware_detail(item: dict[str, Any]) -> str:
    label = item.get("label") or "Labware"
    location = item.get("deck_location") or _deck_location(item)
    catalog = item.get("catalog") or item.get("python_class") or ""
    role = item.get("role") or ""
    detail = f"`{label}`"
    if location:
        detail += f" at carrier position `{location}`"
    if catalog:
        detail += f" ({catalog})"
    if role:
        detail += f"; role `{role}`"
    return detail


def _manual_step_text(step: dict[str, Any]) -> str:
    operation = str(step.get("operation") or "")
    target = step.get("target_labware") or step.get("source_labware") or step.get("destination_labware") or ""
    volume = _manual_volume(step.get("volume_ul"))
    liquid_class = step.get("liquid_class") or ""
    well = _manual_well(step)
    worklist = _manual_worklist(step)

    if operation == "add_labware":
        params = step.get("parameters") or {}
        label = params.get("label") or target or "labware"
        location = _deck_location(params) or params.get("location") or ""
        suffix = f" at carrier position `{location}`" if location else ""
        return f"Load labware `{label}`{suffix}."
    if operation == "move_plate":
        params = step.get("parameters") or {}
        labware = params.get("labware") or target or "selected labware"
        onto_labware = params.get("onto_labware") or params.get("onto")
        if onto_labware:
            return f"Move `{labware}` onto `{onto_labware}` with the RGA gripper."
        destination = _deck_location(
            {
                "location": params.get("destination_location") or params.get("to_location"),
                "position": params.get("destination_site") or params.get("to_site"),
            }
        )
        suffix = f" to `{destination}`" if destination else ""
        return f"Move `{labware}`{suffix} with the RGA gripper."
    if operation in {"pick_up_tips", "mca384_get_tips", "liha_get_tips"}:
        return f"Pick up tips from `{target or 'selected tip box'}`."
    if operation in {"set_tips_back"}:
        return f"Return tips to `{target or 'source tip box'}`."
    if operation in {"mca384_drop_tips", "liha_drop_tips", "drop_tips"}:
        if target:
            return f"Drop tips at `{target}`."
        return "Wash or drop tips according to the source script cleanup pattern."
    if operation in {"aspirate", "liha_aspirate"}:
        return _manual_liquid_step("Aspirate", volume, target, well, liquid_class, preposition="from")
    if operation in {"dispense", "liha_dispense"}:
        return _manual_liquid_step("Dispense", volume, target, well, liquid_class, preposition="into")
    if operation in {"mca384_mix", "liha_mix", "mix"}:
        return _manual_liquid_step("Mix", volume, target, well, liquid_class, preposition="in")
    if operation == "get_head_adapter":
        return "Mount the head adapter."
    if operation == "drop_head_adapter":
        return "Drop the head adapter."
    if operation == "read_worklist" or worklist:
        return f"Read or execute worklist `{worklist or target}`."
    if operation == "wash":
        return "Run the configured wash step."
    if operation == "query_variable":
        params = step.get("parameters") or {}
        variable = params.get("variable") or "selected variable"
        prompt = params.get("prompt") or step.get("name") or ""
        suffix = f" with prompt `{prompt}`" if prompt else ""
        return f"Query FluentControl variable `{variable}`{suffix}."
    if operation == "runtime_variable_prompt":
        params = step.get("parameters") or {}
        title = params.get("screen_title") or "runtime variable prompt"
        names = [item.get("name") for item in params.get("variables") or [] if isinstance(item, dict) and item.get("name")]
        suffix = f" for `{', '.join(names)}`" if names else ""
        return f"Display runtime variable prompt `{title}`{suffix}."
    if operation == "set_remaining_runtime":
        value = (step.get("parameters") or {}).get("value")
        suffix = f" to `{value}`" if _has_value(value) else ""
        return f"Set FluentControl internal variable `RemainingRuntime`{suffix}."
    if operation == "set_variable":
        params = step.get("parameters") or {}
        variable = params.get("variable") or "selected variable"
        value = params.get("value")
        suffix = f" to `{value}`" if _has_value(value) else ""
        return f"Set FluentControl variable `{variable}`{suffix}."
    if operation == "call_subroutine":
        params = step.get("parameters") or {}
        subroutine = params.get("subroutine") or params.get("SubRoutine") or "selected subroutine"
        execution_mode = params.get("execution_mode") or params.get("ExecutionMode")
        suffix = f" with execution mode `{execution_mode}`" if _has_value(execution_mode) else ""
        return f"Call FluentControl subroutine `{subroutine}`{suffix}."
    if operation == "conditional_branch":
        params = step.get("parameters") or {}
        condition = params.get("condition") or "configured condition"
        return f"Run conditional branch when `{condition}`."
    if operation == "default_branch":
        return "Run the default/alternate branch when prior branch conditions do not match."
    if operation == "prompt_user":
        prompt = (step.get("parameters") or {}).get("prompt") or (step.get("parameters") or {}).get("text") or step.get("name")
        return f"Prompt user: {prompt}."
    if operation == "comment":
        comment = (step.get("parameters") or {}).get("comment") or step.get("comment") or step.get("name")
        return f"Comment: {comment}."
    name = step.get("name") or operation.replace("_", " ").title() or "Command"
    return f"Run command `{name}`."


def _manual_liquid_step(
    action: str,
    volume: str,
    labware: Any,
    well: str,
    liquid_class: Any,
    *,
    preposition: str,
) -> str:
    pieces = [action]
    if volume:
        pieces.append(volume)
    if labware:
        pieces.append(f"{preposition} `{labware}`")
    if well:
        pieces.append(f"well `{well}`")
    if liquid_class:
        pieces.append(f"using liquid class `{liquid_class}`")
    return " ".join(pieces) + "."


def _manual_volume(value: Any) -> str:
    if not _has_value(value):
        return ""
    return f"{_format_volume(value)} uL"


def _manual_well(step: dict[str, Any]) -> str:
    params = step.get("parameters") or {}
    for key in ("well", "source_well", "destination_well", "well_position", "position"):
        value = params.get(key)
        if _has_value(value):
            return str(value)
    return ""


def _manual_worklist(step: dict[str, Any]) -> str:
    params = step.get("parameters") or {}
    for key in ("worklist", "worklist_name", "WorklistName", "file", "FileName", "path", "Path"):
        value = params.get(key)
        if _has_value(value):
            return str(value)
    return ""


def _deck_location(item: dict[str, Any]) -> str:
    if item.get("deck_location"):
        return str(item["deck_location"])
    if item.get("location") and item.get("position"):
        return f"{item['location']} {item['position']}"
    if item.get("position"):
        return str(item["position"])
    return ""


def _format_volume(value: Any) -> str:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    return str(int(numeric)) if numeric.is_integer() else f"{numeric:.6f}".rstrip("0").rstrip(".")


def _operation_from_command_id(command_id: str) -> str | None:
    registry_operation = registry_command_operation(command_id)
    if registry_operation:
        return registry_operation
    lowered = command_id.lower()
    if "addlabware" in lowered:
        return "add_labware"
    if "getheadadapter" in lowered:
        return "get_head_adapter"
    if "dropheadadapter" in lowered:
        return "drop_head_adapter"
    if "pickuptips" in lowered:
        return "pick_up_tips"
    if "settipsback" in lowered:
        return "set_tips_back"
    if "aspirate" in lowered:
        return "liha_aspirate" if "liha" in lowered else "aspirate"
    if "dispense" in lowered:
        return "liha_dispense" if "liha" in lowered else "dispense"
    if "mix" in lowered:
        return "liha_mix" if "liha" in lowered else "mca384_mix"
    if "droptips" in lowered:
        return "liha_drop_tips" if "liha" in lowered else "mca384_drop_tips"
    if "gettips" in lowered:
        return "liha_get_tips" if "liha" in lowered else "mca384_get_tips"
    if "conditionalgroup" in lowered:
        return "conditional_branch"
    if "alternategroup" in lowered:
        return "default_branch"
    if "queryvariable" in lowered:
        return "query_variable"
    if "setvariable" in lowered:
        return "set_variable"
    if "rupvariable" in lowered:
        return "runtime_variable_prompt"
    if "executeapplication" in lowered:
        return "execute_application"
    if "executevbscript" in lowered:
        return "execute_vb_script"
    return None


def _operation_name(operation: str) -> str:
    names = {
        "add_labware": "Add Labware",
        "get_head_adapter": "Mount Head Adapter",
        "drop_head_adapter": "Drop Head Adapter",
        "pick_up_tips": "Pick Up Tips",
        "set_tips_back": "Return Tips",
        "aspirate": "Aspirate",
        "dispense": "Dispense",
        "mca384_mix": "Mix",
        "mca384_get_tips": "Pick Up Tips",
        "mca384_drop_tips": "Drop Tips",
        "liha_aspirate": "LiHa Aspirate",
        "liha_dispense": "LiHa Dispense",
        "liha_mix": "LiHa Mix",
        "liha_get_tips": "LiHa Get Tips",
        "liha_drop_tips": "LiHa Drop Tips",
        "conditional_branch": "Conditional Branch",
        "default_branch": "Default Branch",
        "query_variable": "Query Variable",
        "set_variable": "Set Variable",
        "set_remaining_runtime": "Set Remaining Runtime",
        "runtime_variable_prompt": "Runtime Variable Prompt",
        "move_plate": "Move Plate",
        "execute_application": "Execute Application",
        "call_subroutine": "Call Subroutine",
    }
    return names.get(operation, operation.replace("_", " ").title())


def _is_head_step(operation: Any) -> bool:
    return str(operation) in {
        "get_head_adapter",
        "drop_head_adapter",
        "pick_up_tips",
        "set_tips_back",
        "aspirate",
        "dispense",
        "mca384_mix",
        "mca384_get_tips",
        "mca384_drop_tips",
    }


def _python_class_for_labware(item: dict[str, Any]) -> str:
    catalog = str(item.get("catalog") or "").lower()
    label = str(item.get("label") or "").lower()
    if "mca" in catalog and "box" in catalog:
        return "MCA100Box"
    if "tip" in catalog or "tip" in label:
        return "MCA100Box"
    return "Plate96"


def _python_position_arg(value: Any) -> str:
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if re.fullmatch(r"[+-]?\d+", text):
        return str(int(text))
    return repr(text or "1")


def _labware_by_label(labware: list[dict[str, Any]], label: str) -> dict[str, Any]:
    for item in labware:
        if item.get("label") == label:
            return item
    return {}


def _infer_labware_role(label: str, catalog: str) -> str:
    lowered = f"{label} {catalog}".lower()
    if "tip" in lowered:
        return "tips"
    if "source" in lowered or "src" in lowered:
        return "source"
    if "dest" in lowered or "destination" in lowered:
        return "destination"
    return "labware"


def _add_dependency(
    ir: dict[str, Any],
    *,
    kind: str,
    name: str,
    guid: str = "",
    required: bool = True,
    source_path: str = "",
) -> None:
    if not name and not guid:
        return
    dependency = {
        "kind": kind,
        "name": name,
        "guid": guid,
        "required": required,
        "source_path": source_path,
    }
    _upsert_item(ir["dependencies"], dependency, key=("kind", "name", "guid"))


def _upsert_item(items: list[dict[str, Any]], item: dict[str, Any], *, key: str | tuple[str, ...]) -> None:
    keys = (key,) if isinstance(key, str) else key
    incoming = {k: v for k, v in item.items() if _has_value(v)}
    for existing in items:
        if all(existing.get(k) == incoming.get(k) for k in keys):
            existing.update(incoming)
            return
    items.append(incoming)


def _assignment_target_name(statement: ast.stmt) -> str | None:
    if not isinstance(statement, ast.Assign) or not statement.targets:
        return None
    target = statement.targets[0]
    return target.id if isinstance(target, ast.Name) else None


def _is_worktable_from_workspace(call: ast.Call) -> bool:
    return (
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "from_workspace"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "Worktable"
    )


def _is_method_call(call: ast.Call, method_name: str) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == method_name


def _call_name(call: ast.Call) -> str:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return ""


def _keyword_value(call: ast.Call, key: str) -> Any:
    for keyword in call.keywords:
        if keyword.arg == key:
            return _literal_text(keyword.value)
    return None


def _value_label(node: ast.AST, reagent_by_var: dict[str, str], labware_by_var: dict[str, str]) -> str:
    literal = _literal_text(node)
    if _has_value(literal):
        return str(literal)
    if isinstance(node, ast.Name):
        return labware_by_var.get(node.id) or reagent_by_var.get(node.id) or node.id
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        if node.func.attr == "labware_by_label" and node.args:
            return str(_literal_text(node.args[0]) or "")
    if isinstance(node, ast.Attribute):
        return node.attr
    return ast.unparse(node) if hasattr(ast, "unparse") else ""


def _literal_text(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    return None


def _source_path(draft_name: str, current_group: str, source: str, node: ast.AST) -> str:
    segment = ast.get_source_segment(source, node) or ""
    segment = " ".join(segment.split())
    if len(segment) > 140:
        segment = segment[:137].rstrip() + "..."
    return f"{draft_name} -> build_worktable() -> {current_group} -> {segment}"


def _command_id(command_object: ET.Element) -> str:
    for child in list(command_object):
        return _local_name(child.tag)
    return str(command_object.attrib.get("Type") or "").rsplit(".", 1)[-1]


def _application_driver_macro_name(command_object: ET.Element) -> str:
    return _application_driver_macro_attr(command_object, "Name") or ""


def _application_driver_macro_attr(command_object: ET.Element, name: str) -> str | None:
    macro = _direct_child(command_object, "ApplicationDriverMacro")
    if macro is None:
        return None
    value = macro.attrib.get(name)
    return str(value) if value is not None else None


def _command_field_map(command_object: ET.Element) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for child in command_object.iter():
        name = _local_name(child.tag)
        value = _text(child)
        if not name or value is None:
            continue
        existing = fields.get(name)
        if existing is None:
            fields[name] = value
        elif isinstance(existing, list):
            if value not in existing:
                existing.append(value)
        elif existing != value:
            fields[name] = [existing, value]
    return fields


def _registered_xscr_expression_parameters(
    command_object: ET.Element,
    command_id: str,
    *,
    source_entry: str = "",
) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for field_path in expression_fields_for_command(command_id):
        values = _xscr_expression_field_values(command_object, field_path)
        if not values:
            continue
        expression_key = canonical_expression_key(field_path)
        expressions = []
        for value in values:
            expression = expression_to_mapping(parse_or_preserve_source_expression(value))
            if (
                source_entry
                and expression.get("kind") == "source_preserved_expression"
                and not expression.get("source_entry")
            ):
                expression["source_entry"] = source_entry
            expressions.append(expression)
        parameters[expression_key] = (
            expressions if expression_key == "volume_expressions" else expressions[0]
        )
    return parameters


def _xscr_expression_field_values(
    command_object: ET.Element,
    field_path: str,
) -> list[str]:
    parts = tuple(part for part in field_path.split("/") if part)
    if not parts:
        return []
    if len(parts) == 1:
        return [
            (element.text or "").strip()
            for element in command_object.iter()
            if _local_name(element.tag) == parts[0] and not list(element)
        ]
    values: list[str] = []
    for container in command_object.iter():
        if _local_name(container.tag) != parts[0]:
            continue
        for element in container.iter():
            if element is not container and _local_name(element.tag) == parts[-1]:
                values.append((element.text or "").strip())
    return values


def _xscr_variable_mapping_items(
    command_object: ET.Element,
    container_name: str,
    *,
    source_entry: str = "",
) -> list[dict[str, Any]]:
    container = _first_descendant(command_object, container_name)
    if container is None:
        return []
    items: list[dict[str, Any]] = []
    for mapping_object in _iter_local(container, "Object"):
        if _command_id(mapping_object) != "VariableMapping":
            continue
        target = _first_text(mapping_object, "Target")
        source = _first_text(mapping_object, "Source")
        if not target or source is None:
            continue
        items.append(
            {
                "target": target,
                "source": source,
                **_registered_xscr_expression_parameters(
                    mapping_object,
                    "VariableMapping",
                    source_entry=source_entry,
                ),
            }
        )
    return items


def _first_nonempty_text(parent: ET.Element, names: list[str]) -> str | None:
    for name in names:
        value = _first_text(parent, name)
        if value:
            return value
    return None


def _first_text(parent: ET.Element, name: str) -> str | None:
    node = _first_descendant(parent, name)
    return _text(node)


def _direct_text(parent: ET.Element | None, name: str) -> str | None:
    child = _direct_child(parent, name)
    return _text(child)


def _text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    value = node.text.strip()
    return value or None


def _first_descendant(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in parent.iter():
        if _local_name(child.tag) == name:
            return child
    return None


def _direct_child(parent: ET.Element | None, name: str) -> ET.Element | None:
    if parent is None:
        return None
    for child in list(parent):
        if _local_name(child.tag) == name:
            return child
    return None


def _direct_children(parent: ET.Element | None, name: str) -> list[ET.Element]:
    if parent is None:
        return []
    return [child for child in list(parent) if _local_name(child.tag) == name]


def _enclosing_raw_command_id(
    element: ET.Element,
    parent_by_child: dict[ET.Element, ET.Element],
) -> str:
    parent = parent_by_child.get(element)
    while parent is not None:
        if _local_name(parent.tag) == "Object":
            command_id = _command_id(parent)
            operation = _operation_from_command_id(command_id)
            if operation and _preserve_raw_xml_for_operation(operation):
                return command_id
        parent = parent_by_child.get(parent)
    return ""


def _iter_local(parent: ET.Element, name: str):
    for child in parent.iter():
        if _local_name(child.tag) == name:
            yield child


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _number_or_text(value: str) -> Any:
    if value == "":
        return None
    try:
        number = float(value)
    except ValueError:
        return value
    return int(number) if number.is_integer() else number


def _bool_text(value: str | None) -> bool:
    return str(value or "").strip().lower() == "true"


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _safe_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    cleaned = cleaned.strip("._-").lower()
    return cleaned or "protocol"


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return cleaned.strip("._-") or "protocol"


def _safe_var(value: str, *, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]+", "_", value.strip()).strip("_").lower() or "item"
    if base[0].isdigit():
        base = f"item_{base}"
    candidate = base
    counter = 2
    while candidate in used:
        candidate = f"{base}_{counter}"
        counter += 1
    return candidate


def _friendly_device_name(value: str) -> str:
    return {"mca96": "MCA96 head", "liha": "LiHa arm", "fca": "FCA arm"}.get(value.lower(), value)


def _has_value(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def render_execute_application_xml(params: dict[str, Any] | Any) -> str:
    """Synthesize an ExecuteApplicationStatement XML object."""
    raw_path = str(
        params.get("path")
        or params.get("application")
        or params.get("file")
        or params.get("command")
        or ""
    ).strip()
    if len(raw_path) >= 2 and raw_path[0] == '"' and raw_path[-1] == '"':
        raw_path = raw_path[1:-1]
    raw_arguments = str(params.get("arguments") or params.get("args") or "")
    wait = _xml_bool(params.get("wait", True))
    store_return = _xml_bool(params.get("store_return", False))
    disabled = _xml_bool(params.get("disabled", False))
    variable = html.escape(str(params.get("variable") or ""), quote=False)
    application_text = html.escape(f'"{raw_path}"', quote=False)
    arguments_text = html.escape(raw_arguments, quote=False)
    return (
        '<Object Type="Tecan.Core.Scripting.ExecuteApplicationStatement">\n'
        "  <ExecuteApplicationStatement>\n"
        f"    <Application>{application_text}</Application>\n"
        f"    <Arguments>{arguments_text}</Arguments>\n"
        f"    <Wait>{wait}</Wait>\n"
        f"    <StoreReturn>{store_return}</StoreReturn>\n"
        f"    <Variable>{variable}</Variable>\n"
        "    <IsBreakpoint>False</IsBreakpoint>\n"
        f"    <IsDisabledForExecution>{disabled}</IsDisabledForExecution>\n"
        "    <LineNumber>0</LineNumber>\n"
        "  </ExecuteApplicationStatement>\n"
        "</Object>"
    )


def render_execute_vb_script_xml(params: dict[str, Any] | Any) -> str:
    """Synthesize an ExecuteVbScriptStatement XML object."""
    raw_path = str(
        params.get("vb_script")
        or params.get("path")
        or params.get("script")
        or ""
    ).strip()
    if len(raw_path) >= 2 and raw_path[0] == '"' and raw_path[-1] == '"':
        raw_path = raw_path[1:-1]
    mode = str(params.get("execution_mode") or params.get("mode") or "Synchronous").strip() or "Synchronous"
    disabled = "True" if params.get("disabled") else "False"
    vbscript_text = html.escape(f'"{raw_path}"', quote=False)
    mode_text = html.escape(mode, quote=False)
    return (
        '<Object Type="Tecan.Core.Scripting.ExecuteVbScriptStatement">\n'
        "  <ExecuteVbScriptStatement>\n"
        f"    <VbScript>{vbscript_text}</VbScript>\n"
        "    <Mode>\n"
        f"      <ExecutionMode>{mode_text}</ExecutionMode>\n"
        "    </Mode>\n"
        "    <IsStarted>False</IsStarted>\n"
        "    <IsBreakpoint>False</IsBreakpoint>\n"
        f"    <IsDisabledForExecution>{disabled}</IsDisabledForExecution>\n"
        "    <LineNumber>0</LineNumber>\n"
        "  </ExecuteVbScriptStatement>\n"
        "</Object>"
    )


def _xml_bool(value: Any) -> str:
    if isinstance(value, str):
        return "True" if value.strip().casefold() == "true" else "False"
    return "True" if bool(value) else "False"


def _rup_allowed_values_text(allowed: Any) -> str:
    if allowed is None:
        return ""
    if isinstance(allowed, (list, tuple)):
        return ";".join(_rup_allowed_value_text(item) for item in allowed)
    return str(allowed)


def _rup_allowed_value_text(value: Any) -> str:
    """Return a FluentControl RUP AllowedValues item.

    RUPVariableStatement ``AllowedValues`` is a semicolon-delimited UI value
    list, not a FluentControl expression field.  Verification selector recipes
    historically passed string-literal-looking items such as ``"yes"`` and
    ``"no"``, which rendered as ``"yes";"no"``.  FluentControl 3.5.7 can crash
    while displaying that TouchTools dialog, so normalize single-item quoted
    string literals back to their display values at emission time.
    """
    text = str(value)
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        return stripped[1:-1]
    return text


def _rup_variable_display_type(value: Any, *, has_allowed_values: bool = False) -> str:
    raw = str(value or "").strip()
    if raw == "TextboxWithNumpad":
        return raw
    key = re.sub(r"[^a-z0-9]+", "", raw.casefold())
    aliases = {
        "combobox": "Combobox",
        "combo": "Combobox",
        "dropdown": "Combobox",
        "select": "Combobox",
        "radiobutton": "RadioButton",
        "radio": "RadioButton",
        "empty": "Empty",
        "textbox": "Empty",
        "text": "Empty",
        "input": "Empty",
    }
    if key in aliases:
        return aliases[key]
    if raw in {"Combobox", "RadioButton", "Empty"}:
        return raw
    return "Combobox" if has_allowed_values else "Empty"


def render_rup_variable_statement_xml(params: dict[str, Any] | Any) -> str:
    """Synthesize an RUPVariableStatement XML object."""
    screen_title = html.escape(str(params.get("screen_title") or params.get("title") or "User Input"), quote=False)
    instructions = html.escape(
        str(params.get("instructions") or params.get("prompt") or "Please make your selections"),
        quote=False,
    )
    horizontal = "true" if params.get("layout_horizontal", True) else "false"
    try:
        columns = int(params.get("columns") or params.get("number_of_columns") or 1)
    except (TypeError, ValueError):
        columns = 1
    display_and_wait = "True" if params.get("display_and_wait", True) else "False"
    auto_close = "True" if params.get("auto_close") else "False"
    try:
        timeout = int(params.get("timeout", 1) or 0)
    except (TypeError, ValueError):
        timeout = 1
    if timeout < 1:
        timeout = 1
    try:
        line_number = int(params.get("line_number") or 0)
    except (TypeError, ValueError):
        line_number = 0
    disabled = "True" if params.get("disabled") or params.get("is_disabled_for_execution") else "False"
    is_breakpoint = "True" if params.get("is_breakpoint") else "False"

    item_blocks: list[str] = []
    for field in params.get("variables") or []:
        if not isinstance(field, dict):
            continue
        name = html.escape(str(field.get("name") or field.get("variable") or "").strip(), quote=False)
        var_type = html.escape(str(field.get("variable_type") or field.get("type") or "System.String"), quote=False)
        display_text = html.escape(str(field.get("display_text") or field.get("label") or name), quote=False)
        allowed_text_raw = _rup_allowed_values_text(field.get("allowed_values"))
        display_type = html.escape(
            _rup_variable_display_type(
                field.get("display_type"),
                has_allowed_values=bool(allowed_text_raw),
            ),
            quote=False,
        )
        value = field.get("value")
        value_block = (
            f"<VariableValue>{html.escape(str(value), quote=False)}</VariableValue>"
            if _has_value(value)
            else "<VariableValue />"
        )
        allowed_text = html.escape(allowed_text_raw, quote=False)
        allowed_block = f"<AllowedValues>{allowed_text}</AllowedValues>" if allowed_text else "<AllowedValues />"
        enabled = "true" if field.get("enabled", True) else "false"
        has_min_max = _has_value(field.get("min")) or _has_value(field.get("max"))
        min_unresolved = html.escape(str(field.get("min")), quote=False) if _has_value(field.get("min")) else ""
        max_unresolved = html.escape(str(field.get("max")), quote=False) if _has_value(field.get("max")) else ""
        min_block = f"<UnresolvedMinValue>{min_unresolved}</UnresolvedMinValue>" if min_unresolved else "<UnresolvedMinValue />"
        max_block = f"<UnresolvedMaxValue>{max_unresolved}</UnresolvedMaxValue>" if max_unresolved else "<UnresolvedMaxValue />"
        name_block = f"<VariableName>{name}</VariableName>" if name else "<VariableName />"
        item_blocks.append(
            "                        <RupVariableItem>\n"
            f"                          {name_block}\n"
            f"                          <VariableType>{var_type}</VariableType>\n"
            f"                          <DisplayText>{display_text}</DisplayText>\n"
            f"                          <DisplayType>{display_type}</DisplayType>\n"
            f"                          {value_block}\n"
            f"                          {allowed_block}\n"
            "                          <UnresolvedDisabledWhenValue />\n"
            f"                          <IsEnabled>{enabled}</IsEnabled>\n"
            f"                          {min_block}\n"
            "                          <MinValue>0</MinValue>\n"
            f"                          {max_block}\n"
            "                          <MaxValue>0</MaxValue>\n"
            f"                          <IsMinMaxValueUsed>{'true' if has_min_max else 'false'}</IsMinMaxValueUsed>\n"
            "                        </RupVariableItem>"
        )
    variables_xml = "\n".join(item_blocks)
    return (
        '<Object Type="Tecan.VisionX.TouchTools.Driver.RUP.RUPVariableStatement">\n'
        "  <RUPVariableStatement>\n"
        "    <VariableDatas>\n"
        "      <VariableDataModel>\n"
        f"        <Instructions>{instructions}</Instructions>\n"
        f"        <IsLayoutHorizontalOriented>{horizontal}</IsLayoutHorizontalOriented>\n"
        f"        <UnresolvedNumberOfColumns>{columns}</UnresolvedNumberOfColumns>\n"
        f"        <NumberOfColumns>{columns}</NumberOfColumns>\n"
        "        <Variables>\n"
        f"{variables_xml}\n"
        "        </Variables>\n"
        "        <VariableScope />\n"
        "      </VariableDataModel>\n"
        "    </VariableDatas>\n"
        f"    <IsBreakpoint>{is_breakpoint}</IsBreakpoint>\n"
        f"    <IsDisabledForExecution>{disabled}</IsDisabledForExecution>\n"
        f"    <LineNumber>{line_number}</LineNumber>\n"
        f"    <RUPScreenTitle>{screen_title}</RUPScreenTitle>\n"
        f"    <RUPDisplayAndWait>{display_and_wait}</RUPDisplayAndWait>\n"
        f"    <RUPAutoClose>{auto_close}</RUPAutoClose>\n"
        f"    <RUPTimeOut>{timeout}</RUPTimeOut>\n"
        "    <ChangeStatusLightColor>False</ChangeStatusLightColor>\n"
        "    <StatusLightColorString></StatusLightColorString>\n"
        "  </RUPVariableStatement>\n"
        "</Object>"
    )


def _yaml_module() -> Any:
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError("YAML IR requires PyYAML. Use .json output or install PyYAML.") from exc
    return yaml


_PYTHON_OPERATION_BY_METHOD = {
    "mount_adapter": "get_head_adapter",
    "drop_adapter": "drop_head_adapter",
    "pick_up": "pick_up_tips",
    "return_tips": "set_tips_back",
    "drop_tips": "mca384_drop_tips",
    "aspirate": "aspirate",
    "dispense": "dispense",
    "mix": "mca384_mix",
}
