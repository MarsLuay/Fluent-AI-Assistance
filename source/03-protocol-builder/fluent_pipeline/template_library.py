"""Local protocol template discovery for reusable IR shapes."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .config import PROJECT_DIR
from .inference import (
    InferenceCandidate,
    InferenceOrigin,
    apply_inference_decisions,
    build_inference_report,
    infer_value,
    records_to_candidates,
)
from .protocol_ir import load_protocol_ir
from .protocol_ir_schema import validate_protocol_ir_document
from .worktable_geometry import workspace_labware_records


TEMPLATES_DIR = PROJECT_DIR / "templates"

_TEMPLATE_LABWARE_PARAMETERS: dict[str, dict[str, str]] = {
    "plate_transfer": {
        "source_plate": "source_plate",
        "destination_plate": "destination_plate",
        "tips": "tip_box",
    },
    "serial_dilution": {
        "source_plate": "source_plate",
        "dilution_plate": "dilution_plate",
        "diluent_reservoir": "diluent_reservoir",
        "tips": "tip_box",
    },
    "normalization": {
        "input_plate": "input_plate",
        "normalized_plate": "normalized_plate",
        "diluent_reservoir": "diluent_reservoir",
        "tips": "tip_box",
    },
    "reagent_addition": {
        "reagent_reservoir": "reagent_reservoir",
        "assay_plate": "destination_plate",
        "tips": "tip_box",
    },
    "bead_cleanup": {
        "sample_plate": "sample_plate",
        "bead_reservoir": "bead_reservoir",
        "wash_reservoir": "wash_reservoir",
        "elution_buffer_reservoir": "elution_buffer_reservoir",
        "elution_plate": "elution_plate",
        "waste_reservoir": "waste_reservoir",
        "tips": "tip_box",
    },
    "worklist_execution": {
        "source_plate": "source_plate",
        "destination_plate": "destination_plate",
        "tips": "tip_box",
    },
    "tip_strategy_test": {
        "water_reservoir": "water_reservoir",
        "test_plate": "test_plate",
        "tip_box": "tip_box",
        "return_tip_box": "return_tip_box",
        "waste": "waste",
    },
}

_GROUNDING_LIQUID_CLASS_PLACEHOLDERS = {
    "__GROUND_FROM_CONTEXT__",
    "__GROUND_WORKLIST_FROM_CONTEXT__",
}
_MISSING = object()


def list_templates(templates_dir: Path = TEMPLATES_DIR) -> list[dict[str, Any]]:
    """Return installed template summaries."""
    if not templates_dir.exists():
        return []
    items = []
    for path in sorted(templates_dir.iterdir()):
        if not path.is_dir():
            continue
        template_ir = path / "template.ir.json"
        request_schema = path / "request.schema.json"
        if not template_ir.exists() or not request_schema.exists():
            continue
        try:
            ir = load_protocol_ir(template_ir)
        except Exception:
            ir = {}
        template_meta = ir.get("template") if isinstance(ir.get("template"), dict) else {}
        protocol = ir.get("protocol") if isinstance(ir.get("protocol"), dict) else {}
        items.append(
            {
                "name": path.name,
                "path": str(path),
                "template_ir": str(template_ir),
                "request_schema": str(request_schema),
                "description": template_meta.get("description") or protocol.get("comment") or "",
                "step_count": len(ir.get("steps") or []),
            }
        )
    return items


def template_path(name: str, templates_dir: Path = TEMPLATES_DIR) -> Path:
    """Resolve a template folder by name."""
    clean = name.strip()
    if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean:
        raise ValueError(f"Invalid template name: {name!r}")
    path = templates_dir / clean
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {clean}")
    return path


def load_template_ir(name: str, templates_dir: Path = TEMPLATES_DIR) -> dict[str, Any]:
    """Load and validate a template's canonical IR."""
    return load_protocol_ir(template_path(name, templates_dir) / "template.ir.json")


def load_request_schema(name: str, templates_dir: Path = TEMPLATES_DIR) -> dict[str, Any]:
    """Load the template-specific request schema."""
    path = template_path(name, templates_dir) / "request.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def template_info(name: str, templates_dir: Path = TEMPLATES_DIR) -> dict[str, Any]:
    """Return a detailed template inventory record."""
    folder = template_path(name, templates_dir)
    ir = load_template_ir(name, templates_dir)
    schema = load_request_schema(name, templates_dir)
    examples = sorted(str(path) for path in (folder / "examples").glob("*") if path.is_file())
    issues = [
        issue.as_dict() if hasattr(issue, "as_dict") else dict(issue)
        for issue in validate_protocol_ir_document(ir)
    ]
    return {
        "name": folder.name,
        "path": str(folder),
        "template_ir": str(folder / "template.ir.json"),
        "request_schema": str(folder / "request.schema.json"),
        "schema_id": schema.get("$id", ""),
        "protocol_name": (ir.get("protocol") or {}).get("name"),
        "description": (ir.get("template") or {}).get("description") or (ir.get("protocol") or {}).get("comment", ""),
        "step_count": len(ir.get("steps") or []),
        "labware_count": len(ir.get("labware") or []),
        "examples": examples,
        "valid": not [issue for issue in issues if issue.get("severity") == "error"],
        "issues": issues,
    }


def ground_template_ir(
    name: str,
    *,
    request_spec: Mapping[str, Any],
    context: Any,
    templates_dir: Path = TEMPLATES_DIR,
) -> dict[str, Any]:
    """Ground one inert template shape in exact imported-context evidence.

    A failed grounding returns ``ir=None`` so an unresolved template cannot be
    mistaken for behavior authority. The reviewed request spec supplies intent;
    the returned, fully grounded IR becomes behavior authority.
    """
    manifest, context_name = _context_manifest(context)
    if name == "plate_transfer" or name in _TEMPLATE_LABWARE_PARAMETERS:
        return _ground_with_inference(
            name,
            request_spec=request_spec,
            manifest=manifest,
            context_name=context_name,
            templates_dir=templates_dir,
        )
    report = build_inference_report(
        [],
        context=context_name,
        task=_request_intent(request_spec),
    )
    return _grounding_result(
        name,
        context_name,
        None,
        [
            _grounding_finding(
                "template_grounding_not_implemented",
                "$.template.name",
                f"Imported-context grounding is not implemented for template {name!r}.",
                "Choose an installed migrated template.",
            )
        ],
        resolved_request=deepcopy(dict(request_spec)),
        inference_report=report,
    )


def _ground_with_inference(
    name: str,
    *,
    request_spec: Mapping[str, Any],
    manifest: Mapping[str, Any],
    context_name: str,
    templates_dir: Path,
) -> dict[str, Any]:
    template_ir = load_template_ir(name, templates_dir)
    prepared = _prepare_template_request(
        name,
        request_spec=request_spec,
        manifest=manifest,
        template_ir=template_ir,
    )
    report = build_inference_report(
        prepared["decisions"],
        context=context_name,
        task=_request_intent(request_spec),
    )
    if prepared["findings"]:
        return _grounding_result(
            name,
            context_name,
            None,
            prepared["findings"],
            resolved_request=prepared["resolved_request"],
            inference_report=report,
        )

    if name == "plate_transfer":
        grounded = _ground_plate_transfer(
            request_spec=prepared["resolved_request"],
            manifest=manifest,
            context_name=context_name,
            templates_dir=templates_dir,
        )
    else:
        grounded = _ground_inventory_template(
            name,
            request_spec=prepared["resolved_request"],
            manifest=manifest,
            context_name=context_name,
            templates_dir=templates_dir,
        )
    findings = [*prepared["findings"], *(grounded.get("findings") or [])]
    return _grounding_result(
        name,
        context_name,
        grounded.get("ir") if not findings else None,
        findings,
        resolved_request=prepared["resolved_request"],
        inference_report=report,
    )


def _prepare_template_request(
    name: str,
    *,
    request_spec: Mapping[str, Any],
    manifest: Mapping[str, Any],
    template_ir: Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve omitted request fields without turning the inert shape into site truth."""
    resolved_request = deepcopy(dict(request_spec))
    template_request = request_spec.get("template")
    parameters = (
        template_request.get("parameters")
        if isinstance(template_request, Mapping)
        else None
    )
    parameters = dict(parameters) if isinstance(parameters, Mapping) else {}
    schema = _template_parameter_schema(name)
    properties = schema.get("properties", {}) if isinstance(schema, Mapping) else {}
    required = schema.get("required", []) if isinstance(schema, Mapping) else []
    task = _request_intent(request_spec)
    findings: list[dict[str, str]] = []
    decisions = []
    decision_paths: set[str] = set()

    if not isinstance(template_request, Mapping) or template_request.get("name") != name:
        findings.append(
            _grounding_finding(
                "template_request_mismatch",
                "$.template.name",
                f"The reviewed request spec does not select {name}.",
                f"Set template.name to {name} and review the request spec again.",
            )
        )

    selected_scripts = _selected_script_records(manifest, request_spec)
    workspaces = _context_workspaces(manifest)
    worktable_path = "$.template.parameters.worktable"
    worktable_explicit = _parameter_explicit(parameters, "worktable")
    worktable_candidates = _worktable_candidates(workspaces, selected_scripts)
    if not worktable_explicit and not worktable_candidates:
        worktable_candidates = _worktable_fallback_candidates(
            workspaces,
            parameters=parameters,
            labware_parameters=_TEMPLATE_LABWARE_PARAMETERS.get(name, {}),
        )
    worktable_decision = infer_value(
        worktable_path,
        candidates=worktable_candidates,
        **({"explicit": parameters.get("worktable")} if worktable_explicit else {}),
    )
    decisions.append(worktable_decision)
    decision_paths.add(worktable_path)
    resolved_request = apply_inference_decisions(resolved_request, decisions)
    resolved_parameters = _request_parameters(resolved_request)
    workspace = _resolve_workspace(
        manifest,
        resolved_parameters.get("worktable"),
        findings,
    )
    records = workspace_labware_records(workspace) if workspace is not None else []
    records = [record for record in records if _complete_labware_record(record)]

    labware_parameters = _TEMPLATE_LABWARE_PARAMETERS.get(name, {})
    explicitly_used = {
        str(parameters.get(parameter_name)).strip()
        for parameter_name in labware_parameters.values()
        if _parameter_explicit(parameters, parameter_name)
    }
    used_inferred: set[str] = set()
    for labware_id, parameter_name in labware_parameters.items():
        path = f"$.template.parameters.{parameter_name}"
        explicit = _parameter_explicit(parameters, parameter_name)
        candidates = ()
        if not explicit:
            candidates = _labware_candidates(
                records,
                selected_scripts,
                task=f"{task} {parameter_name} {labware_id}",
                excluded=explicitly_used | used_inferred,
            )
        decision = infer_value(
            path,
            candidates=candidates,
            **({"explicit": parameters.get(parameter_name)} if explicit else {}),
        )
        decisions.append(decision)
        decision_paths.add(path)
        if decision.unresolved:
            if not explicit:
                findings.append(
                    _grounding_finding(
                        "template_parameter_required",
                        path,
                        f"{name} requires reviewed parameter {parameter_name!r}; no imported-context candidate was available.",
                        "Supply the reviewed value, using an exact label from the selected worktable.",
                    )
                )
            continue
        value = str(decision.value or "").strip()
        if value:
            if not explicit:
                used_inferred.add(value)
            if workspace is not None:
                _resolve_labware_record(records, value, parameter_name, findings)

    resolved_request = apply_inference_decisions(resolved_request, decisions)
    resolved_parameters = _request_parameters(resolved_request)

    liquid_required = (
        "liquid_class" in properties
        and (
            name != "worklist_execution"
            or "liquid_class" in required
            or any(
                step.get("liquid_class") in _GROUNDING_LIQUID_CLASS_PLACEHOLDERS
                for step in template_ir.get("steps") or []
                if isinstance(step, Mapping)
            )
        )
    )
    if liquid_required or _parameter_explicit(parameters, "liquid_class"):
        path = "$.template.parameters.liquid_class"
        explicit = _parameter_explicit(parameters, "liquid_class")
        candidates = _liquid_class_candidates(manifest, selected_scripts, task)
        decision = infer_value(
            path,
            candidates=candidates,
            **({"explicit": parameters.get("liquid_class")} if explicit else {}),
        )
        decisions.append(decision)
        decision_paths.add(path)
        if decision.unresolved and liquid_required:
            findings.append(
                _grounding_finding(
                    "template_parameter_required",
                    path,
                    f"{name} requires a liquid class and no imported-context candidate was available.",
                    "Choose an exact liquid-class name from the imported context.",
                )
            )
        elif not decision.unresolved and str(decision.value).strip() not in _context_liquid_classes(manifest):
            findings.append(
                _grounding_finding(
                    "liquid_class_not_in_context",
                    path,
                    f"Liquid class {decision.value!r} is not present in the imported context.",
                    "Choose an exact liquid-class name from the imported context.",
                )
            )

    if name == "worklist_execution" or "worklist_path" in properties:
        path = "$.template.parameters.worklist_path"
        explicit = _parameter_explicit(parameters, "worklist_path")
        candidates = _worklist_candidates(manifest, selected_scripts, task)
        decision = infer_value(
            path,
            candidates=candidates,
            fallback=_template_default_candidate(name, "worklist_path", template_ir, properties),
            **({"explicit": parameters.get("worklist_path")} if explicit else {}),
        )
        decisions.append(decision)
        decision_paths.add(path)
        if decision.unresolved:
            findings.append(
                _grounding_finding(
                    "template_parameter_required",
                    path,
                    f"{name} requires an imported worklist path; no candidate was available.",
                    "Choose an exact imported worklist path.",
                )
            )
        elif str(decision.value).strip() not in {str(item) for item in manifest.get("worklist_paths") or []}:
            findings.append(
                _grounding_finding(
                    "worklist_not_in_context",
                    path,
                    f"Worklist {decision.value!r} is not present in the imported context.",
                    "Choose an exact imported worklist path.",
                )
            )

    resolved_request = apply_inference_decisions(resolved_request, decisions)
    resolved_parameters = _request_parameters(resolved_request)
    for parameter_name, property_schema in properties.items():
        path = f"$.template.parameters.{parameter_name}"
        if path in decision_paths or _parameter_explicit(parameters, parameter_name):
            continue
        fallback = _template_default_candidate(name, parameter_name, template_ir, properties)
        candidates = _parameter_evidence_candidates(
            parameter_name,
            _template_evidence_records(manifest, selected_scripts),
            task,
        )
        if fallback is None and not candidates:
            continue
        decision = infer_value(path, candidates=candidates, fallback=fallback)
        decisions.append(decision)
        decision_paths.add(path)

    resolved_request = apply_inference_decisions(resolved_request, decisions)
    resolved_parameters = _request_parameters(resolved_request)
    for parameter_name in required:
        value = resolved_parameters.get(parameter_name)
        if not _has_request_value(value):
            findings.append(
                _grounding_finding(
                    "template_parameter_required",
                    f"$.template.parameters.{parameter_name}",
                    f"{name} requires reviewed parameter {parameter_name!r}; no safe evidence or shape default was available.",
                    "Supply the reviewed value from the task or imported context.",
                )
            )
    findings.extend(_validate_resolved_template_parameters(name, parameters, resolved_parameters, properties))
    if workspace is None and not any(item.get("code") == "worktable_not_found" for item in findings):
        findings.append(
            _grounding_finding(
                "worktable_not_found",
                "$.template.parameters.worktable",
                "No exact imported worktable was available for grounding.",
                "Import a full ZEIA containing one selected worktable or provide its exact name/GUID.",
            )
        )

    return {
        "resolved_request": resolved_request,
        "decisions": decisions,
        "findings": findings,
    }


def _ground_inventory_template(
    name: str,
    *,
    request_spec: Mapping[str, Any],
    manifest: Mapping[str, Any],
    context_name: str,
    templates_dir: Path,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    template_request = request_spec.get("template")
    raw_parameters = template_request.get("parameters") if isinstance(template_request, Mapping) else None
    parameters = raw_parameters if isinstance(raw_parameters, Mapping) else {}
    if not isinstance(template_request, Mapping) or template_request.get("name") != name:
        findings.append(
            _grounding_finding(
                "template_request_mismatch",
                "$.template.name",
                f"The reviewed request spec does not select {name}.",
                f"Set template.name to {name} and review the request spec again.",
            )
        )

    schema_parameters = (
        load_request_schema(name, templates_dir)
        .get("properties", {})
        .get("template", {})
        .get("properties", {})
        .get("parameters", {})
    )
    required_parameters = (
        schema_parameters.get("required", [])
        if isinstance(schema_parameters, Mapping)
        else []
    )
    for parameter_name in required_parameters:
        value = parameters.get(parameter_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            findings.append(
                _grounding_finding(
                    "template_parameter_required",
                    f"$.template.parameters.{parameter_name}",
                    f"{name} requires reviewed parameter {parameter_name!r}; no default is safe.",
                    "Supply the reviewed value, using exact imported-context evidence where applicable.",
                )
            )

    labware_parameters = _TEMPLATE_LABWARE_PARAMETERS[name]
    workspace = _resolve_workspace(manifest, parameters.get("worktable"), findings)
    records = workspace_labware_records(workspace) if workspace is not None else []
    resolved_labware: dict[str, dict[str, Any]] = {}
    for labware_id, parameter_name in labware_parameters.items():
        requested = str(parameters.get(parameter_name) or "").strip()
        if requested:
            record = _resolve_labware_record(records, requested, parameter_name, findings)
            if record is not None:
                resolved_labware[labware_id] = record

    template_ir = load_template_ir(name, templates_dir)
    needs_liquid_class = bool(template_ir.get("liquid_classes")) or any(
        isinstance(step, Mapping) and "liquid_class" in step
        for step in template_ir.get("steps") or []
    )
    requested_liquid_class = str(parameters.get("liquid_class") or "").strip()
    if needs_liquid_class and requested_liquid_class and requested_liquid_class not in _context_liquid_classes(manifest):
        findings.append(
            _grounding_finding(
                "liquid_class_not_in_context",
                "$.template.parameters.liquid_class",
                f"Liquid class {requested_liquid_class!r} is not present in the imported context.",
                "Choose an exact liquid-class name from the imported context.",
            )
        )

    requested_worklist = str(parameters.get("worklist_path") or "").strip()
    if name == "worklist_execution":
        available_worklists = {
            str(item) for item in manifest.get("worklist_paths") or [] if str(item).strip()
        }
        if requested_worklist and requested_worklist not in available_worklists:
            findings.append(
                _grounding_finding(
                    "worklist_not_in_context",
                    "$.template.parameters.worklist_path",
                    f"Worklist {requested_worklist!r} is not present in the imported context.",
                    "Choose an exact imported worklist path.",
                )
            )

    if findings:
        return _grounding_result(name, context_name, None, findings)

    ir = deepcopy(template_ir)
    protocol_request = request_spec.get("request")
    if isinstance(protocol_request, Mapping) and str(protocol_request.get("protocol_name") or "").strip():
        ir["protocol"]["name"] = str(protocol_request["protocol_name"]).strip()
    workspace_name = str(workspace.get("name") or workspace.get("object_name") or "").strip()
    workspace_guid = str(workspace.get("guid") or "").strip()
    ir["worktable"] = {"name": workspace_name, "guid": workspace_guid, "auto_place": False}

    label_rewrites: dict[str, str] = {}
    grounded_labware: list[dict[str, Any]] = []
    for item in ir.get("labware") or []:
        if not isinstance(item, Mapping):
            continue
        labware_id = str(item.get("id") or "")
        record = resolved_labware[labware_id]
        old_label = str(item.get("label") or "")
        new_label = str(record["label"])
        label_rewrites[old_label] = new_label
        grounded_labware.append(
            {
                "id": labware_id,
                "label": new_label,
                "catalog": record["catalog"],
                "location": record["location"],
                "position": record["position"],
                "role": item.get("role"),
                "workspace": workspace_name,
                "workspace_guid": workspace_guid,
            }
        )
    ir["labware"] = grounded_labware
    ir["liquid_classes"] = (
        [{"name": requested_liquid_class, "role": "pipetting"}]
        if requested_liquid_class
        else []
    )

    variable_parameters: dict[Any, str] = {}
    for variable in ir.get("variables") or []:
        if not isinstance(variable, dict):
            continue
        variable_name = str(variable.get("name") or "")
        old_default = variable.get("default", variable.get("default_value"))
        if variable_name in parameters:
            variable["default_value"] = parameters[variable_name]
            variable.pop("default", None)
            variable_parameters[old_default] = variable_name

    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        old_target = str(step.get("target_labware") or "")
        for key in ("target_labware", "source_labware", "destination_labware"):
            if step.get(key) in label_rewrites:
                step[key] = label_rewrites[step[key]]
        if step.get("operation") == "add_labware" and old_target in label_rewrites:
            new_label = label_rewrites[old_target]
            record = next(
                value for value in resolved_labware.values() if str(value["label"]) == new_label
            )
            step["parameters"] = {
                "label": record["label"],
                "labware_type": record["catalog"],
                "location": record["location"],
                "position": record["position"],
            }
        if "liquid_class" in step:
            step["liquid_class"] = requested_liquid_class
        old_volume = step.get("volume_ul")
        variable_name = variable_parameters.get(old_volume)
        if variable_name:
            value = parameters[variable_name]
            step["volume_ul"] = value
            step["volume_ul_expression"] = {"kind": "number_literal", "value": value}
        params = step.get("parameters")
        if isinstance(params, dict):
            old_cycles = params.get("cycles")
            cycle_variable = variable_parameters.get(old_cycles)
            if cycle_variable and "cycles" in cycle_variable:
                cycle_value = parameters[cycle_variable]
                params["cycles"] = cycle_value
                params["cycles_expression"] = {"kind": "number_literal", "value": cycle_value}
            if step.get("operation") == "read_worklist":
                params["path"] = requested_worklist
                params["worklist_name"] = Path(requested_worklist).stem
            if step.get("operation") == "wash" and str(parameters.get("wash_scheme") or "").strip():
                params["scheme"] = str(parameters["wash_scheme"]).strip()
            if "tip_strategy" in params and str(parameters.get("tip_strategy") or "").strip():
                params["tip_strategy"] = str(parameters["tip_strategy"]).strip()

    if name == "worklist_execution":
        ir["worklists"] = [
            {
                "format": str(parameters.get("worklist_format") or "gwl"),
                "name": Path(requested_worklist).stem,
                "path": requested_worklist,
                "required": True,
            }
        ]
    ir["source"].update(
        {
            "format": "grounded_template_protocol",
            "context": context_name,
            "worktable": workspace_name,
            "worktable_guid": workspace_guid,
        }
    )
    validation_issues = [
        issue.as_dict() if hasattr(issue, "as_dict") else dict(issue)
        for issue in validate_protocol_ir_document(ir)
    ]
    for issue in validation_issues:
        if issue.get("severity") == "error":
            findings.append(
                _grounding_finding(
                    "grounded_ir_invalid",
                    str(issue.get("path") or "$"),
                    str(issue.get("message") or "Grounded protocol IR is invalid."),
                    "Review the imported context and request parameters before generating.",
                )
            )
    return _grounding_result(name, context_name, None if findings else ir, findings)


def _ground_plate_transfer(
    *,
    request_spec: Mapping[str, Any],
    manifest: Mapping[str, Any],
    context_name: str,
    templates_dir: Path,
) -> dict[str, Any]:
    findings: list[dict[str, str]] = []
    template_request = request_spec.get("template")
    if not isinstance(template_request, Mapping) or template_request.get("name") != "plate_transfer":
        findings.append(
            _grounding_finding(
                "template_request_mismatch",
                "$.template.name",
                "The reviewed request spec does not select plate_transfer.",
                "Set template.name to plate_transfer and review the request spec again.",
            )
        )
        parameters: Mapping[str, Any] = {}
    else:
        raw_parameters = template_request.get("parameters")
        parameters = raw_parameters if isinstance(raw_parameters, Mapping) else {}

    required_parameters = (
        "source_plate",
        "destination_plate",
        "source_well",
        "destination_well",
        "transfer_volume_ul",
        "liquid_class",
        "tip_box",
    )
    for key in required_parameters:
        value = parameters.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            findings.append(
                _grounding_finding(
                    "template_parameter_required",
                    f"$.template.parameters.{key}",
                    f"plate_transfer requires {key!r}; no default is safe.",
                    f"Choose the exact {key.replace('_', ' ')} from the imported context.",
                )
            )

    volume = parameters.get("transfer_volume_ul")
    if isinstance(volume, bool) or not isinstance(volume, (int, float)) or volume <= 0:
        findings.append(
            _grounding_finding(
                "transfer_volume_invalid",
                "$.template.parameters.transfer_volume_ul",
                "Transfer volume must be a positive number supplied by the reviewed request.",
                "Enter the reviewed transfer volume in microliters.",
            )
        )

    workspace = _resolve_workspace(manifest, parameters.get("worktable"), findings)
    records = workspace_labware_records(workspace) if workspace is not None else []
    resolved_labware: dict[str, dict[str, Any]] = {}
    for key in ("source_plate", "destination_plate", "tip_box"):
        requested = str(parameters.get(key) or "").strip()
        if requested:
            record = _resolve_labware_record(records, requested, key, findings)
            if record is not None:
                resolved_labware[key] = record

    requested_liquid_class = str(parameters.get("liquid_class") or "").strip()
    available_liquid_classes = _context_liquid_classes(manifest)
    if requested_liquid_class and requested_liquid_class not in available_liquid_classes:
        findings.append(
            _grounding_finding(
                "liquid_class_not_in_context",
                "$.template.parameters.liquid_class",
                f"Liquid class {requested_liquid_class!r} is not present in the imported context.",
                "Choose an exact liquid-class name from the imported context.",
            )
        )

    if findings:
        return _grounding_result("plate_transfer", context_name, None, findings)

    ir = deepcopy(load_template_ir("plate_transfer", templates_dir))
    protocol_request = request_spec.get("request")
    if isinstance(protocol_request, Mapping) and str(protocol_request.get("protocol_name") or "").strip():
        ir["protocol"]["name"] = str(protocol_request["protocol_name"]).strip()

    workspace_name = str(workspace.get("name") or workspace.get("object_name") or "").strip()
    workspace_guid = str(workspace.get("guid") or "").strip()
    ir["worktable"] = {"name": workspace_name, "guid": workspace_guid, "auto_place": False}

    logical_labels = {
        "source_plate": "source_plate",
        "destination_plate": "destination_plate",
        "tip_box": "tips",
    }
    grounded_labels: dict[str, str] = {}
    grounded_labware: list[dict[str, Any]] = []
    for parameter_name, logical_label in logical_labels.items():
        record = resolved_labware[parameter_name]
        label = str(record["label"])
        grounded_labels[logical_label] = label
        grounded_labware.append(
            {
                "id": logical_label,
                "label": label,
                "catalog": record["catalog"],
                "location": record["location"],
                "position": record["position"],
                "role": "tips" if parameter_name == "tip_box" else parameter_name.removesuffix("_plate"),
                "workspace": workspace_name,
                "workspace_guid": workspace_guid,
            }
        )
    ir["labware"] = grounded_labware
    ir["liquid_classes"] = [{"name": requested_liquid_class, "role": "pipetting"}]

    source_well = str(parameters["source_well"]).strip()
    destination_well = str(parameters["destination_well"]).strip()
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for key in ("target_labware", "source_labware", "destination_labware"):
            logical_label = step.get(key)
            if logical_label in grounded_labels:
                step[key] = grounded_labels[logical_label]
        step_id = step.get("id")
        if step_id in {"step_001", "step_002", "step_003"}:
            parameter_name = {
                "step_001": "source_plate",
                "step_002": "destination_plate",
                "step_003": "tip_box",
            }[step_id]
            record = resolved_labware[parameter_name]
            step["parameters"] = {
                "label": record["label"],
                "labware_type": record["catalog"],
                "location": record["location"],
                "position": record["position"],
            }
        elif step_id == "step_005":
            step["liquid_class"] = requested_liquid_class
            step["volume_ul"] = volume
            step["volume_ul_expression"] = {"kind": "number_literal", "value": volume}
            step["parameters"] = {"source_well": source_well}
        elif step_id == "step_006":
            step["liquid_class"] = requested_liquid_class
            step["volume_ul"] = volume
            step["volume_ul_expression"] = {"kind": "number_literal", "value": volume}
            step["parameters"] = {"destination_well": destination_well}

    ir["variables"] = [
        {"name": "transfer_volume_ul", "type": "number", "default_value": volume},
        {"name": "source_well", "type": "well", "default_value": source_well},
        {"name": "destination_well", "type": "well", "default_value": destination_well},
    ]
    ir["source"].update(
        {
            "format": "grounded_template_protocol",
            "context": context_name,
            "worktable": workspace_name,
            "worktable_guid": workspace_guid,
        }
    )

    validation_issues = [
        issue.as_dict() if hasattr(issue, "as_dict") else dict(issue)
        for issue in validate_protocol_ir_document(ir)
    ]
    for issue in validation_issues:
        if issue.get("severity") == "error":
            findings.append(
                _grounding_finding(
                    "grounded_ir_invalid",
                    str(issue.get("path") or "$"),
                    str(issue.get("message") or "Grounded protocol IR is invalid."),
                    "Review the imported context and request parameters before generating.",
                )
            )
    return _grounding_result(
        "plate_transfer",
        context_name,
        None if findings else ir,
        findings,
    )


def _template_parameter_schema(name: str) -> Mapping[str, Any]:
    schema = load_request_schema(name)
    parameters = (
        schema.get("properties", {})
        .get("template", {})
        .get("properties", {})
        .get("parameters", {})
    )
    return parameters if isinstance(parameters, Mapping) else {}


def _request_intent(request_spec: Mapping[str, Any]) -> str:
    request = request_spec.get("request")
    if not isinstance(request, Mapping):
        return ""
    for key in ("verbatim_prompt", "original_user_prompt", "intent"):
        value = str(request.get(key) or "").strip()
        if value:
            return value
    return ""


def _request_parameters(request_spec: Mapping[str, Any]) -> dict[str, Any]:
    template = request_spec.get("template")
    if not isinstance(template, Mapping):
        return {}
    parameters = template.get("parameters")
    return dict(parameters) if isinstance(parameters, Mapping) else {}


def _parameter_explicit(parameters: Mapping[str, Any], name: str) -> bool:
    return name in parameters and _has_request_value(parameters.get(name))


def _has_request_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _context_workspaces(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    geometry = manifest.get("worktable_geometry")
    geometry_workspaces = geometry.get("workspaces") if isinstance(geometry, Mapping) else None
    raw = geometry_workspaces if isinstance(geometry_workspaces, list) else manifest.get("workspaces")
    return [item for item in (raw or []) if isinstance(item, Mapping)]


def _selected_script_records(
    manifest: Mapping[str, Any],
    request_spec: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    source = request_spec.get("source")
    requested = {
        str(value).strip().casefold()
        for value in ((source.get("source_scripts") or []) if isinstance(source, Mapping) else [])
        if str(value).strip()
    }
    if not requested:
        return []
    selected: list[Mapping[str, Any]] = []
    for script in manifest.get("scripts") or []:
        if not isinstance(script, Mapping):
            continue
        identities = {
            str(script.get(key) or "").strip().casefold()
            for key in ("name", "object_name", "qualified_name", "entry", "extracted_path")
            if str(script.get(key) or "").strip()
        }
        if requested.intersection(identities):
            selected.append(script)
    return selected


def _worktable_candidates(
    workspaces: list[Mapping[str, Any]],
    selected_scripts: list[Mapping[str, Any]],
) -> tuple[InferenceCandidate, ...]:
    by_guid = {
        str(item.get("guid") or "").strip().casefold(): item
        for item in workspaces
        if str(item.get("guid") or "").strip()
    }
    candidates: list[InferenceCandidate] = []
    for script in selected_scripts:
        for ref in script.get("references") or []:
            if not isinstance(ref, Mapping):
                continue
            if str(ref.get("type_id") or "").strip() != "WorktableWorkspace":
                continue
            name = str(ref.get("object_name") or ref.get("name") or "").strip()
            guid = str(ref.get("guid") or "").strip()
            workspace = by_guid.get(guid.casefold()) if guid else None
            value = name or str((workspace or {}).get("name") or (workspace or {}).get("object_name") or guid).strip()
            if value:
                candidates.append(
                    InferenceCandidate(
                        value=value,
                        origin=InferenceOrigin.EXACT_SOURCE,
                        source=str(script.get("qualified_name") or script.get("object_name") or "selected_script"),
                        reason="The selected source script references this worktable.",
                        priority=500,
                        evidence={"guid": guid} if guid else {},
                    )
                )
        dependencies = script.get("dependencies")
        if not isinstance(dependencies, Mapping):
            continue
        for raw_guid in dependencies.get("workspace_guids") or []:
            guid = str(raw_guid or "").strip()
            workspace = by_guid.get(guid.casefold())
            value = str((workspace or {}).get("name") or (workspace or {}).get("object_name") or guid).strip()
            if value:
                candidates.append(
                    InferenceCandidate(
                        value=value,
                        origin=InferenceOrigin.EXACT_SOURCE,
                        source=str(script.get("qualified_name") or script.get("object_name") or "selected_script"),
                        reason="The selected source script depends on this worktable GUID.",
                        priority=490,
                        evidence={"guid": guid},
                    )
                )
    return tuple(candidates)


def _context_fallback_candidates(
    records: list[Mapping[str, Any]],
    *,
    value_key: str,
    source_prefix: str,
    reason: str,
    priority: int,
) -> tuple[InferenceCandidate, ...]:
    return records_to_candidates(
        records,
        value_keys=(value_key, "object_name", "guid", "label"),
        origin=InferenceOrigin.CONTEXT_FALLBACK,
        source_prefix=source_prefix,
        reason=reason,
        priority=priority,
    )


def _worktable_fallback_candidates(
    workspaces: list[Mapping[str, Any]],
    *,
    parameters: Mapping[str, Any],
    labware_parameters: Mapping[str, str],
) -> tuple[InferenceCandidate, ...]:
    requested_labels = {
        str(parameters.get(parameter_name) or "").strip()
        for parameter_name in labware_parameters.values()
        if str(parameters.get(parameter_name) or "").strip()
    }
    candidates: list[InferenceCandidate] = []
    for index, workspace in enumerate(workspaces):
        value = str(workspace.get("name") or workspace.get("object_name") or workspace.get("guid") or "").strip()
        if not value:
            continue
        records = workspace_labware_records(workspace)
        labels = {str(item.get("label") or "").strip() for item in records}
        coverage = len(requested_labels.intersection(labels))
        complete_count = sum(1 for item in records if _complete_labware_record(item))
        candidates.append(
            InferenceCandidate(
                value=value,
                origin=InferenceOrigin.CONTEXT_FALLBACK,
                source=str(workspace.get("qualified_name") or workspace.get("guid") or f"context:worktable:{index}"),
                reason="Choose the imported worktable covering the most requested labware.",
                priority=100 + coverage * 100,
                score=min(complete_count, 99),
                evidence={
                    "guid": workspace.get("guid"),
                    "requested_labware_coverage": coverage,
                    "complete_placement_count": complete_count,
                },
            )
        )
    return tuple(candidates)


def _complete_labware_record(record: Mapping[str, Any]) -> bool:
    return bool(
        str(record.get("label") or "").strip()
        and str(record.get("catalog") or "").strip()
        and record.get("location") not in (None, "")
        and record.get("position") not in (None, "")
    )


def _labware_candidates(
    records: list[dict[str, Any]],
    selected_scripts: list[Mapping[str, Any]],
    *,
    task: str,
    excluded: set[str],
) -> tuple[InferenceCandidate, ...]:
    available = [item for item in records if str(item.get("label") or "").strip() not in excluded]
    source_labels: set[str] = set()
    for script in selected_scripts:
        dependencies = script.get("dependencies")
        if not isinstance(dependencies, Mapping):
            continue
        for key in ("labware_names", "rack_labels", "tip_boxes"):
            for value in dependencies.get(key) or []:
                if str(value or "").strip():
                    source_labels.add(str(value).strip())
    exact = [
        InferenceCandidate(
            value=str(item["label"]),
            origin=InferenceOrigin.EXACT_SOURCE,
            source="selected_script:labware_dependency",
            reason="The selected source script uses this exact labware label.",
            priority=450,
            evidence={key: item[key] for key in ("catalog", "location", "position") if key in item},
        )
        for item in available
        if str(item.get("label") or "") in source_labels
    ]
    contextual = records_to_candidates(
        available,
        value_keys=("label",),
        origin=InferenceOrigin.CONTEXT_ROLE,
        source_prefix="worktable:labware",
        reason="The imported worktable label/catalog best matches this template role.",
        intent=task,
        priority=200,
    )
    return tuple((*exact, *contextual))


def _liquid_class_candidates(
    manifest: Mapping[str, Any],
    selected_scripts: list[Mapping[str, Any]],
    task: str,
) -> tuple[InferenceCandidate, ...]:
    source_names: set[str] = set()
    for script in selected_scripts:
        dependencies = script.get("dependencies")
        if not isinstance(dependencies, Mapping):
            continue
        source_names.update(
            str(value).strip()
            for value in dependencies.get("liquid_classes") or []
            if str(value or "").strip()
        )
    records = []
    for item in manifest.get("liquid_classes") or []:
        if isinstance(item, Mapping):
            records.append(dict(item))
        elif str(item or "").strip():
            records.append({"name": str(item).strip()})
    exact = [
        InferenceCandidate(
            value=str(item.get("name") or item.get("object_name")),
            origin=InferenceOrigin.EXACT_SOURCE,
            source="selected_script:liquid_class_dependency",
            reason="The selected source script uses this liquid class.",
            priority=450,
        )
        for item in records
        if str(item.get("name") or item.get("object_name") or "").strip() in source_names
    ]
    contextual = records_to_candidates(
        records,
        value_keys=("name", "object_name"),
        origin=InferenceOrigin.CONTEXT_ROLE,
        source_prefix="context:liquid_class",
        reason="The imported liquid-class name best matches the requested task.",
        intent=task,
        priority=200,
    )
    return tuple((*exact, *contextual))


def _worklist_candidates(
    manifest: Mapping[str, Any],
    selected_scripts: list[Mapping[str, Any]],
    task: str,
) -> tuple[InferenceCandidate, ...]:
    source_refs: set[str] = set()
    for script in selected_scripts:
        dependencies = script.get("dependencies")
        if not isinstance(dependencies, Mapping):
            continue
        for value in dependencies.get("external_or_worklist_refs") or []:
            text = str(value or "").strip()
            if text.lower().endswith((".gwl", ".csv", ".tsv")):
                source_refs.add(text)
    paths = [str(value).strip() for value in manifest.get("worklist_paths") or [] if str(value).strip()]
    exact = [
        InferenceCandidate(
            value=value,
            origin=InferenceOrigin.EXACT_SOURCE,
            source="selected_script:worklist_dependency",
            reason="The selected source script references this worklist.",
            priority=450,
        )
        for value in paths
        if value in source_refs
    ]
    contextual = records_to_candidates(
        [{"path": value} for value in paths],
        value_keys=("path",),
        origin=InferenceOrigin.CONTEXT_ROLE,
        source_prefix="context:worklist",
        reason="The imported worklist path best matches the requested task.",
        intent=task,
        priority=200,
    )
    return tuple((*exact, *contextual))


def _template_default_candidate(
    template_name: str,
    parameter_name: str,
    template_ir: Mapping[str, Any],
    properties: Mapping[str, Any],
) -> InferenceCandidate | None:
    resource_parameters = {
        "worktable",
        "liquid_class",
        "worklist_path",
        *_TEMPLATE_LABWARE_PARAMETERS.get(template_name, {}).values(),
    }
    if parameter_name in resource_parameters:
        return None
    property_schema = properties.get(parameter_name)
    property_schema = property_schema if isinstance(property_schema, Mapping) else {}
    value = property_schema.get("default", _MISSING)
    if value is _MISSING:
        for variable in template_ir.get("variables") or []:
            if not isinstance(variable, Mapping) or str(variable.get("name") or "") != parameter_name:
                continue
            candidate = variable.get("default_value", variable.get("default", _MISSING))
            if _has_request_value(candidate):
                value = candidate
                break
    if value is _MISSING:
        value = _shape_default(parameter_name, property_schema)
    if value is _MISSING:
        return None
    return InferenceCandidate(
        value=value,
        origin=InferenceOrigin.TEMPLATE_DEFAULT,
        source=f"template:{template_name}",
        reason="Reuse a deterministic, site-agnostic template-shape default.",
        priority=50,
    )


def _shape_default(parameter_name: str, property_schema: Mapping[str, Any]) -> Any:
    enum = property_schema.get("enum")
    if isinstance(enum, list) and enum:
        return deepcopy(enum[0])
    raw_type = property_schema.get("type")
    types = [str(item) for item in raw_type] if isinstance(raw_type, list) else [str(raw_type or "")]
    value_type = next((item for item in types if item != "null"), "")
    if value_type == "boolean":
        return False
    if value_type in {"number", "integer"}:
        if property_schema.get("minimum") is not None:
            value = property_schema["minimum"]
        elif property_schema.get("exclusiveMinimum") is not None:
            value = property_schema["exclusiveMinimum"] + 1
        else:
            value = 1
        return int(value) if value_type == "integer" else value
    if value_type == "string":
        lowered = parameter_name.casefold()
        if "well" in lowered:
            return "A1"
        if "format" in lowered:
            return "gwl"
        if "path" in lowered or "map" in lowered:
            return f"inferred/{parameter_name}.csv"
        return parameter_name.replace("_", " ").title()
    return _MISSING


def _template_evidence_records(
    manifest: Mapping[str, Any],
    selected_scripts: list[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    records = list(selected_scripts)
    for key in ("pattern_windows", "patterns", "request_evidence"):
        records.extend(item for item in manifest.get(key) or [] if isinstance(item, Mapping))
    return records


def _parameter_evidence_candidates(
    parameter_name: str,
    records: list[Mapping[str, Any]],
    task: str,
) -> tuple[InferenceCandidate, ...]:
    wanted = re.sub(r"[^a-z0-9]", "", parameter_name.casefold())
    candidates: list[InferenceCandidate] = []
    for index, record in enumerate(records):
        for key, value in _walk_mapping_values(record):
            normalized = re.sub(r"[^a-z0-9]", "", key.casefold())
            if normalized != wanted or not _has_request_value(value):
                continue
            candidates.append(
                InferenceCandidate(
                    value=deepcopy(value),
                    origin=InferenceOrigin.EXACT_SOURCE,
                    source=str(record.get("qualified_name") or record.get("object_name") or f"evidence:{index}"),
                    reason=f"Selected source evidence supplies {parameter_name}.",
                    priority=400,
                    score=20 if parameter_name.casefold() in task.casefold() else 0,
                )
            )
    return tuple(candidates)


def _walk_mapping_values(value: Any) -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            found.append((str(key), item))
            if isinstance(item, (Mapping, list, tuple)):
                found.extend(_walk_mapping_values(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_walk_mapping_values(item))
    return found


def _validate_resolved_template_parameters(
    template_name: str,
    original: Mapping[str, Any],
    resolved: Mapping[str, Any],
    properties: Mapping[str, Any],
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for name, raw_schema in properties.items():
        if name not in original or not _has_request_value(original.get(name)):
            continue
        schema = raw_schema if isinstance(raw_schema, Mapping) else {}
        value = resolved.get(name)
        valid = True
        allowed = schema.get("enum")
        if isinstance(allowed, list) and value not in allowed:
            valid = False
        raw_type = schema.get("type")
        types = set(raw_type if isinstance(raw_type, list) else [raw_type])
        types.discard(None)
        if "number" in types and (isinstance(value, bool) or not isinstance(value, (int, float))):
            valid = False
        if "integer" in types and (isinstance(value, bool) or not isinstance(value, int)):
            valid = False
        if "boolean" in types and not isinstance(value, bool):
            valid = False
        if "string" in types and not isinstance(value, str):
            valid = False
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if schema.get("minimum") is not None and value < schema["minimum"]:
                valid = False
            if schema.get("exclusiveMinimum") is not None and value <= schema["exclusiveMinimum"]:
                valid = False
        if not valid:
            findings.append(
                _grounding_finding(
                    "template_parameter_invalid",
                    f"$.template.parameters.{name}",
                    f"Explicit parameter {name!r} is incompatible with the {template_name} request schema.",
                    "Correct the explicit value; automatic inference never overrides reviewed input.",
                )
            )
    return findings


def _context_manifest(context: Any) -> tuple[Mapping[str, Any], str]:
    if isinstance(context, Mapping):
        manifest = context.get("manifest") if isinstance(context.get("manifest"), Mapping) else context
        name = str(context.get("name") or manifest.get("name") or "").strip()
    else:
        raw_manifest = getattr(context, "manifest", None)
        manifest = raw_manifest if isinstance(raw_manifest, Mapping) else {}
        name = str(getattr(context, "name", None) or manifest.get("name") or "").strip()
    return manifest, name


def _resolve_workspace(
    manifest: Mapping[str, Any],
    requested: Any,
    findings: list[dict[str, str]],
) -> Mapping[str, Any] | None:
    geometry = manifest.get("worktable_geometry")
    geometry_workspaces = geometry.get("workspaces") if isinstance(geometry, Mapping) else None
    raw_workspaces = geometry_workspaces if isinstance(geometry_workspaces, list) else manifest.get("workspaces")
    workspaces = [item for item in (raw_workspaces or []) if isinstance(item, Mapping)]
    requested_name = str(requested or "").strip()
    if requested_name:
        matches = [
            item
            for item in workspaces
            if requested_name in {str(item.get("name") or ""), str(item.get("object_name") or ""), str(item.get("guid") or "")}
        ]
    else:
        matches = workspaces
    if len(matches) == 1:
        workspace = matches[0]
        missing = [key for key in ("name", "guid") if not str(workspace.get(key) or "").strip()]
        if missing:
            findings.append(
                _grounding_finding(
                    "worktable_identity_incomplete",
                    "$.source.context.worktable",
                    f"The imported worktable is missing exact {', '.join(missing)} evidence.",
                    "Import a full ZEIA containing the referenced worktable identity.",
                )
            )
            return None
        return workspace
    if not matches:
        message = (
            f"Worktable {requested_name!r} was not found in the imported context."
            if requested_name
            else "No worktable was found in the imported context."
        )
        action = "Choose an exact imported worktable name or import a full ZEIA containing it."
        code = "worktable_not_found"
    else:
        message = "The imported context contains multiple worktables and the request does not select exactly one."
        action = "Set template.parameters.worktable to one exact imported worktable name or GUID."
        code = "worktable_ambiguous"
    findings.append(_grounding_finding(code, "$.template.parameters.worktable", message, action))
    return None


def _resolve_labware_record(
    records: list[dict[str, Any]],
    requested: str,
    parameter_name: str,
    findings: list[dict[str, str]],
) -> dict[str, Any] | None:
    matches = [item for item in records if str(item.get("label") or "") == requested]
    if len(matches) != 1:
        findings.append(
            _grounding_finding(
                "labware_not_unique_in_worktable" if matches else "labware_not_in_worktable",
                f"$.template.parameters.{parameter_name}",
                (
                    f"Labware label {requested!r} appears more than once in the selected worktable."
                    if matches
                    else f"Labware label {requested!r} was not found in the selected imported worktable."
                ),
                "Choose one exact, unique labware label from the selected imported worktable.",
            )
        )
        return None
    record = matches[0]
    missing = [key for key in ("catalog", "location", "position") if record.get(key) in (None, "")]
    if missing:
        findings.append(
            _grounding_finding(
                "labware_placement_incomplete",
                f"$.template.parameters.{parameter_name}",
                f"Imported labware {requested!r} is missing {', '.join(missing)} placement evidence.",
                "Import a full ZEIA with complete worktable placement metadata.",
            )
        )
        return None
    return record


def _context_liquid_classes(manifest: Mapping[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in manifest.get("liquid_classes") or []:
        if isinstance(item, Mapping):
            value = item.get("name") or item.get("object_name")
        else:
            value = item
        if str(value or "").strip():
            names.add(str(value).strip())
    return names


def _grounding_finding(code: str, path: str, message: str, next_action: str) -> dict[str, str]:
    return {
        "code": code,
        "severity": "error",
        "path": path,
        "message": message,
        "next_action": next_action,
    }


def _grounding_result(
    template_name: str,
    context_name: str,
    ir: dict[str, Any] | None,
    findings: list[dict[str, str]],
    *,
    resolved_request: Mapping[str, Any] | None = None,
    inference_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "status": "needs_user" if findings else "grounded",
        "template": template_name,
        "context": context_name or None,
        "ir": ir,
        "findings": findings,
        "resolved_request": deepcopy(dict(resolved_request)) if isinstance(resolved_request, Mapping) else None,
        "inference": deepcopy(dict(inference_report)) if isinstance(inference_report, Mapping) else None,
    }
