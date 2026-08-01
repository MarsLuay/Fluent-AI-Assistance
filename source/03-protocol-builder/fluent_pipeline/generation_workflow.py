"""Official inspect -> plan -> draft -> simulate -> repair -> compile workflow."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
import html
import itertools
from pathlib import Path
import shutil
import tempfile
import threading
from typing import Any, Mapping, Sequence
import json
import re

from .aliases import normalize_protocol_ir_aliases
from .bundle_lifecycle import (
    created_from_record,
    lifecycle_metadata,
    source_export_kind,
    verification_state_from_readiness,
)
from .compiled_xscr_finalizer import (
    finalize_compiled_xscr,
    render_compiled_xscr_finalization_markdown,
)
from .config import READY_TO_IMPORT_DIR
from .delivery_bundle import validate_v2_delivery_bundle
from .determinism import artifact_digest
from .exports import (
    attach_generation_reports_to_protocol_folders,
    cleanup_ready_to_import_stage,
    export_ready_to_import,
    ReadyBundleStage,
    publish_ready_to_import_zeia,
)
from .expression_provenance import (
    bind_protocol_ir_expression_provenance,
    build_expression_provenance_ledger,
    write_expression_provenance_ledger,
)
from .interactive_script import prepare_interactive_recipe
from .ir_planner import synthesize_seed_ir
from .instrument_config import (
    infer_expected_host_config,
    inspect_host_instrument_configs,
    render_host_instrument_config_markdown,
)
from .liquid_state import render_liquid_state_markdown, validate_liquid_state
from .pattern_index import (
    load_pattern_windows,
    pattern_window_dependencies,
    pattern_window_refs,
    summarize_pattern_windows,
)
from .project_catalog import ensure_project_catalog
from .project_context import (
    FULL_ZEIA_ASK,
    ProjectCollection,
    ProjectContext,
    ProjectLike,
    active_project_name,
    build_collection_manifest,
    import_project,
    load_project,
    load_project_collection,
    resolve_context_script,
    resolve_recorded_script_path,
    filter_generation_source_script_records,
)
from .prompt_media import ensure_compiled_prompt_media_references
from .provenance import environment_provenance, sha256_path
from .generation_options import GenerationOptions, normalize_generation_options
from .progress import GENERATION_PROGRESS_STAGES, ProgressCallback, ProgressEmitter
from .protocol_ir import (
    CANONICAL_IR_VERSION,
    CANONICAL_SETUP_GROUP_NAME,
    RUP_VARIABLE_SELECTOR_INSTRUCTIONS,
    annotate_verification_prompts_with_media,
    apply_default_verification_worktable_bindings,
    apply_rga_move_pattern_policy,
    force_worktable_prompt_images,
    is_setup_group_name,
    load_protocol_ir,
    media_slot_specs,
    normalize_group_hierarchy,
    normalize_operator_prompt_text,
    normalize_runtime_variable_prompt_instructions,
    normalize_setup_groups,
    prompt_looks_like_external_initialization_check,
    protocol_filename,
    protocol_is_prompt_only,
    route_unbound_worktable_prompts_to_standard,
    protocol_ir_from_path,
    required_media_slot_specs,
    render_gwl,
    render_python_draft,
    render_recreate_markdown,
    sanitize_worktable_prompt_variable_labware_bindings,
    sync_verification_prompt_target_labware,
    worktable_pipeline_video_slots,
    write_protocol_ir,
)
from .protocol_ir_schema import ProtocolIRIssue, ProtocolIRValidationError
from .request_spec import (
    build_request_spec,
    build_request_validation_diff,
    is_meta_verification_group_comment,
    load_request_spec,
    normalize_request_spec,
    recipe_group_description,
    recipe_step_type,
    recipe_subroutine_name,
    recipe_worktable_patterns,
    render_request_validation_diff_markdown,
    resolve_recipe_worktable_binding,
    request_verbatim_prompt,
    verification_recipe,
    write_request_spec,
)
from .repair import applicable_repair_actions, apply_repair_plan, build_repair_plan, render_repair_markdown
from .reports import render_compile_markdown, render_simulation_markdown
from .runner import PipelineError, run_fluentcoder, write_json
from .runtime_bridge import (
    FluentContextCheckConfig,
    render_fluent_context_check_markdown,
    run_fluent_context_check,
)
from .readiness_gates import (
    readiness_gate_approval_context_keys,
    readiness_gate,
    readiness_gate_request_spec_approved,
)
from .readiness import (
    build_canonical_readiness,
    embed_readiness,
    readiness_status_from_readiness,
)
from .variable_namespaces import localize_variable_declaration_namespaces
from .variable_reconciliation import (
    preflight_variable_reconciliation,
    render_variable_reconciliation_markdown,
)
from .spec_lint import lint_request_spec, render_lint_report
from .subroutine_dependencies import resolve_subroutine_dependencies
from .subroutine_deck_locations import (
    CAPBC_PREP_GROUP_NAME,
    apply_subroutine_deck_location_bindings,
    is_capbc_subroutine,
    normalize_recipe_subroutine_deck_locations,
    resolve_capbc_prep_defaults,
)
from .subroutine_variable_mappings import normalize_ir_subroutine_variable_mappings
from .traceability import (
    annotate_runtime_report_with_trace,
    build_traceability_map,
    render_traceability_markdown,
)
from .validation import (
    render_validation_markdown,
    scaffold_validation_report,
    validate_ready_to_import,
    validation_failure_message,
)
from .worktable_diff import (
    diff_worktable_requirements,
    render_worktable_changes_markdown,
    render_worktable_patch_json,
)
from .workflows.generation import GenerationStageRunner, GenerationState, LoadContextStage
from .media_convert import convert_dropped_video_slots, normalize_worktable_detail_gifs
from .zeia_filesystem import ensure_script_file_references


GENERATION_STAGES = [
    ("capture_request_spec", "Capture request.spec.yaml"),
    ("import_project_context", "Import ZEIA project context"),
    ("verify_full_zeia_export", "Verify full ZEIA export"),
    ("inspect_scripts_and_worktable", "Inspect scripts and worktable"),
    ("inspect_host_instrument_config", "Inspect host instrument configuration"),
    ("select_source_scripts_and_patterns", "Select source scripts/patterns"),
    ("build_protocol_ir", "Build protocol.ir.json"),
    ("reconcile_variables", "Reconcile variable declarations"),
    ("validate_liquid_logic", "Validate liquid logic before script generation"),
    ("generate_python_draft", "Generate Python draft"),
    ("simulate", "Simulate"),
    ("generate_repair_plan", "Generate repair plan"),
    ("apply_safe_repairs", "Apply safe repairs"),
    ("compile_xscr", "Compile to .xscr"),
    ("fluent_context_check", "Optional FluentControl import/load diagnostic"),
    ("generate_recreate_script", "Generate RECREATE_SCRIPT.md"),
    ("generate_worktable_changes", "Generate worktable_changes.md and worktable.patch.json"),
    ("validate_ready_gates", "Validate ready gates"),
    ("generate_validation_diff", "Generate request validation diff"),
    ("package_ready_to_import", "Package into ready-to-import"),
]

PROGRESS_HEARTBEAT_SECONDS = 5.0


@dataclass(frozen=True)
class ApprovalSet:
    approve_partial_zeia: bool = False
    waive_checksum_recompute: bool = False
    approve_deck_layout: bool = False
    approve_command_inventory: bool = False
    approve_unsupported_raw_xml: bool = False


@dataclass(frozen=True)
class GenerationRequest:
    intent: str
    output_directory: Path
    context_name: str | None = None
    source_scripts: tuple[str, ...] = field(default_factory=tuple)
    protocol_ir: Path | None = None
    options: GenerationOptions = field(default_factory=GenerationOptions)
    approvals: ApprovalSet = field(default_factory=ApprovalSet)
    request_spec_path: Path | None = None
    use_active_context: bool = True


def _load_generation_context(
    context_name: str | None,
    *,
    use_active_context: bool = True,
) -> ProjectLike | None:
    resolved_name = context_name or (active_project_name() if use_active_context else None)
    if not resolved_name:
        return None
    try:
        return load_project(resolved_name)
    except PipelineError as project_error:
        try:
            return load_project_collection(resolved_name)
        except PipelineError:
            raise project_error


def run_generation_workflow(
    request: GenerationRequest,
    *,
    progress_callback: ProgressCallback | None = None,
    progress: Any | None = None,
    event_sink: Any | None = None,
    event_log_path: str | None = None,
) -> dict[str, Any]:
    """Run or scaffold the official generation workflow."""
    if progress_callback is None and callable(progress):
        progress_callback = progress
    progress_emitter = ProgressEmitter(
        GENERATION_PROGRESS_STAGES,
        progress_callback,
        operation_id="generate",
    )
    generation_options = normalize_generation_options(request.options)
    state = GenerationState(
        request=request,
        generation_options=generation_options,
        progress_emitter=progress_emitter,
    )
    approvals = request.approvals
    intent = request.intent
    out_dir = request.output_directory
    GenerationStageRunner(
        (
            LoadContextStage(
                load_context=lambda context_name: _load_generation_context(
                    context_name,
                    use_active_context=request.use_active_context,
                ),
                summarize_context=_context_progress_summary,
            ),
        )
    ).run(state)
    context = state.context
    project_archive = generation_options.project_archive
    project_name = generation_options.project_name
    force_import = generation_options.force_import
    source_scripts = list(request.source_scripts)
    pattern_refs = list(generation_options.pattern_refs)
    index_db = generation_options.index_db
    pattern_ids = list(generation_options.pattern_ids)
    pattern_queries = list(generation_options.pattern_queries)
    source_script_rank = generation_options.source_script_rank
    ir_source = request.protocol_ir
    protocol_name = generation_options.protocol_name
    subroutine_dirs = list(generation_options.subroutine_dirs)
    record_snapshots = generation_options.record_snapshots
    deterministic_compile = generation_options.deterministic_compile
    simulate = generation_options.simulate
    compile_xscr = generation_options.compile_xscr
    apply_modeling = generation_options.apply_modeling
    max_repair_iterations = generation_options.max_repair_iterations
    strict_readiness = generation_options.strict_readiness
    approve_partial_zeia = bool(
        approvals.approve_partial_zeia or generation_options.approve_partial_zeia
    )
    waive_checksum_recompute = bool(
        approvals.waive_checksum_recompute or generation_options.waive_checksum_recompute
    )
    approve_deck_layout = bool(
        approvals.approve_deck_layout or generation_options.approve_deck_layout
    )
    approve_command_inventory = bool(
        approvals.approve_command_inventory or generation_options.approve_command_inventory
    )
    approve_unsupported_raw_xml = bool(
        approvals.approve_unsupported_raw_xml or generation_options.approve_unsupported_raw_xml
    )
    fluent_context_check = generation_options.fluent_context_check
    fluent_method = generation_options.fluent_method
    fluent_provider = generation_options.fluent_provider
    fluent_command = generation_options.fluent_command
    fluent_host = generation_options.fluent_host
    fluent_port = generation_options.fluent_port
    fluent_insecure = generation_options.fluent_insecure
    fluent_timeout = generation_options.fluent_timeout
    target_script_folder = generation_options.target_script_folder
    request_spec_source = request.request_spec_path
    request_spec: dict[str, Any] | None = None
    out_dir.mkdir(parents=True, exist_ok=True)
    simulation_backend = _simulation_backend(simulate)
    stages: list[dict[str, Any]] = []
    selected_scripts = source_scripts or []
    selected_patterns = list(pattern_refs or [])
    indexed_pattern_windows = load_pattern_windows(
        index_db,
        pattern_ids=pattern_ids,
        pattern_queries=pattern_queries,
        source_script_rank=source_script_rank,
    )
    selected_patterns.extend(pattern_window_refs(indexed_pattern_windows))
    indexed_pattern_summaries = summarize_pattern_windows(indexed_pattern_windows)

    progress_emitter.started("validate_request")
    try:
        request_spec = load_request_spec(request_spec_source) if request_spec_source else None
        request_spec_doc = _prepare_request_spec(
            request_spec=request_spec,
            intent=intent,
            protocol_name=protocol_name,
            context=context,
            project_archive=project_archive,
            source_scripts=selected_scripts,
            pattern_refs=selected_patterns,
            index_db=index_db,
            pattern_ids=pattern_ids,
            pattern_queries=pattern_queries,
            source_script_rank=source_script_rank,
            generation_options=generation_options,
            fluent_method=fluent_method,
        )
        verbatim_prompt = request_verbatim_prompt(request_spec_doc)
        intent_summary = str((request_spec_doc.get("request") or {}).get("intent") or "")
        request_spec_path = out_dir / "request.spec.yaml"
        write_request_spec(request_spec_doc, request_spec_path)
        _record_stage(
            stages,
            "capture_request_spec",
            "passed",
            "Wrote request.spec.yaml as the user-request contract.",
            outputs={
                "request_spec": str(request_spec_path),
                **({"source": str(request_spec_source)} if request_spec_source else {}),
            },
        )
        spec_lint_result = lint_request_spec(request_spec_doc, context=context)
        spec_lint_path = out_dir / "request_spec_lint.md"
        spec_lint_path.write_text(
            render_lint_report(spec_lint_result, source=str(request_spec_path)),
            encoding="utf-8",
        )
        if not spec_lint_result.ok:
            errors = "; ".join(
                f"{finding.location}: {finding.message}"
                for finding in spec_lint_result.errors
            )
            raise PipelineError(f"Request spec failed pre-generation lint: {errors}")
    except Exception as exc:
        progress_emitter.failed("validate_request", str(exc))
        raise
    progress_emitter.completed("validate_request", "Request spec is valid.")

    if project_archive is not None:
        context = import_project(project_archive, name=project_name, force=force_import)
        _refresh_request_spec_context(request_spec_doc, context)
        write_request_spec(request_spec_doc, request_spec_path)
        _record_stage(
            stages,
            "import_project_context",
            "passed",
            f"Imported {project_archive}",
            outputs={"context": str(context.root), "manifest": str(context.root / "manifest.json")},
        )
    elif context is not None:
        _record_stage(
            stages,
            "import_project_context",
            "passed",
            f"Using existing {_context_kind(context)} context {context.name}",
            outputs={"context": str(context.root), "manifest": str(context.root / "manifest.json")},
        )
    else:
        _record_stage(
            stages,
            "import_project_context",
            "skipped",
            "No ZEIA project context was provided.",
        )
    if context is not None:
        _refresh_request_spec_context(request_spec_doc, context)
        write_request_spec(request_spec_doc, request_spec_path)
    context = _context_with_request_sources(context, request_spec_doc, out_dir=out_dir)
    if context is not None:
        _refresh_request_spec_context(request_spec_doc, context)
        write_request_spec(request_spec_doc, request_spec_path)
    full_zeia_export = _verify_full_zeia_export(context, approve_partial_zeia=approve_partial_zeia)
    _refresh_request_spec_full_zeia_export(
        request_spec_doc,
        full_zeia_export,
        approve_partial_zeia=approve_partial_zeia,
    )
    write_request_spec(request_spec_doc, request_spec_path)
    _record_stage(
        stages,
        "verify_full_zeia_export",
        "passed" if full_zeia_export.get("accepted") else "needs_user",
        _full_zeia_stage_summary(full_zeia_export),
        outputs={"assessment": full_zeia_export},
    )
    if not full_zeia_export.get("accepted"):
        return _write_blocked_full_zeia_manifest(
            out_dir=out_dir,
            stages=stages,
            intent=intent,
            verbatim_prompt=verbatim_prompt,
            intent_summary=intent_summary,
            context=context,
            request_spec_doc=request_spec_doc,
            request_spec_path=request_spec_path,
            request_spec_source=request_spec_source,
            full_zeia_export=full_zeia_export,
            index_db=index_db,
            indexed_pattern_count=len(indexed_pattern_windows),
            generation_options=generation_options,
            simulation_backend=simulation_backend,
        )
    catalog_db = ensure_project_catalog(context)

    inspection = inspect_generation_context(context, selected_scripts)
    source_script_paths = _selected_source_script_paths(
        inspection["selected_source_scripts"],
        context=context,
    )
    recipe = verification_recipe(request_spec_doc)
    regeneration_baseline = _matching_regeneration_baseline_script(
        context,
        protocol_name,
    )
    if regeneration_baseline is not None:
        # A matching baseline remains context for identity, dependency, and file
        # references regardless of which higher-precedence source supplies steps.
        source_script_paths = [
            regeneration_baseline,
            *(
                path
                for path in source_script_paths
                if path.resolve() != regeneration_baseline.resolve()
            ),
        ]
    expression_provenance_path = out_dir / "expression_provenance.json"
    expression_provenance = build_expression_provenance_ledger(
        [
            *_context_source_projects(context),
            *source_script_paths,
        ]
    )
    write_expression_provenance_ledger(expression_provenance_path, expression_provenance)
    ir_source_mode = _generation_ir_source_mode(
        ir_source=ir_source,
        recipe=recipe,
        preserve_regeneration_baseline=generation_options.preserve_regeneration_baseline,
        regeneration_baseline=regeneration_baseline,
    )
    inspection_path = out_dir / "01_context_inspection.json"
    write_json(inspection_path, inspection)
    inspection_report = out_dir / "01_context_inspection.md"
    inspection_report.write_text(render_context_inspection_markdown(inspection), encoding="utf-8")
    _record_stage(
        stages,
        "inspect_scripts_and_worktable",
        "passed",
        "Wrote project inspection summary.",
        outputs={"json": str(inspection_path), "report": str(inspection_report)},
    )

    host_config_expected = _host_config_expected_hint(
        request_spec_doc,
        intent=intent,
        context=context,
        selected_source_scripts=inspection["selected_source_scripts"],
    )
    host_config_report = inspect_host_instrument_configs(host_config_expected)
    host_config_json_path = out_dir / "host_instrument_config.json"
    host_config_report_path = out_dir / "host_instrument_config.md"
    write_json(host_config_json_path, host_config_report)
    host_config_report_path.write_text(render_host_instrument_config_markdown(host_config_report), encoding="utf-8")
    _refresh_request_spec_host_config(request_spec_doc, host_config_report)
    write_request_spec(request_spec_doc, request_spec_path)
    _record_stage(
        stages,
        "inspect_host_instrument_config",
        host_config_report["status"],
        host_config_report["summary"],
        outputs={"json": str(host_config_json_path), "report": str(host_config_report_path)},
    )

    selection = {
        "intent": intent,
        "intent_summary": intent_summary,
        "verbatim_prompt": verbatim_prompt,
        "source_scripts": selected_scripts,
        "pattern_refs": selected_patterns,
        "index_db": str(index_db) if index_db else None,
        "pattern_ids": [int(value) for value in (pattern_ids or [])],
        "pattern_queries": list(pattern_queries or []),
        "source_script_rank": source_script_rank,
        "indexed_pattern_windows": indexed_pattern_summaries,
        "resolved_source_scripts": inspection["selected_source_scripts"],
    }
    selection_path = out_dir / "02_selected_sources.json"
    write_json(selection_path, selection)
    plan_path = out_dir / "GENERATION_PLAN.md"
    plan_path.write_text(render_generation_plan(intent, context, selection, stages), encoding="utf-8")
    _record_stage(
        stages,
        "select_source_scripts_and_patterns",
        "passed" if selected_scripts or selected_patterns or indexed_pattern_windows else "needs_user",
        _selection_stage_summary(selected_scripts, selected_patterns, indexed_pattern_windows),
        outputs={"selection": str(selection_path), "plan": str(plan_path)},
    )

    progress_emitter.started("build_protocol_ir")
    synthesis_path: Path | None = None
    if ir_source_mode == "explicit_ir":
        assert ir_source is not None
        ir = load_protocol_ir(ir_source)
        _augment_ir_generation_metadata(ir, intent, context, selection)
        ir_status = "passed"
        ir_summary = f"Loaded protocol IR from {ir_source}"
    elif ir_source_mode == "explicit_recipe":
        assert recipe is not None
        ir = build_ir_from_recipe(
            recipe,
            intent=intent,
            context=context,
            protocol_name=protocol_name,
            request_spec=request_spec_doc,
        )
        recipe_report = (ir.get("source") or {}).get("verification_recipe") or {}
        planned_steps = int(recipe_report.get("step_count") or len(ir.get("steps") or []))
        if planned_steps:
            ir_status = "passed"
            ir_summary = f"Synthesized {planned_steps} IR step(s) from the verification recipe."
        else:
            ir_status = "needs_user"
            ir_summary = "Verification recipe did not produce any protocol IR steps."
    elif ir_source_mode == "preserve_regeneration_baseline":
        assert regeneration_baseline is not None
        ir = protocol_ir_from_path(regeneration_baseline)
        _augment_ir_generation_metadata(ir, intent, context, selection)
        ir_status = "passed"
        ir_summary = (
            f"Preserved {len(ir.get('steps') or [])} IR step(s) from the matching "
            f"regeneration baseline {regeneration_baseline.name}."
        )
    elif ir_source_mode == "missing_regeneration_baseline":
        message = (
            "generation.preserve_regeneration_baseline is true, but no same-name "
            "baseline script exists in the primary context."
        )
        progress_emitter.failed("build_protocol_ir", message)
        raise PipelineError(message)
    else:
        ir = build_seed_protocol_ir(
            intent=intent,
            context=context,
            selected_scripts=inspection["selected_source_scripts"],
            pattern_refs=selected_patterns,
            pattern_windows=indexed_pattern_windows,
            protocol_name=protocol_name,
            preferred_worktable=_preferred_worktable_from_request(request_spec_doc),
        )
        synthesis = ir.get("source", {}).get("ir_synthesis", {})
        synthesis_path = out_dir / "03_ir_synthesis.json"
        write_json(synthesis_path, synthesis)
        planned_steps = int(synthesis.get("planned_step_count") or 0)
        if planned_steps:
            ir_status = "passed"
            ir_summary = f"Synthesized {planned_steps} IR step(s) from selected scripts/patterns."
        else:
            ir_status = "needs_user"
            ir_summary = "Created a seed protocol IR scaffold; no complete selected script/pattern steps were available."
    if regeneration_baseline is not None:
        _attach_regeneration_baseline_context(
            ir,
            regeneration_baseline=regeneration_baseline,
            context=context,
            protocol_name=protocol_name,
            project_archive=project_archive,
            supplies_steps=ir_source_mode == "preserve_regeneration_baseline",
        )
    _attach_request_spec_metadata(ir, request_spec_doc, request_spec_path)
    _attach_host_config_metadata(ir, host_config_report)
    ir = normalize_protocol_ir_aliases(ir)
    if ir_source_mode == "explicit_recipe":
        ir = _annotate_explicit_recipe_prompt_media(
            ir,
            recipe=recipe,
            generation_options=generation_options,
        )
    if generation_options.verification_prompt_rup == "worktable":
        ir = force_worktable_prompt_images(ir)
    ir = sanitize_worktable_prompt_variable_labware_bindings(ir)
    ir = route_unbound_worktable_prompts_to_standard(
        ir,
        allow_standard=generation_options.verification_prompt_rup in {"mixed", "standard"},
    )
    ir = sync_verification_prompt_target_labware(ir)
    _normalize_ir_labware_labels_against_manifest(ir, context.manifest if context else None)
    ir = _stamp_approved_automated_verification_moves(ir, request_spec_doc)
    ir = _clear_verification_script_protocol_comment(ir)
    ir = apply_rga_move_pattern_policy(ir)
    ir = bind_protocol_ir_expression_provenance(ir, expression_provenance)

    subroutine_resolution = resolve_subroutine_dependencies(
        ir,
        context.manifest if context else None,
    )
    variable_lookup = _subroutine_lookup_from_resolution(subroutine_resolution)
    variable_reconciliation = preflight_variable_reconciliation(
        ir,
        lookup=variable_lookup,
        context_root=_context_root_path(context),
    )
    _normalize_ir_labware_labels_against_manifest(ir, context.manifest if context else None)
    base = Path(protocol_filename(ir, "")).stem or "generated_protocol"
    ir_path = out_dir / f"{base}.protocol-ir.json"
    write_protocol_ir(ir, ir_path)
    variable_reconciliation_json_path = out_dir / f"{base}.variable-reconciliation.json"
    variable_reconciliation_report_path = out_dir / f"{base}.variable-reconciliation.md"
    write_json(variable_reconciliation_json_path, variable_reconciliation)
    variable_reconciliation_report_path.write_text(
        render_variable_reconciliation_markdown(variable_reconciliation),
        encoding="utf-8",
    )
    rga_policy = (ir.get("source") or {}).get("rga_move_policy") or {}
    rga_policy_json_path = out_dir / "rga_move_policy.json"
    rga_policy_report_path = out_dir / "rga_move_policy.md"
    write_json(rga_policy_json_path, rga_policy)
    rga_policy_report_path.write_text(render_rga_move_policy_markdown(rga_policy), encoding="utf-8")
    ir_outputs = {
        "ir": str(ir_path),
        "expression_provenance": str(expression_provenance_path),
        "rga_move_policy": str(rga_policy_report_path),
        "rga_move_policy_json": str(rga_policy_json_path),
    }
    if synthesis_path is not None:
        ir_outputs["synthesis"] = str(synthesis_path)
    _record_stage(
        stages,
        "build_protocol_ir",
        ir_status,
        ir_summary,
        outputs=ir_outputs,
    )
    _record_stage(
        stages,
        "reconcile_variables",
        "passed" if variable_reconciliation.get("ok") else "failed",
        (
            "Variable declarations were reconciled against source subroutines."
            if variable_reconciliation.get("ok")
            else "Variable declaration reconciliation found blocking conflicts."
        ),
        outputs={
            "json": str(variable_reconciliation_json_path),
            "report": str(variable_reconciliation_report_path),
        },
    )
    if not variable_reconciliation.get("ok"):
        message = _variable_reconciliation_failure_summary(variable_reconciliation)
        progress_emitter.failed("build_protocol_ir", message)
        raise ProtocolIRValidationError(_variable_reconciliation_protocol_issues(variable_reconciliation))

    liquid_state_report = validate_liquid_state(ir)
    liquid_state_json_path = out_dir / f"{base}.liquid-state.json"
    liquid_state_report_path = out_dir / f"{base}.liquid-state.md"
    write_json(liquid_state_json_path, liquid_state_report)
    liquid_state_report_path.write_text(render_liquid_state_markdown(liquid_state_report), encoding="utf-8")
    _record_stage(
        stages,
        "validate_liquid_logic",
        liquid_state_report["status"],
        liquid_state_report["summary"],
        outputs={"json": str(liquid_state_json_path), "report": str(liquid_state_report_path)},
    )
    progress_emitter.completed("build_protocol_ir", ir_summary)

    progress_emitter.started("render_script")
    _ensure_ir_worktable_bound(ir)
    python_path = out_dir / f"{base}.py"
    python_path.write_text(render_python_draft(ir), encoding="utf-8")
    gwl_text = render_gwl(ir)
    generated_files = {
        "request_spec": request_spec_path.name,
        "ir": ir_path.name,
        "expression_provenance": expression_provenance_path.name,
        "variable_reconciliation": variable_reconciliation_report_path.name,
        "variable_reconciliation_json": variable_reconciliation_json_path.name,
        "python": python_path.name,
        "rga_move_policy": rga_policy_report_path.name,
        "rga_move_policy_json": rga_policy_json_path.name,
    }
    gwl_path = out_dir / f"{base}.gwl"
    if gwl_text.strip():
        gwl_path.write_text(gwl_text, encoding="utf-8")
        generated_files["gwl"] = gwl_path.name
    _record_stage(
        stages,
        "generate_python_draft",
        "passed",
        "Rendered Python draft from protocol IR.",
        outputs={"python": str(python_path), **({"gwl": str(out_dir / generated_files["gwl"])} if "gwl" in generated_files else {})},
    )
    progress_emitter.completed("render_script", f"Rendered {python_path.name}.")

    max_repair_iterations = max(0, int(max_repair_iterations))
    repair_history_path = out_dir / "repair_history.json"
    repair_history: list[dict[str, Any]] = []
    simulation_result = None
    simulation_data = None
    simulation_json_path = out_dir / f"{base}.simulation.json"
    simulation_report_path = out_dir / f"{base}.simulation.md"
    repair_plan = None
    repair_json_path = out_dir / f"{base}.repair-plan.json"
    repair_report_path = out_dir / f"{base}.repair-plan.md"
    repaired_path = _repair_candidate_path(out_dir, base, 1)
    selected_candidate_index: int | None = None
    selected_candidate_path: Path | None = None
    selected_simulation_result = None
    selected_simulation_data = None
    selected_simulation_json_path: Path | None = None
    selected_simulation_report_path: Path | None = None
    selected_repair_plan = None
    last_simulation_result = None
    last_simulation_data = None
    last_simulation_json_path: Path | None = None
    last_simulation_report_path: Path | None = None
    last_repair_plan = None
    repair_loop_termination_reason: str | None = None
    repair_history_record: dict[str, Any] = {"repair_iterations": repair_history}

    if simulate:
        progress_emitter.started("simulate")
        current_candidate_index = 0
        current_candidate_path = python_path
        total_simulation_attempts = max_repair_iterations + 1
        for _ in range(max_repair_iterations + 1):
            current_simulation_json_path, current_simulation_report_path = _repair_candidate_simulation_paths(
                out_dir,
                base,
                current_candidate_index,
            )
            progress_emitter.running(
                "simulate",
                f"Attempt {current_candidate_index + 1} of {total_simulation_attempts}",
            )
            try:
                current_simulation_result = _run_fluentcoder_with_progress(
                    ["simulate", current_candidate_path, "--json"],
                    catalog_db=catalog_db,
                    progress_emitter=progress_emitter,
                    stage_id="simulate",
                )
            except Exception as exc:
                progress_emitter.failed("simulate", str(exc))
                raise
            try:
                current_simulation_data = (
                    json.loads(current_simulation_result.stdout.strip()) if current_simulation_result.stdout.strip() else None
                )
            except json.JSONDecodeError:
                current_simulation_data = None
            if current_simulation_data is not None:
                write_json(current_simulation_json_path, current_simulation_data)
            current_simulation_report_path.write_text(
                render_simulation_markdown(current_candidate_path, current_simulation_data, current_simulation_result),
                encoding="utf-8",
            )
            current_repair_plan = build_repair_plan(
                current_candidate_path,
                context=context,
                simulation_json_path=current_simulation_json_path if current_simulation_json_path.exists() else None,
            )
            applicable_repairs = applicable_repair_actions(current_repair_plan, apply_modeling=apply_modeling)
            current_findings = [action.to_dict() for action in current_repair_plan.actions]
            history_entry = _repair_history_entry(
                current_candidate_index,
                simulation_status="passed" if current_simulation_result.ok else "failed",
                findings=current_findings,
            )
            if current_simulation_result.ok:
                selected_candidate_index = current_candidate_index
                selected_candidate_path = current_candidate_path
                selected_simulation_result = current_simulation_result
                selected_simulation_data = current_simulation_data
                selected_simulation_json_path = current_simulation_json_path
                selected_simulation_report_path = current_simulation_report_path
                selected_repair_plan = current_repair_plan
            last_simulation_result = current_simulation_result
            last_simulation_data = current_simulation_data
            last_simulation_json_path = current_simulation_json_path
            last_simulation_report_path = current_simulation_report_path
            last_repair_plan = current_repair_plan
            if current_simulation_result.ok and not applicable_repairs:
                repair_loop_termination_reason = "simulation_passed_no_repairs"
                repair_history.append(history_entry)
                break
            if current_candidate_index < max_repair_iterations:
                next_candidate_path = _repair_candidate_path(out_dir, base, current_candidate_index + 1)
                applied = apply_repair_plan(current_repair_plan, next_candidate_path, apply_modeling=apply_modeling)
                history_entry["repairs_applied"] = [action.to_dict() for action in applied]
                current_candidate_path = next_candidate_path
            repair_history.append(history_entry)
            if current_candidate_index < max_repair_iterations:
                current_candidate_index += 1
        if repair_loop_termination_reason is None:
            repair_loop_termination_reason = "repair_budget_exhausted"
        repair_history_record["repair_iterations"] = repair_history
        repair_history_record["termination_reason"] = repair_loop_termination_reason
        repair_history_record["selected_candidate"] = selected_candidate_index
        simulation_result = selected_simulation_result or last_simulation_result
        simulation_data = selected_simulation_data if selected_simulation_result is not None else last_simulation_data
        simulation_json_path = selected_simulation_json_path or last_simulation_json_path
        simulation_report_path = selected_simulation_report_path or last_simulation_report_path
        repair_plan = selected_repair_plan or last_repair_plan
        repaired_path = selected_candidate_path or current_candidate_path
        write_json(repair_history_path, repair_history_record)
        _record_stage(
            stages,
            "simulate",
            "passed" if selected_simulation_result is not None else "failed",
            (
                f"Simulated candidate {selected_candidate_index} after {len(repair_history) - 1} repair round(s)."
                if selected_simulation_result is not None
                else f"Simulation failed after {len(repair_history)} candidate(s); inspect the repair history."
            ),
            outputs={
                "json": str(simulation_json_path) if simulation_json_path else "",
                "report": str(simulation_report_path) if simulation_report_path else "",
                "history": str(repair_history_path),
            },
            command=simulation_result.command_line() if simulation_result is not None else None,
            exit_code=simulation_result.returncode if simulation_result is not None else None,
        )
        if selected_simulation_result is not None:
            progress_emitter.completed("simulate", "Simulation completed.")
        else:
            progress_emitter.failed("simulate", "Simulation failed; inspect the repair history.")
    else:
        simulation_result = None
        simulation_data = None
        repair_plan = build_repair_plan(
            python_path,
            context=context,
            simulation_json_path=None,
        )
        applied = apply_repair_plan(repair_plan, repaired_path, apply_modeling=apply_modeling)
        repair_history = [
            _repair_history_entry(
                0,
                simulation_status="skipped",
                findings=[action.to_dict() for action in repair_plan.actions],
                repairs_applied=[action.to_dict() for action in applied],
            )
        ]
        repair_history_record["repair_iterations"] = repair_history
        write_json(
            repair_history_path,
            repair_history_record,
        )
        _record_stage(stages, "simulate", "skipped", "Simulation was skipped by request.", outputs={"history": str(repair_history_path)})
        progress_emitter.skipped("simulate", "Simulation was skipped by request.")

    generated_files["repair_history"] = repair_history_path.name
    progress_emitter.started("repair")
    write_json(repair_json_path, repair_plan.to_dict())
    repair_report_path.write_text(render_repair_markdown(repair_plan), encoding="utf-8")
    _record_stage(
        stages,
        "generate_repair_plan",
        "passed",
        "Generated project-aware repair plan and repair history.",
        outputs={"json": str(repair_json_path), "report": str(repair_report_path), "history": str(repair_history_path)},
    )

    _record_stage(
        stages,
        "apply_safe_repairs",
        "passed",
        f"Selected candidate {selected_candidate_index if selected_candidate_index is not None else max(0, len(repair_history) - 1)} for compilation.",
        outputs={"python": str(repaired_path), "history": str(repair_history_path)},
    )
    progress_emitter.completed("repair", "Repair plan is ready.")

    compile_input = repaired_path
    internal_workspace = tempfile.TemporaryDirectory(prefix="tecan-generation-")
    xscr_path = Path(internal_workspace.name) / f"{base}.xscr"
    compile_report_path = out_dir / f"{base}.compile.md"
    finalization_json_path = out_dir / f"{base}.compiled-xscr-finalization.json"
    command_validation_json_path = out_dir / f"{base}.command-validation.json"
    generic_command_validation_json_path = out_dir / f"{base}.generic-command-validation.json"
    canonical_roundtrip_json_path = out_dir / f"{base}.canonical-roundtrip.json"
    compiled_ok = False
    finalization_report = None
    if compile_xscr:
        if simulate and simulation_result is not None and not simulation_result.ok:
            _record_stage(
                stages,
                "compile_xscr",
                "skipped",
                "Compile skipped because simulation failed.",
            )
            progress_emitter.skipped("compile_xscr", "Compile skipped because simulation failed.")
            progress_emitter.skipped("finalize_xscr", "Finalization skipped because compile did not run.")
        else:
            progress_emitter.started("compile_xscr")
            try:
                compile_result = _run_fluentcoder_with_progress(
                    ["compile", compile_input, "-o", xscr_path],
                    catalog_db=catalog_db,
                    progress_emitter=progress_emitter,
                    stage_id="compile_xscr",
                )
            except Exception as exc:
                progress_emitter.failed("compile_xscr", str(exc))
                progress_emitter.skipped("finalize_xscr", "Finalization skipped because compile failed.")
                raise
            compile_report_path.write_text(
                render_compile_markdown(compile_input, xscr_path, compile_result),
                encoding="utf-8",
            )
            compiled_ok = compile_result.ok
            if compile_result.ok:
                progress_emitter.completed("compile_xscr", "Compile completed.")
                progress_emitter.started("finalize_xscr")
                try:
                    finalization_report = finalize_compiled_xscr(
                        xscr_path,
                        ir,
                        context.manifest if context else None,
                        source_script_paths,
                        {"source_ir_origin": "workflow_ir"},
                    )
                except Exception as exc:
                    progress_emitter.failed("finalize_xscr", str(exc))
                    raise
                with compile_report_path.open("a", encoding="utf-8") as handle:
                    handle.write("\n")
                    handle.write(render_compiled_xscr_finalization_markdown(finalization_report))
                finalization_payload = (
                    finalization_report.as_dict()
                    if hasattr(finalization_report, "as_dict")
                    else {}
                )
                write_json(finalization_json_path, finalization_payload)
                write_json(
                    command_validation_json_path,
                    finalization_payload.get("command_validation")
                    or getattr(finalization_report, "command_validation", {}),
                )
                write_json(
                    generic_command_validation_json_path,
                    finalization_payload.get("generic_command_validation")
                    or getattr(finalization_report, "generic_command_validation", {}),
                )
                write_json(
                    canonical_roundtrip_json_path,
                    finalization_payload.get("roundtrip")
                    or getattr(finalization_report, "roundtrip", {}),
                )
                compiled_ok = finalization_report.ok
                if compiled_ok:
                    progress_emitter.completed("finalize_xscr", "XSCR finalization completed.")
                else:
                    progress_emitter.failed("finalize_xscr", "Mandatory XSCR finalization failed.")
            else:
                progress_emitter.failed("compile_xscr", "Compile failed; inspect command output.")
                progress_emitter.skipped("finalize_xscr", "Finalization skipped because compile failed.")
            _record_stage(
                stages,
                "compile_xscr",
                "passed" if compiled_ok else "failed",
                (
                    "Compiled and finalized XSCR draft."
                    if compiled_ok
                    else "Compiled XSCR draft, but mandatory finalization failed; inspect compile report."
                )
                if compile_result.ok
                else "Compile failed; inspect command output.",
                outputs={
                    "compile_report": str(compile_report_path),
                },
                command=compile_result.command_line(),
                exit_code=compile_result.returncode if not compile_result.ok else (0 if compiled_ok else 1),
            )
    else:
        _record_stage(stages, "compile_xscr", "skipped", "Compile was skipped by request.")
        progress_emitter.skipped("compile_xscr", "Compile was skipped by request.")
        progress_emitter.skipped("finalize_xscr", "Finalization skipped because compile was skipped.")

    fluent_context_result = None
    fluent_context_json_path = out_dir / f"{base}.fluent-context-check.json"
    fluent_context_report_path = out_dir / f"{base}.fluent-context-check.md"
    if fluent_context_check:
        if compiled_ok and xscr_path.exists():
            method_name = fluent_method or _fluent_method_from_ir(ir, base)
            fluent_context_result = run_fluent_context_check(
                FluentContextCheckConfig(
                    method=method_name,
                    xscr_path=xscr_path,
                    provider=fluent_provider,
                    host=fluent_host,
                    port=fluent_port,
                    insecure=fluent_insecure,
                    timeout_seconds=fluent_timeout,
                    command=fluent_command,
                )
            )
            write_json(fluent_context_json_path, fluent_context_result)
            fluent_context_report_path.write_text(
                render_fluent_context_check_markdown(fluent_context_result),
                encoding="utf-8",
            )
            _record_stage(
                stages,
                "fluent_context_check",
                "passed" if fluent_context_result.get("ok") else "failed",
                fluent_context_result.get("summary") or "FluentControl import/load diagnostic completed.",
                outputs={
                    "json": str(fluent_context_json_path),
                    "report": str(fluent_context_report_path),
                },
            )
        else:
            _record_stage(
                stages,
                "fluent_context_check",
                "skipped",
                "FluentControl import/load diagnostic skipped because no compiled XSCR was available.",
            )
    else:
        _record_stage(
            stages,
            "fluent_context_check",
            "skipped",
            "Optional FluentControl import/load diagnostic was not requested.",
        )

    traceability_json_path = out_dir / "traceability_map.json"
    traceability_report_path = out_dir / "TRACEABILITY.md"
    traceability_map = build_traceability_map(
        request_spec=request_spec_doc,
        request_spec_path=request_spec_path,
        protocol_ir=ir,
        protocol_ir_path=ir_path,
        python_path=compile_input,
        compiled_xscr_path=xscr_path if xscr_path.exists() else None,
        runtime_report=fluent_context_result,
    )
    if fluent_context_result is not None:
        fluent_context_result = annotate_runtime_report_with_trace(fluent_context_result, traceability_map)
        write_json(fluent_context_json_path, fluent_context_result)
        fluent_context_report_path.write_text(
            render_fluent_context_check_markdown(fluent_context_result),
            encoding="utf-8",
        )
        traceability_map = build_traceability_map(
            request_spec=request_spec_doc,
            request_spec_path=request_spec_path,
            protocol_ir=ir,
            protocol_ir_path=ir_path,
            python_path=compile_input,
            compiled_xscr_path=xscr_path if xscr_path.exists() else None,
            runtime_report=fluent_context_result,
        )
    write_json(traceability_json_path, traceability_map)
    traceability_report_path.write_text(render_traceability_markdown(traceability_map), encoding="utf-8")
    generated_files["traceability_map"] = traceability_json_path.name
    generated_files["traceability_report"] = traceability_report_path.name

    recreate_path = out_dir / "RECREATE_SCRIPT.md"
    recreate_path.write_text(render_recreate_markdown(ir, generated_files=generated_files), encoding="utf-8")
    generated_files["recreate"] = recreate_path.name
    recipe_notes = ((ir.get("source") or {}).get("recipe_group_notes") or []) if isinstance(ir.get("source"), dict) else []
    if recipe_notes:
        notes_path = out_dir / "RECIPE_GROUP_NOTES.md"
        notes_lines = [
            "# Recipe group notes",
            "",
            "Variable/setup bookkeeping kept out of the FluentControl script comments.",
            "",
        ]
        for note in recipe_notes:
            if not isinstance(note, dict):
                continue
            group = str(note.get("group") or "Verification").strip() or "Verification"
            description = str(note.get("description") or "").strip()
            if not description:
                continue
            notes_lines.append(f"## {group}")
            notes_lines.append("")
            notes_lines.append(description)
            notes_lines.append("")
        notes_path.write_text("\n".join(notes_lines).rstrip() + "\n", encoding="utf-8")
        generated_files["recipe_group_notes"] = notes_path.name
        # Also stage under source/ so packaging can publish it into ready-to-import.
        source_notes = out_dir / "source"
        source_notes.mkdir(parents=True, exist_ok=True)
        (source_notes / "RECIPE_GROUP_NOTES.md").write_text(notes_path.read_text(encoding="utf-8"), encoding="utf-8")
    _record_stage(
        stages,
        "generate_recreate_script",
        "passed",
        "Generated manual recreation guide.",
        outputs={"recreate": str(recreate_path)},
    )

    worktable_changes_path = out_dir / "worktable_changes.md"
    worktable_patch_path = out_dir / "worktable.patch.json"
    source_irs = _source_irs_for_paths(source_script_paths)
    worktable_diff = diff_worktable_requirements(
        ir,
        source_manifest=context.manifest if context else None,
        source_irs=source_irs,
    )
    worktable_changes_path.write_text(render_worktable_changes_markdown(worktable_diff), encoding="utf-8")
    worktable_patch_path.write_text(render_worktable_patch_json(worktable_diff), encoding="utf-8")
    generated_files["worktable_changes"] = worktable_changes_path.name
    generated_files["worktable_patch"] = worktable_patch_path.name
    _record_stage(
        stages,
        "generate_worktable_changes",
        "passed",
        "Generated source worktable/context diff and machine-readable patch.",
        outputs={
            "worktable_changes": str(worktable_changes_path),
            "worktable_patch": str(worktable_patch_path),
        },
    )

    source_projects = _context_source_projects(context, primary_only=True)
    filesystem_source_projects = _context_source_projects(context)
    deck_layout_approved = bool(
        approve_deck_layout
        or readiness_gate_request_spec_approved(request_spec_doc, "deck_layout_consistent")
    )
    review = request_spec_doc.setdefault("review", {})
    if isinstance(review, dict):
        review["deck_layout"] = deck_layout_approved
    write_request_spec(request_spec_doc, request_spec_path)
    deck_layout_context_keys = readiness_gate_approval_context_keys("deck_layout_consistent")
    validation_context = {
        "generation_options": generation_options.as_dict(),
        "strict_readiness": bool(strict_readiness),
        "simulation_passed": bool(simulation_result and simulation_result.ok),
        "simulation": simulation_data,
        "repair_plan": repair_plan.to_dict(),
        "compile_passed": compiled_ok,
        "liquid_state": liquid_state_report,
        "checksums_recompute_waived": bool(waive_checksum_recompute),
        "command_inventory_approved": bool(approve_command_inventory),
        "allow_unsupported_raw_xml": bool(approve_unsupported_raw_xml),
        "approved_unsupported_command_ids": list(generation_options.approved_unsupported_command_ids),
        "fluent_context_check_required": bool(fluent_context_check),
        "fluent_context_check": fluent_context_result,
        "repair_history": repair_history_record,
        "compiled_candidate": selected_candidate_index if simulate else None,
        "full_zeia_export": full_zeia_export,
        "partial_zeia_export_approved": approve_partial_zeia,
        "context_kind": _context_kind(context) if context else None,
        "source_contexts": _context_sources(context),
        "host_instrument_configuration": host_config_report,
        "traceability": traceability_map,
    }
    for approval_key in deck_layout_context_keys:
        validation_context[approval_key] = deck_layout_approved
    validation_report = None
    final_validation_report = None
    validation_report_path = out_dir / "ready_validation.md"
    validation_report_json_path = out_dir / "validation_report.json"
    progress_emitter.started("validate_bundle")
    if compiled_ok and xscr_path.exists():
        validation_report = validate_ready_to_import(
            compiled_xscr=xscr_path,
            draft_path=compile_input,
            protocol_ir=ir_path,
            expression_provenance=expression_provenance_path,
            worklist=gwl_path if gwl_path.exists() else None,
            source_projects=source_projects,
            source_scripts=source_script_paths,
            provenance_source_artifacts=[
                *filesystem_source_projects,
                *source_script_paths,
            ],
            source_manifest=context.manifest if context else None,
            recreate_guide=recreate_path,
            validation_context=validation_context,
        )
        traceability_map = build_traceability_map(
            request_spec=request_spec_doc,
            request_spec_path=request_spec_path,
            protocol_ir=ir,
            protocol_ir_path=ir_path,
            python_path=compile_input,
            compiled_xscr_path=xscr_path if xscr_path.exists() else None,
            validation_report=validation_report,
            runtime_report=fluent_context_result,
        )
        validation_context["traceability"] = traceability_map
        write_json(traceability_json_path, traceability_map)
        traceability_report_path.write_text(render_traceability_markdown(traceability_map), encoding="utf-8")
        final_validation_report = validation_report
        _record_stage(
            stages,
            "validate_ready_gates",
            "passed" if validation_report["ready"] else "failed",
            (
                (validation_report.get("offline_validation") or {}).get("summary")
                or "All required offline ready gates passed."
            )
            if validation_report["ready"]
            else validation_failure_message(validation_report),
            outputs={"validation_report": str(validation_report_path)},
        )
        if validation_report["ready"]:
            progress_emitter.completed("validate_bundle", "Ready gates passed.")
        else:
            progress_emitter.failed("validate_bundle", validation_failure_message(validation_report))
    else:
        if not compile_xscr:
            scaffold_reason = (
                "Compile was skipped by request (for example `--no-compile`), so ready "
                "validation could not run. This output is an unvalidated scaffold."
            )
        elif simulate and simulation_result is not None and not simulation_result.ok:
            scaffold_reason = (
                "Compile was skipped because simulation failed, so ready validation "
                "could not run. This output is an unvalidated scaffold."
            )
        else:
            scaffold_reason = (
                "Compile did not produce a usable XSCR, so ready validation could not "
                "run. This output is an unvalidated scaffold."
            )
        scaffold_report = scaffold_validation_report(scaffold_reason)
        scaffold_report["host_instrument_configuration"] = host_config_report
        final_validation_report = scaffold_report
        _record_stage(
            stages,
            "validate_ready_gates",
            "skipped",
            f"Ready validation skipped (scaffold only): {scaffold_reason} "
            f"See `{validation_report_path.name}`.",
            outputs={"validation_report": str(validation_report_path)},
        )
        progress_emitter.skipped("validate_bundle", "Ready validation skipped.")

    validation_diff = build_request_validation_diff(
        request_spec=request_spec_doc,
        protocol_ir=ir,
        request_spec_path=request_spec_path,
        protocol_ir_path=ir_path,
        generated_files=generated_files,
        worktable_diff=worktable_diff,
        validation_report=validation_report,
    )
    validation_diff_json_path = out_dir / "validation_diff.json"
    validation_diff_report_path = out_dir / "validation_diff.md"
    write_json(validation_diff_json_path, validation_diff)
    validation_diff_report_path.write_text(render_request_validation_diff_markdown(validation_diff), encoding="utf-8")
    generated_files["validation_diff"] = validation_diff_report_path.name
    generated_files["validation_diff_json"] = validation_diff_json_path.name
    _record_stage(
        stages,
        "generate_validation_diff",
        validation_diff["status"],
        f"Request validation diff is {validation_diff['status']}.",
        outputs={
            "json": str(validation_diff_json_path),
            "report": str(validation_diff_report_path),
        },
    )

    package_outputs: list[str] = []
    generated_zeia_paths: list[Path] = []
    export_summary: dict[str, Any] = {}
    bundle_stage: ReadyBundleStage | None = None
    bundle_published = False
    progress_emitter.started("publish_bundle")
    if compiled_ok and xscr_path.exists() and validation_report and validation_report["ready"]:
        reports = [
            plan_path,
            inspection_report,
            host_config_report_path,
            host_config_json_path,
            selection_path,
            ir_path,
            rga_policy_report_path,
            rga_policy_json_path,
            repair_report_path,
            repair_json_path,
            repair_history_path,
            liquid_state_report_path,
            liquid_state_json_path,
            variable_reconciliation_report_path,
            variable_reconciliation_json_path,
            expression_provenance_path,
        ]
        if simulation_report_path.exists():
            reports.append(simulation_report_path)
        if simulation_json_path.exists():
            reports.append(simulation_json_path)
        if validation_report_path.exists():
            reports.append(validation_report_path)
        if validation_report_json_path.exists():
            reports.append(validation_report_json_path)
        if fluent_context_report_path.exists():
            reports.append(fluent_context_report_path)
        if fluent_context_json_path.exists():
            reports.append(fluent_context_json_path)
        for finalization_artifact in (
            finalization_json_path,
            command_validation_json_path,
            generic_command_validation_json_path,
            canonical_roundtrip_json_path,
        ):
            if finalization_artifact.exists():
                reports.append(finalization_artifact)
        reports.append(traceability_report_path)
        reports.append(traceability_json_path)
        try:
            bundle_stage = export_ready_to_import(
                xscr_path,
                bundle_name=base,
                context_name=context.name if context else None,
                draft_path=compile_input,
                protocol_ir=ir_path,
                expression_provenance=expression_provenance_path,
                worklist=gwl_path if gwl_path.exists() else None,
                source_projects=source_projects,
                filesystem_source_projects=filesystem_source_projects,
                source_scripts=source_script_paths,
                source_manifest=context.manifest if context else None,
                worktable_changes=worktable_changes_path,
                worktable_patch=worktable_patch_path,
                recreate_guide=recreate_path,
                request_spec=request_spec_path,
                validation_diff=validation_diff_report_path,
                validation_diff_json=validation_diff_json_path,
                reports=reports,
                report_files={
                    **_context_project_report(context),
                    "simulation_report": simulation_report_path,
                    "repair_plan": repair_report_path,
                    "repair_history": repair_history_path,
                    "traceability": traceability_report_path,
                    "traceability_json": traceability_json_path,
                    "compile_report": compile_report_path,
                },
                validation_context=validation_context,
                target_script_folder=target_script_folder,
                export_summary=export_summary,
                publish=False,
            )
            published_exports = publish_ready_to_import_zeia(bundle_stage)
            bundle_published = True
            package_outputs = [str(artifact.destination) for artifact in published_exports]
            generated_zeia_paths = [artifact.destination for artifact in published_exports]
            _record_stage(
                stages,
                "package_ready_to_import",
                "passed",
                "Published the validated ZEIA and companion artifacts atomically into its ready-to-import protocol folder.",
                outputs={
                    "protocol_folder": str(_ready_protocol_folder_from_zeia(package_outputs[0]))
                    if package_outputs
                    else None,
                    "artifacts": package_outputs,
                },
            )
            progress_emitter.completed("publish_bundle", "Ready-to-import protocol delivery folder published.")
        except PipelineError as exc:
            _record_stage(
                stages,
                "package_ready_to_import",
                "failed",
                str(exc),
            )
            progress_emitter.failed("publish_bundle", str(exc))
    else:
        _record_stage(
            stages,
            "package_ready_to_import",
            "skipped",
            "No validated compiled XSCR was available to package.",
        )
        progress_emitter.skipped("publish_bundle", "No validated compiled XSCR was available to package.")

    packaged_validation_report = export_summary.get("final_validation_report")
    if isinstance(packaged_validation_report, dict):
        final_validation_report = packaged_validation_report
    packaged_readiness = export_summary.get("readiness")
    packaged_readiness_status = export_summary.get("readiness_status")

    packaged_bundle_dir = None
    ready_to_import = _generation_published_zeia_success(
        package_outputs,
        validation_report=final_validation_report,
        require_final_reports=False,
    )
    if ready_to_import:
        workflow_status = "ready_to_import"
    elif validation_report is not None:
        workflow_status = "validated_not_ready"
    else:
        workflow_status = "scaffold_not_validated"
    readiness = (
        packaged_readiness
        if isinstance(packaged_readiness, dict)
        else build_canonical_readiness(
            validation_report=final_validation_report,
            package_outputs=package_outputs,
        )
    )
    readiness_status = (
        str(packaged_readiness_status)
        if isinstance(packaged_readiness_status, str) and packaged_readiness_status
        else readiness_status_from_readiness(
            readiness,
            workflow_status=workflow_status,
        )
    )
    if final_validation_report is not None:
        embed_readiness(
            final_validation_report,
            readiness=readiness,
            readiness_status=readiness_status,
        )
        validation_report_path.write_text(render_validation_markdown(final_validation_report), encoding="utf-8")
        write_json(validation_report_json_path, final_validation_report)
    failed_artifacts_dir = None
    if generation_options.preserve_failed_artifacts and not ready_to_import and xscr_path.exists():
        failed_artifacts_dir = _preserve_failed_generation_artifacts(
            out_dir=out_dir,
            base=base,
            xscr_path=xscr_path,
            reports=[
                compile_report_path,
                validation_report_path,
                validation_report_json_path,
                traceability_report_path,
                traceability_json_path,
            ],
        )
    published_artifacts = _published_artifact_records(package_outputs)
    internal_artifacts = _internal_artifact_records(
        compiled=compiled_ok and xscr_path.exists(),
        preserved_path=failed_artifacts_dir / "protocol.xscr" if failed_artifacts_dir else None,
    )
    deliverable = published_artifacts[0] if ready_to_import and published_artifacts else None

    lifecycle = lifecycle_metadata(
        bundle_role="ready" if ready_to_import else "debug",
        source_export_kind=source_export_kind(full_zeia_export, approved_partial=approve_partial_zeia),
        verification_state=verification_state_from_readiness(
            ready_to_import=ready_to_import,
            readiness=readiness,
            workflow_status=workflow_status,
        ),
        created_from=created_from_record(
            context_name=context.name if context else None,
            context_kind=_context_kind(context) if context else None,
            source_contexts=_context_sources(context),
            source_projects=source_projects,
        ),
    )

    manifest = {
        "workflow": "request_spec_ir_artifacts_validation_diff_ready_bundle",
        "workflow_status": workflow_status,
        "readiness_status": readiness_status,
        "readiness": readiness,
        "generation_options": generation_options.as_dict(),
        "ready_to_import": ready_to_import,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bundle_role": lifecycle["bundle_role"],
        "source_export_kind": lifecycle["source_export_kind"],
        "verification_state": lifecycle["verification_state"],
        "supersedes": lifecycle["supersedes"],
        "superseded_by": lifecycle["superseded_by"],
        "lifecycle": lifecycle,
        "intent": intent,
        "intent_summary": intent_summary,
        "verbatim_prompt": verbatim_prompt,
        "request_spec": str(request_spec_path),
        "request_spec_source": str(request_spec_source) if request_spec_source else None,
        "context": context.name if context else None,
        "context_kind": _context_kind(context) if context else None,
        "source_contexts": _context_sources(context),
        "full_zeia_export": full_zeia_export,
        "partial_zeia_export_approved": approve_partial_zeia,
        "host_instrument_configuration": host_config_report,
        "host_instrument_config_report": str(host_config_report_path),
        "host_instrument_config_json": str(host_config_json_path),
        "out_dir": str(out_dir),
        "pattern_index": str(index_db) if index_db else None,
        "indexed_pattern_count": len(indexed_pattern_windows),
        "protocol_ir": str(ir_path),
        "ir_synthesis": str(synthesis_path) if synthesis_path else None,
        "rga_move_policy": str(rga_policy_report_path),
        "rga_move_policy_json": str(rga_policy_json_path),
        "liquid_state_validation": str(liquid_state_report_path),
        "simulation_report": str(simulation_report_path) if simulation_report_path and simulation_report_path.exists() else None,
        "simulation_json": str(simulation_json_path) if simulation_json_path and simulation_json_path.exists() else None,
        "repair_history": str(repair_history_path),
        "python_draft": str(python_path),
        "repaired_draft": str(repaired_path),
        "compiled_candidate": selected_candidate_index if simulate else None,
        "compiled_xscr": None,
        "compile_report": str(compile_report_path) if compile_report_path.exists() else None,
        "compiled_xscr_finalization": finalization_report.as_dict() if finalization_report else None,
        "compiled_xscr_finalization_json": str(finalization_json_path) if finalization_json_path.exists() else None,
        "command_validation_json": str(command_validation_json_path) if command_validation_json_path.exists() else None,
        "generic_command_validation_json": str(generic_command_validation_json_path)
        if generic_command_validation_json_path.exists()
        else None,
        "canonical_roundtrip_json": str(canonical_roundtrip_json_path) if canonical_roundtrip_json_path.exists() else None,
        "fluent_context_check": str(fluent_context_report_path) if fluent_context_report_path.exists() else None,
        "fluent_context_check_json": str(fluent_context_json_path) if fluent_context_json_path.exists() else None,
        "traceability_map": str(traceability_json_path),
        "traceability_report": str(traceability_report_path),
        "recreate_script": str(recreate_path),
        "worktable_changes": str(worktable_changes_path),
        "worktable_patch": str(worktable_patch_path),
        "ready_validation": str(validation_report_path) if validation_report_path.exists() else None,
        "validation_report_json": str(validation_report_json_path) if validation_report_json_path.exists() else None,
        "validation_diff": str(validation_diff_report_path),
        "validation_diff_json": str(validation_diff_json_path),
        "packaged_bundle_dir": str(packaged_bundle_dir) if packaged_bundle_dir else None,
        "published_zeia_path": str(Path(package_outputs[0])) if package_outputs else None,
        "published_protocol_folder": str(_ready_protocol_folder_from_zeia(package_outputs[0])) if package_outputs else None,
        "ready_to_import_artifacts": package_outputs,
        "deliverables": _deliverable_artifact_records(package_outputs),
        "companion_artifacts": _companion_artifact_records(package_outputs),
        "published_artifacts": published_artifacts,
        "internal_artifacts": internal_artifacts,
        "deliverable": deliverable,
        "failed_artifacts": str(failed_artifacts_dir) if failed_artifacts_dir else None,
        "stages": stages,
    }
    _attach_manifest_provenance(
        manifest,
        context=context,
        request_spec_doc=request_spec_doc,
        request_spec_path=request_spec_path,
        request_spec_source=request_spec_source,
        ir_path=ir_path,
        ir_source=ir_source,
        python_path=python_path,
        repaired_path=repaired_path,
        xscr_path=None,
        generated_zeia_paths=generated_zeia_paths,
        generation_options=generation_options,
        repair_history_path=repair_history_path,
        repair_history=repair_history,
        repair_plan_path=repair_json_path,
        repair_report_path=repair_report_path,
        finalization_report=finalization_report,
        simulation_backend=simulation_backend,
    )
    if simulate:
        manifest["repair_iterations"]["termination_reason"] = repair_loop_termination_reason
        manifest["repair_iterations"]["selected_candidate"] = selected_candidate_index
    manifest_path = out_dir / "generation_manifest.json"
    write_json(manifest_path, manifest)
    summary_path = out_dir / "GENERATION_WORKFLOW.md"
    summary_path.write_text(render_generation_summary(manifest), encoding="utf-8")
    if ready_to_import and package_outputs:
        attach_generation_reports_to_protocol_folders(
            package_outputs,
            ready_root=READY_TO_IMPORT_DIR,
            generation_manifest=manifest_path,
            workflow_report=summary_path,
            companion_files={
                "RECREATE_SCRIPT.md": recreate_path,
                "RECIPE_GROUP_NOTES.md": out_dir / "RECIPE_GROUP_NOTES.md",
                "request.spec.yaml": request_spec_path,
                "protocol.ir.json": ir_path,
                "generated/protocol.py": python_path,
                "reports/readiness.md": validation_report_path,
                "reports/validation.json": validation_report_json_path,
                "reports/simulation.md": simulation_report_path,
                "reports/simulation.json": simulation_json_path,
                "reports/compile.md": compile_report_path,
                "reports/compiled-xscr-finalization.json": finalization_json_path,
                "reports/command-validation.json": command_validation_json_path,
                "reports/generic-command-validation.json": generic_command_validation_json_path,
                "reports/canonical-roundtrip.json": canonical_roundtrip_json_path,
                "reports/repair-plan.md": repair_report_path,
                "reports/repair-plan.json": repair_json_path,
                "reports/repair-history.json": repair_history_path,
                "reports/liquid-state.md": liquid_state_report_path,
                "reports/liquid-state.json": liquid_state_json_path,
                "reports/variable-reconciliation.md": variable_reconciliation_report_path,
                "reports/variable-reconciliation.json": variable_reconciliation_json_path,
                "reports/expression_provenance.json": expression_provenance_path,
                "reports/traceability.md": traceability_report_path,
                "reports/traceability.json": traceability_json_path,
                "reports/validation_diff.md": validation_diff_report_path,
                "reports/validation_diff.json": validation_diff_json_path,
                "reports/worktable_changes.md": worktable_changes_path,
                "reports/worktable.patch.json": worktable_patch_path,
            },
        )
        if not _generation_published_zeia_success(
            package_outputs,
            validation_report=final_validation_report,
            require_final_reports=True,
        ):
            raise PipelineError("Published protocol folder failed strict V2 bundle validation after final report attachment.")
    if bundle_stage is not None and not bundle_published:
        cleanup_ready_to_import_stage(bundle_stage)
    internal_workspace.cleanup()

    return {
        **manifest,
        "generation_manifest": str(manifest_path),
        "workflow_report": str(summary_path),
    }


def inspect_generation_context(
    context: ProjectLike | None,
    selected_scripts: list[str],
) -> dict[str, Any]:
    if context is None:
        return {
            "context": None,
            "context_kind": None,
            "source_contexts": [],
            "scripts": [],
            "worktables": [],
            "snapshot_evidence": [],
            "snapshot_summary": {},
            "liquid_classes": [],
            "labware_names": [],
            "selected_source_scripts": [],
        }

    manifest = context.manifest
    selected = []
    for value in selected_scripts:
        selected.append(_source_script_record(context, value))
    return {
        "context": context.name,
        "context_kind": _context_kind(context),
        "source_contexts": _context_sources(context),
        "manifest": str(context.root / "manifest.json"),
        "project_report": str(context.root / "project_report.md"),
        "scripts": [
            {
                "source_context": script.get("source_context"),
                "qualified_name": script.get("qualified_name"),
                "object_name": script.get("object_name"),
                "entry": script.get("entry"),
                "extracted_path": script.get("extracted_path"),
                "command_count": script.get("command_count"),
                "family_counts": script.get("family_counts", {}),
                "dependencies": script.get("dependencies", {}),
            }
            for script in manifest.get("scripts", [])
        ],
        "worktables": [
            {
                "source_context": item.get("source_context"),
                "qualified_name": item.get("qualified_name"),
                "object_name": item.get("object_name"),
                "entry": item.get("entry"),
                "extracted_path": item.get("extracted_path"),
                "guids": item.get("guids", []),
            }
            for item in manifest.get("workspaces", [])
        ],
        "snapshot_evidence": [
            {
                "source_context": item.get("source_context"),
                "object_name": item.get("object_name"),
                "entry": item.get("entry"),
                "extracted_path": item.get("extracted_path"),
                "roles": item.get("roles", []),
                "signals": item.get("signals", []),
                "summary": item.get("summary", ""),
            }
            for item in manifest.get("snapshot_evidence", [])
        ],
        "snapshot_summary": manifest.get("snapshot_summary", {}),
        "liquid_classes": manifest.get("liquid_classes", []),
        "labware_names": manifest.get("labware_names", []),
        "rack_types": manifest.get("rack_types", []),
        "selected_source_scripts": selected,
    }


def build_seed_protocol_ir(
    *,
    intent: str,
    context: ProjectLike | None,
    selected_scripts: list[dict[str, Any]],
    pattern_refs: list[str],
    pattern_windows: list[dict[str, Any]] | None = None,
    protocol_name: str | None = None,
    preferred_worktable: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    name = protocol_name or _protocol_name_from_intent(intent)
    worktable = _default_worktable(context, selected_scripts, preferred=preferred_worktable)
    pattern_window_summaries = summarize_pattern_windows(pattern_windows or [])
    dependencies = []
    if context is not None:
        for liquid_class in context.manifest.get("liquid_classes", [])[:50]:
            dependencies.append({"kind": "liquid_class", "name": liquid_class, "required": True})
    for script in selected_scripts:
        dependencies.append(
            {
                "kind": "source_script",
                "name": script.get("object_name") or script.get("entry") or "",
                "source_path": script.get("extracted_path") or script.get("entry") or "",
                "required": False,
            }
        )
    for pattern in pattern_refs:
        if str(pattern).startswith("pattern:"):
            continue
        dependencies.append({"kind": "source_pattern", "name": pattern, "required": False})
    dependencies.extend(pattern_window_dependencies(pattern_windows or []))

    ir = {
        "ir_version": CANONICAL_IR_VERSION,
        "id": _safe_id(name),
        "protocol": {
            "name": name,
            "comment": intent,
            "schema_intent": "canonical source of truth for generated Tecan artifacts",
        },
        "source": {
            "format": "generation_request",
            "intent": intent,
            "verbatim_prompt": intent,
            "context": context.name if context else None,
            "context_kind": _context_kind(context) if context else None,
            "contexts": _context_sources(context),
            "selected_source_scripts": selected_scripts,
            "selected_patterns": pattern_refs,
            "selected_pattern_windows": pattern_window_summaries,
        },
        "worktable": worktable,
        "labware": [],
        "reagents": [],
        "liquid_classes": [
            {"name": name, "role": "candidate"}
            for name in (context.manifest.get("liquid_classes", [])[:20] if context else [])
        ],
        "variables": [],
        "worklists": [],
        "dependencies": dependencies,
        "safety_assumptions": [
            {
                "id": "codex_must_fill_steps",
                "text": "This seed IR is a scaffold. Codex must fill ordered steps from selected source scripts/patterns before final compile.",
            },
            {
                "id": "manual_validation_required",
                "text": "Generated artifacts must be reviewed, simulated, and validated in FluentControl before instrument use.",
            },
        ],
        "steps": [],
    }
    synthesize_seed_ir(
        ir,
        selected_scripts=selected_scripts,
        pattern_windows=pattern_windows or [],
        context_manifest=context.manifest if context else None,
    )
    return ir


def render_generation_plan(
    intent: str,
    context: ProjectLike | None,
    selection: dict[str, Any],
    stages: list[dict[str, Any]],
) -> str:
    lines = [
        "# Fluent Generation Plan",
        "",
        "- Original request prompt:",
        "",
        *_verbatim_prompt_block(str(selection.get("verbatim_prompt") or intent)),
        "",
        f"- Intent summary: {selection.get('intent_summary') or intent}",
        f"- Context: `{context.name if context else 'none'}`",
        f"- Context kind: `{_context_kind(context) if context else 'none'}`",
        "",
        "## Official Pipeline",
        "",
    ]
    for idx, (stage_id, title) in enumerate(GENERATION_STAGES, start=1):
        lines.append(f"{idx}. {title}")
        lines.append(f"   - Stage ID: `{stage_id}`")

    lines.extend(["", "## Selected Source Scripts", ""])
    if selection.get("resolved_source_scripts"):
        for script in selection["resolved_source_scripts"]:
            prefix = f"{script.get('source_context')}:" if script.get("source_context") else ""
            lines.append(f"- `{prefix}{script.get('object_name') or script.get('entry')}`")
            lines.append(f"  - Path: `{script.get('extracted_path') or script.get('entry')}`")
    else:
        lines.append("- none selected yet")

    lines.extend(["", "## Selected Pattern References", ""])
    if selection.get("pattern_refs"):
        for pattern in selection["pattern_refs"]:
            lines.append(f"- `{pattern}`")
    else:
        lines.append("- none selected yet")

    lines.extend(["", "## Selected Mined Pattern Windows", ""])
    if selection.get("indexed_pattern_windows"):
        for pattern in selection["indexed_pattern_windows"]:
            command_range = pattern.get("command_range") or {}
            lines.append(f"- `pattern:{pattern.get('id')}` {pattern.get('name')}")
            lines.append(f"  - Type: `{pattern.get('pattern_type')}`")
            lines.append(f"  - Source script: `{pattern.get('source_script') or pattern.get('source_path')}`")
            lines.append(
                "  - Command window: "
                f"`{command_range.get('start')}` to `{command_range.get('end')}`"
            )
            lines.append(f"  - Signature: `{pattern.get('command_signature')}`")
    else:
        lines.append("- none selected yet")

    lines.extend(
        [
            "",
            "## Codex Instructions",
            "",
            "- Review `request.spec.yaml` before editing protocol behavior.",
            "- Prefer exact worktable, labware, liquid class, worklist, and command names from the selected sources.",
            "- Build or edit `protocol.ir.json` first; regenerate Python, XSCR, GWL, and recreate docs from IR.",
            "- Simulate before compiling. Generate a repair plan from simulation output before applying repairs.",
            "- Read `validation_diff.md` before copying or claiming a ready bundle.",
            "- Package only compiled drafts that have passed the configured gates.",
            "",
        ]
    )
    return "\n".join(lines)


def _verbatim_prompt_block(prompt: str) -> list[str]:
    opening_fence, closing_fence = _markdown_fences_for(prompt)
    return [opening_fence, prompt, closing_fence]


def _markdown_fences_for(text: str) -> tuple[str, str]:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}text", fence


def render_rga_move_policy_markdown(policy: dict[str, Any]) -> str:
    lines = [
        "# RGA Move Pattern Policy",
        "",
        f"- Policy: {policy.get('policy') or 'physical RGA/gripper moves require mined source evidence'}",
        f"- Pattern-backed moves: `{len(policy.get('pattern_backed') or [])}`",
        f"- Manual fallback moves: `{len(policy.get('manual_fallback') or [])}`",
        "",
        "## Pattern-Backed Moves",
        "",
    ]
    backed = policy.get("pattern_backed") or []
    if backed:
        for item in backed:
            pattern = item.get("source_pattern") or {}
            lines.append(f"- `{item.get('name') or item.get('step_id') or 'move_plate'}`")
            lines.append(f"  - Labware: `{item.get('labware') or 'unknown'}`")
            if item.get("onto_labware"):
                lines.append(f"  - Onto: `{item.get('onto_labware')}`")
            lines.append(f"  - Pattern ID: `{pattern.get('source_pattern_id') or 'unknown'}`")
            lines.append(f"  - Pattern type: `{pattern.get('source_pattern_type') or 'unknown'}`")
            lines.append(f"  - Source script: `{pattern.get('source_script') or item.get('source_path') or 'unknown'}`")
            if pattern.get("command_index") not in (None, "", []):
                lines.append(f"  - Command index: `{pattern.get('command_index')}`")
    else:
        lines.append("- none")

    lines.extend(["", "## Manual Fallback Moves", ""])
    fallback = policy.get("manual_fallback") or []
    if fallback:
        for item in fallback:
            lines.append(f"- `{item.get('name') or item.get('step_id') or 'move_plate'}`")
            lines.append(f"  - Labware: `{item.get('labware') or 'unknown'}`")
            if item.get("onto_labware"):
                lines.append(f"  - Onto: `{item.get('onto_labware')}`")
            lines.append(f"  - Reason: `{item.get('reason') or 'rga_move_requires_mined_source_pattern'}`")
            if item.get("source_path"):
                lines.append(f"  - Source path: `{item.get('source_path')}`")
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "Pattern-backed moves may render as automated gripper commands, subject to the normal worktable and validation gates.",
            "Manual fallback moves are rendered as operator prompts and are not claimed as automated RGA motion.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_context_inspection_markdown(inspection: dict[str, Any]) -> str:
    lines = [
        "# Generation Context Inspection",
        "",
        f"- Context: `{inspection.get('context') or 'none'}`",
        f"- Context kind: `{inspection.get('context_kind') or 'none'}`",
        f"- Scripts: `{len(inspection.get('scripts', []))}`",
        f"- Worktables: `{len(inspection.get('worktables', []))}`",
        f"- Snapshot evidence: `{len(inspection.get('snapshot_evidence', []))}`",
        f"- Liquid classes: `{len(inspection.get('liquid_classes', []))}`",
        f"- Labware names: `{len(inspection.get('labware_names', []))}`",
        "",
    ]
    if inspection.get("source_contexts"):
        lines.extend(["## Source Projects", ""])
        for source in inspection["source_contexts"]:
            lines.append(f"- `{source.get('name')}`")
        lines.append("")
    if inspection.get("worktables"):
        lines.extend(["## Worktables", ""])
        for worktable in inspection["worktables"][:30]:
            prefix = f"{worktable.get('source_context')}:" if worktable.get("source_context") else ""
            lines.append(f"- `{prefix}{worktable.get('object_name')}`")
            lines.append(f"  - Path: `{worktable.get('extracted_path') or worktable.get('entry')}`")
    if inspection.get("snapshot_evidence"):
        lines.extend(["", "## Snapshot Evidence", ""])
        summary = inspection.get("snapshot_summary") or {}
        role_counts = summary.get("role_counts") or {}
        for role, count in role_counts.items():
            lines.append(f"- `{role}`: `{count}`")
        for item in inspection["snapshot_evidence"][:20]:
            prefix = f"{item.get('source_context')}:" if item.get("source_context") else ""
            roles = ", ".join(item.get("roles") or [])
            lines.append(f"- `{prefix}{item.get('object_name')}` ({roles})")
            lines.append(f"  - Path: `{item.get('extracted_path') or item.get('entry')}`")
    if inspection.get("selected_source_scripts"):
        lines.extend(["", "## Selected Source Scripts", ""])
        for script in inspection["selected_source_scripts"]:
            prefix = f"{script.get('source_context')}:" if script.get("source_context") else ""
            lines.append(f"- `{prefix}{script.get('object_name') or script.get('entry')}`")
            lines.append(f"  - Commands: `{script.get('command_count')}`")
            lines.append(f"  - Path: `{script.get('extracted_path') or script.get('entry')}`")
    return "\n".join(lines).rstrip() + "\n"


_WORKFLOW_STATUS_LABELS = {
    "ready_to_import": "ready to import (all required gates passed and bundle packaged)",
    "validated_not_ready": "NOT ready to import (ready validation ran but one or more gates failed)",
    "scaffold_not_validated": "NOT validated (scaffold only; ready validation did not run)",
    "needs_full_zeia_export": "needs user input (full ZEIA export required before generation)",
}


def _ready_bundle_dir_from_artifacts(artifact_paths: list[str | Path]) -> Path | None:
    ready_root = READY_TO_IMPORT_DIR.resolve()
    for value in artifact_paths:
        try:
            relative = Path(value).resolve().relative_to(ready_root)
        except (OSError, ValueError):
            continue
        if relative.parts:
            return ready_root / relative.parts[0]
    return None


def _generation_published_zeia_success(
    artifact_paths: list[str | Path],
    *,
    validation_report: dict[str, Any] | None,
    require_final_reports: bool = True,
) -> bool:
    if not (validation_report and validation_report.get("ready")):
        return False
    ready_root = READY_TO_IMPORT_DIR.resolve()
    published = []
    for value in artifact_paths:
        path = Path(value)
        try:
            resolved = path.resolve()
        except OSError:
            return False
        if resolved.suffix.lower() != ".zeia":
            return False
        if resolved.parent.parent != ready_root:
            return False
        if resolved.parent.name != resolved.stem:
            return False
        if not resolved.exists():
            return False
        delivery_validation = validate_v2_delivery_bundle(
            resolved.parent,
            protocol_name=resolved.stem,
            require_final_reports=require_final_reports,
        )
        if not delivery_validation.ok:
            return False
        published.append(resolved)
    return bool(published)


def _protocol_delivery_folder_complete(
    protocol_folder: Path,
    *,
    protocol_name: str,
    require_final_reports: bool = True,
) -> bool:
    required = [
        protocol_folder / f"{protocol_name}.zeia",
        protocol_folder / "RECREATE_SCRIPT.md",
        protocol_folder / "request.spec.yaml",
        protocol_folder / "protocol.ir.json",
        protocol_folder / "generated" / "protocol.py",
        protocol_folder / "reports",
    ]
    if require_final_reports:
        required.extend(
            [
                protocol_folder / "generation_manifest.json",
                protocol_folder / "GENERATION_WORKFLOW.md",
            ]
        )
    return all(path.exists() for path in required)


def _ready_protocol_folder_from_zeia(value: str | Path) -> Path | None:
    path = Path(value)
    if path.suffix.lower() != ".zeia":
        return None
    return path.parent


def _deliverable_artifact_records(artifact_paths: list[str | Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in artifact_paths:
        path = Path(value)
        if path.suffix.lower() != ".zeia":
            continue
        records.append(
            {
                "kind": "fluent_project_archive",
                "path": path.name,
                "absolute_path": str(path),
                "protocol_folder": str(path.parent),
            }
        )
    return records


def _companion_artifact_records(artifact_paths: list[str | Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in artifact_paths:
        folder = _ready_protocol_folder_from_zeia(value)
        if folder is None:
            continue
        for kind, relative in (
            ("recreation_instructions", "RECREATE_SCRIPT.md"),
            ("workflow_report", "GENERATION_WORKFLOW.md"),
            ("generation_manifest", "generation_manifest.json"),
            ("request_specification", "request.spec.yaml"),
            ("protocol_ir", "protocol.ir.json"),
            ("generated_python", "generated/protocol.py"),
            ("reports", "reports/"),
        ):
            records.append(
                {
                    "kind": kind,
                    "path": relative,
                    "absolute_path": str(folder / relative.rstrip("/")),
                }
            )
    return records


def _published_artifact_records(artifact_paths: list[str | Path]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for value in artifact_paths:
        path = Path(value)
        if path.suffix.lower() != ".zeia":
            continue
        records.append(
            {
                "kind": "fluent_project_archive",
                "path": str(path),
                "protocol_folder": str(path.parent),
                "visibility": "published",
                "deliverable": True,
            }
        )
    return records


def _internal_artifact_records(
    *,
    compiled: bool,
    preserved_path: Path | None = None,
) -> list[dict[str, Any]]:
    if not compiled:
        return []
    return [
        {
            "kind": "compiled_xscr_intermediate",
            "path": str(preserved_path) if preserved_path else None,
            "visibility": "internal",
            "deliverable": False,
            "retained": preserved_path is not None,
        }
    ]


def _preserve_failed_generation_artifacts(
    *,
    out_dir: Path,
    base: str,
    xscr_path: Path,
    reports: list[Path],
) -> Path:
    failed_root = out_dir / "failed_artifacts"
    failed_root.mkdir(parents=True, exist_ok=True)
    run_dir = Path(
        tempfile.mkdtemp(
            prefix=f"{_failed_artifact_prefix(base)}-",
            dir=failed_root,
        )
    )
    shutil.copy2(xscr_path, run_dir / "protocol.xscr")
    reports_dir = run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    for report in reports:
        if report.exists():
            shutil.copy2(report, reports_dir / report.name)
    return run_dir


def _failed_artifact_prefix(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return cleaned[:80] or "generation"


def _build_readiness_profile(
    *,
    workflow_status: str,
    ready_to_import: bool,
    validation_report: dict[str, Any] | None,
    package_outputs: list[str],
) -> dict[str, Any]:
    del workflow_status, ready_to_import
    return build_canonical_readiness(
        validation_report=validation_report,
        package_outputs=package_outputs,
    )


def render_generation_summary(manifest: dict[str, Any]) -> str:
    status = str(manifest.get("workflow_status") or "scaffold_not_validated")
    status_label = _WORKFLOW_STATUS_LABELS.get(status, status)
    readiness = manifest.get("readiness") or {}
    lines = [
        "# Fluent Generation Workflow",
        "",
        f"- Status: `{status}` — {status_label}",
        f"- Readiness status: `{manifest.get('readiness_status') or status}`",
        f"- Ready to import: `{bool(manifest.get('ready_to_import'))}`",
        f"- Ready-to-import protocol folder: `{manifest.get('published_protocol_folder') or 'none'}`",
        f"- Ready-to-import ZEIA: `{manifest.get('published_zeia_path') or 'none'}`",
        "- Artifact policy: `.xscr` files are internal intermediates; a validated `.zeia` is the only deliverable.",
        f"- Bundle role: `{manifest.get('bundle_role') or 'unknown'}`",
        f"- Source export kind: `{manifest.get('source_export_kind') or 'unknown'}`",
        f"- Verification state: `{manifest.get('verification_state') or 'unknown'}`",
    ]
    if manifest.get("ready_to_import") and (readiness.get("script_editor_load") or {}).get("status") != "load_clean":
        load_status = (readiness.get("script_editor_load") or {}).get("status")
        lines.extend(
            [
                "",
                "> "
                + (
                    "IMPORT READY, LOAD FAILED. The bundle passed offline import gates, but the optional "
                    "FluentControl import/load diagnostic reported a load failure. Resolve the Script Editor "
                    "load error before calling the bundle load-clean. Hardware use still requires operator review."
                    if load_status == "load_failed"
                    else "IMPORT READY, LOAD NOT VERIFIED. The bundle passed offline import gates, but "
                    "Script Editor load/open is a separate check. Run the optional FluentControl "
                    "import/load diagnostic or manually open the generated method before calling it "
                    "load-clean. Hardware use still requires operator review."
                ),
            ]
        )
    if not manifest.get("ready_to_import"):
        lines.extend(
            [
                "",
                "> NOT READY TO IMPORT. Do not copy these files into `ready-to-import` "
                "or treat them as a validated bundle. "
                + (
                    "Ask for the full FluentControl ZEIA export, wait for it, or get explicit approval to continue with the partial/non-full export."
                    if status == "needs_full_zeia_export"
                    else
                    "Run the final generation pass with compile enabled to validate."
                    if status == "scaffold_not_validated"
                    else "Inspect `ready_validation.md` for the failing gate(s)."
                ),
            ]
        )
    if readiness:
        lines.extend(["", "## Readiness Boundaries", ""])
        for title, key in (
            ("Offline Validation", "offline_validation"),
            ("Review State", "review_state"),
            ("FluentControl Load Diagnostic", "fluentcontrol_load_diagnostic"),
            ("Generated ZEIA Import", "generated_zeia_import"),
            ("Script Editor Load", "script_editor_load"),
            ("Simulation", "simulation"),
            ("Hardware Run", "hardware_run"),
        ):
            item = readiness.get(key) or {}
            lines.append(f"- {title}: `{item.get('status') or 'unknown'}`")
            summary = item.get("summary") or item.get("meaning")
            if summary:
                lines.append(f"  - {summary}")
    lines.extend(
        [
            "",
            f"- Intent: {manifest.get('intent')}",
        f"- Request spec: `{manifest.get('request_spec')}`",
        f"- Context: `{manifest.get('context') or 'none'}`",
        f"- Context kind: `{manifest.get('context_kind') or 'none'}`",
        f"- Source contexts: `{len(manifest.get('source_contexts') or [])}`",
        f"- Full ZEIA export: `{(manifest.get('full_zeia_export') or {}).get('status') or 'not_checked'}`",
        f"- Partial ZEIA approved: `{bool(manifest.get('partial_zeia_export_approved'))}`",
        f"- Host instrument configuration: `{(manifest.get('host_instrument_configuration') or {}).get('status') or 'not_checked'}`",
        f"- Host config report: `{manifest.get('host_instrument_config_report') or 'not available'}`",
        f"- Output folder: `{manifest.get('out_dir')}`",
        f"- Indexed pattern windows: `{manifest.get('indexed_pattern_count', 0)}`",
        f"- Protocol IR: `{manifest.get('protocol_ir')}`",
        f"- RGA move policy: `{manifest.get('rga_move_policy') or 'not available'}`",
        f"- Liquid state validation: `{manifest.get('liquid_state_validation') or 'not available'}`",
        f"- Simulation report: `{manifest.get('simulation_report') or 'not available'}`",
        f"- Simulation JSON: `{manifest.get('simulation_json') or 'not available'}`",
        f"- Repair history: `{manifest.get('repair_history') or 'not available'}`",
        f"- Python draft: `{manifest.get('python_draft')}`",
        f"- Repaired draft: `{manifest.get('repaired_draft')}`",
        f"- Compiled candidate: `{manifest.get('compiled_candidate') if manifest.get('compiled_candidate') is not None else 'none'}`",
        f"- FluentControl import/load diagnostic: `{manifest.get('fluent_context_check') or 'not requested'}`",
        f"- Traceability map: `{manifest.get('traceability_map') or 'not available'}`",
        f"- Traceability report: `{manifest.get('traceability_report') or 'not available'}`",
        f"- Recreate guide: `{manifest.get('recreate_script')}`",
        f"- Worktable changes: `{manifest.get('worktable_changes')}`",
        f"- Worktable patch JSON: `{manifest.get('worktable_patch')}`",
        f"- Ready validation: `{manifest.get('ready_validation') or 'not available'}`",
        f"- Validation diff: `{manifest.get('validation_diff')}`",
        "",
        "## Provenance",
        "",
        f"- Repository commit: `{(manifest.get('environment') or {}).get('repository_commit') or 'unknown'}`",
        f"- Python version: `{(manifest.get('environment') or {}).get('python_version') or 'unknown'}`",
        f"- Protocol builder version: `{(manifest.get('environment') or {}).get('protocol_builder_version') or 'unknown'}`",
        f"- Fluentcoder version: `{(manifest.get('environment') or {}).get('fluentcoder_version') or 'unknown'}`",
        f"- Command registry SHA-256: `{(manifest.get('environment') or {}).get('command_registry_sha256') or 'unknown'}`",
        f"- Request spec SHA-256: `{((manifest.get('artifact_hashes') or {}).get('request_spec') or {}).get('sha256') or 'unknown'}`",
        f"- Input IR SHA-256: `{((manifest.get('artifact_hashes') or {}).get('input_ir') or {}).get('sha256') or 'unknown'}`",
        f"- Repaired Python SHA-256: `{((manifest.get('artifact_hashes') or {}).get('repaired_python') or {}).get('sha256') or 'unknown'}`",
        f"- Generated ZEIA hashes: `{len((manifest.get('artifact_hashes') or {}).get('generated_zeia') or [])}`",
        "",
        "## Stages",
        "",
        ]
    )
    for idx, stage in enumerate(manifest.get("stages", []), start=1):
        lines.append(f"{idx}. {stage.get('title')}")
        lines.append(f"   - Status: `{stage.get('status')}`")
        lines.append(f"   - Summary: {stage.get('summary')}")
        outputs = stage.get("outputs") or {}
        if isinstance(outputs, dict):
            for key, value in outputs.items():
                if isinstance(value, list):
                    lines.append(f"   - {key}: `{len(value)}` item(s)")
                else:
                    lines.append(f"   - {key}: `{value}`")
    return "\n".join(lines).rstrip() + "\n"


def _attach_manifest_provenance(
    manifest: dict[str, Any],
    *,
    context: ProjectLike | None,
    request_spec_doc: Mapping[str, Any],
    request_spec_path: Path | None,
    request_spec_source: Path | None,
    ir_path: Path | None,
    ir_source: Path | None,
    python_path: Path | None,
    repaired_path: Path | None,
    xscr_path: Path | None,
    generated_zeia_paths: list[Path],
    generation_options: GenerationOptions,
    repair_history_path: Path | None,
    repair_history: list[dict[str, Any]],
    repair_plan_path: Path | None,
    repair_report_path: Path | None,
    finalization_report: Any | None,
    simulation_backend: str,
) -> None:
    manifest["environment"] = environment_provenance(simulation_backend=simulation_backend)
    manifest["source_archive_hashes"] = _source_archive_hashes(context)
    manifest["snapshot_hashes"] = _snapshot_hashes(context)
    hash_roots = _provenance_hash_roots(
        context,
        out_dir=Path(str(manifest.get("out_dir") or "")) if manifest.get("out_dir") else None,
        source_paths=[request_spec_source, ir_source],
    )
    manifest["artifact_hashes"] = {
        "request_spec": _artifact_hash_record(request_spec_path, source_path=request_spec_source, roots=hash_roots),
        "input_ir": _artifact_hash_record(ir_path, source_path=ir_source, roots=hash_roots),
        "python_draft": _artifact_hash_record(python_path, roots=hash_roots),
        "repaired_python": _artifact_hash_record(repaired_path, roots=hash_roots),
        "finalized_xscr": _artifact_hash_record(xscr_path, roots=hash_roots),
        "generated_zeia": [_artifact_hash_record(path, raw_bytes=True) for path in generated_zeia_paths],
    }
    manifest["generation_options"] = generation_options.as_dict()
    manifest["approval_records"] = _approval_records(
        request_spec_doc,
        approve_partial_zeia=generation_options.approve_partial_zeia,
        waive_checksum_recompute=generation_options.waive_checksum_recompute,
        approve_deck_layout=generation_options.approve_deck_layout,
        approve_command_inventory=generation_options.approve_command_inventory,
        approve_unsupported_raw_xml=generation_options.approve_unsupported_raw_xml,
    )
    manifest["repair_iterations"] = {
        "history_path": str(repair_history_path) if repair_history_path else None,
        "plan_path": str(repair_plan_path) if repair_plan_path else None,
        "report_path": str(repair_report_path) if repair_report_path else None,
        "apply_modeling": bool(generation_options.apply_modeling),
        "selected_repaired_path": str(repaired_path) if repaired_path else None,
        "selected_repaired_sha256": sha256_path(repaired_path),
        "iterations": [
            {
                **entry,
                "finding_count": len(entry.get("findings") or []),
                "repair_applied_count": len(entry.get("repairs_applied") or []),
            }
            for entry in repair_history
        ],
    }
    manifest["finalization_changes"] = _finalization_provenance(finalization_report)


def _artifact_hash_record(
    path: Path | None,
    *,
    source_path: Path | None = None,
    roots: list[str] | None = None,
    raw_bytes: bool = False,
) -> dict[str, Any]:
    return {
        "path": str(path) if path else None,
        "source_path": str(source_path) if source_path else None,
        "sha256": sha256_path(path) if raw_bytes else _normalized_artifact_hash(path, roots or []),
        "hash_basis": "file_bytes" if raw_bytes else "normalized_text",
    }


def _normalized_artifact_hash(path: Path | None, roots: list[str]) -> str | None:
    if path is None or not path.is_file():
        return None
    try:
        return artifact_digest(path, roots)
    except OSError:
        return None


def _provenance_hash_roots(
    context: ProjectLike | None,
    *,
    out_dir: Path | None,
    source_paths: list[Path | None],
) -> list[str]:
    roots: list[str] = []

    def add(path: Path | None) -> None:
        if path is None:
            return
        candidate = path.expanduser()
        if not candidate.is_absolute():
            candidate = candidate.absolute()
        value = str(candidate)
        if value not in roots:
            roots.append(value)

    add(out_dir)
    root_path = _context_root_path(context)
    add(root_path)
    for item in _context_project_manifests(context):
        manifest = item["manifest"]
        manifest_root = manifest.get("root")
        if manifest_root:
            add(Path(str(manifest_root)))
        for key in ("source_archive", "copied_archive"):
            raw = manifest.get(key)
            if raw:
                add(Path(str(raw)).expanduser().absolute().parent)
        for snapshot in manifest.get("snapshot_archives") or []:
            if not isinstance(snapshot, Mapping):
                continue
            for key in ("source_archive", "copied_archive"):
                raw = snapshot.get(key)
                if raw:
                    add(Path(str(raw)).expanduser().absolute().parent)
    for source_path in source_paths:
        if source_path is not None:
            add(source_path.expanduser().absolute().parent)
    return roots


def _source_archive_hashes(context: ProjectLike | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _context_project_manifests(context):
        manifest = item["manifest"]
        archive_path = _preferred_archive_path(
            manifest.get("copied_archive"),
            manifest.get("source_archive"),
        )
        if archive_path is None and not manifest.get("source_archive") and not manifest.get("copied_archive"):
            continue
        records.append(
            {
                "context": item["name"],
                "kind": manifest.get("kind"),
                "source_archive": manifest.get("source_archive"),
                "copied_archive": manifest.get("copied_archive"),
                "sha256": sha256_path(archive_path),
                "fingerprint": manifest.get("source_archive_fingerprint"),
                "source_import_identity": manifest.get("source_import_identity"),
                "entry_count": manifest.get("entry_count"),
            }
        )
    return records


def _snapshot_hashes(context: ProjectLike | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in _context_project_manifests(context):
        manifest = item["manifest"]
        for snapshot in manifest.get("snapshot_archives") or []:
            if not isinstance(snapshot, Mapping):
                continue
            archive_path = _preferred_archive_path(
                snapshot.get("copied_archive"),
                snapshot.get("source_archive"),
            )
            records.append(
                {
                    "context": str(snapshot.get("source_context") or item["name"]),
                    "archive_kind": snapshot.get("archive_kind"),
                    "source_archive": snapshot.get("source_archive"),
                    "copied_archive": snapshot.get("copied_archive"),
                    "prefix": snapshot.get("prefix"),
                    "entry_count": snapshot.get("entry_count"),
                    "sha256": sha256_path(archive_path),
                }
            )
    return records


def _context_project_manifests(context: ProjectLike | None) -> list[dict[str, Any]]:
    if context is None:
        return []
    source_projects = context.manifest.get("source_projects")
    if isinstance(source_projects, list) and source_projects:
        out: list[dict[str, Any]] = []
        for item in source_projects:
            if not isinstance(item, Mapping):
                continue
            manifest = _load_manifest_dict(item.get("manifest"))
            if not manifest:
                manifest = {
                    "kind": "project",
                    "source_archive": item.get("source_archive"),
                    "copied_archive": item.get("copied_archive"),
                    "snapshot_archives": [],
                }
            out.append({"name": str(item.get("name") or ""), "manifest": manifest})
        return out
    return [{"name": context.name, "manifest": context.manifest}]


def _load_manifest_dict(raw_path: Any) -> dict[str, Any]:
    if not raw_path:
        return {}
    try:
        payload = json.loads(Path(str(raw_path)).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _preferred_archive_path(primary: Any, fallback: Any) -> Path | None:
    for raw in (primary, fallback):
        if not raw:
            continue
        path = Path(str(raw))
        if path.exists():
            return path
    for raw in (primary, fallback):
        if raw:
            return Path(str(raw))
    return None


def _approval_records(
    spec: Mapping[str, Any],
    *,
    approve_partial_zeia: bool,
    waive_checksum_recompute: bool,
    approve_deck_layout: bool,
    approve_command_inventory: bool,
    approve_unsupported_raw_xml: bool,
) -> dict[str, dict[str, Any]]:
    partial_from_spec = bool(
        _request_generation_bool(spec, "approve_partial_zeia")
        or _nested_mapping_bool(spec, "source", "full_zeia_export", "approved_partial_zeia")
    )
    checksum_waive_from_spec = _request_generation_bool(spec, "waive_checksum_recompute")
    deck_layout_gate = readiness_gate("deck_layout_consistent")
    deck_layout_context_keys = readiness_gate_approval_context_keys("deck_layout_consistent")
    deck_layout_record_key = (
        deck_layout_context_keys[0]
        if deck_layout_context_keys
        else f"{deck_layout_gate.approval_key}_approved"
    )
    deck_from_review = bool(readiness_gate_request_spec_approved(spec, "deck_layout_consistent"))
    deck_from_generation = _request_generation_bool(spec, "approve_deck_layout")
    deck_from_spec = bool(deck_from_review or deck_from_generation)
    command_inventory_from_spec = _request_generation_bool(spec, "approve_command_inventory")
    raw_xml_from_spec = _request_generation_bool(spec, "approve_unsupported_raw_xml")
    return {
        "partial_zeia_export_approved": _approval_record(
            approved=bool(approve_partial_zeia or partial_from_spec),
            source=(
                "request_spec"
                if partial_from_spec
                else "runtime_argument"
                if approve_partial_zeia
                else "not_recorded"
            ),
        ),
        "checksums_recompute_waived": _approval_record(
            approved=bool(waive_checksum_recompute or checksum_waive_from_spec),
            source=(
                "request_spec.generation.waive_checksum_recompute"
                if checksum_waive_from_spec
                else "runtime_argument"
                if waive_checksum_recompute
                else "not_recorded"
            ),
        ),
        deck_layout_record_key: _approval_record(
            approved=bool(approve_deck_layout or deck_from_spec),
            source=(
                "request_spec.review.deck_layout"
                if deck_from_review
                else "request_spec.generation.approve_deck_layout"
                if deck_from_generation
                else "runtime_argument"
                if approve_deck_layout
                else "not_recorded"
            ),
            aliases=list(deck_layout_context_keys[1:]),
            approval_key=deck_layout_gate.approval_key,
            cli_flag=deck_layout_gate.cli_flag,
            mcp_capability=deck_layout_gate.mcp_capability,
            request_spec_path=deck_layout_gate.request_spec_path,
            remediation=deck_layout_gate.remediation,
            artifact_inputs=list(deck_layout_gate.artifact_inputs),
        ),
        "command_inventory_approved": _approval_record(
            approved=bool(approve_command_inventory or command_inventory_from_spec),
            source=(
                "request_spec.generation.approve_command_inventory"
                if command_inventory_from_spec
                else "runtime_argument"
                if approve_command_inventory
                else "not_recorded"
            ),
        ),
        "unsupported_raw_xml_approved": _approval_record(
            approved=bool(approve_unsupported_raw_xml or raw_xml_from_spec),
            source=(
                "request_spec.generation.approve_unsupported_raw_xml"
                if raw_xml_from_spec
                else "runtime_argument"
                if approve_unsupported_raw_xml
                else "not_recorded"
            ),
        ),
    }


def _approval_record(
    *,
    approved: bool,
    source: str,
    aliases: list[str] | None = None,
    **metadata: Any,
) -> dict[str, Any]:
    record = {
        "approved": bool(approved),
        "source": source,
    }
    if aliases:
        record["aliases"] = aliases
    for key, value in metadata.items():
        if value is not None:
            record[key] = value
    return record


def _request_generation_bool(spec: Mapping[str, Any], key: str) -> bool:
    generation = spec.get("generation") if isinstance(spec, Mapping) else None
    if not isinstance(generation, Mapping):
        return False
    return bool(generation.get(key))


def _nested_mapping_bool(spec: Mapping[str, Any], *keys: str) -> bool:
    current: Any = spec
    for key in keys:
        if not isinstance(current, Mapping):
            return False
        current = current.get(key)
    return bool(current)


def _finalization_provenance(report: Any | None) -> dict[str, Any]:
    if report is None:
        return {
            "modified": False,
            "modification_count": 0,
            "checksum_before": None,
            "checksum_after": None,
            "roundtrip": {},
            "changes": [],
            "warnings": [],
            "errors": [],
        }
    data = report.as_dict() if hasattr(report, "as_dict") else dict(report)
    return {
        "modified": bool(data.get("modified")),
        "modification_count": int(data.get("modification_count") or 0),
        "checksum_before": data.get("checksum_before"),
        "checksum_after": data.get("checksum_after"),
        "roundtrip": data.get("roundtrip") or {},
        "changes": data.get("changes") or [],
        "warnings": data.get("warnings") or [],
        "errors": data.get("errors") or [],
    }


def _simulation_backend(simulate: bool) -> str:
    return "fluentcoder.offline_simulator" if simulate else "not_run"


def _prepare_request_spec(
    *,
    request_spec: dict[str, Any] | None,
    intent: str,
    protocol_name: str | None,
    context: ProjectLike | None,
    project_archive: Path | None,
    source_scripts: list[str],
    pattern_refs: list[str],
    index_db: Path | None,
    pattern_ids: list[int | str] | None,
    pattern_queries: list[str] | None,
    source_script_rank: int,
    generation_options: GenerationOptions,
    fluent_method: str | None,
) -> dict[str, Any]:
    if request_spec is not None:
        spec = normalize_request_spec(request_spec)
        request_block = spec.setdefault("request", {})
        if isinstance(request_block, dict):
            if intent:
                request_block["intent"] = intent
                request_block.setdefault("verbatim_prompt", intent)
                request_block.setdefault("original_user_prompt", intent)
            if protocol_name:
                request_block["protocol_name"] = protocol_name
        generation = spec.setdefault("generation", {})
        if isinstance(generation, dict):
            generation.update(generation_options.as_dict())
            if fluent_method is not None or "fluent_method" not in generation:
                generation["fluent_method"] = fluent_method
        source = spec.setdefault("source", {})
        if isinstance(source, dict):
            if context is not None:
                source["context"] = context.name
                if isinstance(context, ProjectCollection):
                    source["context_kind"] = "project_collection"
            if source_scripts:
                source["source_scripts"] = list(source_scripts)
            if pattern_refs:
                source["pattern_refs"] = list(pattern_refs)
            pattern_index = source.setdefault("pattern_index", {})
            if isinstance(pattern_index, dict):
                if index_db is not None:
                    pattern_index["database"] = str(index_db)
                if pattern_ids:
                    pattern_index["pattern_ids"] = [str(value) for value in pattern_ids]
                if pattern_queries:
                    pattern_index["pattern_queries"] = list(pattern_queries)
                if source_script_rank != 1:
                    pattern_index["source_script_rank"] = source_script_rank
        return spec
    return build_request_spec(
        intent=intent,
        protocol_name=protocol_name,
        context=context.name if context else None,
        context_kind=_context_kind(context) if context else None,
        contexts=_context_sources(context),
        project_archives=[project_archive] if project_archive else [],
        source_scripts=source_scripts,
        pattern_refs=pattern_refs,
        index_db=index_db,
        pattern_ids=pattern_ids,
        pattern_queries=pattern_queries,
        source_script_rank=source_script_rank,
        generation_options=generation_options,
        fluent_method=fluent_method,
    )


def _request_review_bool(spec: Mapping[str, Any], key: str) -> bool:
    review = spec.get("review") if isinstance(spec, Mapping) else None
    if not isinstance(review, Mapping):
        return False
    return bool(review.get(key))


def _refresh_request_spec_context(spec: dict[str, Any], context: ProjectLike) -> None:
    source = spec.setdefault("source", {})
    if not context.manifest.get("ephemeral_request_collection"):
        source["context"] = context.name
        source["context_kind"] = _context_kind(context)
    source["contexts"] = _context_sources(context)


def _context_with_request_sources(
    context: ProjectLike | None,
    spec: Mapping[str, Any],
    *,
    out_dir: Path,
) -> ProjectLike | None:
    """Use every request-declared project as one generation context.

    Request specs often use a small edited export for the main script and a full
    export for subroutines, worktables, and external files. Treating only the
    primary context as authoritative silently drops those dependencies.
    """
    if context is None:
        return None
    source = spec.get("source") if isinstance(spec, Mapping) else None
    raw_contexts = source.get("contexts") if isinstance(source, Mapping) else None
    if not isinstance(raw_contexts, list):
        return context
    requested_names = [
        str(item.get("name") if isinstance(item, Mapping) else item or "").strip()
        for item in raw_contexts
    ]
    requested_names = [name for name in requested_names if name]
    if isinstance(context, ProjectCollection):
        existing_names = {
            str(item.get("name") or "").strip().casefold()
            for item in context.manifest.get("source_projects") or []
            if isinstance(item, Mapping) and str(item.get("name") or "").strip()
        }
        if all(name.casefold() in existing_names for name in requested_names):
            return context

    contexts: list[ProjectContext] = []
    seen: set[str] = set()

    def _add(candidate: ProjectContext) -> None:
        key = candidate.name.casefold()
        if key in seen:
            return
        seen.add(key)
        contexts.append(candidate)

    if isinstance(context, ProjectContext):
        _add(context)
    elif isinstance(context, ProjectCollection):
        for item in context.manifest.get("source_projects") or []:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            try:
                _add(load_project(name))
            except Exception:
                continue

    for name in requested_names:
        if not name or name.casefold() in seen:
            continue
        try:
            _add(load_project(name))
        except Exception:
            continue

    if len(contexts) <= 1:
        return context
    collection_root = out_dir / "request-context"
    collection_root.mkdir(parents=True, exist_ok=True)
    collection_name = f"{contexts[0].name}-request-contexts"
    manifest = build_collection_manifest(
        collection_name=collection_name,
        contexts=contexts,
        root=collection_root,
    )
    manifest["ephemeral_request_collection"] = True
    (collection_root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return ProjectCollection(collection_name, collection_root, manifest)


def _refresh_request_spec_full_zeia_export(
    spec: dict[str, Any],
    assessment: dict[str, Any],
    *,
    approve_partial_zeia: bool,
) -> None:
    source = spec.setdefault("source", {})
    source["full_zeia_export"] = {
        **assessment,
        "approved_partial_zeia": approve_partial_zeia,
    }
    generation = spec.setdefault("generation", {})
    generation["approve_partial_zeia"] = approve_partial_zeia
    review = spec.setdefault("review", {})
    questions = review.setdefault("unresolved_questions", [])
    question = (
        "Provide a full FluentControl ZEIA export, or explicitly approve "
        "continuing with this partial/non-full ZEIA export."
    )
    if not assessment.get("accepted") and question not in questions:
        questions.append(question)


def _host_config_expected_hint(
    spec: dict[str, Any],
    *,
    intent: str,
    context: ProjectLike | None,
    selected_source_scripts: list[dict[str, Any]],
    protocol_ir: dict[str, Any] | None = None,
) -> dict[str, Any]:
    configured = ((spec.get("source") or {}).get("host_instrument_configuration") or {})
    if configured.get("exact_names") or configured.get("patterns") or configured.get("required"):
        return dict(configured)
    return infer_expected_host_config(
        intent=intent,
        source_manifest=context.manifest if context else None,
        selected_source_scripts=selected_source_scripts,
        protocol_ir=protocol_ir,
    )


def _refresh_request_spec_host_config(spec: dict[str, Any], report: dict[str, Any]) -> None:
    source = spec.setdefault("source", {})
    source["host_instrument_configuration"] = dict(report.get("expected") or {})
    review = spec.setdefault("review", {})
    questions = review.setdefault("unresolved_questions", [])
    status = str(report.get("status") or "")
    if status in {"needs_review", "failed"}:
        instruction = report.get("user_instruction") or (
            "Verify FluentControl is using the correct instrument configuration before import/run."
        )
        if instruction not in questions:
            questions.append(instruction)


def _verify_full_zeia_export(
    context: ProjectLike | None,
    *,
    approve_partial_zeia: bool,
) -> dict[str, Any]:
    if context is None:
        assessment = {
            "required": True,
            "status": "needs_user",
            "accepted": False,
            "summary": "No ZEIA project context was provided for this generation run.",
            "ask_user": FULL_ZEIA_ASK,
            "blocking_findings": [
                {
                    "id": "no_zeia_context",
                    "summary": "Protocol generation requires an existing full ZEIA export as the base context.",
                }
            ],
            "warnings": [],
        }
    else:
        assessment = dict(context.manifest.get("full_zeia_export") or {})
        if not assessment:
            assessment = {
                "required": True,
                "status": "not_checked",
                "accepted": False,
                "summary": "The source context has no full-ZEIA assessment in its manifest.",
                "ask_user": FULL_ZEIA_ASK,
                "blocking_findings": [
                    {
                        "id": "missing_full_zeia_assessment",
                        "summary": "Re-import the ZEIA with the current protocol-builder before generating.",
                    }
                ],
                "warnings": [],
            }
    if assessment.get("accepted"):
        return {**assessment, "approved_partial_zeia": approve_partial_zeia}
    if approve_partial_zeia:
        warnings = list(assessment.get("warnings") or [])
        warnings.append(
            {
                "id": "partial_zeia_explicitly_approved",
                "summary": "User explicitly approved continuing without a confirmed full ZEIA export.",
            }
        )
        return {
            **assessment,
            "status": "approved_partial_zeia",
            "accepted": True,
            "approved_partial_zeia": True,
            "summary": (
                "The source context does not clearly look like a full ZEIA export, "
                "but explicit approval to use it was recorded."
            ),
            "warnings": warnings,
        }
    return {**assessment, "approved_partial_zeia": False}


def _full_zeia_stage_summary(assessment: dict[str, Any]) -> str:
    if assessment.get("accepted"):
        if assessment.get("approved_partial_zeia"):
            return "Partial/non-full ZEIA use was explicitly approved; continuing with warnings."
        return "Source context appears to be a full ZEIA export."
    return (
        "Full ZEIA export is required before generation continues. "
        "Ask for the full export, wait for it, or get explicit partial-export approval."
    )


def _write_blocked_full_zeia_manifest(
    *,
    out_dir: Path,
    stages: list[dict[str, Any]],
    intent: str,
    verbatim_prompt: str,
    intent_summary: str,
    context: ProjectLike | None,
    request_spec_doc: Mapping[str, Any],
    request_spec_path: Path,
    request_spec_source: Path | None,
    full_zeia_export: dict[str, Any],
    index_db: Path | None,
    indexed_pattern_count: int,
    generation_options: GenerationOptions,
    simulation_backend: str,
) -> dict[str, Any]:
    report_path = out_dir / "full_zeia_export_check.md"
    report_path.write_text(_render_full_zeia_export_markdown(full_zeia_export), encoding="utf-8")
    lifecycle = lifecycle_metadata(
        bundle_role="debug",
        source_export_kind=source_export_kind(full_zeia_export, approved_partial=False),
        verification_state="failed_or_blocked",
        created_from=created_from_record(
            context_name=context.name if context else None,
            context_kind=_context_kind(context) if context else None,
            source_contexts=_context_sources(context),
            source_projects=[],
        ),
    )
    manifest = {
        "workflow": "request_spec_ir_artifacts_validation_diff_ready_bundle",
        "workflow_status": "needs_full_zeia_export",
        "generation_options": generation_options.as_dict(),
        "ready_to_import": False,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bundle_role": lifecycle["bundle_role"],
        "source_export_kind": lifecycle["source_export_kind"],
        "verification_state": lifecycle["verification_state"],
        "supersedes": lifecycle["supersedes"],
        "superseded_by": lifecycle["superseded_by"],
        "lifecycle": lifecycle,
        "intent": intent,
        "intent_summary": intent_summary,
        "verbatim_prompt": verbatim_prompt,
        "request_spec": str(request_spec_path),
        "request_spec_source": str(request_spec_source) if request_spec_source else None,
        "context": context.name if context else None,
        "context_kind": _context_kind(context) if context else None,
        "source_contexts": _context_sources(context),
        "full_zeia_export": full_zeia_export,
        "partial_zeia_export_approved": False,
        "out_dir": str(out_dir),
        "pattern_index": str(index_db) if index_db else None,
        "indexed_pattern_count": indexed_pattern_count,
        "protocol_ir": None,
        "ir_synthesis": None,
        "liquid_state_validation": None,
        "python_draft": None,
        "repaired_draft": None,
        "compiled_xscr": None,
        "compile_report": None,
        "fluent_context_check": None,
        "recreate_script": None,
        "worktable_changes": None,
        "worktable_patch": None,
        "ready_validation": None,
        "validation_diff": None,
        "validation_diff_json": None,
        "full_zeia_export_report": str(report_path),
        "ready_to_import_artifacts": [],
        "published_zeia_path": None,
        "published_protocol_folder": None,
        "published_artifacts": [],
        "internal_artifacts": [],
        "deliverable": None,
        "failed_artifacts": None,
        "stages": stages,
    }
    _attach_manifest_provenance(
        manifest,
        context=context,
        request_spec_doc=request_spec_doc,
        request_spec_path=request_spec_path,
        request_spec_source=request_spec_source,
        ir_path=None,
        ir_source=None,
        python_path=None,
        repaired_path=None,
        xscr_path=None,
        generated_zeia_paths=[],
        generation_options=generation_options,
        repair_history_path=None,
        repair_history=[],
        repair_plan_path=None,
        repair_report_path=None,
        finalization_report=None,
        simulation_backend=simulation_backend,
    )
    manifest_path = out_dir / "generation_manifest.json"
    write_json(manifest_path, manifest)
    summary_path = out_dir / "GENERATION_WORKFLOW.md"
    summary_path.write_text(render_generation_summary(manifest), encoding="utf-8")
    return {**manifest, "generation_manifest": str(manifest_path), "workflow_report": str(summary_path)}


def _render_full_zeia_export_markdown(assessment: dict[str, Any]) -> str:
    lines = [
        "# Full ZEIA Export Check",
        "",
        f"- Status: `{assessment.get('status') or 'unknown'}`",
        f"- Accepted: `{bool(assessment.get('accepted'))}`",
        f"- Summary: {assessment.get('summary') or ''}",
        "",
        "## Required User Action",
        "",
        assessment.get("ask_user") or FULL_ZEIA_ASK,
    ]
    findings = assessment.get("blocking_findings") or []
    if findings:
        lines.extend(["", "## Blocking Signals", ""])
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            lines.append(f"- `{finding.get('id')}`: {finding.get('summary')}")
            for item in finding.get("items") or []:
                lines.append(f"  - `{json.dumps(item, sort_keys=True)}`")
    return "\n".join(lines).rstrip() + "\n"


def _attach_request_spec_metadata(ir: dict[str, Any], spec: dict[str, Any], request_spec_path: Path) -> None:
    source = ir.setdefault("source", {})
    request = spec.get("request") or {}
    verbatim_prompt = request_verbatim_prompt(spec)
    if verbatim_prompt:
        source["verbatim_prompt"] = verbatim_prompt
        source["original_user_prompt"] = verbatim_prompt
    source["request_spec"] = {
        "schema_version": spec.get("schema_version"),
        "path": str(request_spec_path),
        "status": request.get("status"),
        "intent_summary": request.get("intent"),
        "verbatim_prompt": verbatim_prompt,
        "original_user_prompt": verbatim_prompt,
        "review_state": (spec.get("review") or {}).get("state"),
    }


def _attach_host_config_metadata(ir: dict[str, Any], report: dict[str, Any]) -> None:
    source = ir.setdefault("source", {})
    source["host_instrument_configuration"] = dict(report)
    assumptions = ir.setdefault("safety_assumptions", [])
    assumption_id = "verify_host_instrument_configuration"
    if not any(isinstance(item, dict) and item.get("id") == assumption_id for item in assumptions):
        assumptions.append(
            {
                "id": assumption_id,
                "text": (
                    "Before import/run, verify FluentControl is using the expected host "
                    "instrument configuration; switch via the configuration dropdown if it is not."
                ),
            }
        )


def _ensure_compiled_subroutine_references(
    xscr_path: Path,
    ir: dict[str, Any],
    context: ProjectLike | None,
) -> list[dict[str, str]]:
    if not xscr_path.exists():
        return []
    try:
        from . import xml_compat as ET
    except ImportError:  # pragma: no cover - stdlib is always available
        return []

    try:
        tree = ET.parse(xscr_path)
    except ET.ParseError:
        return []
    root = tree.getroot()
    payload = _first_xml_child(root, "Payload")
    if payload is None:
        return []

    existing: dict[str, Any] = {}
    for ref in list(payload):
        if _local_xml_name(ref.tag) != "Reference" or _first_xml_text(ref, "TypeId") != "Script":
            continue
        object_name = _clean_subroutine_reference(_first_xml_text(ref, "ObjectName"))
        for alias in (object_name, object_name.rsplit("\\", 1)[-1]):
            key = _norm_subroutine_key(alias)
            if key:
                existing[key] = ref
    required = _required_subroutine_references(ir)
    source_manifest = getattr(context, "manifest", None) if context is not None else None
    resolved_dependencies: dict[str, dict[str, Any]] = {}
    for item in resolve_subroutine_dependencies(ir, source_manifest).get("resolved", []):
        if not isinstance(item, dict):
            continue
        aliases = (
            item.get("ref"),
            item.get("object_name"),
            str(item.get("ref") or "").rsplit("\\", 1)[-1],
            str(item.get("object_name") or "").rsplit("\\", 1)[-1],
        )
        for alias in aliases:
            key = _norm_subroutine_key(alias)
            if key:
                resolved_dependencies[key] = item
    added: list[dict[str, str]] = []
    for subroutine in required:
        clean = _clean_subroutine_reference(subroutine)
        if not clean:
            continue
        bare = clean.rsplit("\\", 1)[-1]
        metadata = resolved_dependencies.get(_norm_subroutine_key(clean)) or resolved_dependencies.get(_norm_subroutine_key(bare)) or {}
        guid = str(metadata.get("guid") or _ir_script_dependency_guid(ir, bare) or "")
        object_name = str(metadata.get("object_name") or bare)
        if not metadata and not guid:
            continue
        existing_ref = existing.get(_norm_subroutine_key(clean)) or existing.get(
            _norm_subroutine_key(bare)
        )
        if existing_ref is not None:
            changed = False
            guid_node = _first_xml_child(existing_ref, "Guid")
            if guid_node is None:
                guid_node = ET.Element("Guid")
                existing_ref.insert(0, guid_node)
            if guid and str(guid_node.text or "").strip().casefold() != guid.casefold():
                guid_node.text = guid
                changed = True
            object_name_node = _first_xml_child(existing_ref, "ObjectName")
            if object_name_node is None:
                object_name_node = ET.SubElement(existing_ref, "ObjectName")
            if object_name and str(object_name_node.text or "").strip() != object_name:
                object_name_node.text = object_name
                changed = True
            if changed:
                added.append(
                    {
                        "subroutine": clean,
                        "object_name": object_name,
                        "guid": guid,
                        "action": "repaired",
                    }
                )
            continue
        ref = ET.Element("Reference")
        ET.SubElement(ref, "Guid").text = guid
        ET.SubElement(ref, "TypeId").text = "Script"
        ET.SubElement(ref, "ObjectName").text = object_name
        payload.insert(_first_payload_data_index(payload), ref)
        for alias in (object_name, object_name.rsplit("\\", 1)[-1]):
            key = _norm_subroutine_key(alias)
            if key:
                existing[key] = ref
        added.append(
            {
                "subroutine": clean,
                "object_name": object_name,
                "guid": guid,
                "action": "added",
            }
        )

    if added:
        _register_root_namespace(root)
        tree.write(xscr_path, encoding="utf-8", xml_declaration=True)
    return added


def _normalize_compiled_variable_declaration_namespaces(xscr_path: Path) -> list[dict[str, str]]:
    """Keep DataContract ``xsi:type`` prefixes in scope after XML reserialization.

    ElementTree preserves namespaces used in element names, but it does not know
    that the string value of ``xsi:type`` is also a QName. If a prior fixup
    reserializes the compiled XSCR, FluentControl can see
    ``:VariableDefinitionHelper`` and fail to open the script. Prefer an already
    declared VariableHandling prefix, and only edit files that contain the risky
    declaration.
    """
    if not xscr_path.exists():
        return []
    try:
        original = xscr_path.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    if "VariableDefinitionHelper" not in original:
        return []

    text, fixups = localize_variable_declaration_namespaces(original)
    if text != original:
        xscr_path.write_text(text, encoding="utf-8")
    return fixups


_VX_WORKSPACE_DATA_RE = re.compile(
    r"<(?P<tag>(?:[A-Za-z_][\w.-]*:)?VxWorkspaceData)\b[^>]*>.*?</(?P=tag)>",
    re.DOTALL,
)
_WORKTABLE_WORKSPACE_REFERENCE_RE = re.compile(
    r"<Reference\b[^>]*>\s*<Guid>(?P<guid>[^<]+)</Guid>\s*<TypeId>WorktableWorkspace</TypeId>",
    re.DOTALL,
)
_BASE_WORKSPACE_NAME_RE = re.compile(
    r"(<(?P<tag>(?:[A-Za-z_][\w.-]*:)?BaseWorkspaceName)\b[^>]*>)(?P<value>.*?)(</(?P=tag)>)",
    re.DOTALL,
)
_WORKSPACE_DELTA_IDENTIFIER_RE = re.compile(r"<Identifier>\s*(?P<value>[^<\s][^<]*)</Identifier>")


def _copy_source_workspace_data(
    xscr_path: Path,
    source_scripts: list[Path],
) -> dict[str, str]:
    """Preserve native workspace-delta metadata for RUP Worktable prompts."""
    try:
        target_text = xscr_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return {}
    if "RUPWorktableStatement" not in target_text:
        return {}
    target_match = _VX_WORKSPACE_DATA_RE.search(target_text)
    if target_match is None:
        return {}
    expected_base = _worktable_reference_guid(target_text)
    candidates: list[dict[str, str]] = []
    for source_script in source_scripts:
        try:
            source_text = source_script.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        for source_match in _VX_WORKSPACE_DATA_RE.finditer(source_text):
            source_block = source_match.group(0)
            candidates.append(
                {
                    "block": source_block,
                    "source_script": str(source_script),
                    "base_workspace": _workspace_block_base(source_block),
                    "delta_identifier": _workspace_block_delta_identifier(source_block),
                }
            )
    if not candidates:
        return {}
    source = _select_workspace_data_candidate(candidates, expected_base)
    source_block = source["block"]
    target_block = target_match.group(0)
    if re.sub(r"\s+", " ", target_block).strip() == re.sub(r"\s+", " ", source_block).strip():
        return {}
    updated = target_text[: target_match.start()] + source_block + target_text[target_match.end() :]
    xscr_path.write_text(updated, encoding="utf-8")
    source_base = source.get("base_workspace") or ""
    return {
        "source_script": source["source_script"],
        "status": "replaced",
        "base_workspace": source_base,
        "delta_identifier": source.get("delta_identifier") or "",
        "matched_worktable_reference": str(bool(expected_base and source_base.casefold() == expected_base.casefold())),
    }


def _worktable_reference_guid(text: str) -> str:
    reference = _WORKTABLE_WORKSPACE_REFERENCE_RE.search(text)
    return reference.group("guid").strip() if reference else ""


def _workspace_block_base(block: str) -> str:
    match = _BASE_WORKSPACE_NAME_RE.search(block)
    return match.group("value").strip() if match else ""


def _workspace_block_delta_identifier(block: str) -> str:
    match = _WORKSPACE_DELTA_IDENTIFIER_RE.search(html.unescape(block))
    return match.group("value").strip() if match else ""


def _select_workspace_data_candidate(
    candidates: list[dict[str, str]],
    expected_base: str,
) -> dict[str, str]:
    def score(candidate: dict[str, str]) -> tuple[int, int, int]:
        base = candidate.get("base_workspace") or ""
        delta = candidate.get("delta_identifier") or ""
        return (
            1 if expected_base and base.casefold() == expected_base.casefold() else 0,
            1 if delta else 0,
            1 if base else 0,
        )

    return max(candidates, key=score)


def _required_subroutine_references(ir: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        subroutine = _clean_subroutine_reference(params.get("subroutine") or params.get("SubRoutine"))
        if subroutine and subroutine not in refs:
            refs.append(subroutine)
    return refs


def _source_script_lookup(context: ProjectLike | None) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    manifest = getattr(context, "manifest", None) if context is not None else None
    if not isinstance(manifest, dict):
        return lookup
    for script in manifest.get("scripts") or []:
        if not isinstance(script, dict):
            continue
        object_name = str(script.get("object_name") or script.get("name") or "")
        if not object_name:
            continue
        record = dict(script)
        record.setdefault("object_name", object_name)
        record.setdefault("guid", str(script.get("guid") or ""))
        lookup[_norm_subroutine_key(object_name)] = record
        lookup[_norm_subroutine_key(object_name.rsplit("\\", 1)[-1])] = record
    return lookup


def _ir_script_dependency_guid(ir: dict[str, Any], object_name: str) -> str:
    target = _norm_subroutine_key(object_name)
    for dep in ir.get("dependencies") or []:
        if (
            not isinstance(dep, dict)
            or str(dep.get("kind") or "").casefold() not in {"script", "subroutine"}
        ):
            continue
        dep_name = str(dep.get("name") or "")
        if target in {
            _norm_subroutine_key(dep_name),
            _norm_subroutine_key(dep_name.rsplit("\\", 1)[-1]),
        }:
            return str(dep.get("guid") or "")
    return ""


def _first_payload_data_index(payload: Any) -> int:
    for index, child in enumerate(list(payload)):
        if _local_xml_name(child.tag) == "PayloadData":
            return index
    return len(list(payload))


def _first_xml_child(root: Any, name: str) -> Any | None:
    for child in list(root):
        if _local_xml_name(child.tag) == name:
            return child
    return None


def _first_xml_text(root: Any, name: str) -> str:
    for child in root.iter():
        if _local_xml_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _local_xml_name(tag: Any) -> str:
    text = str(tag)
    if "}" in text:
        return text.rsplit("}", 1)[-1]
    return text


def _clean_subroutine_reference(value: Any) -> str:
    return str(value or "").strip().strip('"').replace("/", "\\")


def _norm_subroutine_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean_subroutine_reference(value).casefold())


def _subroutine_reference_present(subroutine: str, references: set[str]) -> bool:
    target = _norm_subroutine_key(subroutine)
    bare = _norm_subroutine_key(subroutine.rsplit("\\", 1)[-1])
    return any(
        _norm_subroutine_key(ref) in {target, bare}
        or _norm_subroutine_key(ref.rsplit("\\", 1)[-1]) == bare
        for ref in references
    )


def _register_root_namespace(root: Any) -> None:
    tag = str(root.tag)
    if tag.startswith("{") and "}" in tag:
        from . import xml_compat as ET

        ET.register_namespace("sd", tag[1:].split("}", 1)[0])


def _variable_reconciliation_failure_summary(report: Mapping[str, Any]) -> str:
    failures = report.get("failures") or []
    if not failures:
        return "Variable declaration reconciliation failed."
    first = failures[0] if isinstance(failures[0], Mapping) else {}
    message = str(first.get("message") or "Variable declaration reconciliation failed.")
    if len(failures) > 1:
        message += f" ({len(failures) - 1} more failure(s))"
    return message


def _variable_reconciliation_protocol_issues(report: Mapping[str, Any]) -> list[ProtocolIRIssue]:
    issues: list[ProtocolIRIssue] = []
    for failure in report.get("failures") or []:
        if not isinstance(failure, Mapping):
            continue
        details = failure.get("details") if isinstance(failure.get("details"), Mapping) else {}
        path = str(details.get("path") or "$")
        message = str(failure.get("message") or failure.get("code") or "Variable reconciliation failed.")
        step_id = str(details.get("step_id") or "").strip()
        operation = str(details.get("operation") or "").strip()
        if step_id or operation:
            parts = []
            if step_id:
                parts.append(f"step_id={step_id}")
            if operation:
                parts.append(f"operation={operation}")
            message = f"{message} ({', '.join(parts)})"
        issues.append(ProtocolIRIssue(path, message))
    return issues or [ProtocolIRIssue("$", "Variable declaration reconciliation failed.")]


def _record_stage(
    stages: list[dict[str, Any]],
    stage_id: str,
    status: str,
    summary: str,
    *,
    outputs: dict[str, Any] | None = None,
    command: str | None = None,
    exit_code: int | None = None,
) -> None:
    title = dict(GENERATION_STAGES).get(stage_id, stage_id)
    stage = {
        "stage": len(stages) + 1,
        "id": stage_id,
        "title": title,
        "status": status,
        "summary": summary,
        "outputs": outputs or {},
    }
    if command:
        stage["command"] = command
    if exit_code is not None:
        stage["exit_code"] = exit_code
    stages.append(stage)


def _run_fluentcoder_with_progress(
    arguments: list[str | Path],
    *,
    catalog_db: Path | None,
    progress_emitter: ProgressEmitter,
    stage_id: str,
):
    stop = threading.Event()

    def heartbeat() -> None:
        while not stop.wait(PROGRESS_HEARTBEAT_SECONDS):
            progress_emitter.running(stage_id)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        return run_fluentcoder(arguments, catalog_db=catalog_db)
    finally:
        stop.set()
        thread.join(timeout=0.2)


def _context_progress_summary(context: ProjectLike | None) -> str:
    if context is None:
        return "No active project context."
    return f"Using {_context_kind(context)} context {context.name}."


def _repair_candidate_path(out_dir: Path, base: str, candidate: int) -> Path:
    if candidate <= 0:
        return out_dir / f"{base}.py"
    if candidate == 1:
        return out_dir / f"{base}.repaired.py"
    return out_dir / f"{base}.repaired.{candidate}.py"


def _repair_candidate_simulation_paths(out_dir: Path, base: str, candidate: int) -> tuple[Path, Path]:
    if candidate <= 0:
        stem = f"{base}.simulation"
    else:
        stem = f"{base}.repair-{candidate}.simulation"
    return out_dir / f"{stem}.json", out_dir / f"{stem}.md"


def _repair_history_entry(
    candidate: int,
    *,
    simulation_status: str,
    findings: list[dict[str, Any]] | None = None,
    repairs_applied: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "candidate": candidate,
        "simulation_status": simulation_status,
        "findings": findings or [],
        "repairs_applied": repairs_applied or [],
    }


def _selection_stage_summary(
    source_scripts: list[str],
    pattern_refs: list[str],
    indexed_pattern_windows: list[dict[str, Any]],
) -> str:
    parts = []
    if source_scripts:
        parts.append(f"{len(source_scripts)} source script(s)")
    if pattern_refs:
        parts.append(f"{len(pattern_refs)} pattern reference(s)")
    if indexed_pattern_windows:
        parts.append(f"{len(indexed_pattern_windows)} indexed pattern window(s)")
    if not parts:
        return "No source scripts or reusable patterns were selected yet."
    return "Recorded " + ", ".join(parts) + "."


def _context_kind(context: ProjectLike | None) -> str:
    if context is None:
        return "none"
    return str(context.manifest.get("kind") or "project")


def _context_sources(context: ProjectLike | None) -> list[dict[str, Any]]:
    if context is None:
        return []
    source_projects = context.manifest.get("source_projects")
    if isinstance(source_projects, list) and source_projects:
        return [
            {
                "name": item.get("name"),
                "root": item.get("root"),
                "manifest": item.get("manifest"),
                "source_archive": item.get("source_archive"),
                "copied_archive": item.get("copied_archive"),
            }
            for item in source_projects
            if isinstance(item, dict)
        ]
    return [
        {
            "name": context.name,
            "root": str(context.root),
            "manifest": str(context.root / "manifest.json"),
            "source_archive": context.manifest.get("source_archive"),
            "copied_archive": context.manifest.get("copied_archive"),
        }
    ]


def _context_root_path(context: ProjectLike | None) -> Path | None:
    if context is None:
        return None
    root = getattr(context, "root", None)
    if root:
        return Path(root)
    for source in _context_sources(context):
        root_text = str(source.get("root") or "").strip()
        if root_text:
            return Path(root_text)
    return None


def _subroutine_lookup_from_resolution(resolution: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for record in resolution.get("resolved") or []:
        if not isinstance(record, Mapping):
            continue
        keys = {
            str(record.get("ref") or ""),
            str(record.get("object_name") or ""),
            str(record.get("qualified_name") or ""),
        }
        folder = str(record.get("folder") or record.get("object_subfolder_path") or "").strip()
        object_name = str(record.get("object_name") or "").strip()
        if folder and object_name:
            keys.add(f"{folder}\\{object_name}")
        for key in keys:
            normalized = re.sub(r"[^a-z0-9]+", "", key.casefold())
            if normalized:
                lookup[normalized] = dict(record)
    return lookup


def _dedupe_context_sources(contexts: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in contexts:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").strip()
        root = str(item.get("root") or "").strip()
        manifest = str(item.get("manifest") or "").strip()
        key = (name.casefold(), root.casefold(), manifest.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(item))
    return result


def _stamp_approved_automated_verification_moves(
    ir: dict[str, Any],
    request_spec_doc: dict[str, Any] | None,
) -> dict[str, Any]:
    """Keep baseline/preserved move_plate steps automated when the spec approves it."""
    generation = (request_spec_doc or {}).get("generation") if isinstance(request_spec_doc, dict) else {}
    review = (request_spec_doc or {}).get("review") if isinstance(request_spec_doc, dict) else {}
    approved = bool(isinstance(generation, dict) and generation.get("approve_automated_motion"))
    decisions = " ".join(
        str(item)
        for item in ((review or {}).get("decisions") or [])
        if str(item).strip()
    ).casefold()
    if "approved automated verification motion" in decisions:
        approved = True
    if not approved:
        return ir
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or str(step.get("operation") or "") != "move_plate":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        params = dict(params)
        params["allow_automated_verification_motion"] = True
        params.setdefault("verification_after_motion_required", True)
        step["parameters"] = params
        flags = {str(flag) for flag in (step.get("safety_flags") or [])}
        flags.add("automated_verification_motion")
        step["safety_flags"] = sorted(flags)
    return ir


def _clear_verification_script_protocol_comment(ir: dict[str, Any]) -> dict[str, Any]:
    """Do not publish the generation prompt as the FluentControl script comment."""
    protocol = ir.get("protocol") if isinstance(ir.get("protocol"), dict) else {}
    name = str(protocol.get("name") or ir.get("id") or "").casefold()
    if "verification" not in name:
        return ir
    protocol = dict(protocol)
    protocol["comment"] = ""
    ir["protocol"] = protocol
    return ir


def _augment_ir_generation_metadata(
    ir: dict[str, Any],
    intent: str,
    context: ProjectLike | None,
    selection: dict[str, Any],
) -> None:
    source = ir.setdefault("source", {})
    source.setdefault("generation_intent", intent)
    source.setdefault("verbatim_prompt", intent)
    source.setdefault("original_user_prompt", intent)
    source.setdefault("context", context.name if context else None)
    source.setdefault("context_kind", _context_kind(context) if context else None)
    source.setdefault("contexts", _context_sources(context))
    source.setdefault("selected_source_scripts", selection.get("resolved_source_scripts", []))
    source.setdefault("selected_patterns", selection.get("pattern_refs", []))
    source.setdefault("selected_pattern_windows", selection.get("indexed_pattern_windows", []))
    if selection.get("index_db"):
        source.setdefault(
            "pattern_index",
            {
                "database": selection.get("index_db"),
                "pattern_ids": selection.get("pattern_ids", []),
                "pattern_queries": selection.get("pattern_queries", []),
                "source_script_rank": selection.get("source_script_rank", 1),
            },
        )
    dependencies = ir.setdefault("dependencies", [])
    for dependency in pattern_window_dependencies(selection.get("indexed_pattern_windows", [])):
        key = (
            dependency.get("kind"),
            dependency.get("name"),
            dependency.get("pattern_id"),
            dependency.get("source_path"),
        )
        if not any(
            (
                existing.get("kind"),
                existing.get("name"),
                existing.get("pattern_id"),
                existing.get("source_path"),
            )
            == key
            for existing in dependencies
            if isinstance(existing, dict)
        ):
            dependencies.append(dependency)
    assumptions = ir.setdefault("safety_assumptions", [])
    if not any(
        isinstance(assumption, dict) and assumption.get("id") == "generated_from_official_workflow"
        for assumption in assumptions
    ):
        assumptions.append(
            {
                "id": "generated_from_official_workflow",
                "text": "This IR entered the official inspect-plan-draft-simulate-repair-compile generation workflow.",
            }
        )


def _script_candidate_path(context: ProjectLike, script: dict[str, Any]) -> Path:
    raw = script.get("resolved_path") or script.get("extracted_path") or ""
    path = Path(str(raw)).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (context.root / path).resolve()


def _source_script_record(context: ProjectLike, value: str) -> dict[str, Any]:
    resolved = resolve_context_script(context, value)
    for script in context.manifest.get("scripts", []):
        candidate = _script_candidate_path(context, script)
        if candidate == resolved:
            record = {**script, "resolved_path": str(resolved)}
            fresh = resolve_recorded_script_path(record, context=context)
            if fresh is not None:
                record["resolved_path"] = str(fresh)
            return record
    record = {"entry": value, "extracted_path": str(resolved), "resolved_path": str(resolved)}
    fresh = resolve_recorded_script_path(record, context=context)
    if fresh is not None:
        record["resolved_path"] = str(fresh)
    return record


def _context_source_projects(context: ProjectLike | None, *, primary_only: bool = False) -> list[Path]:
    if context is None:
        return []
    if primary_only:
        return _primary_context_source_projects(context)
    contextual_paths: list[Path] = []
    for source in _context_sources(context):
        source_root = Path(str(source.get("root") or context.root))
        local_source_dir = source_root / "source"
        candidates = [
            source.get("copied_archive"),
            source.get("source_archive"),
        ]
        paths = _existing_archive_paths(candidates, local_source_dir=local_source_dir)
        if paths:
            contextual_paths.append(paths[0])
    if contextual_paths:
        return contextual_paths
    raw_values = []
    local_source_dir = context.root / "source"
    if local_source_dir.exists():
        raw_values.extend(sorted(local_source_dir.glob("*.zeia")))
    raw_values.extend(context.manifest.get("copied_archives") or [])
    raw_values.extend(context.manifest.get("source_archives") or [])
    raw = context.manifest.get("copied_archive") or context.manifest.get("source_archive")
    if raw:
        raw_values.append(raw)

    paths = []
    seen = set()
    for raw_value in raw_values:
        path = raw_value if isinstance(raw_value, Path) else Path(str(raw_value))
        if not path.exists() and path.name:
            local_candidate = local_source_dir / path.name
            if local_candidate.exists():
                path = local_candidate
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        paths.append(resolved)
    return paths


def _primary_context_source_projects(context: ProjectLike) -> list[Path]:
    """Return the ZEIA archive(s) that should be used as generated-output bases.

    A project collection can include a script-specific source export plus a
    large full-system export used only for reference resolution. Packaging every
    archive in the collection creates duplicate deliverables and can spend
    minutes reprocessing the full export. The first source context is the
    request's primary base; later contexts remain provenance/reference inputs.
    """
    sources = _context_sources(context)
    if sources:
        first = sources[0]
        source_root = Path(str(first.get("root") or context.root))
        candidates = [
            first.get("copied_archive"),
            first.get("source_archive"),
        ]
        local_source_dir = source_root / "source"
        paths = _existing_archive_paths(candidates, local_source_dir=local_source_dir)
        if paths:
            return paths[:1]
    return _context_source_projects(context, primary_only=False)[:1]


def _existing_archive_paths(raw_values: list[Any], *, local_source_dir: Path | None = None) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for raw_value in raw_values:
        if not raw_value:
            continue
        path = raw_value if isinstance(raw_value, Path) else Path(str(raw_value))
        if not path.exists() and path.name and local_source_dir is not None:
            local_candidate = local_source_dir / path.name
            if local_candidate.exists():
                path = local_candidate
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        paths.append(resolved)
    return paths


def _context_project_report(context: ProjectLike | None) -> dict[str, Path]:
    if context is None:
        return {}
    report = context.root / "project_report.md"
    return {"project_report": report} if report.exists() else {}


def _selected_source_script_paths(
    selected: list[dict[str, Any]],
    *,
    context: ProjectLike | None = None,
) -> list[Path]:
    paths: list[Path] = []
    for item in selected:
        candidate = resolve_recorded_script_path(item, context=context)
        if candidate is not None:
            paths.append(candidate)
    return paths


def _generation_ir_source_mode(
    *,
    ir_source: Path | None,
    recipe: Mapping[str, Any] | None,
    preserve_regeneration_baseline: bool,
    regeneration_baseline: Path | None,
) -> str:
    """Select the sole step source using the documented strict precedence."""
    if ir_source is not None:
        return "explicit_ir"
    if recipe is not None:
        return "explicit_recipe"
    if preserve_regeneration_baseline:
        return (
            "preserve_regeneration_baseline"
            if regeneration_baseline is not None
            else "missing_regeneration_baseline"
        )
    return "automatic_synthesis"


def _annotate_explicit_recipe_prompt_media(
    ir: dict[str, Any],
    *,
    recipe: Mapping[str, Any] | None,
    generation_options: GenerationOptions,
) -> dict[str, Any]:
    """Attach recipe prompt media slots before IR export, draft render, and compile."""
    ir = apply_default_verification_worktable_bindings(ir, recipe=recipe)
    return annotate_verification_prompts_with_media(
        ir,
        default_rup_kind=generation_options.verification_prompt_rup,
    )


def _attach_regeneration_baseline_context(
    ir: dict[str, Any],
    *,
    regeneration_baseline: Path,
    context: ProjectLike | None,
    protocol_name: str | None,
    project_archive: Path | None,
    supplies_steps: bool,
) -> None:
    """Retain baseline provenance without implicitly replacing requested steps."""
    source_metadata = ir.setdefault("source", {})
    primary_source = (_context_sources(context) or [{}])[0]
    baseline_record = {
        "object_name": str(
            (ir.get("protocol") or {}).get("name") or protocol_name or ""
        ),
        "resolved_path": str(regeneration_baseline),
        "source_context": primary_source.get("name"),
        "context_root": primary_source.get("root"),
        "selection_reason": (
            "preserved_regeneration_baseline"
            if supplies_steps
            else "regeneration_baseline_context"
        ),
    }
    selected = [
        item
        for item in (source_metadata.get("selected_source_scripts") or [])
        if isinstance(item, dict)
        and str(item.get("resolved_path") or "").casefold()
        != str(regeneration_baseline).casefold()
    ]
    source_metadata["selected_source_scripts"] = (
        [baseline_record] if supplies_steps else [baseline_record, *selected]
    )
    source_metadata["regeneration_baseline"] = {
        "path": str(regeneration_baseline),
        "project_archive": str(project_archive) if project_archive is not None else None,
        "selection_reason": "exact_protocol_name_match",
        "role": "step_source" if supplies_steps else "context_only",
    }


def _matching_regeneration_baseline_script(
    context: ProjectLike | None,
    protocol_name: str | None,
) -> Path | None:
    """Return the primary-project script matching the requested protocol identity."""
    if context is None or not protocol_name:
        return None
    target = str(protocol_name).strip().casefold()
    if not target:
        return None

    primary_root = context.root.resolve()
    sources = _context_sources(context)
    if sources and sources[0].get("root"):
        primary_root = Path(str(sources[0]["root"])).resolve()

    candidates: list[tuple[int, Path]] = []
    for script in context.manifest.get("scripts") or []:
        if not isinstance(script, Mapping):
            continue
        object_name = str(script.get("object_name") or script.get("name") or "").strip()
        if object_name.casefold() != target:
            continue
        raw = str(script.get("resolved_path") or script.get("extracted_path") or "").strip()
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            source_root = Path(str(script.get("source_root") or primary_root))
            path = source_root / path
        if not path.is_file():
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(primary_root)
            primary_score = 0
        except ValueError:
            primary_score = 1
        candidates.append((primary_score, resolved))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], str(item[1]).casefold()))
    return candidates[0][1]


def _source_file_reference_paths(paths: list[Path]) -> list[str]:
    references: list[str] = []
    pattern = re.compile(
        r"<FileReference>.*?<File>(.*?)</File>.*?</FileReference>",
        re.DOTALL | re.IGNORECASE,
    )
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        references.extend(_clean_windows_literal(match) for match in pattern.findall(text))
    return [value for value in references if re.match(r"^[A-Za-z]:\\", value)]


def _compiled_external_command_paths(path: Path) -> list[str]:
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    values: list[str] = []
    for tag in ("Application", "VbScript"):
        for raw in re.findall(
            rf"<{tag}\b[^>]*>(.*?)</{tag}>",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            value = _clean_windows_literal(raw)
            if re.match(r"^[A-Za-z]:\\", value):
                values.append(value)
    return values


def _clean_windows_literal(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'").replace("/", "\\")


def _dedupe_casefolded_strings(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


def _source_irs_for_paths(paths: list[Path]) -> list[dict[str, Any]]:
    source_irs = []
    for path in paths:
        try:
            payload = protocol_ir_from_path(path)
        except Exception:
            continue
        if payload.get("ir_version") == CANONICAL_IR_VERSION:
            source_irs.append(payload)
    return source_irs


def _preferred_worktable_from_request(request_spec: Mapping[str, Any] | None) -> dict[str, Any]:
    """Pull an explicit worktable name/guid from request.spec when present."""
    if not isinstance(request_spec, Mapping):
        return {"name": "", "guid": ""}
    candidates: list[Any] = [
        request_spec.get("worktable"),
        (request_spec.get("recipe") or {}).get("worktable") if isinstance(request_spec.get("recipe"), Mapping) else None,
        (request_spec.get("generation") or {}).get("worktable")
        if isinstance(request_spec.get("generation"), Mapping)
        else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            name = str(candidate.get("name") or "").strip()
            guid = str(candidate.get("guid") or "").strip()
            if name or guid:
                return {"name": name, "guid": guid}
        elif isinstance(candidate, str) and candidate.strip():
            return {"name": candidate.strip(), "guid": ""}
    recipe = request_spec.get("recipe") if isinstance(request_spec.get("recipe"), Mapping) else {}
    name = str(recipe.get("worktable") or "").strip() if not isinstance(recipe.get("worktable"), Mapping) else ""
    guid = str(recipe.get("worktable_guid") or "").strip()
    return {"name": name, "guid": guid}


def _ensure_ir_worktable_bound(ir: Mapping[str, Any]) -> None:
    """Fail closed before Python render when the IR has no mined worktable."""
    worktable = ir.get("worktable") if isinstance(ir.get("worktable"), Mapping) else {}
    name = str(worktable.get("name") or "").strip()
    guid = str(worktable.get("guid") or "").strip()
    if name or guid:
        return
    raise PipelineError(
        "Protocol IR worktable is unbound (empty name and guid). "
        "Mine the worktable from the source ZEIA script binding or set "
        "request/recipe worktable before generate."
    )


def _default_worktable(
    context: ProjectLike | None,
    selected_scripts: list[dict[str, Any]],
    *,
    preferred: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve the protocol worktable from script refs / recipe, not archive order.

    Preference order:
    1. Recipe ``worktable`` / ``worktable_guid`` matching a Script→WorktableWorkspace ref
    2. Consensus WorktableWorkspace refs across selected scripts
    3. Recipe name/guid matching a manifest workspace
    4. Recipe name/guid as-is when provided
    5. Sole manifest workspace (only when there is exactly one)

    Never pick ``workspaces[0]`` among multiple workspaces — that drifts with ZEIA
    extract order.
    """
    preferred_name = str((preferred or {}).get("name") or "").strip()
    preferred_guid = str((preferred or {}).get("guid") or "").strip()
    script_refs = _worktable_workspace_refs_from_scripts(selected_scripts)

    matched_ref = _match_worktable_candidate(
        script_refs,
        name=preferred_name,
        guid=preferred_guid,
    )
    if matched_ref is not None:
        return matched_ref

    consensus = _consensus_worktable_ref(script_refs)
    if consensus is not None and not (preferred_name or preferred_guid):
        return consensus

    workspaces = []
    if context is not None:
        workspaces = [
            item
            for item in (context.manifest.get("workspaces") or [])
            if isinstance(item, Mapping)
        ]

    if preferred_name or preferred_guid:
        matched_workspace = _match_manifest_workspace_record(
            workspaces,
            name=preferred_name,
            guid=preferred_guid,
        )
        if matched_workspace is not None:
            return matched_workspace
        # Prefer script consensus over a non-matching preferred overlay onto a
        # random first workspace.
        if consensus is not None:
            return consensus
        return {
            "name": preferred_name,
            "guid": preferred_guid,
            "auto_place": False,
        }

    if consensus is not None:
        return consensus

    if len(workspaces) == 1:
        return _worktable_from_manifest_workspace(workspaces[0])

    return {"name": "", "guid": "", "auto_place": False}


def _worktable_workspace_refs_from_scripts(
    selected_scripts: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for script in selected_scripts or []:
        if not isinstance(script, Mapping):
            continue
        for ref in script.get("references") or []:
            if not isinstance(ref, Mapping):
                continue
            if str(ref.get("type_id") or "").strip() != "WorktableWorkspace":
                continue
            name = str(ref.get("object_name") or ref.get("name") or "").strip()
            guid = str(ref.get("guid") or "").strip()
            # Also accept dependency workspace_guids when references are sparse.
            key = (name.casefold(), guid.casefold())
            if not name and not guid:
                continue
            if key in seen:
                continue
            seen.add(key)
            refs.append({"name": name, "guid": guid, "auto_place": False})
        deps = script.get("dependencies") if isinstance(script.get("dependencies"), Mapping) else {}
        for value in deps.get("workspace_guids") or []:
            guid = str(value or "").strip()
            if not guid:
                continue
            # Skip GUID-only rows when a named WorktableWorkspace ref already
            # carries this GUID; otherwise consensus can keep the empty name.
            if any(str(ref.get("guid") or "").strip().casefold() == guid.casefold() for ref in refs):
                continue
            key = ("", guid.casefold())
            if key in seen:
                continue
            seen.add(key)
            refs.append({"name": "", "guid": guid, "auto_place": False})
    return refs


def _match_worktable_candidate(
    candidates: Sequence[Mapping[str, Any]],
    *,
    name: str,
    guid: str,
) -> dict[str, Any] | None:
    if not (name or guid):
        return None
    name_key = name.casefold()
    guid_key = guid.casefold()
    for candidate in candidates:
        candidate_name = str(candidate.get("name") or "").strip()
        candidate_guid = str(candidate.get("guid") or "").strip()
        name_matches = bool(name_key and candidate_name.casefold() == name_key)
        guid_matches = bool(guid_key and candidate_guid.casefold() == guid_key)
        if (name and guid and name_matches and guid_matches) or (name_matches and not guid) or (guid_matches and not name):
            return {
                "name": candidate_name or name,
                "guid": candidate_guid or guid,
                "auto_place": False,
            }
        if name and guid and (name_matches or guid_matches):
            # Partial recipe pin: keep the stronger identity from the script ref.
            return {
                "name": candidate_name or name,
                "guid": candidate_guid or guid,
                "auto_place": False,
            }
    return None


def _consensus_worktable_ref(
    refs: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    if not refs:
        return None
    by_guid: dict[str, Mapping[str, Any]] = {}
    for item in refs:
        guid = str(item.get("guid") or "").strip()
        if not guid:
            continue
        key = guid.casefold()
        existing = by_guid.get(key)
        if existing is None:
            by_guid[key] = item
            continue
        # Prefer the row that still has an object name.
        if not str(existing.get("name") or "").strip() and str(item.get("name") or "").strip():
            by_guid[key] = item
    if len(by_guid) == 1:
        item = next(iter(by_guid.values()))
        return {
            "name": str(item.get("name") or "").strip(),
            "guid": str(item.get("guid") or "").strip(),
            "auto_place": False,
        }
    by_name = {
        str(item.get("name") or "").strip().casefold(): item
        for item in refs
        if str(item.get("name") or "").strip()
    }
    if len(by_name) == 1 and not by_guid:
        item = next(iter(by_name.values()))
        return {
            "name": str(item.get("name") or "").strip(),
            "guid": str(item.get("guid") or "").strip(),
            "auto_place": False,
        }
    return None


def _match_manifest_workspace_record(
    workspaces: Sequence[Mapping[str, Any]],
    *,
    name: str,
    guid: str,
) -> dict[str, Any] | None:
    if not (name or guid):
        return None
    name_key = name.casefold()
    guid_key = guid.casefold()
    for workspace in workspaces:
        workspace_name = str(workspace.get("object_name") or workspace.get("name") or "").strip()
        workspace_guid = _workspace_record_guid(workspace)
        name_matches = bool(name_key and workspace_name.casefold() == name_key)
        guid_matches = bool(guid_key and workspace_guid.casefold() == guid_key)
        if (name and guid and name_matches and guid_matches) or (name_matches and not guid) or (guid_matches and not name):
            return _worktable_from_manifest_workspace(workspace, fallback_name=name, fallback_guid=guid)
        if name and guid and (name_matches or guid_matches):
            return _worktable_from_manifest_workspace(workspace, fallback_name=name, fallback_guid=guid)
    return None


def _worktable_from_manifest_workspace(
    workspace: Mapping[str, Any],
    *,
    fallback_name: str = "",
    fallback_guid: str = "",
) -> dict[str, Any]:
    name = str(workspace.get("object_name") or workspace.get("name") or "").strip() or fallback_name
    guid = _workspace_record_guid(workspace) or fallback_guid
    return {"name": name, "guid": guid, "auto_place": False}


def _recipe_selected_source_records(
    context: ProjectLike | None,
    request_spec: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    if context is None or not isinstance(request_spec, Mapping):
        return []
    source = request_spec.get("source")
    if not isinstance(source, Mapping):
        return []
    records: list[dict[str, Any]] = []
    for value in source.get("source_scripts") or []:
        name = str(value or "").strip()
        if not name:
            continue
        try:
            records.append(_source_script_record(context, name))
        except Exception:
            continue
    return records


def _worktable_request_matches_context(
    context: ProjectLike | None,
    requested: Mapping[str, Any],
) -> bool:
    name = str(requested.get("name") or "").strip()
    guid = str(requested.get("guid") or "").strip()
    if context is None:
        return bool(name or guid)
    workspaces = context.manifest.get("workspaces") or []
    if not workspaces:
        return bool(name or guid)
    for workspace in workspaces:
        if not isinstance(workspace, Mapping):
            continue
        workspace_name = str(workspace.get("object_name") or workspace.get("name") or "").strip()
        workspace_guid = _workspace_record_guid(workspace)
        name_matches = bool(name and workspace_name.casefold() == name.casefold())
        guid_matches = bool(guid and workspace_guid.casefold() == guid.casefold())
        if (name and guid and name_matches and guid_matches) or (name_matches and not guid) or (guid_matches and not name):
            return True
    return False


def _workspace_record_guid(workspace: Mapping[str, Any]) -> str:
    for key in ("workspace_guid", "guid"):
        value = str(workspace.get(key) or "").strip()
        if value:
            return value
    guids = workspace.get("guids") or []
    if isinstance(guids, (list, tuple)):
        for value in guids:
            text = str(value or "").strip()
            if text:
                return text
    for key in ("extracted_path", "entry", "file_path"):
        value = str(workspace.get(key) or "").strip()
        if value:
            return Path(value.replace("\\", "/")).stem
    return ""


def _protocol_name_from_intent(intent: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", intent)[:8]
    return " ".join(words) if words else "Generated protocol"


def _fluent_method_from_ir(ir: dict[str, Any], fallback: str) -> str:
    protocol = ir.get("protocol") if isinstance(ir, dict) else {}
    method = str((protocol or {}).get("name") or "").strip()
    return method or fallback


def _safe_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return text or "generated_protocol"


def finalize_media_slot_files(
    ir: dict[str, Any],
    media_dir: Path,
    *,
    build_dir: Path | None = None,
) -> tuple[Path, list[dict[str, Any]]]:
    """Convert dropped videos and normalize Worktable GIF slots in-place."""
    specs = required_media_slot_specs(media_slot_specs(ir))
    worktable_slots = worktable_pipeline_video_slots(specs)
    media_ops: list[dict[str, Any]] = []
    media_ops.extend(
        convert_dropped_video_slots(
            media_dir,
            specs,
            worktable_video_slots=worktable_slots,
        )
    )
    media_ops.extend(
        normalize_worktable_detail_gifs(
            media_dir,
            specs,
            worktable_video_slots=worktable_slots,
        )
    )
    return media_dir, media_ops


def _merge_setup_recipe_groups(recipe: dict[str, Any]) -> str:
    groups = recipe.get("groups")
    if not isinstance(groups, list):
        return CANONICAL_SETUP_GROUP_NAME

    merged_group: dict[str, Any] | None = None
    merged_steps: list[Any] = []
    merged_descriptions: list[str] = []
    non_setup_groups: list[Any] = []

    for group in groups:
        if not isinstance(group, dict) or not is_setup_group_name(group.get("name")):
            non_setup_groups.append(group)
            continue
        if merged_group is None:
            merged_group = {
                key: copy.deepcopy(value)
                for key, value in group.items()
                if key not in {"name", "description", "steps", "toggle_variable", "toggle_label", "toggle_default"}
            }
            merged_group["name"] = CANONICAL_SETUP_GROUP_NAME
        description = str(group.get("description") or "").strip()
        if description and description.casefold() not in {item.casefold() for item in merged_descriptions}:
            merged_descriptions.append(description)
        steps = group.get("steps")
        if isinstance(steps, list):
            merged_steps.extend(copy.deepcopy(steps))

    if merged_group is not None:
        merged_group["description"] = " ".join(merged_descriptions).strip()
        merged_group["steps"] = merged_steps
        recipe["groups"] = [merged_group, *non_setup_groups]
    return CANONICAL_SETUP_GROUP_NAME


def build_ir_from_recipe(
    recipe: dict[str, Any],
    *,
    intent: str,
    context: ProjectLike | None,
    protocol_name: str | None = None,
    request_spec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synthesize a verification-recipe protocol IR."""
    recipe = prepare_interactive_recipe(recipe, request_spec=request_spec, intent=intent)
    recipe = copy.deepcopy(recipe)
    source_script_name = _recipe_source_script_name(recipe, request_spec=request_spec)
    setup_group_name = _merge_setup_recipe_groups(recipe)
    prep = resolve_capbc_prep_defaults(recipe, context=context)
    deck_location = str(prep.get("tube_deck_location") or "").strip()
    if deck_location:
        normalize_recipe_subroutine_deck_locations(recipe, deck_location)
    name = protocol_name or _protocol_name_from_intent(intent)
    selected_source_records = _recipe_selected_source_records(context, request_spec)
    request_source = request_spec.get("source") if isinstance(request_spec, Mapping) else {}
    requested_worktable = {
        "name": str(recipe.get("worktable") or "").strip(),
        "guid": str(recipe.get("worktable_guid") or "").strip(),
    }
    worktable = _default_worktable(
        context,
        selected_source_records,
        preferred=requested_worktable,
    )
    counter = itertools.count(1)

    def _next_step() -> tuple[int, str]:
        idx = next(counter)
        return idx, f"step_{idx:03d}"

    labware_entries: list[dict[str, Any]] = []
    setup_steps: list[dict[str, Any]] = []
    for lw in recipe.get("labware") or []:
        if not isinstance(lw, dict):
            continue
        label = str(lw.get("label") or "").strip()
        if not label:
            continue
        catalog = str(lw.get("catalog") or lw.get("labware_type") or "").strip()
        location = str(lw.get("location") or "").strip()
        position = int(lw.get("site") or lw.get("position") or 0)
        rotation = int(lw.get("rotation") or 0)
        has_lid = bool(lw.get("has_lid", False))
        deck_location = f"{location} {position}".strip() if location else ""
        labware_entries.append(
            {
                "catalog": catalog,
                "deck_location": deck_location,
                "has_lid": has_lid,
                "id": _safe_id(label),
                "label": label,
                "location": location,
                "position": position,
                "role": "protocol_target",
                "rotation": rotation,
                "source": "verification_recipe",
            }
        )
        idx, sid = _next_step()
        add_labware_parameters = {
            "has_lid": has_lid,
            "label": label,
            "labware_type": catalog,
            "location": location,
            "position": position,
            "rotation": rotation,
        }
        if _add_labware_location_requires_raw_xml(location):
            add_labware_parameters["raw_xml"] = _render_add_labware_raw_xml(
                add_labware_parameters,
                line_number=idx,
            )
        setup_steps.append(
            {
                "command_id": "AddLabwareDataV1",
                "group": setup_group_name,
                "id": sid,
                "index": idx,
                "name": "Add Labware",
                "operation": "add_labware",
                "parameters": add_labware_parameters,
                "safety_flags": [],
                "target_labware": label,
            }
        )

    worktable_patterns = recipe_worktable_patterns(recipe)
    subroutine_names: list[str] = []
    prep_steps_ir: list[dict[str, Any]] = []
    for raw_step in recipe.get("prep_steps") or []:
        built = _recipe_step_to_ir(
            raw_step,
            CAPBC_PREP_GROUP_NAME,
            _next_step,
            subroutine_names,
            labware_entries=labware_entries,
            worktable_patterns=worktable_patterns,
        )
        if built is None:
            continue
        if isinstance(built, list):
            prep_steps_ir.extend(built)
        else:
            prep_steps_ir.append(built)

    body_steps: list[dict[str, Any]] = []
    recipe_group_notes: list[dict[str, str]] = []
    source_startup_sequences = _source_startup_sequences(recipe, context, source_script_name)
    injected_source_startups: list[dict[str, Any]] = []
    source_inherited_variables: list[dict[str, Any]] = []

    category_conditions: dict[str, dict[str, Any]] = {}
    toggle_form_fields: list[dict[str, Any]] = []
    extra_variables: list[dict[str, Any]] = []
    existing_variable_names = {
        str(record.get("name") or "").strip()
        for record in (recipe.get("variables") or [])
        if isinstance(record, dict)
    }
    for group in recipe.get("groups") or []:
        if not isinstance(group, dict):
            continue
        toggle_var = str(group.get("toggle_variable") or "").strip()
        if not toggle_var:
            continue
        group_name = str(group.get("name") or "Verification")
        default_value = str(group.get("toggle_default") or "yes").strip() or "yes"
        category_conditions[group_name] = {
            "variable": toggle_var,
            "op": "==",
            "value": default_value,
            "name": group_name,
        }
        toggle_form_fields.append(
            {
                "name": toggle_var,
                "display_text": str(group.get("toggle_label") or f"Run {group_name}?"),
                "display_type": "Combobox",
                "variable_type": "System.String",
                "value": default_value,
                "allowed_values": ["yes", "no"],
            }
        )
        if toggle_var not in existing_variable_names:
            existing_variable_names.add(toggle_var)
            extra_variables.append({"name": toggle_var, "value": default_value})
    simulation_values = _normalize_toggle_simulation_values(
        list(recipe.get("simulation_values") or []),
        category_conditions,
    )

    if toggle_form_fields:
        selector_group = str(recipe.get("category_selector_group") or setup_group_name)
        if is_setup_group_name(selector_group):
            selector_group = setup_group_name
        startup_prompt = recipe.get(
            "category_selector_start_prompt",
            "Script started. Press OK to choose which verification tests to run.",
        )
        if startup_prompt:
            idx, sid = _next_step()
            body_steps.append(
                {
                    "command_id": "UserPromptStatement",
                    "group": selector_group,
                    "id": sid,
                    "index": idx,
                    "name": "Runtime Started",
                    "operation": "prompt_user",
                    "parameters": {
                        "prompt": str(startup_prompt),
                        "timeout": 0,
                    },
                    "safety_flags": [],
                }
            )
        selector_title = str(recipe.get("category_selector_title") or "Select verification tests")
        selector_instructions = str(
            recipe.get("category_selector_instructions")
            or RUP_VARIABLE_SELECTOR_INSTRUCTIONS
        )
        idx, sid = _next_step()
        body_steps.append(
            {
                "command_id": "RUPVariableStatement",
                "group": selector_group,
                "id": sid,
                "index": idx,
                "name": "Select verification tests",
                "operation": "runtime_variable_prompt",
                "parameters": {
                    "screen_title": selector_title,
                    "instructions": selector_instructions,
                    "display_and_wait": True,
                    "timeout": 0,
                    "line_number": idx,
                    "columns": 1,
                    "variables": toggle_form_fields,
                },
                "safety_flags": [],
            }
        )

    for group in recipe.get("groups") or []:
        if not isinstance(group, dict):
            continue
        group_name = str(group.get("name") or "Verification")
        group_description = recipe_group_description(group)
        if group_description:
            # Keep variable/setup notes out of FluentControl XSCR comments.
            recipe_group_notes.append({"group": group_name, "description": group_description})
        for raw_step in group.get("steps") or []:
            if isinstance(raw_step, dict) and recipe_step_type(raw_step) == "comment":
                comment_text = str(raw_step.get("comment") or raw_step.get("text") or "").strip()
                if group_description and comment_text.casefold() == group_description.casefold():
                    continue
                if is_meta_verification_group_comment(comment_text):
                    recipe_group_notes.append({"group": group_name, "description": comment_text})
                    continue
            built = _recipe_step_to_ir(
                raw_step,
                group_name,
                _next_step,
                subroutine_names,
                labware_entries=labware_entries,
                worktable_patterns=worktable_patterns,
            )
            if built is None:
                continue
            appended_steps = built if isinstance(built, list) else [built]
            if isinstance(built, list):
                body_steps.extend(built)
            else:
                body_steps.append(built)
            for sequence in source_startup_sequences:
                if _source_startup_already_injected(sequence, injected_source_startups):
                    continue
                if not _should_inject_source_startup_after(group_name, appended_steps, sequence):
                    continue
                inherited_steps = _instantiate_source_startup_steps(sequence, group_name, _next_step)
                body_steps.extend(inherited_steps)
                injected_source_startups.append(sequence)
                source_inherited_variables.extend(copy.deepcopy(sequence.get("variables") or []))

    dependencies: list[dict[str, Any]] = []
    seen_catalogs: set[str] = set()
    for entry in labware_entries:
        catalog = entry["catalog"]
        if catalog and catalog not in seen_catalogs:
            seen_catalogs.add(catalog)
            dependencies.append({"kind": "labware", "name": catalog, "required": True})
    for subroutine in dict.fromkeys(name for name in subroutine_names if name):
        dependencies.append({"kind": "subroutine", "name": subroutine, "required": True})
    if worktable.get("guid"):
        dependencies.append(
            {
                "kind": "WorktableWorkspace",
                "name": worktable.get("name"),
                "guid": worktable.get("guid"),
                "required": True,
            }
        )

    ir = {
        "ir_version": CANONICAL_IR_VERSION,
        "id": _safe_id(name),
        "protocol": {
            "name": name,
            "comment": "",
            "schema_intent": "canonical source of truth for generated Tecan artifacts",
        },
        "source": {
            "format": "verification_recipe",
            "intent": intent,
            "verbatim_prompt": intent,
            "original_user_prompt": intent,
            "context": context.name if context else None,
            "context_kind": _context_kind(context) if context else None,
            "contexts": _context_sources(context),
            "source_scripts": [
                str(value).strip()
                for value in (request_source.get("source_scripts") or [])
                if str(value).strip()
            ],
            "selected_source_scripts": selected_source_records,
            "selected_patterns": [],
            "selected_pattern_windows": [],
            "verification_recipe": {
                "group_count": len(recipe.get("groups") or []),
                "labware_count": len(labware_entries),
                "step_count": len(setup_steps) + len(prep_steps_ir) + len(body_steps),
            },
            "recipe_group_notes": recipe_group_notes,
        },
        "worktable": worktable,
        "labware": labware_entries,
        "reagents": [],
        "liquid_classes": [],
        "variables": list(recipe.get("variables") or []) + extra_variables,
        "simulation_values": simulation_values,
        "worklists": [],
        "dependencies": dependencies,
        "category_conditions": category_conditions,
        "safety_assumptions": [
            {
                "id": "verification_recipe_authored",
                "text": "Steps were synthesized from a declarative verification recipe in request.spec.yaml. Review the recipe and generated IR against the deck layout before instrument use.",
            },
            {
                "id": "manual_validation_required",
                "text": "Generated artifacts must be reviewed, simulated, and validated in FluentControl before instrument use.",
            },
        ],
        "steps": setup_steps + prep_steps_ir + body_steps,
    }
    if injected_source_startups:
        if source_inherited_variables:
            ir["variables"] = _dedupe_ir_variables([*ir.get("variables", []), *source_inherited_variables])
        ir["source"].setdefault("source_startup_inheritance", [])
        ir["source"]["source_startup_inheritance"] = [
            {
                "source_context": item.get("source_context"),
                "source_script": item.get("source_script"),
                "source_path": item.get("source_path"),
                "source_group": item.get("source_group"),
                "target_group": item.get("target_group"),
                "detection_method": item.get("detection_method") or "source_group",
                "step_count": len(item.get("steps") or []),
            }
            for item in injected_source_startups
        ]
        for item in injected_source_startups:
            dependencies.append(
                {
                    "kind": "source_startup_group",
                    "name": item.get("source_group"),
                    "source_context": item.get("source_context"),
                    "source_script": item.get("source_script"),
                    "required": True,
                }
            )
    ir = normalize_runtime_variable_prompt_instructions(ir)
    ir = normalize_setup_groups(ir)
    apply_subroutine_deck_location_bindings(
        ir,
        recipe=recipe,
        context=context,
        manifest=context.manifest if context else None,
        source_script_name=source_script_name,
        prep_group_name=_capbc_prep_target_group(ir),
    )
    subroutine_lookup = _source_script_lookup(context)
    if subroutine_lookup:
        normalize_ir_subroutine_variable_mappings(
            ir,
            subroutine_lookup,
            context_root=getattr(context, "root", None),
        )
    _normalize_ir_labware_labels_against_manifest(ir, context.manifest if context else None)
    _attach_source_move_patterns(ir, selected_source_records, context=context)
    ir = normalize_group_hierarchy(ir)
    return ir


def _capbc_prep_target_group(ir: Mapping[str, Any]) -> str:
    for step in ir.get("steps") or []:
        if not isinstance(step, Mapping) or step.get("operation") != "call_subroutine":
            continue
        params = step.get("parameters") if isinstance(step.get("parameters"), Mapping) else {}
        if is_capbc_subroutine(str(params.get("subroutine") or "")):
            group = str(step.get("group") or "").strip()
            if group:
                return group
    return CAPBC_PREP_GROUP_NAME


_LABWARE_LABEL_VALUE_KEYS = {
    "destination_labware",
    "labware",
    "labware_name",
    "label",
    "onto_labware",
    "selected_labware_name",
    "source_labware",
    "target_labware",
}


def _normalize_ir_labware_labels_against_manifest(
    ir: dict[str, Any],
    manifest: Mapping[str, Any] | None,
) -> list[dict[str, str]]:
    """Align synthesized dynamic labware labels with labels proven by context.

    Some source scripts use variables such as ``NumSourceTubes_Main`` for loop
    logic while the actual worktable label remains ``SourceTube15[NumSourceTubes]``.
    Only rewrite the IR when the canonical label is present in the source
    manifest, so this stays a provenance-backed normalization rather than an
    alias guess.
    """

    if not isinstance(ir, dict) or not isinstance(manifest, Mapping):
        return []
    available = _source_manifest_labware_lookup(manifest)
    if not available:
        return []

    rewrites: dict[str, str] = {}
    for label in _iter_ir_labware_label_values(ir):
        canonical = _manifest_canonical_labware_label(label, available)
        if canonical and canonical != label:
            rewrites.setdefault(label, canonical)
    if not rewrites:
        return []

    _rewrite_ir_labware_labels(ir, rewrites)
    records = [
        {
            "from": source,
            "to": target,
            "reason": "source_manifest_dynamic_labware_label",
        }
        for source, target in sorted(rewrites.items())
    ]
    ir.setdefault("source", {})["labware_label_normalization"] = records
    return records


def _source_manifest_labware_lookup(manifest: Mapping[str, Any]) -> dict[str, str]:
    labels: dict[str, str] = {}

    def add(value: Any) -> None:
        label = str(value or "").strip()
        if label:
            labels.setdefault(_norm_labware_label(label), label)

    for value in manifest.get("labware_names") or []:
        add(value)
    for value in manifest.get("rack_labels") or []:
        add(value)
    for script in manifest.get("scripts") or []:
        if not isinstance(script, Mapping):
            continue
        dependencies = script.get("dependencies") or {}
        if not isinstance(dependencies, Mapping):
            continue
        for value in dependencies.get("labware_names") or []:
            add(value)
        for value in dependencies.get("rack_labels") or []:
            add(value)
    for project in manifest.get("source_projects") or []:
        if not isinstance(project, Mapping):
            continue
        project_manifest = _load_manifest_dict(project.get("manifest"))
        if not project_manifest:
            continue
        for value in project_manifest.get("labware_names") or []:
            add(value)
        for value in project_manifest.get("rack_labels") or []:
            add(value)
    return labels


def _manifest_canonical_labware_label(label: Any, available: Mapping[str, str]) -> str | None:
    raw = str(label or "").strip()
    if not raw:
        return None
    exact = re.sub(r"\s+", "", raw).casefold()
    # Compare against known labels themselves. Family keys strip brackets, so
    # SourceTube15[NumSourceTubes_Main] must not count as already-canonical just
    # because SourceTube15[NumSourceTubes] shares the SourceTube15 family key.
    for canonical in available.values():
        if re.sub(r"\s+", "", str(canonical)).casefold() == exact:
            return raw
    for candidate in _dynamic_labware_label_candidates(raw):
        candidate_exact = re.sub(r"\s+", "", candidate).casefold()
        for canonical in available.values():
            if re.sub(r"\s+", "", str(canonical)).casefold() == candidate_exact:
                return str(canonical)
        family_hit = available.get(_norm_labware_label(candidate))
        if family_hit:
            return family_hit
    return None


def _dynamic_labware_label_candidates(label: str) -> list[str]:
    candidates: list[str] = []
    match = re.fullmatch(r"(?P<head>.+\[)(?P<variable>[^\[\]]+)_Main(?P<tail>\])", label)
    if match:
        candidates.append(f"{match.group('head')}{match.group('variable')}{match.group('tail')}")
    return candidates


def _iter_ir_labware_label_values(value: Any, parent_key: str | None = None) -> list[str]:
    labels: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            key_str = str(key)
            if key_str == "raw_xml":
                continue
            if key_str in _LABWARE_LABEL_VALUE_KEYS and isinstance(item, str):
                labels.append(item)
            labels.extend(_iter_ir_labware_label_values(item, key_str))
    elif isinstance(value, list):
        for item in value:
            labels.extend(_iter_ir_labware_label_values(item, parent_key))
    return labels


def _rewrite_ir_labware_labels(value: Any, rewrites: Mapping[str, str], parent_key: str | None = None) -> None:
    if isinstance(value, dict):
        id_old_label = ""
        id_new_label = ""
        for key, item in list(value.items()):
            key_str = str(key)
            if key_str == "raw_xml":
                continue
            if key_str in _LABWARE_LABEL_VALUE_KEYS and isinstance(item, str) and item in rewrites:
                if key_str == "label":
                    id_old_label = item
                    id_new_label = rewrites[item]
                value[key] = rewrites[item]
            else:
                _rewrite_ir_labware_labels(item, rewrites, key_str)
        if id_old_label and value.get("id") == _safe_id(id_old_label):
            value["id"] = _safe_id(id_new_label)
    elif isinstance(value, list):
        for item in value:
            _rewrite_ir_labware_labels(item, rewrites, parent_key)


def _norm_labware_label(value: Any) -> str:
    text = re.sub(r"\s+", "", str(value or "")).casefold()
    # Treat FilterDWP[001] / FilterDWP[platecount] as the same family label.
    return re.sub(r"\[[^\]]*\]$", "", text)


def _attach_source_move_patterns(
    ir: dict[str, Any],
    selected_source_records: list[dict[str, Any]],
    *,
    context: ProjectLike | None = None,
) -> list[dict[str, Any]]:
    """Back verification moves with matching commands from ungenerated XSCRs."""
    source_moves: list[dict[str, Any]] = []
    for record in filter_generation_source_script_records(selected_source_records, ir):
        path = resolve_recorded_script_path(record, context=context)
        if path is None:
            continue
        try:
            source_ir = protocol_ir_from_path(path)
        except (OSError, ValueError):
            continue
        for source_step in source_ir.get("steps") or []:
            if not isinstance(source_step, dict) or source_step.get("operation") != "move_plate":
                continue
            params = source_step.get("parameters")
            if not isinstance(params, dict):
                continue
            source_moves.append(
                {
                    "params": params,
                    "path": str(path.resolve()),
                    "compiled_path": str(source_step.get("compiled_path") or ""),
                }
            )

    attached: list[dict[str, Any]] = []
    for step in ir.get("steps") or []:
        if not isinstance(step, dict) or step.get("operation") != "move_plate":
            continue
        params = step.get("parameters")
        if not isinstance(params, dict) or params.get("source_pattern_id"):
            continue
        match = next(
            (
                candidate
                for candidate in source_moves
                if _move_parameters_match(params, candidate["params"])
            ),
            None,
        )
        if match is None:
            continue
        source_params = match["params"]
        for key in (
            "labware",
            "onto_labware",
            "destination_location",
            "destination_site",
            "fixed_site",
            "move_to_base",
            "raw_xml",
        ):
            if key in source_params:
                params[key] = source_params[key]
        params.update(
            {
                "source_pattern_id": f"{Path(match['path']).stem}:{match['compiled_path']}",
                "source_pattern_type": "move_plate",
                "source_pattern_path": match["path"],
                "source_pattern_compiled_path": match["compiled_path"],
            }
        )
        attached.append(
            {
                "step_id": step.get("id"),
                "labware": params.get("labware"),
                "onto_labware": params.get("onto_labware"),
                "destination_location": params.get("destination_location"),
                "destination_site": params.get("destination_site"),
                "source_path": match["path"],
                "source_command": match["compiled_path"],
            }
        )
    if attached:
        ir.setdefault("source", {})["inherited_move_patterns"] = attached
    return attached


def _move_parameters_match(requested: Mapping[str, Any], source: Mapping[str, Any]) -> bool:
    def _norm(value: Any) -> str:
        return _norm_labware_label(value)

    if _norm(requested.get("labware")) != _norm(source.get("labware")):
        return False
    requested_onto = _norm(requested.get("onto_labware") or requested.get("onto"))
    source_onto = _norm(source.get("onto_labware") or source.get("onto"))
    if requested_onto or source_onto:
        return bool(requested_onto and requested_onto == source_onto)
    requested_location = re.sub(
        r"\s+", "", str(requested.get("destination_location") or requested.get("to_location") or "")
    ).casefold()
    source_location = re.sub(
        r"\s+", "", str(source.get("destination_location") or source.get("to_location") or "")
    ).casefold()
    requested_site = re.sub(
        r"\s+", "", str(requested.get("destination_site") or requested.get("to_site") or "")
    ).casefold()
    source_site = re.sub(
        r"\s+", "", str(source.get("destination_site") or source.get("to_site") or "")
    ).casefold()
    return bool(
        requested_location
        and requested_location == source_location
        and (not requested_site or requested_site == source_site)
    )


def _recipe_set_variable_payload(data: dict[str, Any]) -> dict[str, Any]:
    payload = data.get("set_variable")
    return payload if isinstance(payload, dict) else data


def _recipe_source_script_name(
    recipe: Mapping[str, Any] | None,
    *,
    request_spec: Mapping[str, Any] | None = None,
) -> str | None:
    if isinstance(recipe, Mapping):
        for key in ("source_script", "source_script_name", "script_name"):
            value = str(recipe.get(key) or "").strip()
            if value:
                return value
    if isinstance(request_spec, Mapping):
        source = request_spec.get("source")
        if isinstance(source, Mapping):
            for item in source.get("source_scripts") or []:
                value = str(item or "").strip()
                if value:
                    return value
    return None


def _normalize_toggle_simulation_values(
    values: list[Any],
    category_conditions: Mapping[str, Mapping[str, Any]],
) -> list[Any]:
    toggle_defaults = {
        str(condition.get("variable") or "").strip(): str(condition.get("value") or "yes").strip() or "yes"
        for condition in category_conditions.values()
        if isinstance(condition, Mapping) and str(condition.get("variable") or "").strip()
    }
    normalized: list[Any] = []
    for item in values:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        name = str(item.get("name") or "").strip()
        if name not in toggle_defaults:
            normalized.append(item)
            continue
        row = dict(item)
        value = row.get("value")
        if isinstance(value, bool):
            row["value"] = toggle_defaults[name] if value else "no"
        elif isinstance(value, (int, float)) and value in (0, 1):
            row["value"] = toggle_defaults[name] if value == 1 else "no"
        normalized.append(row)
    return normalized


def _source_startup_sequences(
    recipe: Mapping[str, Any],
    context: ProjectLike | None,
    source_script_name: str | None,
) -> list[dict[str, Any]]:
    specs = _source_startup_specs(recipe)
    if not specs and _recipe_mentions_token(recipe, "tubeeye"):
        specs = [
            {
                "source_script": source_script_name,
                "source_group": "Initialize TubeEye software",
                "target_group_contains": "tubeeye",
                "trigger_prompt_contains": "launching",
                "auto_detect": "tubeeye",
            }
        ]
    sequences: list[dict[str, Any]] = []
    for spec in specs:
        source_group = str(spec.get("source_group") or spec.get("group") or "").strip()
        auto_detect = str(spec.get("auto_detect") or spec.get("_auto_detect") or "").strip().casefold()
        if not source_group and auto_detect != "tubeeye":
            continue
        requested_source = str(spec.get("source_script") or source_script_name or "").strip()
        if not requested_source:
            continue
        startup_context = _source_startup_context(spec, context)
        if startup_context is None:
            continue
        try:
            source_path = resolve_context_script(startup_context, requested_source)
        except Exception:
            continue
        if not source_path.exists():
            continue
        detection_method = "source_group"
        steps = _source_group_direct_command_steps(source_path, source_group) if source_group else []
        if not steps and auto_detect == "tubeeye":
            detected = _source_tubeeye_startup_command_steps(source_path)
            if detected is not None:
                source_group = str(detected.get("source_group") or source_group or "").strip()
                steps = [dict(step) for step in detected.get("steps") or [] if isinstance(step, Mapping)]
                detection_method = str(detected.get("detection_method") or "tubeeye_executable_scan")
        if not steps:
            continue
        variable_refs = _source_startup_variable_refs(steps)
        variables = _source_variables_for_refs(source_path, variable_refs)
        sequences.append(
            {
                "source_script": requested_source,
                "source_context": startup_context.name,
                "source_path": str(source_path),
                "source_group": source_group,
                "target_group": str(spec.get("target_group") or "").strip(),
                "target_group_contains": str(spec.get("target_group_contains") or "").strip(),
                "trigger_prompt_contains": str(spec.get("trigger_prompt_contains") or "").strip(),
                "detection_method": detection_method,
                "steps": steps,
                "variables": variables,
            }
        )
    return sequences


def _source_startup_context(
    spec: Mapping[str, Any],
    default_context: ProjectLike | None,
) -> ProjectLike | None:
    raw_name = str(
        spec.get("source_context")
        or spec.get("context")
        or spec.get("project_context")
        or ""
    ).strip()
    if not raw_name:
        return default_context
    if default_context is not None and raw_name.casefold() == str(default_context.name).casefold():
        return default_context
    try:
        return load_project(raw_name)
    except Exception:
        return None


def _source_startup_specs(recipe: Mapping[str, Any]) -> list[dict[str, Any]]:
    for key in ("source_startup_groups", "source_startup_sequences", "source_startup_commands"):
        raw = recipe.get(key) if isinstance(recipe, Mapping) else None
        if isinstance(raw, list):
            return [dict(item) for item in raw if isinstance(item, Mapping)]
        if isinstance(raw, Mapping):
            return [dict(raw)]
    return []


def _recipe_mentions_token(recipe: Mapping[str, Any], token: str) -> bool:
    return token.casefold() in json.dumps(recipe, sort_keys=True, default=str).casefold()


def _source_group_direct_command_steps(source_path: Path, source_group: str) -> list[dict[str, Any]]:
    from . import xml_compat as ET

    try:
        root = ET.parse(source_path).getroot()
    except Exception:
        return []
    steps: list[dict[str, Any]] = []
    for group_object in _xml_iter_local(root, "Object"):
        object_type = str(group_object.attrib.get("Type") or "")
        if not object_type.endswith("ScriptGroupDataV1"):
            continue
        group_data = _xml_direct_child(group_object, "ScriptGroupDataV1")
        group_name = _xml_direct_text(group_data, "Name") if group_data is not None else ""
        if group_name.casefold() != source_group.casefold():
            continue
        statements = _xml_first_descendant(group_object, "Statements")
        for command_object in _xml_direct_children(statements, "Object"):
            step = _source_startup_step_from_object(command_object, source_path=source_path, source_group=group_name)
            if step is not None:
                steps.append(step)
        break
    return steps


def _source_tubeeye_startup_command_steps(source_path: Path) -> dict[str, Any] | None:
    from . import xml_compat as ET

    try:
        root = ET.parse(source_path).getroot()
    except Exception:
        return None
    best: dict[str, Any] | None = None
    best_score = -1
    for group_object in _xml_iter_local(root, "Object"):
        object_type = str(group_object.attrib.get("Type") or "")
        if not object_type.endswith("ScriptGroupDataV1"):
            continue
        group_data = _xml_direct_child(group_object, "ScriptGroupDataV1")
        group_name = _xml_direct_text(group_data, "Name") if group_data is not None else ""
        group_xml = ET.tostring(group_object, encoding="unicode")
        group_key = f"{group_name}\n{group_xml}".casefold()
        if "teyeclient.exe" not in group_key:
            continue
        if not any(token in group_key for token in ("tubeeye", "barcode", "scan")):
            continue
        statements = _xml_first_descendant(group_object, "Statements")
        command_objects = _tubeeye_startup_command_objects(statements)
        if not command_objects:
            continue
        steps = [
            step
            for command_object in command_objects
            if (step := _source_startup_step_from_object(command_object, source_path=source_path, source_group=group_name))
            is not None
        ]
        if not any(step.get("operation") == "execute_application" for step in steps):
            continue
        score = _tubeeye_startup_candidate_score(group_name, steps)
        if score > best_score:
            best_score = score
            best = {
                "source_group": group_name,
                "detection_method": "tubeeye_executable_scan",
                "steps": steps,
            }
    return best


def _tubeeye_startup_command_objects(statements: Any) -> list[Any]:
    selected: list[Any] = []
    pending_comment: Any | None = None
    saw_tubeeye_launch = False
    for command_object in _xml_nested_command_objects(statements):
        command_id = _xml_command_id(command_object)
        if command_id == "CommentStatement":
            payload = _xml_direct_child(command_object, command_id)
            comment = _xml_direct_text(payload, "Comment").casefold()
            if "tubeeye" in comment and any(token in comment for token in ("initialize", "init", "launch", "start")):
                pending_comment = command_object
            continue
        if command_id == "ExecuteApplicationStatement":
            payload = _xml_direct_child(command_object, command_id)
            application = _xml_direct_text(payload, "Application").casefold()
            if "teyeclient.exe" not in application:
                continue
            if not selected and pending_comment is not None:
                selected.append(pending_comment)
            selected.append(command_object)
            saw_tubeeye_launch = True
            continue
        if command_id == "ConditionalGroup" and saw_tubeeye_launch and _is_tubeeye_startup_error_conditional(command_object):
            selected.append(command_object)
            break
    return selected


def _xml_nested_command_objects(parent: Any):
    for command_object in _xml_direct_children(parent, "Object"):
        yield command_object
        command_id = _xml_command_id(command_object)
        payload = _xml_direct_child(command_object, command_id)
        if payload is None:
            continue
        for container_name in ("Statements", "Objects"):
            for container in _xml_direct_children(payload, container_name):
                yield from _xml_nested_command_objects(container)


def _is_tubeeye_startup_error_conditional(command_object: Any) -> bool:
    from . import xml_compat as ET

    payload = _xml_direct_child(command_object, "ConditionalGroup")
    condition = _normalize_fluent_condition_expression(_xml_direct_text(payload, "Condition")).casefold()
    raw_xml = ET.tostring(command_object, encoding="unicode").casefold()
    if "raiseerrorstatement" not in raw_xml:
        return False
    if "subroutinestatement" in raw_xml or "executevbscriptstatement" in raw_xml:
        return False
    if any(token in raw_xml for token in ("could not start", "failed", "startup", "initialize", "tubeeye", "teye")):
        return True
    return bool(condition and any(token in condition for token in ("res", "simulation", "teye_status")))


def _tubeeye_startup_candidate_score(group_name: str, steps: list[dict[str, Any]]) -> int:
    score = 0
    for step in steps:
        operation = step.get("operation")
        if operation == "execute_application":
            score += 20
        elif operation == "conditional_branch":
            score += 8
        elif operation == "comment":
            score += 3
    group_key = group_name.casefold()
    if "initialize" in group_key:
        score += 5
    if "tubeeye" in group_key:
        score += 3
    if "barcode" in group_key or "scan" in group_key:
        score += 2
    return score


def _source_startup_step_from_object(command_object: Any, *, source_path: Path, source_group: str) -> dict[str, Any] | None:
    from . import xml_compat as ET

    command_id = _xml_command_id(command_object)
    raw_xml = ET.tostring(command_object, encoding="unicode")
    if command_id == "CommentStatement":
        payload = _xml_direct_child(command_object, command_id)
        return {
            "command_id": command_id,
            "name": "Comment",
            "operation": "comment",
            "parameters": {"comment": _xml_direct_text(payload, "Comment")},
            "safety_flags": [],
            "source_path": f"{source_path} -> {source_group} -> {command_id}",
        }
    if command_id == "ExecuteApplicationStatement":
        payload = _xml_direct_child(command_object, command_id)
        return {
            "command_id": command_id,
            "name": "Execute Application",
            "operation": "execute_application",
            "parameters": {
                "path": _xml_direct_text(payload, "Application").strip().strip('"'),
                "arguments": _xml_direct_text(payload, "Arguments"),
                "wait": _xml_bool_text(_xml_direct_text(payload, "Wait")),
                "store_return": _xml_bool_text(_xml_direct_text(payload, "StoreReturn")),
                "variable": _xml_direct_text(payload, "Variable"),
                "raw_xml": raw_xml,
            },
            "safety_flags": ["source_backed_external_application"],
            "source_path": f"{source_path} -> {source_group} -> {command_id}",
        }
    if command_id == "ConditionalGroup":
        payload = _xml_direct_child(command_object, command_id)
        condition = _normalize_fluent_condition_expression(_xml_direct_text(payload, "Condition"))
        raw_xml = _replace_condition_text_in_xml(raw_xml, condition)
        return {
            "command_id": command_id,
            "name": _xml_direct_text(payload, "Name") or "Conditional Branch",
            "operation": "conditional_branch",
            "parameters": {
                "condition": condition,
                "branch_name": _xml_direct_text(payload, "Name"),
                "raw_xml": raw_xml,
            },
            "safety_flags": ["source_backed_external_application"],
            "source_path": f"{source_path} -> {source_group} -> {command_id}",
        }
    return None


def _normalize_fluent_condition_expression(condition: Any) -> str:
    text = str(condition or "").strip()
    if not text:
        return text
    return re.sub(r"\s+&\s+", " AND ", text)


def _replace_condition_text_in_xml(raw_xml: str, condition: str) -> str:
    if "<Condition>" not in raw_xml:
        return raw_xml
    replacement = html.escape(condition, quote=False)
    return re.sub(
        r"(<Condition>)(.*?)(</Condition>)",
        lambda match: f"{match.group(1)}{replacement}{match.group(3)}",
        raw_xml,
        count=1,
        flags=re.DOTALL,
    )


def _source_startup_variable_refs(steps: list[dict[str, Any]]) -> set[str]:
    refs: set[str] = set()
    for step in steps:
        params = step.get("parameters") if isinstance(step.get("parameters"), dict) else {}
        _add_source_variable_ref(refs, params.get("variable"))
        refs.update(_source_condition_variable_refs(params.get("condition")))
    return refs


def _source_variables_for_refs(source_path: Path, refs: set[str]) -> list[dict[str, Any]]:
    if not refs:
        return []
    try:
        source_ir = protocol_ir_from_path(source_path)
    except Exception:
        source_ir = {}
    by_name = {
        str(record.get("name") or ""): record
        for record in source_ir.get("variables", [])
        if isinstance(record, dict) and str(record.get("name") or "") in refs
    }
    variables: list[dict[str, Any]] = []
    for name in sorted(refs):
        if name in by_name:
            variables.append(copy.deepcopy(by_name[name]))
        else:
            variables.append({"name": name, "value": 0, "source_path": str(source_path)})
    return variables


def _source_startup_already_injected(sequence: Mapping[str, Any], injected: list[dict[str, Any]]) -> bool:
    key = (
        sequence.get("source_path"),
        sequence.get("source_group"),
        sequence.get("target_group") or sequence.get("target_group_contains"),
    )
    return any(
        (
            item.get("source_path"),
            item.get("source_group"),
            item.get("target_group") or item.get("target_group_contains"),
        )
        == key
        for item in injected
    )


def _should_inject_source_startup_after(
    group_name: str,
    appended_steps: list[dict[str, Any]],
    sequence: Mapping[str, Any],
) -> bool:
    target_group = str(sequence.get("target_group") or "").strip()
    target_contains = str(sequence.get("target_group_contains") or "").strip()
    group_key = group_name.casefold()
    if target_group and group_key != target_group.casefold():
        return False
    if target_contains and target_contains.casefold() not in group_key:
        return False
    if not target_group and not target_contains:
        return False
    trigger = str(sequence.get("trigger_prompt_contains") or "").strip().casefold()
    for step in appended_steps:
        if step.get("operation") != "prompt_user":
            continue
        prompt = str((step.get("parameters") or {}).get("prompt") or "").casefold()
        if not trigger or trigger in prompt:
            return True
    return False


def _instantiate_source_startup_steps(
    sequence: Mapping[str, Any],
    group_name: str,
    next_step,
) -> list[dict[str, Any]]:
    instantiated: list[dict[str, Any]] = []
    for template in sequence.get("steps") or []:
        if not isinstance(template, dict):
            continue
        idx, sid = next_step()
        step = copy.deepcopy(template)
        step["group"] = group_name
        step["id"] = sid
        step["index"] = idx
        instantiated.append(step)
    return instantiated


def _dedupe_ir_variables(variables: list[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in variables:
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        result.append(record)
    return result


def _add_labware_location_requires_raw_xml(location: Any) -> bool:
    text = str(location or "").strip().casefold()
    return "coversite" in text


def _render_add_labware_raw_xml(params: Mapping[str, Any], *, line_number: int) -> str:
    from .api_v2.commands import AddLabware

    return AddLabware(
        labware_type=str(params.get("labware_type") or params.get("catalog") or ""),
        labware_label=str(params.get("label") or params.get("labware") or ""),
        location=str(params.get("location") or ""),
        site=params.get("position") or params.get("site") or 1,
        rotation=params.get("rotation") or 0,
        has_lid=bool(params.get("has_lid", False)),
        line_number=line_number,
    ).to_xml(validate=False)


def _xml_iter_local(root: Any, name: str):
    for element in root.iter():
        if _xml_local_name(element.tag) == name:
            yield element


def _xml_direct_children(parent: Any, name: str) -> list[Any]:
    if parent is None:
        return []
    return [child for child in list(parent) if _xml_local_name(child.tag) == name]


def _xml_direct_child(parent: Any, name: str) -> Any | None:
    for child in _xml_direct_children(parent, name):
        return child
    return None


def _xml_first_descendant(parent: Any, name: str) -> Any | None:
    if parent is None:
        return None
    for child in parent.iter():
        if _xml_local_name(child.tag) == name:
            return child
    return None


def _xml_direct_text(parent: Any, name: str) -> str:
    child = _xml_direct_child(parent, name)
    return str(child.text or "") if child is not None else ""


def _xml_command_id(command_object: Any) -> str:
    for child in list(command_object):
        return _xml_local_name(child.tag)
    raw_type = str(command_object.attrib.get("Type") or "")
    return raw_type.rsplit(".", 1)[-1]


def _xml_local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _xml_bool_text(value: Any) -> bool:
    return str(value or "").strip().casefold() == "true"


def _source_condition_variable_refs(value: Any) -> set[str]:
    expression = re.sub(r'"[^"]*"|\'[^\']*\'', " ", str(value or ""))
    reserved = {"and", "or", "not", "true", "false", "none"}
    return {
        name
        for name in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", expression)
        if name.casefold() not in reserved
    }


def _add_source_variable_ref(refs: set[str], value: Any) -> None:
    text = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        refs.add(text)


def _seconds_to_iso_duration(seconds: int) -> str:
    value = max(int(seconds), 1)
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        parts = [f"{hours}H"]
        if minutes:
            parts.append(f"{minutes}M")
        if secs:
            parts.append(f"{secs}S")
        return "PT" + "".join(parts)
    if minutes:
        return f"PT{minutes}M{secs}S" if secs else f"PT{minutes}M"
    return f"PT{secs}S"


def _legacy_driver_macro_execution_time(*, macro_name: str, execution_settings: str) -> str:
    if macro_name.endswith("_WaitFinished"):
        settings = str(execution_settings or "").strip().strip("~")
        if settings.isdigit():
            return _seconds_to_iso_duration(int(settings))
        return "PT1M40S"
    return "PT2S"


def _legacy_driver_macro_raw_xml(
    *,
    macro_name: str,
    module_name: str,
    execution_settings: str,
) -> str:
    execution_time = _legacy_driver_macro_execution_time(
        macro_name=macro_name,
        execution_settings=execution_settings,
    )
    return (
        '<Object Type="Tecan.VisionX.ApplicationDriver.LegacyDriverMacro">\n'
        f'  <LegacyDriverMacro Version="1" Name="{macro_name}" ModuleName="{module_name}" '
        f'ExecutionTime="{execution_time}" IsBreakpoint="false" IsDisabledForExecution="false" '
        'LineNumber="0">\n'
        f"    <ExecutionSettings>{execution_settings}</ExecutionSettings>\n"
        "  </LegacyDriverMacro>\n"
        "</Object>"
    )


def _recipe_step_to_ir(
    raw_step: Any,
    group_name: str,
    next_step,
    subroutine_names: list[str],
    *,
    labware_entries: list[dict[str, Any]] | None = None,
    worktable_patterns: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | list[dict[str, Any]] | None:
    if not isinstance(raw_step, dict):
        return None
    step_type = recipe_step_type(raw_step)
    if step_type is None:
        return None
    data = raw_step

    idx, sid = next_step()
    base: dict[str, Any] = {"group": group_name, "id": sid, "index": idx, "safety_flags": []}

    if step_type == "comment":
        text = str(data.get("comment") or data.get("text") or "")
        base.update(
            {
                "command_id": "CommentStatement",
                "name": "Comment",
                "operation": "comment",
                "parameters": {"comment": text},
            }
        )
        return base
    if step_type == "prompt":
        text = normalize_operator_prompt_text(str(data.get("prompt") or data.get("text") or ""))
        params: dict[str, Any] = {"prompt": text, "timeout": 0}
        if data.get("plain_prompt"):
            params["plain_prompt"] = True
        elif data.get("instrument_init_check") or prompt_looks_like_external_initialization_check(text):
            params["instrument_init_check"] = True
        elif data.get("deck_presence_check"):
            params["deck_presence_check"] = True
            binding = resolve_recipe_worktable_binding(
                data,
                labware_entries=labware_entries,
                worktable_patterns=worktable_patterns,
            )
            if binding:
                params["worktable_labware"] = binding
                labware_name = str(binding.get("labware") or "").strip()
                if labware_name:
                    base["target_labware"] = labware_name
        for media_key in ("media_kind", "media_kinds"):
            if media_key in data:
                params[media_key] = data.get(media_key)
        if data.get("sound_file"):
            params["sound_file"] = str(data.get("sound_file"))
        base.update(
            {
                "command_id": "UserPromptStatement",
                "name": "Prompt User",
                "operation": "prompt_user",
                "parameters": params,
            }
        )
        return base
    if step_type == "query_variable":
        payload = data.get("query_variable") if "query_variable" in data else data.get("query")
        if isinstance(payload, dict):
            merged = dict(payload)
            merged.update({key: value for key, value in data.items() if key not in {"type", "query_variable", "query"}})
            data = merged
        variable = str(data.get("variable") or "").strip()
        prompt = normalize_operator_prompt_text(
            str(data.get("prompt") or data.get("query_prompt") or data.get("text") or "")
        )
        if not variable or not prompt:
            return None
        params: dict[str, Any] = {"variable": variable, "prompt": prompt, "timeout": int(data.get("timeout") or 0)}
        for key in ("minimum", "maximum"):
            if key in data and data.get(key) not in (None, ""):
                params[key] = data.get(key)
        base.update(
            {
                "command_id": "QueryVariableStatement",
                "name": "Query Variable",
                "operation": "query_variable",
                "parameters": params,
            }
        )
        return base
    if step_type == "runtime_variable_prompt":
        payload = data.get("runtime_variable_prompt")
        if isinstance(payload, dict):
            merged = dict(payload)
            merged.update({key: value for key, value in data.items() if key not in {"type", "runtime_variable_prompt"}})
            data = merged
        variables = data.get("variables")
        if not isinstance(variables, list) or not variables:
            return None
        params = {
            "screen_title": str(data.get("screen_title") or data.get("title") or "User Input"),
            "display_and_wait": bool(data.get("display_and_wait", True)),
            "timeout": int(data.get("timeout") or 0),
            "variables": variables,
        }
        raw_xml = str(data.get("raw_xml") or "").strip()
        if raw_xml:
            params["raw_xml"] = raw_xml
        base.update(
            {
                "command_id": "RUPVariableStatement",
                "name": "Runtime Variable Prompt",
                "operation": "runtime_variable_prompt",
                "parameters": params,
            }
        )
        return base
    if step_type == "execute_application":
        payload = data.get("execute_application") if isinstance(data.get("execute_application"), dict) else None
        source = payload or data
        app_path = str(
            source.get("path")
            or source.get("application")
            or source.get("file")
            or source.get("command")
            or (data.get("execute_application") if isinstance(data.get("execute_application"), str) else "")
            or ""
        ).strip()
        if not app_path:
            return None
        params = {
            "path": app_path,
            "arguments": str(source.get("arguments") or source.get("args") or ""),
            "wait": bool(source.get("wait", True)),
            "store_return": bool(source.get("store_return", False)),
            "variable": str(source.get("variable") or ""),
        }
        raw_xml = str(source.get("raw_xml") or "").strip()
        if raw_xml:
            params["raw_xml"] = raw_xml
        base.update(
            {
                "command_id": "ExecuteApplicationStatement",
                "name": "Execute Application",
                "operation": "execute_application",
                "parameters": params,
            }
        )
        return base
    if step_type == "execute_vb_script":
        payload = data.get("execute_vb_script") if isinstance(data.get("execute_vb_script"), dict) else None
        source = payload or data
        vb_path = str(
            source.get("vb_script")
            or source.get("path")
            or source.get("script")
            or (payload if isinstance(payload, str) else "")
            or (data.get("execute_vb_script") if isinstance(data.get("execute_vb_script"), str) else "")
            or ""
        ).strip()
        if not vb_path:
            return None
        params = {
            "vb_script": vb_path,
            "execution_mode": str(source.get("execution_mode") or source.get("mode") or "Synchronous"),
        }
        base.update(
            {
                "command_id": "ExecuteVbScriptStatement",
                "name": "Execute VBScript",
                "operation": "execute_vb_script",
                "parameters": params,
            }
        )
        return base
    if step_type == "subroutine":
        sub = data.get("subroutine") if "subroutine" in data else data.get("name")
        if isinstance(sub, dict):
            mode = str(sub.get("execution_mode") or "JoinSubroutine")
        else:
            mode = str(data.get("execution_mode") or "JoinSubroutine")
        sub_name = recipe_subroutine_name(data)
        if not sub_name:
            return None
        mappings_start = data.get("variable_mappings_start")
        mappings_end = data.get("variable_mappings_end")
        if isinstance(sub, dict):
            mappings_start = sub.get("variable_mappings_start", mappings_start)
            mappings_end = sub.get("variable_mappings_end", mappings_end)
        params = {"subroutine": sub_name, "execution_mode": mode}
        if isinstance(mappings_start, list):
            params["variable_mappings_start"] = mappings_start
        if isinstance(mappings_end, list):
            params["variable_mappings_end"] = mappings_end
        inline_requested = bool(
            data.get("inline")
            or data.get("inline_local")
            or data.get("force_inline")
            or (isinstance(sub, dict) and (sub.get("inline") or sub.get("inline_local") or sub.get("force_inline")))
        )
        if inline_requested:
            params["inline_local"] = True
        subroutine_names.append(sub_name)
        base.update(
            {
                "command_id": "SubRoutineStatement",
                "name": "Call Subroutine",
                "operation": "call_subroutine",
                "parameters": params,
            }
        )
        return base
    if step_type == "set_variable":
        payload = _recipe_set_variable_payload(data)
        variable = str(payload.get("variable") or "").strip()
        if not variable:
            return None
        base.update(
            {
                "command_id": "SetVariableStatement",
                "name": "Set Variable",
                "operation": "set_variable",
                "parameters": {
                    "variable": variable,
                    "value": payload.get("value"),
                },
            }
        )
        return base
    if step_type == "move":
        move = data.get("verified_move") or data.get("move") or data.get("manual_move") or data
        if not isinstance(move, dict):
            move = data
        labware = str(move.get("labware") or "")
        onto = str(move.get("onto") or move.get("onto_labware") or "")
        to_location = move.get("to_location") or move.get("destination_location")
        to_site = move.get("to_site") or move.get("destination_site")
        allow_automated = (
            "verified_move" in data
            or bool(data.get("allow_automated_verification_motion"))
            or bool(move.get("allow_automated_verification_motion"))
            or bool(move.get("automated"))
        )
        params: dict[str, Any] = {"labware": labware, "force_manual_verification": not allow_automated}
        if allow_automated:
            params["allow_automated_verification_motion"] = True
            params["verification_after_motion_required"] = True
            if move.get("move_to_base"):
                params["move_to_base"] = True
        if onto:
            params["onto_labware"] = onto
        if to_location is not None:
            params["destination_location"] = to_location
        if to_site is not None:
            params["destination_site"] = to_site
        base.update(
            {
                "command_id": "ApplicationDriverMacro",
                "name": "Move Plate",
                "operation": "move_plate",
                "parameters": params,
                "safety_flags": ["automated_verification_motion"] if allow_automated else ["manual_verification"],
                "target_labware": labware,
            }
        )
        return base
    if step_type == "liha_dispense":
        payload = data.get("liha_dispense") if isinstance(data.get("liha_dispense"), dict) else {}
        if not payload:
            payload = {
                key: value
                for key, value in data.items()
                if key not in {"type", "liha_dispense"} and value is not None
            }
        labware = str(payload.get("labware") or "").strip()
        if not labware:
            return None
        volume_ul = payload.get("volume_ul")
        if volume_ul is None or str(volume_ul).strip() == "":
            return None
        # Liquid class must come from recipe / ZEIA — never invent AcidExtract (or any LC).
        liquid_class = str(payload.get("liquid_class") or "").strip()
        if not liquid_class:
            return None
        well = str(payload.get("well") or "").strip()
        if not well:
            # Fail closed — do not invent well A1.
            return None
        params = {
            "labware": labware,
            "volume_ul": volume_ul,
            "liquid_class": liquid_class,
            "well": well,
            "device_alias": str(payload.get("device_alias") or "").strip(),
        }
        if not params["device_alias"]:
            # Fail closed — do not invent Instrument=1/Device=LIHA:1.
            return None
        base.update(
            {
                "command_id": "LihaDispenseScriptCommandDataV6",
                "name": "Dispense",
                "operation": "liha_dispense",
                "parameters": params,
                "target_labware": labware,
                "volume_ul": volume_ul,
                "liquid_class": liquid_class,
            }
        )
        return base
    if step_type == "a200_dispense":
        payload = data.get("a200_dispense") if isinstance(data.get("a200_dispense"), dict) else {}
        if not payload:
            payload = {
                key: value
                for key, value in data.items()
                if key not in {"type", "a200_dispense"} and value is not None
            }
        volume_ul = payload.get("volume_ul")
        if volume_ul is None or str(volume_ul).strip() == "":
            return None
        # Driver macros/module must come from recipe / mined ZEIA — never invent ResolvexA200_*.
        macro_name = str(
            payload.get("macro_name") or payload.get("run_macro") or ""
        ).strip()
        module_name = str(payload.get("module_name") or "").strip()
        wait_macro = str(
            payload.get("wait_macro") or payload.get("wait_macro_name") or ""
        ).strip()
        if not macro_name or not module_name or not wait_macro:
            return None
        # execution_settings / wait / wells must come from recipe or mined ZEIA —
        # never invent SPE 4,~a200startwell~,~a200endwell~,0 or wait 300.
        execution_settings = str(payload.get("execution_settings") or "").strip()
        wait_timeout = str(
            payload.get("wait_timeout") or payload.get("wait_seconds") or ""
        ).strip()
        start_well = payload.get("start_well")
        end_well = payload.get("end_well")
        if not execution_settings or not wait_timeout:
            return None
        if start_well is None or str(start_well).strip() == "":
            return None
        if end_well is None or str(end_well).strip() == "":
            return None
        steps: list[dict[str, Any]] = []

        def _append_step(operation: str, *, command_id: str, name: str, parameters: dict[str, Any], **extra: Any) -> None:
            idx, step_id = next_step()
            step_doc: dict[str, Any] = {
                "group": group_name,
                "id": step_id,
                "index": idx,
                "safety_flags": extra.pop("safety_flags", []),
                "command_id": command_id,
                "name": name,
                "operation": operation,
                "parameters": parameters,
            }
            step_doc.update(extra)
            steps.append(step_doc)

        # VolTransferMax is a structural Fluent script variable consumed by mined
        # A200/driver macros. Emit it only when the full macro path
        # (macro_name/module_name/wait_macro + settings/wells) is already supplied
        # above — not a product invent of Resolvex/SPE semantics.
        _append_step(
            "set_variable",
            command_id="SetVariableStatement",
            name="Set Variable",
            parameters={"variable": "VolTransferMax", "value": volume_ul},
        )
        for variable, value in (
            ("a200startwell", start_well),
            ("a200endwell", end_well),
        ):
            _append_step(
                "set_variable",
                command_id="SetVariableStatement",
                name="Set Variable",
                parameters={"variable": variable, "value": value},
            )
        run_xml = _legacy_driver_macro_raw_xml(
            macro_name=macro_name,
            module_name=module_name,
            execution_settings=execution_settings,
        )
        _append_step(
            "application_driver_macro",
            command_id="LegacyDriverMacro",
            name=f"{module_name} Run",
            parameters={
                "macro_name": macro_name,
                "module_name": module_name,
                "execution_settings": execution_settings,
                "raw_xml": run_xml,
            },
            safety_flags=["automated_verification_motion"],
        )
        wait_xml = _legacy_driver_macro_raw_xml(
            macro_name=wait_macro,
            module_name=module_name,
            execution_settings=wait_timeout,
        )
        _append_step(
            "application_driver_macro",
            command_id="LegacyDriverMacro",
            name=f"{module_name} Wait Finished",
            parameters={
                "macro_name": wait_macro,
                "module_name": module_name,
                "execution_settings": wait_timeout,
                "raw_xml": wait_xml,
            },
            safety_flags=["automated_verification_motion"],
        )
        return steps or None
    return None
