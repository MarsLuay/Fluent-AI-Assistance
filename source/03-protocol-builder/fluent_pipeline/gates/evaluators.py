"""Canonical readiness-gate evaluator implementations.

This module owns the evaluator logic previously kept in validation.py.  The
validation module remains a compatibility/orchestration facade.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any, Mapping

from fluentcoder.expressions import (
    canonical_expression_key,
    expression_fields_for_command,
    expression_from_mapping,
    expression_inventory_from_xscr_text,
    parse_or_preserve_source_expression,
    render_expression,
    walk_expression_values,
)

from .. import xml_compat as ET
from ..aliases import alias_candidates, load_alias_maps, resolve_alias
from ..api_v2.add_labware_golden import enrich_compiled_inventory_with_golden_compare
from ..api_v2.command_validate import validate_compiled_xscr_commands
from ..api_v2.generic_command_validate import validate_passthrough_commands_from_xscr
from ..api_v2.xml_compare import NON_EXECUTABLE_OBJECT_TYPES
from ..checksums import checksum_bridge_available, entry_checksum_state
from ..command_registry import registry_command_operation, registry_command_support_status
from ..liquid_state import validate_liquid_state
from ..protocol_ir import (
    load_protocol_ir,
    prompt_step_media_path,
    prompt_step_worktable_media_path,
    protocol_ir_from_python,
    protocol_ir_from_xscr,
    render_recreate_markdown,
)
from ..protocol_ir_schema import (
    LIQUID_CLASS_OPERATIONS,
    VOLUME_OPERATIONS,
    migrate_protocol_ir,
)
from ..subroutine_dependencies import (
    resolve_subroutine_dependencies,
    validate_compiled_subroutine_references,
)
from .models import GateFactory, GateRecord, ValidationContext


# Artifact and workflow gates (Gates 3-10, 18, and 27).
def evaluate_labware(context: ValidationContext) -> GateRecord:
    diff = context.worktable_diff
    if diff is None:
        return context.make_gate("labware_resolves", "failed", "Labware could not be checked without valid protocol IR.")
    missing = [item for item in diff.get("missing_labware") or [] if item.get("status") != "available"]
    if missing:
        return context.make_gate("labware_resolves", "failed", "Some required labware names are not resolved.", {"labware": missing})
    return context.make_gate("labware_resolves", "passed", "All required labware names resolve in the source context.")

def evaluate_liquid_classes(context: ValidationContext) -> GateRecord:
    diff = context.worktable_diff
    if diff is None:
        return context.make_gate("liquid_classes_resolve", "failed", "Liquid classes could not be checked without valid protocol IR.")
    missing = [item for item in diff.get("required_liquid_classes") or [] if item.get("status") != "available"]
    if missing:
        return context.make_gate("liquid_classes_resolve", "failed", "Some required liquid classes are missing or unverified.", {"liquid_classes": missing})
    return context.make_gate("liquid_classes_resolve", "passed", "All required liquid classes resolve.")

def evaluate_worklists(context: ValidationContext) -> GateRecord:
    diff = context.worktable_diff
    worklist = context.worklist
    if diff is None:
        return context.make_gate("worklist_paths_valid", "failed", "Worklist paths could not be checked without valid protocol IR.")
    missing = [item for item in diff.get("worklist_paths") or [] if item.get("status") != "available"]
    if missing and not (worklist and worklist.exists()):
        return context.make_gate("worklist_paths_valid", "failed", "Some required worklist paths are missing or unverified.", {"worklists": missing})
    return context.make_gate("worklist_paths_valid", "passed", "All required worklist paths are available or no worklists are required.")

def evaluate_python_draft(context: ValidationContext) -> GateRecord:
    draft_path = context.draft_path
    if draft_path is None or not draft_path.exists():
        return context.make_gate("python_draft_generated", "failed", "Python draft was not generated.")
    try:
        tree = ast.parse(draft_path.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return context.make_gate("python_draft_generated", "failed", f"Python draft has syntax errors: {exc}")
    has_build = any(isinstance(node, ast.FunctionDef) and node.name == "build_worktable" for node in tree.body)
    if not has_build:
        return context.make_gate("python_draft_generated", "failed", "Python draft does not define build_worktable().")
    return context.make_gate("python_draft_generated", "passed", "Python draft exists and defines build_worktable().")

def evaluate_simulation(context: ValidationContext) -> GateRecord:
    options = context.validation_options
    if "simulation_passed" in options:
        return context.make_gate(
            "simulation_passes",
            "passed" if options.get("simulation_passed") else "failed",
            "Simulation passed." if options.get("simulation_passed") else "Simulation did not pass.",
        )
    data = options.get("simulation")
    if isinstance(data, dict) and str(data.get("status") or "").lower() in {"ok", "passed", "pass", "success"} and not data.get("failure"):
        return context.make_gate("simulation_passes", "passed", "Simulation JSON reports a passing status.")
    return context.make_gate("simulation_passes", "failed", "No passing simulation result was provided.")

def evaluate_repair_plan(context: ValidationContext) -> GateRecord:
    options = context.validation_options
    plan = options.get("repair_plan")
    if plan is None:
        return context.make_gate("repair_plan_clear", "failed", "No repair plan was provided.")
    actions = plan.get("actions") or [] if isinstance(plan, dict) else []
    critical = [action for action in actions if action.get("status") == "needs_review"]
    if critical:
        return context.make_gate("repair_plan_clear", "failed", "Repair plan has unresolved needs_review actions.", {"actions": critical})
    return context.make_gate("repair_plan_clear", "passed", "Repair plan has no unresolved critical errors.")

def evaluate_xscr(context: ValidationContext) -> GateRecord:
    compiled_xscr = context.compiled_xscr
    options = context.validation_options
    compile_passed = options.get("compile_passed")
    if compile_passed is False:
        return context.make_gate("xscr_compiles", "failed", "Compile step failed.")
    if not compiled_xscr.exists() or compiled_xscr.stat().st_size == 0:
        return context.make_gate("xscr_compiles", "failed", "Compiled XSCR file is missing or empty.")
    return context.make_gate("xscr_compiles", "passed", "Compiled XSCR file exists.")

def evaluate_recreate(context: ValidationContext) -> GateRecord:
    ir = context.protocol_ir
    recreate_guide = context.recreate_guide
    if ir is None:
        return context.make_gate("recreate_matches_ir", "failed", "RECREATE_SCRIPT.md cannot be checked without valid protocol IR.")
    expected = render_recreate_markdown(
        ir,
        generated_files={
            "ir": "protocol.ir.json",
            "python": "protocol_draft.py",
            "xscr": "generated_script.xscr",
            "gwl": "generated_worklist.gwl",
        },
    )
    if recreate_guide is None or not recreate_guide.exists():
        return context.make_gate("recreate_matches_ir", "passed", "Bundle recreate guide will be generated directly from protocol IR.")
    actual = recreate_guide.read_text(encoding="utf-8")
    protocol_name = (ir.get("protocol") or {}).get("name") or "Generated protocol"
    required_fragments = [
        f"# Recreate Script: {protocol_name}",
        "This guide is generated from the same canonical protocol IR",
        "## Manual FluentControl Steps",
        "## IR Command Reference",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in actual]
    expected_command_count = len(ir.get("steps") or [])
    actual_command_count = actual.count("   - Command name:")
    if missing or actual_command_count != expected_command_count:
        return context.make_gate(
            "recreate_matches_ir",
            "failed",
            "Provided RECREATE_SCRIPT.md does not match the protocol IR structure.",
            {
                "missing_fragments": missing,
                "expected_command_count": expected_command_count,
                "actual_command_count": actual_command_count,
            },
        )
    if expected == actual:
        return context.make_gate("recreate_matches_ir", "passed", "Provided RECREATE_SCRIPT.md exactly matches protocol IR.")
    return context.make_gate("recreate_matches_ir", "passed", "Provided RECREATE_SCRIPT.md matches the protocol IR structure.")

def evaluate_liquid_state(context: ValidationContext) -> GateRecord:
    ir = context.domain_ir
    options = context.validation_options
    if ir is None:
        return context.make_gate("liquid_state_valid", "failed", "Liquid state cannot be checked without valid protocol IR.")
    report = options.get("liquid_state")
    if not isinstance(report, dict):
        report = validate_liquid_state(ir)
    details = {
        "status": report.get("status"),
        "failure_count": report.get("failure_count"),
        "unresolved_count": report.get("unresolved_count"),
        "warning_count": report.get("warning_count"),
        "failures": (report.get("failures") or [])[:20],
        "unresolved": (report.get("unresolved") or [])[:20],
        "assumptions": (report.get("assumptions") or [])[:20],
    }
    if report.get("status") == "passed":
        if _liquid_handling_step_count(ir) == 0:
            details = {**details, "trivial": True}
        return context.make_gate(
            "liquid_state_valid",
            "passed",
            report.get("summary") or "Robotools-style liquid state validation passed.",
            details,
        )
    return context.make_gate(
        "liquid_state_valid",
        "failed",
        report.get("summary") or "Robotools-style liquid state validation did not pass.",
        details,
    )

def evaluate_fluent_context_check(context: ValidationContext) -> GateRecord | None:
    options = context.validation_options
    required = bool(options.get("fluent_context_check_required"))
    report = options.get("fluent_context_check")
    if report is None and not required:
        return None
    if not isinstance(report, dict):
        return context.make_gate(
            "fluent_context_check",
            "failed",
            "The optional FluentControl import/load diagnostic was requested but no runtime result was provided.",
            {"required": required},
        )

    status = str(report.get("status") or "").lower()
    ok = bool(report.get("ok")) and status not in {"failed", "unavailable"}
    errors = [
        *[str(value) for value in (report.get("errors") or []) if str(value).strip()],
        *[str(value) for value in (report.get("runtime_errors") or []) if str(value).strip()],
    ]
    if ok and not errors:
        return context.make_gate(
            "fluent_context_check",
            "passed",
            report.get("summary") or "FluentControl import/load diagnostic passed.",
            _compact_fluent_context_details(report),
        )
    return context.make_gate(
        "fluent_context_check",
        "failed",
        report.get("summary") or "FluentControl import/load diagnostic did not pass.",
        _compact_fluent_context_details(report),
    )

def _compact_fluent_context_details(report: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "provider",
        "method",
        "simulation_mode",
        "status",
        "xscr_path",
        "state",
        "last_error",
        "errors",
        "runtime_errors",
        "diagnostics",
        "messages",
        "command",
        "returncode",
        "duration_seconds",
        "stdout_sample",
        "stderr_sample",
    )
    details = {key: report.get(key) for key in keys if report.get(key) not in (None, "", [], {})}
    nested = report.get("details")
    if isinstance(nested, dict):
        details["details"] = {
            key: value
            for key, value in nested.items()
            if value not in (None, "", [], {})
        }
    return details

def _liquid_handling_step_count(ir: dict[str, Any] | None) -> int:
    if not isinstance(ir, dict):
        return 0
    return sum(
        1
        for step in ir.get("steps") or []
        if isinstance(step, dict) and step.get("operation") in VOLUME_OPERATIONS
    )

# Compiled-artifact and domain gates (Gates 11-17).
def evaluate_post_compile_xscr(context: ValidationContext) -> GateRecord:
    compiled_ir = context.compiled_ir
    error = context.compiled_ir_error
    inventory = dict(context.compiled_inventory)
    if compiled_ir is None:
        return context.make_gate(
            "post_compile_xscr_reinspect",
            "failed",
            error or "Compiled XSCR could not be parsed after compile.",
        )
    if not inventory.get("command_ids") and not compiled_ir.get("steps"):
        return context.make_gate(
            "post_compile_xscr_reinspect",
            "failed",
            "Compiled XSCR parsed but no command objects or IR steps were found.",
        )
    command_validation = inventory.get("command_validation") if isinstance(inventory.get("command_validation"), dict) else {}
    generic_validation = (
        inventory.get("generic_command_validation")
        if isinstance(inventory.get("generic_command_validation"), dict)
        else {}
    )
    fc_native_compare = inventory.get("fc_native_xml_compare") if isinstance(inventory.get("fc_native_xml_compare"), dict) else {}
    recipe_golden = (
        inventory.get("verification_recipe_add_labware_golden")
        if isinstance(inventory.get("verification_recipe_add_labware_golden"), dict)
        else {}
    )
    fluent_findings = inventory.get("fluentcontrol_findings") or []
    expression_inventory = (
        inventory.get("expression_inventory")
        if isinstance(inventory.get("expression_inventory"), dict)
        else {}
    )
    details = {
        "compiled_step_count": len(compiled_ir.get("steps") or []),
        "compiled_command_count": len(inventory.get("command_ids") or []),
    }
    if command_validation:
        details["command_validation"] = command_validation
        details["command_validation_failures"] = bool(command_validation.get("failures"))
    if generic_validation:
        details["generic_command_validation"] = generic_validation
        details["generic_command_validation_failures"] = bool(generic_validation.get("failures"))
    if fc_native_compare:
        details["fc_native_xml_compare"] = fc_native_compare
    if recipe_golden:
        details["verification_recipe_add_labware_golden"] = recipe_golden
    if expression_inventory:
        details["expression_inventory"] = {
            "valid": expression_inventory.get("valid"),
            "record_count": expression_inventory.get("record_count", 0),
            "failure_count": expression_inventory.get("failure_count", 0),
            "failures": (expression_inventory.get("failures") or [])[:20],
        }
    expression_provenance = inventory.get("expression_provenance")
    if isinstance(expression_provenance, dict):
        details["expression_provenance"] = expression_provenance

    needs_review = any(
        summary.get("status") == "needs_review"
        for summary in (fc_native_compare, recipe_golden)
        if isinstance(summary, dict)
    )
    if needs_review:
        details["needs_review"] = True
    if fluent_findings:
        details["findings"] = fluent_findings
        return context.make_gate(
            "post_compile_xscr_reinspect",
            "failed",
            "Compiled XSCR contains FluentControl field/runtime issues that must be fixed before handoff.",
            details,
        )
    return context.make_gate(
        "post_compile_xscr_reinspect",
        "passed",
        "Compiled XSCR parses back into canonical IR.",
        details,
    )

def evaluate_xscr_ir_roundtrip(context: ValidationContext) -> GateRecord:
    ir = context.protocol_ir
    compiled_ir = context.compiled_ir
    if ir is None:
        return context.make_gate("xscr_ir_roundtrip_matches", "failed", "Roundtrip comparison needs valid protocol IR.")
    if compiled_ir is None:
        return context.make_gate("xscr_ir_roundtrip_matches", "failed", "Roundtrip comparison needs re-inspected compiled XSCR.")

    canonical_ir = migrate_protocol_ir(ir)
    canonical_compiled_ir = migrate_protocol_ir(compiled_ir)
    expected = _step_signatures(canonical_ir)
    actual = _step_signatures(canonical_compiled_ir)
    expected_setup_expressions = _setup_expression_signatures(canonical_ir)
    actual_setup_expressions = _setup_expression_signatures(canonical_compiled_ir)
    setup_expressions_match = not expected_setup_expressions or all(
        actual_setup_expressions.get(label) == expressions
        for label, expressions in expected_setup_expressions.items()
    )
    if expected != actual or not setup_expressions_match:
        return context.make_gate(
            "xscr_ir_roundtrip_matches",
            "failed",
            "Compiled XSCR IR differs from the source protocol IR.",
            {
                "expected_step_count": len(expected),
                "actual_step_count": len(actual),
                "expected": expected[:20],
                "actual": actual[:20],
                "expected_setup_expressions": expected_setup_expressions,
                "actual_setup_expressions": actual_setup_expressions,
            },
        )
    return context.make_gate(
        "xscr_ir_roundtrip_matches",
        "passed",
        "Compiled XSCR roundtrip preserves operation order, targets, volumes, and liquid classes.",
        {"step_count": len(expected)},
    )

def evaluate_volume_bounds(context: ValidationContext) -> GateRecord:
    ir = context.domain_ir
    options = context.validation_options
    if ir is None:
        return context.make_gate("volume_bounds_valid", "failed", "Volume bounds cannot be checked without valid protocol IR.")
    failures = []
    checked = 0
    default_max = _number(options.get("max_volume_ul")) or 1000
    mca_max = _number(options.get("max_mca_volume_ul")) or 100
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") not in VOLUME_OPERATIONS:
            continue
        checked += 1
        volume = _number(step.get("volume_ul"))
        max_volume = mca_max if _is_mca_step(step) else default_max
        if volume is None:
            failures.append(_step_failure(step, "missing_volume_ul", "Volume is not numeric."))
        elif volume <= 0:
            failures.append(_step_failure(step, "non_positive_volume", f"Volume {volume:g} uL must be greater than 0."))
        elif volume > max_volume:
            failures.append(
                _step_failure(
                    step,
                    "volume_exceeds_bound",
                    f"Volume {volume:g} uL exceeds the {max_volume:g} uL bound.",
                    {"max_volume_ul": max_volume},
                )
            )
    if failures:
        return context.make_gate("volume_bounds_valid", "failed", "Some liquid handling volumes are outside allowed bounds.", {"failures": failures})
    if checked == 0:
        return context.make_gate(
            "volume_bounds_valid",
            "passed",
            "No liquid handling volume operations were present in the IR.",
            {"trivial": True},
        )
    return context.make_gate("volume_bounds_valid", "passed", f"All {checked} liquid handling volume(s) are within configured bounds.")

def evaluate_well_ranges(context: ValidationContext) -> GateRecord:
    ir = context.domain_ir
    if ir is None:
        return context.make_gate("well_ranges_valid", "failed", "Well ranges cannot be checked without valid protocol IR.")
    labware_by_label = {
        str(item.get("label") or ""): item
        for item in ir.get("labware") or []
        if isinstance(item, dict) and item.get("label")
    }
    failures = []
    checked = 0
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        target = str(step.get("target_labware") or step.get("source_labware") or step.get("destination_labware") or "")
        dimensions = _labware_dimensions(labware_by_label.get(target) or {})
        for key, value in _well_values(step):
            for well in _expand_well_tokens(value):
                parsed = _parse_well(well)
                if parsed is None:
                    failures.append(_step_failure(step, "invalid_well_syntax", f"{key}={well!r} is not a valid well reference."))
                    continue
                checked += 1
                row, column = parsed
                if row > dimensions["rows"] or column > dimensions["columns"]:
                    failures.append(
                        _step_failure(
                            step,
                            "well_out_of_range",
                            f"{key}={well!r} is outside {dimensions['rows']}x{dimensions['columns']} labware bounds.",
                            {"rows": dimensions["rows"], "columns": dimensions["columns"]},
                        )
                    )
    if failures:
        return context.make_gate("well_ranges_valid", "failed", "Some explicit well references are outside labware bounds.", {"failures": failures})
    if checked == 0:
        return context.make_gate("well_ranges_valid", "passed", "No explicit well references were present.", {"trivial": True})
    return context.make_gate("well_ranges_valid", "passed", f"Validated {checked} explicit well reference(s).")

def evaluate_tip_capacity(context: ValidationContext) -> GateRecord:
    ir = context.domain_ir
    if ir is None:
        return context.make_gate("tip_capacity_valid", "failed", "Tip capacity cannot be checked without valid protocol IR.")
    steps = [step for step in ir.get("steps") or [] if isinstance(step, dict)]
    has_tip_strategy = any(_is_tip_pickup(step) or _is_tip_release(step) for step in steps)
    if not has_tip_strategy:
        return context.make_gate("tip_capacity_valid", "passed", "No explicit tip handling was present in IR.", {"trivial": True})

    labware_by_label = {
        str(item.get("label") or ""): item
        for item in ir.get("labware") or []
        if isinstance(item, dict) and item.get("label")
    }
    active_capacity: float | None = None
    failures = []
    checked = 0
    for step in steps:
        if _is_tip_pickup(step):
            target = str(step.get("target_labware") or "")
            active_capacity = _tip_capacity_ul(labware_by_label.get(target) or {}, step)
            if active_capacity is None:
                failures.append(_step_failure(step, "unknown_tip_capacity", f"Tip capacity for {target or 'selected tip box'} is unknown."))
            continue
        if _is_tip_release(step):
            active_capacity = None
            continue
        if step.get("operation") in VOLUME_OPERATIONS:
            volume = _number(step.get("volume_ul"))
            if active_capacity is None:
                failures.append(_step_failure(step, "liquid_handling_without_active_tips", "Liquid handling occurs without active picked-up tips in the IR sequence."))
            elif volume is not None:
                checked += 1
                if volume > active_capacity:
                    failures.append(
                        _step_failure(
                            step,
                            "volume_exceeds_tip_capacity",
                            f"Volume {volume:g} uL exceeds active tip capacity {active_capacity:g} uL.",
                            {"tip_capacity_ul": active_capacity},
                        )
                    )
    if failures:
        return context.make_gate("tip_capacity_valid", "failed", "Tip strategy cannot support all liquid handling steps.", {"failures": failures})
    return context.make_gate("tip_capacity_valid", "passed", f"Tip capacity checked for {checked} liquid handling step(s).")

def evaluate_liquid_class_compatibility(context: ValidationContext) -> GateRecord:
    ir = context.domain_ir
    source_manifest = context.source_manifest
    if ir is None:
        return context.make_gate("liquid_class_compatible", "failed", "Liquid class compatibility cannot be checked without valid protocol IR.")
    alias_maps = load_alias_maps()
    declared = {
        resolve_alias(item.get("name"), "liquid_class", alias_maps)
        for item in ir.get("liquid_classes") or []
        if isinstance(item, dict) and item.get("name")
    }
    available = {
        resolve_alias(name, "liquid_class", alias_maps)
        for name in (source_manifest or {}).get("liquid_classes") or []
        if name
    }
    compatibility = _liquid_class_compatibility_map(source_manifest, alias_maps)
    failures = []
    checked = 0
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") not in LIQUID_CLASS_OPERATIONS:
            continue
        checked += 1
        raw_liquid_class = str(step.get("liquid_class") or "")
        liquid_class = resolve_alias(raw_liquid_class, "liquid_class", alias_maps)
        if not raw_liquid_class:
            failures.append(_step_failure(step, "missing_liquid_class", "Liquid handling step has no liquid class."))
            continue
        if declared and liquid_class not in declared:
            failures.append(_step_failure(step, "undeclared_liquid_class", f"{raw_liquid_class!r} is not listed in IR liquid_classes."))
        if available and liquid_class not in available:
            failures.append(_step_failure(step, "unavailable_liquid_class", f"{raw_liquid_class!r} is not available in the source context."))
        allowed_operations = compatibility.get(liquid_class)
        if allowed_operations and str(step.get("operation")) not in allowed_operations:
            failures.append(
                _step_failure(
                    step,
                    "operation_not_supported_by_liquid_class",
                    f"{raw_liquid_class!r} is not declared compatible with {step.get('operation')!r}.",
                    {"allowed_operations": sorted(allowed_operations)},
                )
            )
    if failures:
        return context.make_gate("liquid_class_compatible", "failed", "Some liquid classes are incompatible with their operations.", {"failures": failures})
    if checked == 0:
        return context.make_gate(
            "liquid_class_compatible",
            "passed",
            "No liquid-class operations were present in the IR.",
            {"trivial": True},
        )
    return context.make_gate("liquid_class_compatible", "passed", f"Liquid classes are compatible for {checked} liquid handling step(s).")

def evaluate_no_unapproved_raw_xml(context: ValidationContext) -> GateRecord:
    draft_path = context.draft_path
    compiled_inventory = dict(context.compiled_inventory)
    options = context.validation_options
    draft_calls = _raw_xml_calls(draft_path)
    unsupported = compiled_inventory.get("unsupported_commands") or []
    approved = bool(
        options.get("allow_unsupported_raw_xml")
        or options.get("unsupported_raw_xml_approved")
        or options.get("raw_xml_approved")
    )
    approved_ids = {str(value) for value in options.get("approved_unsupported_command_ids") or []}
    unapproved_draft_calls = [] if approved else [
        item
        for item in draft_calls
        if not _raw_xml_call_approved(item, approved_ids)
    ]
    unapproved = [
        item
        for item in unsupported
        if not approved and str(item.get("command_id") or "") not in approved_ids
    ]
    if unapproved_draft_calls or unapproved:
        return context.make_gate(
            "no_unapproved_raw_xml",
            "failed",
            "Unsupported raw XML or compiled command gaps require explicit approval.",
            {
                "draft_raw_xml_calls": unapproved_draft_calls,
                "unsupported_commands": unapproved,
                "approval_keys": [
                    "allow_unsupported_raw_xml",
                    "unsupported_raw_xml_approved",
                    "raw_xml_approved",
                    "approved_unsupported_command_ids",
                ],
            },
        )
    if draft_calls or unsupported:
        return context.make_gate(
            "no_unapproved_raw_xml",
            "passed",
            "Unsupported raw XML usage was explicitly approved.",
            {
                "approved_draft_raw_xml_calls": draft_calls,
                "approved_unsupported_commands": unsupported,
            },
        )
    return context.make_gate("no_unapproved_raw_xml", "passed", "No unsupported raw XML or unsupported compiled commands were found.")

def _step_signatures(ir: dict[str, Any]) -> list[dict[str, Any]]:
    signatures = []
    setup_operations = {"add_labware", "load_labware"}
    for step in ir.get("steps") or []:
        if not isinstance(step, dict):
            continue
        operation = str(step.get("operation") or "")
        if not operation:
            continue
        if operation in setup_operations:
            continue
        signature = {
            "operation": operation,
            "target_labware": _canonical_first_instance_label(
                step.get("target_labware")
                or step.get("source_labware")
                or step.get("destination_labware")
                or ""
            ),
            "volume_ul": _normalized_number(step.get("volume_ul")),
            "liquid_class": str(step.get("liquid_class") or ""),
        }
        expressions = _registered_step_expression_signatures(step)
        if expressions:
            signature["expressions"] = expressions
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        if operation == "move_plate":
            labware = str(params.get("labware") or step.get("target_labware") or "")
            onto_labware = str(params.get("onto_labware") or params.get("onto") or "")
            destination_location = str(
                params.get("destination_location")
                or params.get("to_location")
                or step.get("destination_location")
                or ""
            )
            destination_site = _normalized_number(
                params.get("destination_site") or params.get("to_site") or step.get("destination_site")
            )
            cover_match = re.fullmatch(r'GetCoverSiteName\("([^"]+)"\)', destination_location)
            if cover_match and (not onto_labware or onto_labware == cover_match.group(1)):
                onto_labware = cover_match.group(1)
                destination_location = ""
                destination_site = ""
            elif onto_labware:
                # Source IR onto-moves omit deck coords; ignore residual site literals.
                destination_location = ""
                destination_site = ""
            signature["target_labware"] = _canonical_first_instance_label(labware)
            signature.update(
                {
                    "labware": labware,
                    "onto_labware": onto_labware,
                    "destination_location": destination_location,
                    "destination_site": destination_site,
                }
            )
        elif operation == "call_subroutine":
            signature["subroutine"] = _clean_subroutine_name(params.get("subroutine") or params.get("SubRoutine"))
        elif operation == "prompt_user":
            image_path = str(
                params.get("image_path")
                or prompt_step_worktable_media_path(params)
                or prompt_step_media_path(params)
                or ""
            )
            sound_file = str(params.get("sound_file") or "")
            signature.update(
                {
                    "prompt": str(params.get("prompt") or ""),
                    "image_file": _media_basename(image_path),
                    "image_step_label": _media_step_label(image_path),
                    "sound_file": _media_basename(sound_file),
                    "sound_step_label": _media_step_label(sound_file),
                }
            )
        signatures.append(signature)
    return signatures

def _setup_expression_signatures(ir: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    signatures: dict[str, dict[str, list[str]]] = {}
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("operation") or "") not in {
            "add_labware",
            "load_labware",
        }:
            continue
        expressions = _registered_step_expression_signatures(step)
        if not expressions:
            continue
        label = _canonical_first_instance_label(
            step.get("target_labware")
            or (step.get("parameters") or {}).get("label")
            or ""
        )
        if label:
            signatures[label] = expressions
    return signatures

def _registered_step_expression_signatures(step: dict[str, Any]) -> dict[str, list[str]]:
    command_id = str(step.get("command_id") or "")
    operation = str(step.get("operation") or "")
    if not expression_fields_for_command(command_id):
        command_id = {
            "add_labware": "AddLabwareDataV1",
            "set_variable": "SetVariableStatement",
            "set_remaining_runtime": "SetVariableStatement",
            "conditional_branch": "ConditionalGroup",
            "loop": "LoopGroup",
            "wait": "Wait",
            "delay": "Delay",
            "set_location": "SetLocation",
            "aspirate": "Mca384Aspirate",
            "dispense": "Mca384Dispense",
            "mca384_mix": "Mca384Mix",
            "mca384_empty_tips": "Mca384EmptyTips",
            "liha_aspirate": "LihaAspirate",
            "liha_dispense": "LihaDispense",
            "liha_mix": "LihaMix",
            "liha_empty_tips": "LihaEmptyTips",
            "wait_for_timer": "WaitForTimer",
            "tegio_set_pwm_output": "TeGioSetPWMOutput",
            "move_axis_command": "MoveAxisCommand",
        }.get(operation, "")
    allowed_keys = {
        canonical_expression_key(field_path)
        for field_path in expression_fields_for_command(command_id)
    }
    if operation == "call_subroutine":
        allowed_keys.add(canonical_expression_key("Source"))
    if not allowed_keys:
        return {}
    collected: dict[str, set[str]] = {}

    for record in walk_expression_values(step):
        if record.key not in allowed_keys:
            continue
        if not isinstance(record.expression, dict):
            continue
        try:
            rendered = render_expression(expression_from_mapping(record.expression))
        except (TypeError, ValueError):
            rendered = json.dumps(record.expression, sort_keys=True, separators=(",", ":"))
        collected.setdefault(record.key, set()).add(rendered)
    if operation == "call_subroutine":
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        for section in ("variable_mappings_start", "variable_mappings_end"):
            for mapping in params.get(section) or []:
                if not isinstance(mapping, dict) or mapping.get("source") in (None, ""):
                    continue
                expression = mapping.get("source_expression")
                if isinstance(expression, dict):
                    continue
                parsed = parse_or_preserve_source_expression(str(mapping.get("source")))
                collected.setdefault("source_expression", set()).add(
                    render_expression(parsed)
                )
    return {
        key: sorted(values)
        for key, values in sorted(collected.items())
        if values
    }

def _canonical_first_instance_label(value: Any) -> str:
    """Treat FluentControl's optional ``[001]`` suffix as the first base instance."""
    return re.sub(r"\[0*1\]$", "", str(value or "").strip())

def _media_basename(value: Any) -> str:
    return re.split(r"[\\/]", str(value or "").strip())[-1]

def _media_step_label(value: Any) -> str:
    basename = _media_basename(value)
    match = re.match(r"(step[_-]?\d+)", basename, flags=re.IGNORECASE)
    return match.group(1).replace("_", "").replace("-", "").casefold() if match else ""

def _clean_subroutine_name(value: Any) -> str:
    return str(value or "").strip().strip('"').replace("/", "\\")

def _normalized_number(value: Any) -> float | int | str:
    number = _number(value)
    if number is None:
        return str(value or "")
    return int(number) if number.is_integer() else number

def _step_failure(
    step: dict[str, Any],
    reason: str,
    message: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "step_id": step.get("id"),
        "index": step.get("index"),
        "operation": step.get("operation"),
        "target_labware": step.get("target_labware"),
        "reason": reason,
        "message": message,
    }
    if extra:
        payload.update(extra)
    return {key: value for key, value in payload.items() if value not in (None, "", [], {})}

def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None

def _is_mca_step(step: dict[str, Any]) -> bool:
    text = " ".join(
        str(value)
        for value in (
            step.get("operation"),
            step.get("command_id"),
            step.get("compiled_path"),
            step.get("source_path"),
            (step.get("parameters") or {}).get("device_alias") if isinstance(step.get("parameters"), dict) else "",
        )
        if value
    ).lower()
    return "mca" in text

def _well_values(step: dict[str, Any]) -> list[tuple[str, Any]]:
    keys = (
        "well",
        "wells",
        "source_well",
        "source_wells",
        "destination_well",
        "destination_wells",
        "well_position",
        "well_positions",
        "well_range",
        "well_ranges",
    )
    out = []
    for key in keys:
        if step.get(key) not in (None, "", []):
            out.append((key, step[key]))
    params = step.get("parameters") or {}
    if isinstance(params, dict):
        for key in keys:
            if params.get(key) not in (None, "", []):
                out.append((f"parameters.{key}", params[key]))
    return out

def _expand_well_tokens(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        tokens = []
        for item in value:
            tokens.extend(_expand_well_tokens(item))
        return tokens
    if isinstance(value, dict):
        tokens = []
        for item in value.values():
            tokens.extend(_expand_well_tokens(item))
        return tokens
    text = str(value or "")
    tokens = []
    for piece in re.split(r"[,;\s]+", text):
        piece = piece.strip()
        if not piece:
            continue
        if ":" in piece:
            tokens.extend(part for part in piece.split(":") if part)
        else:
            tokens.append(piece)
    return tokens

def _parse_well(value: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"([A-Za-z]+)(\d+)", value.strip())
    if not match:
        return None
    row = 0
    for char in match.group(1).upper():
        row = row * 26 + (ord(char) - ord("A") + 1)
    return row, int(match.group(2))

def _labware_dimensions(item: dict[str, Any]) -> dict[str, int]:
    text = f"{item.get('catalog') or ''} {item.get('label') or ''} {item.get('python_class') or ''}".lower()
    if "384" in text:
        return {"rows": 16, "columns": 24}
    if "1536" in text:
        return {"rows": 32, "columns": 48}
    if "24" in text:
        return {"rows": 4, "columns": 6}
    if "12" in text and "96" not in text:
        return {"rows": 3, "columns": 4}
    return {"rows": 8, "columns": 12}

def _is_tip_pickup(step: dict[str, Any]) -> bool:
    return str(step.get("operation") or "") in {"pick_up_tips", "mca384_get_tips", "liha_get_tips"}

def _is_tip_release(step: dict[str, Any]) -> bool:
    return str(step.get("operation") or "") in {"set_tips_back", "drop_tips", "mca384_drop_tips", "liha_drop_tips"}

def _tip_capacity_ul(labware: dict[str, Any], step: dict[str, Any]) -> float | None:
    params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
    for value in (
        labware.get("capacity_ul"),
        labware.get("tip_capacity_ul"),
        params.get("capacity_ul") if isinstance(params, dict) else None,
        params.get("tip_capacity_ul") if isinstance(params, dict) else None,
    ):
        number = _number(value)
        if number is not None:
            return number
    text = " ".join(
        str(value)
        for value in (
            labware.get("catalog"),
            labware.get("label"),
            labware.get("python_class"),
            params.get("catalog") if isinstance(params, dict) else "",
            params.get("labware_type") if isinstance(params, dict) else "",
        )
        if value
    )
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:u\s*l|ul|µl)", text, flags=re.IGNORECASE)
    return float(match.group(1)) if match else None

def _liquid_class_compatibility_map(
    source_manifest: dict[str, Any] | None,
    alias_maps: dict[str, dict[str, str]],
) -> dict[str, set[str]]:
    if not isinstance(source_manifest, dict):
        return {}
    out: dict[str, set[str]] = {}
    for key in ("liquid_class_compatibility", "liquid_class_capabilities"):
        raw = source_manifest.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = resolve_alias(item.get("name"), "liquid_class", alias_maps)
            operations = item.get("operations") or item.get("supported_operations") or item.get("compatible_operations")
            if name and isinstance(operations, list):
                out[name] = {str(operation) for operation in operations if operation}
    return out

def _raw_xml_calls(draft_path: Path | None) -> list[dict[str, Any]]:
    if draft_path is None or not draft_path.exists():
        return []
    try:
        source = draft_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except Exception:
        return []
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = ""
        if isinstance(func, ast.Attribute):
            name = func.attr
        elif isinstance(func, ast.Name):
            name = func.id
        if name in {"raw_xml_step", "raw_xml_command"}:
            command_id = _raw_xml_call_command_id(node)
            call = {"line": getattr(node, "lineno", None), "function": name}
            if command_id:
                call["command_id"] = command_id
                support_status = registry_command_support_status(command_id)
                if support_status:
                    call["support_status"] = support_status
            calls.append(call)
    return calls

def _raw_xml_call_command_id(node: ast.Call) -> str | None:
    if not node.args:
        return None
    try:
        value = ast.literal_eval(node.args[0])
    except (ValueError, SyntaxError):
        return None
    text = str(value or "").strip()
    return text or None

def _raw_xml_call_approved(item: dict[str, Any], approved_ids: set[str]) -> bool:
    command_id = str(item.get("command_id") or "").strip()
    if not command_id:
        return False
    if command_id in approved_ids:
        return True
    return registry_command_support_status(command_id) is not None

# Packaging and source-dependency gates (Gates 23-26).
def evaluate_checksums(context: ValidationContext) -> GateRecord:
    compiled_xscr = context.compiled_xscr
    source_projects = list(context.source_projects)
    options = context.validation_options
    """Verify the generated ZEIA will carry valid FluentControl checksums.

    FluentControl validates ``<Checksum>`` on every datastore object at load, so
    any edited entry shipped with a blank checksum makes the bundle reject or
    prompt on import. This gate fails when blank checksums will ship, unless the
    operator waives it (acknowledging they will recompute on a FluentControl
    machine or accept the in-app recalculation prompt).

    When packaging has already run, the precise per-entry audit is passed in via
    ``options['project_checksum_audit']``. Otherwise the gate predicts the
    outcome from checksum-bridge availability and the compiled script's own
    checksum state, both known before packaging.
    """
    produces_archive = any(
        path.suffix.lower() == ".zeia" and path.exists() for path in source_projects
    )
    if not produces_archive:
        return context.make_gate(
            "checksums_valid",
            "passed",
            "No project ZEIA is packaged, so no datastore checksums are produced.",
            {"trivial": True},
        )

    waived = bool(
        options.get("checksums_recompute_waived")
        or options.get("checksum_recompute_waived")
    )

    audit = options.get("project_checksum_audit")
    if isinstance(audit, dict):
        blank_entries = audit.get("blank_entries") or []
        absent_entries = audit.get("absent_entries") or []
        invalid_entries = audit.get("invalid_entries") or []
        problem_entries = [*blank_entries, *absent_entries, *invalid_entries]
        if not problem_entries:
            return context.make_gate(
                "checksums_valid",
                "passed",
                "All edited generated-ZEIA entries carry valid checksums; "
                "preserved base entries keep their originals.",
            )
        details = {
            "blank_entries": blank_entries,
            "blank_count": len(blank_entries),
            "absent_entries": absent_entries,
            "absent_count": len(absent_entries),
            "invalid_entries": invalid_entries,
            "invalid_count": len(invalid_entries),
            "bridge_available": bool(audit.get("bridge_available")),
            "approval_keys": ["checksums_recompute_waived"],
        }
        if invalid_entries:
            return context.make_gate(
                "checksums_valid",
                "failed",
                f"{len(invalid_entries)} edited generated-ZEIA entr(y/ies) have invalid checksums; "
                "FluentControl will reject them on import/load. Duplicate, malformed, or stale "
                "checksums must be repaired before packaging and cannot be waived.",
                details,
            )
        if waived:
            return context.make_gate(
                "checksums_valid",
                "passed",
                f"{len(problem_entries)} edited entr(y/ies) ship without valid checksums; waived. "
                "Recompute on a FluentControl machine or accept the recalculation prompt before import.",
                {**details, "waived": True, "needs_review": True},
            )
        return context.make_gate(
            "checksums_valid",
            "failed",
            f"{len(problem_entries)} edited generated-ZEIA entr(y/ies) lack valid checksums; "
            "FluentControl will reject them on import/load. Recompute on a FluentControl machine "
            "or set checksums_recompute_waived only after accepting the in-app recalculation path.",
            details,
        )

    # No packaged audit available yet: predict from bridge + compiled script.
    bridge_override = options.get("_checksum_bridge_available")
    bridge_available = bool(bridge_override()) if callable(bridge_override) else checksum_bridge_available()
    if bridge_available:
        return context.make_gate(
            "checksums_valid",
            "passed",
            "FluentControl checksum bridge is available; edited entries will be re-checksummed "
            "during packaging.",
        )
    compiled_state = "absent"
    try:
        compiled_state = entry_checksum_state(compiled_xscr.read_bytes())
    except OSError:
        compiled_state = "absent"
    if compiled_state == "valid":
        return context.make_gate(
            "checksums_valid",
            "passed",
            "Compiled script carries a valid checksum and no checksum-invalidating edits are "
            "expected.",
            {"needs_review": True},
        )
    details = {
        "bridge_available": False,
        "compiled_checksum_state": compiled_state,
        "approval_keys": ["checksums_recompute_waived"],
    }
    if waived:
        return context.make_gate(
            "checksums_valid",
            "passed",
            "Checksum bridge is unavailable so edited entries will ship with blank checksums; "
            "waived. Recompute on a FluentControl machine or accept the in-app prompt before import.",
            {**details, "waived": True, "needs_review": True},
        )
    return context.make_gate(
        "checksums_valid",
        "failed",
        "Checksum bridge (fluentcontrol_core) is unavailable, so the generated ZEIA will ship "
        "edited entries with blank checksums that FluentControl rejects or prompts to recalculate "
        "on import. Recompute on a FluentControl machine or set checksums_recompute_waived.",
        details,
    )

def evaluate_generated_zeia(context: ValidationContext) -> GateRecord:
    source_projects = list(context.source_projects)
    options = context.validation_options
    """Validate the packaged generated ZEIA as a one-file import artifact.

    The other gates check the IR and the standalone ``.xscr``; this gate opens the
    packaged ``generated_project.zeia`` itself and reports whether it is a
    structurally importable artifact (zip integrity, every ``<Reference>`` GUID
    resolving inside the archive, and ``meta/content.xml`` entries matching real
    files).

    The audit can only run after packaging writes the archive, so the precise
    result is supplied via ``options['project_archive_audit']``. During the
    pre-flight validation (before packaging) the audit is absent and the gate
    defers with a trivial pass; ``export_ready_to_import`` re-runs validation with
    the audit attached to produce the authoritative report shipped in the bundle.
    """
    produces_archive = any(
        path.suffix.lower() == ".zeia" and path.exists() for path in source_projects
    )
    if not produces_archive:
        return context.make_gate(
            "generated_zeia_valid",
            "passed",
            "No project ZEIA is packaged, so there is no generated archive to validate.",
            {"trivial": True},
        )

    audit = options.get("project_archive_audit")
    if not isinstance(audit, dict):
        return context.make_gate(
            "generated_zeia_valid",
            "passed",
            "Generated ZEIA is audited after packaging writes it; no archive exists yet at "
            "this pre-flight stage.",
            {"trivial": True, "deferred": True},
        )

    blocking = list(audit.get("blocking") or [])
    needs_review = list(audit.get("needs_review") or [])
    archives = audit.get("archives") or []
    if not audit.get("zip_ok", True) and not any(
        item.get("kind") in {"not_a_zip", "corrupt_entry", "unreadable_zip", "missing_archive"}
        for item in blocking
    ):
        blocking.append({"kind": "zip_integrity", "detail": "archive failed its zip integrity check"})

    if blocking:
        return context.make_gate(
            "generated_zeia_valid",
            "failed",
            f"The packaged generated ZEIA has {len(blocking)} blocking problem(s) and will not "
            "load in FluentControl (unresolved references, datastore-metadata mismatch, or a "
            "corrupt archive). See project_import_report.md for the full list.",
            {
                "blocking": blocking,
                "needs_review": needs_review,
                "archives": archives,
            },
        )
    if needs_review:
        return context.make_gate(
            "generated_zeia_valid",
            "passed",
            f"The packaged generated ZEIA opens and all used references resolve; "
            f"{len(needs_review)} item(s) need review (dependencies that must already exist in "
            "the target system).",
            {
                "needs_review": needs_review,
                "needs_review_count": len(needs_review),
                "archives": archives,
            },
        )
    return context.make_gate(
        "generated_zeia_valid",
        "passed",
        "The packaged generated ZEIA opens cleanly; all references resolve inside the archive "
        "and datastore metadata matches the shipped files.",
        {"archives": archives},
    )

def _evaluate_subroutine_additions(
    source_projects: list[Path],
    options: Mapping[str, Any],
    make_gate: GateFactory,
) -> GateRecord:
    """Validate the datastore metadata of subroutines ADDED to the generated ZEIA.

    Replacing an existing subroutine reuses its GUID/entry and is the safe path;
    adding a brand-new subroutine synthesizes datastore metadata (fresh GUID,
    incremented ``<V>``, ``<FileRef>``) and is inherently riskier. This gate:

    * passes trivially when no ZEIA is packaged or no subroutine was added,
    * fails when an added subroutine has a metadata defect that breaks datastore
      identity (missing entry, malformed GUID, or no nodedescription node), and
    * otherwise passes as needs-review, surfacing the additions and the
      recommendation to build the subroutine into the base ZEIA and re-run so it
      is replaced instead of added.

    The precise audit is produced after packaging and supplied via
    ``options['project_subroutine_audit']``; during pre-flight validation it is
    absent and the gate defers with a trivial pass.
    """
    produces_archive = any(
        path.suffix.lower() == ".zeia" and path.exists() for path in source_projects
    )
    if not produces_archive:
        return make_gate(
            "subroutine_additions_safe",
            "passed",
            "No project ZEIA is packaged, so no subroutines are added.",
            {"trivial": True},
        )

    audit = options.get("project_subroutine_audit")
    if not isinstance(audit, dict):
        return make_gate(
            "subroutine_additions_safe",
            "passed",
            "Added subroutines are audited after packaging writes the archive; nothing to "
            "check at this pre-flight stage.",
            {"trivial": True, "deferred": True},
        )

    added = audit.get("added") or []
    blocking = audit.get("blocking") or []
    needs_review = audit.get("needs_review") or []
    if blocking:
        return make_gate(
            "subroutine_additions_safe",
            "failed",
            f"{len(blocking)} added subroutine(s) have datastore metadata defects (missing entry, "
            "malformed GUID, or no nodedescription node) that break the datastore object. See "
            "project_import_report.md.",
            {"blocking": blocking, "added_count": len(added), "needs_review": needs_review},
        )
    if not added:
        return make_gate(
            "subroutine_additions_safe",
            "passed",
            "No subroutines were added; any packaged subroutines reuse existing base entries "
            "(the safe replace path).",
            {"trivial": True, "replaced_count": int(audit.get("replaced_count") or 0)},
        )
    return make_gate(
        "subroutine_additions_safe",
        "passed",
        f"{len(added)} subroutine(s) were added to the base ZEIA with sound synthesized metadata. "
        "Prefer building them into the base so they are replaced rather than added; confirm before import.",
        {
            "added": added,
            "added_count": len(added),
            "needs_review": True,
            "metadata_review": needs_review,
            "approval_keys": ["subroutine_additions_acknowledged"],
        },
    )

def evaluate_subroutine_additions(context: ValidationContext) -> GateRecord:
    """Evaluate added-subroutine metadata for legacy direct callers."""
    return _evaluate_subroutine_additions(
        list(context.source_projects),
        context.validation_options,
        context.make_gate,
    )

def evaluate_subroutine_dependencies(context: ValidationContext) -> GateRecord:
    protocol_ir = context.domain_ir
    compiled_xscr = context.compiled_xscr
    source_manifest = context.source_manifest
    source_projects = list(context.source_projects)
    options = context.validation_options
    resolution = resolve_subroutine_dependencies(protocol_ir, source_manifest)
    additions_gate = _evaluate_subroutine_additions(source_projects, options, context.make_gate)
    calls = resolution.get("required") or []
    missing = resolution.get("missing") or []
    ambiguous = resolution.get("ambiguous") or []
    resolved = resolution.get("resolved") or []
    compiled_findings = validate_compiled_subroutine_references(compiled_xscr, resolved)
    package_audit = options.get("project_subroutine_audit") if isinstance(options.get("project_subroutine_audit"), dict) else None
    package_dependencies = (package_audit or {}).get("dependencies") or []
    package_findings = _subroutine_package_findings(resolved, package_dependencies, package_audit is not None)
    details = {
        "required": calls,
        "resolved": resolved,
        "missing": missing,
        "ambiguous": ambiguous,
        "compiled_reference_findings": compiled_findings,
        "package_findings": package_findings,
        "packaged_dependencies": package_dependencies,
        "addition_audit": additions_gate,
    }
    if not calls:
        return context.make_gate(
            "subroutine_dependencies_valid",
            "passed",
            "No subroutine calls were present.",
            {"trivial": True, "addition_audit": additions_gate},
        )
    blockers = [*missing, *ambiguous, *compiled_findings, *package_findings]
    if additions_gate.get("status") == "failed":
        blockers.extend((additions_gate.get("details") or {}).get("blocking") or [])
    if blockers:
        return context.make_gate(
            "subroutine_dependencies_valid",
            "failed",
            "One or more subroutine dependencies are missing, ambiguous, mismatched, or not packaged consistently.",
            details,
        )
    if additions_gate.get("status") != "passed":
        return context.make_gate(
            "subroutine_dependencies_valid",
            additions_gate.get("status") or "needs_review",
            "Subroutine calls resolve, but added subroutine metadata still needs review.",
            details,
        )
    return context.make_gate(
        "subroutine_dependencies_valid",
        "passed",
        f"Resolved and verified {len(resolved)} required subroutine dependency(ies).",
        details,
    )

def evaluate_command_inventory(context: ValidationContext) -> GateRecord:
    compiled_inventory = dict(context.compiled_inventory)
    source_manifest = context.source_manifest
    options = context.validation_options
    """Validate the literal name strings the compiled XSCR command XML uses.

    The earlier gates resolve labware/liquid-class/device names from the IR, but
    a compiled ``.xscr`` can still embed a literal ``LabwareType``,
    ``LabwareName``/``LabwareLable``, ``DeviceAlias``, ``AvailableID``, or
    ``LiquidClassName`` string that FluentControl does not actually have. This
    post-compile inventory gate extracts those strings from the compiled command
    XML and diffs them against what the source options exposes
    (``source_manifest``) and the alias maps (``config/aliases/``).

    Classification mirrors the worktable-resource gates:

    * ``missing`` — the source options exposed an inventory for that category and
      the used name (after alias resolution) is not in it. FluentControl will not
      have the string, so this is blocking.
    * ``unverified`` — the source options exposed no inventory for that category,
      so the name cannot be confidently checked offline. The gate passes but
      surfaces it as ``needs_review`` instead of hiding the gap.

    Blocking findings can be acknowledged with ``command_inventory_approved`` (or
    ``command_names_approved``) once an operator confirms the target FluentControl
    system carries the names; the gate then passes as needs-review.
    """
    name_fields = (compiled_inventory or {}).get("name_fields") or {}
    categories = [
        ("unknown_labware_types", "labware_types", "catalog", "labware type"),
        ("unknown_labware_names", "labware_names", "labware", "labware name"),
        ("unknown_liquid_classes", "liquid_class_names", "liquid_class", "liquid class"),
        ("unknown_device_aliases", "device_aliases", "device_alias", "device alias"),
        ("unknown_available_ids", "available_ids", "device_alias", "available device ID"),
    ]
    total_used = sum(len(name_fields.get(field) or []) for _, field, _, _ in categories)
    if total_used == 0:
        return context.make_gate(
            "command_inventory_resolves",
            "passed",
            "Compiled XSCR command XML exposes no labware/liquid-class/device name strings to validate.",
            {"trivial": True},
        )

    alias_maps = load_alias_maps()
    inventory = _manifest_name_inventory(source_manifest, alias_maps)
    blocking: dict[str, list[dict[str, Any]]] = {}
    needs_review: dict[str, list[dict[str, Any]]] = {}
    checked_counts: dict[str, int] = {}
    for detail_key, field, kind, label in categories:
        used = name_fields.get(field) or []
        if not used:
            continue
        checked_counts[field] = len(used)
        available = inventory[field]
        for name in used:
            status = _command_name_status(name, kind, available, alias_maps)
            if status == "available":
                continue
            record = _command_name_record(name, kind, status, label, alias_maps)
            if status == "missing":
                blocking.setdefault(detail_key, []).append(record)
            else:
                needs_review.setdefault(detail_key, []).append(record)

    approved = bool(
        options.get("command_inventory_approved")
        or options.get("command_names_approved")
    )
    if blocking:
        blocking_count = sum(len(items) for items in blocking.values())
        if approved:
            details = {
                **blocking,
                **needs_review,
                "needs_review": True,
                "approved": True,
                "checked_counts": checked_counts,
            }
            return context.make_gate(
                "command_inventory_resolves",
                "passed",
                f"{blocking_count} compiled command name string(s) resolve nowhere in the source "
                "options, but were explicitly approved. Confirm the target FluentControl system "
                "carries them before import.",
                details,
            )
        details = {
            **blocking,
            **needs_review,
            "approval_keys": ["command_inventory_approved", "command_names_approved"],
            "checked_counts": checked_counts,
        }
        if needs_review:
            details["needs_review"] = True
        return context.make_gate(
            "command_inventory_resolves",
            "failed",
            f"{blocking_count} compiled command name string(s) (catalog/labware/liquid-class/device) "
            "are absent from the source manifest and alias maps; FluentControl will not have them. "
            "Fix the names or approve via command_inventory_approved once confirmed in the target system.",
            details,
        )
    if needs_review:
        review_count = sum(len(items) for items in needs_review.values())
        return context.make_gate(
            "command_inventory_resolves",
            "passed",
            f"All checkable compiled command name strings resolve; {review_count} name(s) could not "
            "be verified because the source manifest exposed no inventory for their category. "
            "Confirm before import.",
            {**needs_review, "needs_review": True, "checked_counts": checked_counts},
        )
    return context.make_gate(
        "command_inventory_resolves",
        "passed",
        f"All {total_used} compiled command name string(s) resolve in the source options and alias maps.",
        {"checked_counts": checked_counts},
    )

def _subroutine_package_findings(
    resolved: list[dict[str, Any]],
    package_dependencies: list[dict[str, Any]],
    package_audit_present: bool,
) -> list[dict[str, Any]]:
    if not package_audit_present:
        return []
    package_keys = {
        _norm_subroutine_dependency_key(item.get("object_name") or item.get("ref"))
        for item in package_dependencies
        if isinstance(item, dict)
    }
    findings: list[dict[str, Any]] = []
    for dep in resolved:
        key = _norm_subroutine_dependency_key(dep.get("object_name") or dep.get("ref"))
        if key and key in package_keys:
            continue
        findings.append(
            {
                "reason": "packaged_subroutine_dependency_missing",
                "message": "Generated ZEIA packaging did not record the resolved subroutine dependency.",
                "subroutine": dep.get("ref"),
                "object_name": dep.get("object_name"),
                "guid": dep.get("guid"),
                "entry": dep.get("entry"),
            }
        )
    return findings

def _norm_subroutine_dependency_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().strip('"').casefold())

def _manifest_name_inventory(
    source_manifest: dict[str, Any] | None,
    alias_maps: dict[str, dict[str, str]],
) -> dict[str, set[str]]:
    """Build the per-category set of names the source context actually provides."""
    inventory: dict[str, set[str]] = {
        "labware_types": set(),
        "labware_names": set(),
        "liquid_class_names": set(),
        "device_aliases": set(),
        "available_ids": set(),
    }
    if not isinstance(source_manifest, dict):
        return inventory

    def add(target: str, value: Any, kind: str) -> None:
        for candidate in alias_candidates(value, kind, alias_maps):
            inventory[target].add(_norm_name(candidate))

    for name in source_manifest.get("labware_names") or []:
        add("labware_names", name, "labware")
    for name in source_manifest.get("rack_types") or []:
        add("labware_types", name, "catalog")
    for name in source_manifest.get("labware_types") or []:
        add("labware_types", name, "catalog")
    for name in source_manifest.get("liquid_classes") or []:
        add("liquid_class_names", name, "liquid_class")
    for name in source_manifest.get("device_aliases") or []:
        add("device_aliases", name, "device_alias")
    for name in source_manifest.get("available_ids") or []:
        add("available_ids", name, "device_alias")

    for script in source_manifest.get("scripts") or []:
        deps = script.get("dependencies") or {} if isinstance(script, dict) else {}
        for name in deps.get("labware_names") or []:
            add("labware_names", name, "labware")
        for name in deps.get("rack_labels") or []:
            add("labware_names", name, "labware")
        for name in deps.get("rack_types") or []:
            add("labware_types", name, "catalog")
        for name in deps.get("liquid_classes") or []:
            add("liquid_class_names", name, "liquid_class")
        for name in deps.get("device_aliases") or []:
            add("device_aliases", name, "device_alias")
        for name in deps.get("available_ids") or []:
            add("available_ids", name, "device_alias")
    return inventory

def _command_name_status(
    name: str,
    kind: str,
    available: set[str],
    alias_maps: dict[str, dict[str, str]],
) -> str:
    if not available:
        return "unverified"
    candidates = alias_candidates(name, kind, alias_maps) or [name]
    if any(_norm_name(candidate) in available for candidate in candidates):
        return "available"
    return "missing"

def _command_name_record(
    name: str,
    kind: str,
    status: str,
    label: str,
    alias_maps: dict[str, dict[str, str]],
) -> dict[str, Any]:
    resolved = resolve_alias(name, kind, alias_maps)
    record = {"name": name, "status": status, "category": label}
    if resolved and resolved != name:
        record["resolved_name"] = resolved
    return record

def _norm_name(value: Any) -> str:
    return str(value or "").strip().casefold()
