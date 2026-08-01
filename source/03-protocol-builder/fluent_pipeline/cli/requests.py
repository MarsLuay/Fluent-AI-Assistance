"""Request builders for thin CLI adapters."""

from __future__ import annotations

import argparse
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from ..application_services import (
    BundleVerificationRequest,
    LogAnalysisRequest,
    ProjectImportRequest,
    ProjectInspectionRequest,
    RepairApplyRequest,
    RepairPlanRequest,
    RequestSpecCreateRequest,
    RequestSpecValidationRequest,
)
from ..config import READY_TO_IMPORT_DIR, TEMP_FILES_DIRNAME, resolve_user_path
from ..generation_options import GenerationOptions, generation_options_from_cli_args
from ..generation_workflow import GenerationRequest
from ..project_context import (
    ProjectLike,
    create_project_collection,
    load_project,
    load_project_collection,
    resolve_context_path,
    resolve_context_script,
)
from ..request_factory import build_generation_request, build_request_spec_create_request
from ..request_spec import load_request_spec, request_spec_generation_defaults
from ..request_spec_resolver import resolve_request_spec_path
from ..runner import PipelineError
from ..application_services import import_project as import_project_service
from ..application_services import ProjectImportRequest as ImportedProjectRequest
from .runtime import (
    _command_context,
    _default_generation_dir,
    _resolve_artifact_output_path,
    _safe_output_label,
    cli_module,
)


def generation_request_from_cli(args: argparse.Namespace) -> GenerationRequest:
    request_spec = None
    request_spec_source = None
    merged_args = args
    if args.spec:
        request_spec_source, _resolution = resolve_request_spec_path(
            args.spec,
            protocol_name=getattr(args, "protocol_name", None),
            context_name=getattr(args, "name", None),
            pin=bool(getattr(args, "pin_spec", False)),
        )
        request_spec = load_request_spec(request_spec_source)
        merged_args = merge_generate_spec_args(args, request_spec)
    if not merged_args.intent:
        raise PipelineError("generate requires an intent or --spec request.spec.yaml")
    cli = cli_module()
    ctx, archive = cli._generation_context_from_args(merged_args)
    ir_source = cli._resolve_ir_source(ctx, merged_args.ir) if merged_args.ir else None
    index_db = resolve_user_path(merged_args.index_db) if merged_args.index_db else None
    out_dir = (
        _resolve_artifact_output_path(merged_args.out_dir, ctx=ctx)
        if merged_args.out_dir
        else _default_generation_dir(ctx, merged_args.protocol_name or merged_args.intent)
    )
    generation_options = dataclass_replace(
        generation_options_from_generate_args(merged_args, request_spec),
        project_archive=archive,
        project_name=merged_args.name,
        force_import=merged_args.force_import,
        pattern_refs=tuple(merged_args.pattern or []),
        index_db=index_db,
        pattern_ids=tuple(merged_args.pattern_id or []),
        pattern_queries=tuple(merged_args.pattern_query or []),
        source_script_rank=merged_args.source_script_rank,
        protocol_name=merged_args.protocol_name,
        subroutine_dirs=tuple(resolve_user_path(path) for path in getattr(merged_args, "subroutine_dir", [])),
        record_snapshots=getattr(merged_args, "record_snapshots", None),
        deterministic_compile=getattr(merged_args, "deterministic_compile", False),
        fluent_method=getattr(merged_args, "fluent_method", None),
        fluent_command=getattr(merged_args, "fluent_command", None),
        fluent_host=getattr(merged_args, "fluent_host", "127.0.0.1"),
        fluent_port=getattr(merged_args, "fluent_port", 50052),
        fluent_insecure=getattr(merged_args, "fluent_insecure", False),
    )
    return build_generation_request(
        intent=merged_args.intent,
        output_directory=out_dir,
        context_name=getattr(ctx, "name", None) if ctx else None,
        source_scripts=tuple(merged_args.source_script or []),
        protocol_ir=ir_source,
        options=generation_options,
        request_spec_path=request_spec_source,
    )


def project_import_request_from_cli(args: argparse.Namespace) -> ProjectImportRequest:
    return ProjectImportRequest(
        archive=resolve_user_path(args.archive),
        name=args.name,
        force=args.force,
        snapshot_archives=tuple(resolve_user_path(path) for path in args.snapshot),
        activate=args.activate,
    )


def project_inspection_request_from_cli(args: argparse.Namespace) -> ProjectInspectionRequest:
    return ProjectInspectionRequest(context_name=args.name)


def request_spec_create_request_from_cli(args: argparse.Namespace) -> RequestSpecCreateRequest:
    context_names = list(args.context or [])
    project_label = context_names[0] if len(context_names) == 1 else args.protocol_name or args.intent
    output_path = (
        _resolve_artifact_output_path(args.output)
        if args.output
        else (
            READY_TO_IMPORT_DIR
            / _safe_output_label(project_label)
            / TEMP_FILES_DIRNAME
            / "build"
            / "request.spec.yaml"
        ).resolve()
    )
    return build_request_spec_create_request(
        intent=args.intent,
        output_path=output_path,
        protocol_name=args.protocol_name,
        context=context_names[0] if len(context_names) == 1 else None,
        context_kind="project_collection" if len(context_names) > 1 or args.collection else None,
        contexts=tuple({"name": name} for name in context_names),
        project_archives=tuple(resolve_user_path(path) for path in (args.project_archive or [])),
        collection=args.collection,
        source_scripts=tuple(args.source_script or []),
        pattern_refs=tuple(args.pattern or []),
        index_db=resolve_user_path(args.index_db) if args.index_db else None,
        pattern_ids=tuple(args.pattern_id or []),
        pattern_queries=tuple(args.pattern_query or []),
        source_script_rank=args.source_script_rank,
        generation_options=generation_options_from_cli_args(args),
        fluent_method=args.fluent_method,
    )


def request_spec_validation_request_from_cli(args: argparse.Namespace) -> RequestSpecValidationRequest:
    return RequestSpecValidationRequest(spec_path=resolve_user_path(args.spec))


def repair_plan_request_from_cli(args: argparse.Namespace) -> RepairPlanRequest:
    ctx = _command_context(args.context)
    return RepairPlanRequest(
        draft_path=resolve_context_path(ctx, args.input),
        context_name=ctx.name if ctx else None,
        simulation_json_path=resolve_context_path(ctx, args.simulation_json) if args.simulation_json else None,
        report_path=resolve_context_path(ctx, args.report) if args.report else None,
    )


def repair_apply_request_from_cli(args: argparse.Namespace) -> RepairApplyRequest:
    ctx = _command_context(args.context)
    draft_path = resolve_context_path(ctx, args.input)
    return RepairApplyRequest(
        draft_path=draft_path,
        output_path=(
            resolve_context_path(ctx, args.output)
            if args.output
            else _default_output_for_repair(ctx, draft_path)
        ),
        context_name=ctx.name if ctx else None,
        simulation_json_path=resolve_context_path(ctx, args.simulation_json) if args.simulation_json else None,
        apply_modeling=args.apply_modeling,
        report_path=resolve_context_path(ctx, args.report) if args.report else None,
    )


def log_analysis_request_from_cli(args: argparse.Namespace) -> LogAnalysisRequest:
    return LogAnalysisRequest(
        log_path=resolve_user_path(args.log),
        audit_paths=tuple(resolve_user_path(path) for path in args.audit_log),
        report_path=resolve_user_path(args.report) if args.report else None,
        json_path=resolve_user_path(args.json_out) if args.json_out else None,
    )


def bundle_verification_request_from_cli(args: argparse.Namespace) -> BundleVerificationRequest:
    ctx = _command_context(getattr(args, "context", None))
    compiled_xscr = resolve_context_path(ctx, args.compiled_xscr)
    return BundleVerificationRequest(
        compiled_xscr=compiled_xscr,
        draft_path=resolve_context_path(ctx, args.draft_path) if args.draft_path else None,
        protocol_ir=resolve_context_path(ctx, args.protocol_ir) if args.protocol_ir else None,
        worklist=resolve_context_path(ctx, args.worklist) if args.worklist else None,
        source_projects=tuple(resolve_user_path(path) for path in (args.source_project or [])),
        source_scripts=tuple(resolve_context_script(ctx, path) for path in (args.source_script or [])),
        source_xscr=resolve_context_script(ctx, args.source_xscr) if args.source_xscr else None,
        source_manifest=ctx.manifest if ctx else None,
        recreate_guide=resolve_context_path(ctx, args.recreate_guide) if args.recreate_guide else None,
        report_path=(
            resolve_context_path(ctx, args.report)
            if args.report
            else compiled_xscr.with_suffix(".ready_validation.md")
        ),
        json_path=(
            resolve_context_path(ctx, args.json_out)
            if args.json_out
            else compiled_xscr.with_suffix(".ready_validation.json")
        ),
    )


def generation_options_from_generate_args(
    args: argparse.Namespace,
    request_spec: dict[str, Any] | None,
) -> GenerationOptions:
    defaults = None
    if request_spec is not None:
        defaults = request_spec_generation_defaults(request_spec).get("generation_options")
    return generation_options_from_cli_args(args, defaults=defaults)


def generation_context_from_args(args: argparse.Namespace) -> tuple[ProjectLike | None, Path | None]:
    archive_paths = [resolve_user_path(path) for path in (args.project_archive or [])]
    context_names = list(args.context or [])

    if args.collection:
        if archive_paths or context_names:
            raise PipelineError("--collection cannot be combined with --project-archive or --context")
        return load_project_collection(args.collection), None

    if len(archive_paths) == 1 and not context_names:
        imported = import_project_service(
            ImportedProjectRequest(
                archive=archive_paths[0],
                name=args.name,
                force=args.force_import,
            )
        )
        return imported.context, None

    if archive_paths:
        imported_contexts = []
        for archive in archive_paths:
            imported_contexts.append(
                import_project_service(
                    ImportedProjectRequest(
                        archive=archive,
                        force=args.force_import,
                    )
                ).context
            )
        context_names.extend(ctx.name for ctx in imported_contexts)

    if len(context_names) > 1:
        collection_name = args.name or _collection_name_from_parts(context_names)
        return create_project_collection(collection_name, context_names, force=args.force_import), None

    if len(context_names) == 1:
        return load_project(context_names[0]), None

    return _command_context(None), None


def merge_generate_spec_args(args: argparse.Namespace, request_spec: dict[str, Any]) -> argparse.Namespace:
    defaults = request_spec_generation_defaults(request_spec)
    merged = argparse.Namespace(**vars(args))
    if not merged.intent:
        merged.intent = defaults["intent"]
    if not merged.protocol_name:
        merged.protocol_name = defaults.get("protocol_name")
    if not merged.collection and defaults.get("collection"):
        merged.collection = defaults["collection"]
    if not merged.project_archive and not merged.collection:
        merged.project_archive = defaults["project_archives"]
    if not merged.context and not merged.collection and defaults.get("context"):
        merged.context = [defaults["context"]]
    if not merged.context and not merged.collection and defaults.get("contexts"):
        merged.context = defaults["contexts"]
    if not merged.source_script:
        merged.source_script = defaults["source_scripts"]
    if not merged.pattern:
        merged.pattern = defaults["pattern_refs"]
    if merged.index_db is None:
        merged.index_db = defaults["index_db"]
    if not merged.pattern_id:
        merged.pattern_id = defaults["pattern_ids"]
    if not merged.pattern_query:
        merged.pattern_query = defaults["pattern_queries"]
    if merged.source_script_rank == 1:
        merged.source_script_rank = defaults["source_script_rank"]
    default_options = defaults["generation_options"]
    merged_options = generation_options_from_cli_args(merged, defaults=default_options)
    merged.no_simulate = not merged_options.simulate
    merged.no_compile = not merged_options.compile_xscr
    merged.max_repair_iterations = merged_options.max_repair_iterations
    merged.strict_readiness = merged_options.strict_readiness
    merged.apply_modeling = merged_options.apply_modeling
    merged.target_fluentcontrol_version = merged_options.target_fluentcontrol_version
    merged.target_script_folder = merged_options.target_script_folder
    merged.approve_partial_zeia = merged_options.approve_partial_zeia
    merged.approve_deck_layout = merged_options.approve_deck_layout
    merged.approve_command_inventory = merged_options.approve_command_inventory
    merged.approve_unsupported_raw_xml = merged_options.approve_unsupported_raw_xml
    merged.approved_unsupported_command_ids = list(merged_options.approved_unsupported_command_ids)
    merged.waive_checksum_recompute = merged_options.waive_checksum_recompute
    merged.preserve_regeneration_baseline = (
        merged_options.preserve_regeneration_baseline
    )
    merged.fluent_context_check = merged_options.fluent_context_check
    merged.fluent_provider = merged_options.fluent_provider
    merged.fluent_timeout = merged_options.fluent_timeout
    if not merged.fluent_method and defaults.get("fluent_method"):
        merged.fluent_method = defaults["fluent_method"]
    return merged


def resolve_ir_source(ctx: ProjectLike | None, value: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        cwd_relative = (Path.cwd() / path).resolve()
        if cwd_relative.exists():
            return cwd_relative
    if ctx is not None and value.suffix.lower() == ".xscr":
        return resolve_context_script(ctx, value)
    return resolve_context_path(ctx, value)


def resolve_generation_event_log(args: argparse.Namespace, out_dir: Path) -> Path | None:
    if getattr(args, "no_event_log", False):
        return None
    if getattr(args, "event_log", None) is not None:
        return _resolve_artifact_output_path(args.event_log)
    return (out_dir / "logs" / "generation.events.jsonl").resolve()


def _collection_name_from_parts(parts: list[str]) -> str:
    label = "-and-".join(parts[:4])
    if len(parts) > 4:
        label = f"{label}-plus-{len(parts) - 4}"
    return _safe_output_label(label)[:80] or "project-collection"


def _default_output_for_repair(ctx: ProjectLike | None, draft_path: Path) -> Path:
    if ctx is None:
        return (
            READY_TO_IMPORT_DIR
            / "unscoped"
            / TEMP_FILES_DIRNAME
            / "drafts"
            / f"{draft_path.stem}_repaired.py"
        ).resolve()
    return (ctx.drafts_dir / f"{draft_path.stem}_repaired.py").resolve()
