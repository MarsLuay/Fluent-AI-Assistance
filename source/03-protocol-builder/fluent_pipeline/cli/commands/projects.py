"""CLI for the local Codex-friendly Fluent protocol builder."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ...aliases import alias_records, load_alias_maps, normalize_protocol_ir_aliases, resolve_alias
from ...bundle_lifecycle import (
    DEFAULT_PROBE_ROOT,
    archive_recommended_bundles,
    render_bundle_index,
    scan_bundle_lifecycle,
)
from ... import bootstrap
from ...config import (
    DEFAULT_FLUENTCODER_PYTHON,
    PROJECT_DIR,
    READY_TO_IMPORT_DIR,
    TECAN_AI_DIR,
    fluentcoder_python,
    fluentcoder_root,
    resolve_user_path,
    workflow_event_log_path,
)
from ...compatibility import (
    TargetSetup,
    build_compatibility_report,
    current_manual_target,
    render_compatibility_markdown,
)
from ...bundle_media import process_prompt_media_captures
from ...determinism import compare_run_dirs, render_determinism_report
from ...diagnostics import diagnose_input
from ...external_commands import (
    inspect_external_command,
    render_external_command_contract_markdown,
    write_external_command_contract,
)
from ...application_services import ProjectImportRequest, import_project as import_project_service
from ...exports import export_ready_to_import
from ...fluent_library import resolve_local_fluent_script, stage_local_fluent_script
from ...mcp_gateway import resolve_process_media_ir_path
from ...fluent_log_parser import (
    build_fluent_log_report,
    build_latest_fluent_log_report,
    render_fluent_log_report_markdown,
    report_to_json,
)
from ...generation_workflow import run_generation_workflow
from ...project_catalog import ensure_project_catalog
from ...protocol_ir import (
    CANONICAL_IR_BUNDLE_VERSION,
    CANONICAL_IR_VERSION,
    apply_touchtools_media_path_map_to_xscr,
    build_media_path_map,
    build_media_path_map_from_placeholder_rows,
    is_ir_bundle,
    load_ir_payload,
    load_protocol_ir,
    migrate_protocol_ir,
    migrate_protocol_ir_bundle,
    protocol_filename,
    protocol_ir_bundle_json_schema,
    protocol_ir_json_schema,
    protocol_ir_schema_markdown,
    protocol_ir_schema_versions,
    protocol_ir_from_path,
    render_gwl,
    render_media_path_map_markdown,
    render_python_draft,
    render_recreate_markdown,
    resolve_touchtools_media_subfolder,
    touchtools_media_subfolder,
    validate_protocol_ir,
    validate_protocol_ir_bundle,
    validate_protocol_ir_document,
    validate_protocol_ir_bundle_document,
    write_ir_payload,
    write_protocol_ir,
)
from ...request_spec import (
    build_request_spec,
    load_request_spec,
    request_spec_generation_defaults,
    write_request_spec,
)
from ...project_context import (
    ProjectContext,
    ProjectLike,
    active_project_name,
    clear_active_project,
    create_project_collection,
    is_context_archive,
    list_project_collections,
    list_projects,
    load_project,
    load_project_collection,
    resolve_context_path,
    resolve_context_script,
    set_active_project,
)
from ...reports import (
    compact_simulation,
    render_compile_markdown,
    render_doctor_markdown,
    render_roundtrip_markdown,
    render_simulation_markdown,
)
from ...repair import apply_repair_plan, build_repair_plan, render_repair_markdown
from ...runner import (
    PipelineError,
    ensure_parent,
    parse_json_stdout,
    run_fluentcoder,
    run_fluentcoder_with_log_watch,
    run_python,
    write_json,
)
from ..runtime import _command_context, _print_process
from ..rendering import normalize_progress_mode, progress_callback_from_mode
from ...runtime_bridge import (
    FluentContextCheckConfig,
    render_fluent_context_check_markdown,
    run_fluent_context_check,
)
from ...script_analysis import analyze_script, render_script_analysis_markdown
from ...spec_lint import lint_request_spec_file, render_lint_report
from ...template_library import list_templates, template_info
from ...worktable_diff import (
    diff_worktable_requirements,
    render_worktable_changes_markdown,
    render_worktable_patch_json,
)
ANALYSIS_REPORT_VERSION = "tecan.analysis_report.v1"



from ..runtime import cli_module

def _cmd_import_project(args: argparse.Namespace) -> int:
    cli = cli_module()
    request = cli.project_import_request_from_cli(args)
    result = cli.import_project(request)
    cli.print_project_import_result(result)
    return 0

def _cmd_list_projects(args: argparse.Namespace) -> int:
    projects = list_projects()
    if args.as_json:
        print(json.dumps(projects, indent=2, sort_keys=True))
        return 0
    active = active_project_name()
    if not projects:
        print("No project contexts imported yet.")
        return 0
    for project in projects:
        marker = "*" if project["name"] == active else " "
        print(
            f"{marker} {project['name']} "
            f"scripts={project['script_count']} "
            f"workspaces={project['workspace_count']} "
            f"objects={project['object_count']} "
            f"snapshots={project.get('snapshot_evidence_count', 0)}"
        )
    return 0

def _cmd_use_project(args: argparse.Namespace) -> int:
    ctx = set_active_project(args.name)
    print(f"Active project: {ctx.name}")
    print(f"  Root: {ctx.root}")
    return 0

def _cmd_current_project(args: argparse.Namespace) -> int:
    active = active_project_name()
    if not active:
        print("No active project context.")
        return 1
    ctx = load_project(active)
    print(f"Active project: {ctx.name}")
    print(f"  Root: {ctx.root}")
    return 0

def _cmd_clear_project(args: argparse.Namespace) -> int:
    clear_active_project()
    print("Active project cleared.")
    return 0

def _cmd_project_info(args: argparse.Namespace) -> int:
    cli = cli_module()
    request = cli.project_inspection_request_from_cli(args)
    result = cli.inspect_project(request)
    cli.print_project_inspection_result(result, as_json=args.as_json)
    return 0

def _cmd_project_find(args: argparse.Namespace) -> int:
    ctx = load_project(args.context)
    from fluent_pipeline.project_context import query_project

    payload = query_project(ctx, args.pattern, kind=args.kind, limit=args.limit)
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["match_count"] else 1
    matches = payload["matches"]
    if not matches:
        print(f"No project entries match {args.pattern!r}.")
        return 1
    for match in matches:
        name = match.get("object_name") or match.get("project_name") or Path(match.get("entry", "")).name
        entry = match.get("entry") or match.get("base_name") or ""
        print(f"  [{match.get('kind', 'object'):14s}] {name}  {entry}")
    print(f"\n{payload['match_count']} match(es).", end="")
    if payload.get("truncated"):
        print(f" truncated at limit={payload['limit']}.", end="")
    print()
    return 0

def _cmd_inspect_external_command(args: argparse.Namespace) -> int:
    ctx = load_project(args.context)
    report = inspect_external_command(
        ctx.manifest,
        context_root=ctx.root,
        command_name=args.command_name,
        module_name=args.module,
        source_script=args.source_script,
    )
    write_external_command_contract(
        report,
        json_path=args.json_out,
        markdown_path=args.markdown_out,
    )
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(render_external_command_contract_markdown(report), end="")
    if not report.get("match_count"):
        raise PipelineError(
            f"No existing source usage found for external command {args.command_name!r}. "
            "Do not generate it until a source script or vendor contract is provided."
        )
    return 0

def _cmd_script_report(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    if ctx is None:
        raise PipelineError("script-report requires --context or an active project")
    out_dir = resolve_context_path(ctx, args.out_dir) if args.out_dir else ctx.reports_dir / "script_analysis"
    report = analyze_script(
        ctx,
        script=args.script,
        script_index=args.script_index,
        out_dir=out_dir,
        max_commands=args.max_commands,
    )
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0
    artifacts = report.get("artifacts") or {}
    print(render_script_analysis_markdown(report).rstrip())
    if artifacts:
        print(f"\nScript analysis: {artifacts.get('script_analysis_markdown')}")
        print(f"Script analysis JSON: {artifacts.get('script_analysis_json')}")
        print(f"Recreate guide: {artifacts.get('recreate_markdown')}")
    return 0

def _cmd_create_collection(args: argparse.Namespace) -> int:
    progress_mode = normalize_progress_mode(getattr(args, "progress", "auto"))
    progress_callback = progress_callback_from_mode(progress_mode)
    if progress_mode in {"auto", "plain"}:
        print(f"Creating collection: {args.name}", file=sys.stderr, flush=True)
        print(file=sys.stderr, flush=True)
    started_at = time.monotonic()
    collection = create_project_collection(
        args.name,
        args.context,
        force=args.force,
        progress_callback=progress_callback,
    )
    elapsed_seconds = time.monotonic() - started_at
    manifest = collection.manifest
    print("Collection created successfully.")
    print(f"Name: {collection.name}")
    print(f"  Root:       {collection.root}")
    print(f"  Manifest:   {collection.root / 'manifest.json'}")
    print(f"  Report:     {collection.root / 'project_report.md'}")
    print(f"  Projects:   {len(manifest.get('source_projects', []))}")
    print(f"  Scripts:    {len(manifest.get('scripts', []))}")
    print(f"  Objects:    {len(manifest.get('objects', []))}")
    print(f"  Workspaces: {len(manifest.get('workspaces', []))}")
    print(f"  Snapshots:  {len(manifest.get('snapshot_evidence', []))}")
    print(f"  Elapsed:    {_format_collection_elapsed(elapsed_seconds)}")
    return 0


def _format_collection_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(round(seconds)))
    minutes, remaining_seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {remaining_seconds}s"
    if minutes:
        return f"{minutes}m {remaining_seconds}s"
    return f"{remaining_seconds}s"

def _cmd_list_collections(args: argparse.Namespace) -> int:
    collections = list_project_collections()
    if args.as_json:
        print(json.dumps(collections, indent=2, sort_keys=True))
        return 0
    if not collections:
        print("No project collections created yet.")
        return 0
    for collection in collections:
        projects = ", ".join(collection.get("projects") or [])
        print(
            f"  {collection['name']} "
            f"projects={collection['project_count']} "
            f"scripts={collection['script_count']} "
            f"workspaces={collection['workspace_count']} "
            f"objects={collection['object_count']} "
            f"snapshots={collection.get('snapshot_evidence_count', 0)}"
        )
        if projects:
            print(f"    sources={projects}")
    return 0

def _cmd_collection_info(args: argparse.Namespace) -> int:
    collection = load_project_collection(args.name)
    if args.as_json:
        print(json.dumps(collection.manifest, indent=2, sort_keys=True))
        return 0
    report = collection.root / "project_report.md"
    if report.exists():
        print(report.read_text(encoding="utf-8").rstrip())
    else:
        manifest = collection.manifest
        print(f"Collection: {collection.name}")
        print(f"  Root:       {collection.root}")
        print(f"  Projects:   {len(manifest.get('source_projects', []))}")
        print(f"  Scripts:    {len(manifest.get('scripts', []))}")
        print(f"  Objects:    {len(manifest.get('objects', []))}")
        print(f"  Workspaces: {len(manifest.get('workspaces', []))}")
        print(f"  Snapshots:  {len(manifest.get('snapshot_evidence', []))}")
    return 0

def _cmd_catalog_info(args: argparse.Namespace) -> int:
    result = run_fluentcoder(["catalog", "info"])
    _print_process(result)
    return result.returncode

def _cmd_catalog_find(args: argparse.Namespace) -> int:
    command: list[str | Path] = ["catalog", "find", args.pattern]
    if args.category:
        command.extend(["--category", args.category])
    result = run_fluentcoder(command)
    _print_process(result)
    return result.returncode

def _cmd_alias_list(args: argparse.Namespace) -> int:
    records = alias_records(load_alias_maps())
    if args.as_json:
        print(json.dumps({"aliases": records}, indent=2, sort_keys=True))
        return 0
    if not records:
        print("No aliases configured.")
        return 0
    for record in records:
        print(f"{record['kind']}: {record['alias']} -> {record['canonical']}")
    return 0

def _cmd_alias_resolve(args: argparse.Namespace) -> int:
    aliases = load_alias_maps()
    resolved = resolve_alias(args.name, args.kind, aliases)
    payload = {"kind": args.kind, "name": args.name, "resolved": resolved, "changed": resolved != args.name}
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    print(f"{args.name} -> {resolved}")
    return 0

def _cmd_alias_normalize_ir(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    source = resolve_context_path(ctx, args.input)
    payload = load_ir_payload(source)
    if is_ir_bundle(payload):
        normalized = migrate_protocol_ir_bundle(payload, validate=False)
        normalized["protocols"] = [
            normalize_protocol_ir_aliases(protocol)
            for protocol in normalized.get("protocols") or []
        ]
    else:
        normalized = normalize_protocol_ir_aliases(migrate_protocol_ir(payload, validate=False))
    output = (
        resolve_context_path(ctx, args.output)
        if args.output
        else source.with_name(f"{source.stem}.alias-normalized{source.suffix}")
    )
    write_ir_payload(normalized, output)
    print(f"Alias-normalized IR: {output}")
    if ctx:
        print(f"Project context: {ctx.name}")
    return 0
