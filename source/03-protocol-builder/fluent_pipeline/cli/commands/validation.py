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
from ...delivery_bundle import render_delivery_bundle_validation, validate_v2_delivery_bundle
from ...bundle_media import process_prompt_media_captures
from ...determinism import compare_run_dirs, render_determinism_report
from ...diagnostics import diagnose_input
from ...external_commands import (
    inspect_external_command,
    render_external_command_contract_markdown,
    write_external_command_contract,
)
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
from ...request_spec_resolver import resolve_request_spec_path
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
from ...generation_options import generation_options_from_cli_args
from ...project_context import (
    ProjectContext,
    ProjectLike,
    active_project_name,
    clear_active_project,
    create_project_collection,
    find_in_project,
    import_project,
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



from ..runtime import _command_context, cli_module

def _cmd_fluent_prepare_check(args: argparse.Namespace) -> int:
    xscr = resolve_user_path(args.xscr) if args.xscr else None
    zeia = resolve_user_path(args.zeia) if getattr(args, "zeia", None) else None
    method = args.method or args.fluent_method
    result = run_fluent_context_check(
        FluentContextCheckConfig(
            method=method or "",
            xscr_path=xscr,
            zeia_path=zeia,
            provider=args.fluent_provider,
            host=args.fluent_host,
            port=args.fluent_port,
            insecure=args.fluent_insecure,
            timeout_seconds=args.fluent_timeout,
            command=args.fluent_command,
            close_method=not args.no_fluent_close_method,
        )
    )
    if args.json_out:
        json_out = resolve_user_path(args.json_out)
        write_json(json_out, result)
        print(f"FluentControl context-check JSON: {json_out}")
    if args.report:
        report = resolve_user_path(args.report)
        ensure_parent(report)
        report.write_text(render_fluent_context_check_markdown(result), encoding="utf-8")
        print(f"FluentControl import/load diagnostic report: {report}")
    print(
        "FluentControl import/load diagnostic "
        f"{'passed' if result.get('ok') else 'failed'}; "
        f"provider={result.get('provider')}; method={result.get('method')}"
    )
    for line in [*(result.get("errors") or []), *(result.get("runtime_errors") or [])][:10]:
        print(f"  {line}", file=sys.stderr)
    return 0 if result.get("ok") else 1

def _cmd_ir_validate(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    source = resolve_context_path(ctx, args.input)
    try:
        payload = load_ir_payload(source)
        if is_ir_bundle(payload):
            normalized = migrate_protocol_ir_bundle(payload, validate=False)
            issues = validate_protocol_ir_bundle_document(payload, normalize=args.normalize)
            kind = "protocol_ir_bundle"
        else:
            normalized = migrate_protocol_ir(payload, validate=False)
            issues = validate_protocol_ir_document(payload, normalize=args.normalize)
            kind = "protocol_ir"
    except Exception as exc:
        normalized = None
        issues = [{"path": "$", "message": str(exc), "severity": "error"}]
        kind = "unknown"

    issue_dicts = [issue.as_dict() if hasattr(issue, "as_dict") else dict(issue) for issue in issues]
    ok = not [issue for issue in issue_dicts if issue.get("severity") == "error"]
    report = {
        "kind": kind,
        "path": str(source),
        "valid": ok,
        "issue_count": len(issue_dicts),
        "issues": issue_dicts,
    }

    if args.write_normalized and ok and isinstance(normalized, dict):
        output = resolve_context_path(ctx, args.write_normalized)
        write_ir_payload(normalized, output)
        report["normalized_output"] = str(output)

    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif ok:
        print(f"Protocol IR valid: {source}")
        if report.get("normalized_output"):
            print(f"Normalized IR: {report['normalized_output']}")
    else:
        print(f"Protocol IR invalid: {source}", file=sys.stderr)
        for issue in issue_dicts:
            print(f"- {issue.get('path')}: {issue.get('message')}", file=sys.stderr)
    return 0 if ok else 1

def _cmd_worktable_diff(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    source = resolve_context_path(ctx, args.input)
    ir = load_protocol_ir(source)
    source_irs = []
    for raw in args.source_script or []:
        script_path = resolve_context_script(ctx, raw)
        payload = protocol_ir_from_path(script_path)
        if payload.get("ir_version") == CANONICAL_IR_VERSION:
            source_irs.append(payload)
        elif is_ir_bundle(payload):
            source_irs.extend(payload.get("protocols") or [])
    diff = diff_worktable_requirements(
        ir,
        source_manifest=ctx.manifest if ctx else None,
        source_irs=source_irs,
    )
    if args.as_json:
        print(json.dumps(diff, indent=2, sort_keys=True))
        return 0

    output = (
        resolve_context_path(ctx, args.output)
        if args.output
        else source.parent / "worktable_changes.md"
    )
    patch_output = output.parent / "worktable.patch.json"
    ensure_parent(output)
    output.write_text(render_worktable_changes_markdown(diff), encoding="utf-8")
    patch_output.write_text(render_worktable_patch_json(diff), encoding="utf-8")
    print(f"Worktable changes report: {output}")
    print(f"Worktable patch JSON: {patch_output}")
    if ctx:
        print(f"Project context: {ctx.name}")
    return 0

def _cmd_validate_spec(args: argparse.Namespace) -> int:
    cli = cli_module()
    request = cli.request_spec_validation_request_from_cli(args)
    result = cli.validate_request_spec(request)
    cli.print_request_spec_validation_result(result)
    return cli.request_spec_validation_exit_code(result)


def _cmd_resolve_spec(args: argparse.Namespace) -> int:
    """Resolve a regeneration spec without generating or importing anything."""
    path, info = resolve_request_spec_path(
        args.spec,
        protocol_name=args.protocol_name,
        context_name=args.context,
        pin=bool(args.pin_spec),
    )
    if path is None:
        print("No matching request.spec.yaml found.", file=sys.stderr)
        return 1
    if args.as_json:
        print(json.dumps(info, indent=2, sort_keys=True))
    else:
        print(f"Resolved request spec: {path}")
        print(f"Resolution: {info['reason']}")
        source_bundle = info.get("source_bundle_dir")
        if source_bundle:
            print(f"Ready bundle: {source_bundle}")
    return 0

def _cmd_request_spec(args: argparse.Namespace) -> int:
    cli = cli_module()
    request = cli.request_spec_create_request_from_cli(args)
    result = cli.create_request_spec(request)
    cli.print_request_spec_result(result)
    return 0


def _cmd_validate_delivery_bundle(args: argparse.Namespace) -> int:
    bundle_dir = resolve_user_path(args.bundle_dir)
    result = validate_v2_delivery_bundle(
        bundle_dir,
        protocol_name=args.protocol_name,
        require_final_reports=not args.allow_missing_final_reports,
    )
    if args.json_out:
        json_out = resolve_user_path(args.json_out)
        write_json(json_out, result.to_dict())
        print(f"V2 delivery-bundle validation JSON: {json_out}")
    if args.as_json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_delivery_bundle_validation(result))
    return 0 if result.ok else 1


def _cmd_verify_bundle(args: argparse.Namespace) -> int:
    cli = cli_module()
    request = cli.bundle_verification_request_from_cli(args)
    result = cli.verify_bundle(request)
    cli.print_bundle_verification_result(result, as_json=args.as_json)
    return cli.bundle_verification_exit_code(result)
