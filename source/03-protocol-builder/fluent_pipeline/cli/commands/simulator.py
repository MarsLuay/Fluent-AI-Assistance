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



from ..runtime import (
    _command_context,
    _print_process,
    _render_log_watch_markdown,
    _simulate_protocol,
    _simulate_protocol_with_log_watch,
    _simulator_app_dir,
)

def _cmd_simulate(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    protocol = resolve_context_path(ctx, args.input)
    catalog_db = ensure_project_catalog(ctx)
    log_watch = None
    if args.watch_log:
        result, data, log_watch = _simulate_protocol_with_log_watch(
            protocol,
            catalog_db=catalog_db,
            strict=args.strict,
            fail_on_opaque=args.fail_on_opaque,
            min_coverage=args.min_coverage,
            log_path=resolve_context_path(ctx, args.watch_log),
            script_filter=args.log_script,
        )
    else:
        result, data = _simulate_protocol(
            protocol,
            catalog_db=catalog_db,
            strict=args.strict,
            fail_on_opaque=args.fail_on_opaque,
            min_coverage=args.min_coverage,
        )

    if args.json_out and data is not None:
        json_out = resolve_context_path(ctx, args.json_out)
        write_json(json_out, data)
        print(f"Simulation JSON: {json_out}")

    if args.report:
        report = resolve_context_path(ctx, args.report)
        ensure_parent(report)
        report.write_text(render_simulation_markdown(protocol, data, result), encoding="utf-8")
        print(f"Simulation report: {report}")

    if log_watch is not None:
        log_report = resolve_context_path(ctx, args.log_report) if args.log_report else protocol.with_suffix(".logs.md")
        ensure_parent(log_report)
        log_report.write_text(_render_log_watch_markdown(protocol, log_watch), encoding="utf-8")
        print(f"Log watch report: {log_report}")
        print(f"Watched log lines: {len(log_watch.captured_lines)}")

    if data is not None:
        summary = compact_simulation(data)
        print(
            "Simulation "
            f"{summary['status']}; "
            f"steps={summary['total_executed_steps']}; "
            f"coverage={summary['modeled_coverage']}; "
            f"raw_xml={summary['raw_xml_generic_steps']}"
        )
        if summary["warnings"]:
            print(f"Warnings: {len(summary['warnings'])}")
        if summary["failure"]:
            print("Simulation failure is included in the report.", file=sys.stderr)
    else:
        _print_process(result)
    if ctx:
        print(f"Project context: {ctx.name}")
    return result.returncode

def _cmd_launch_simulator(args: argparse.Namespace) -> int:
    simulator_dir = _simulator_app_dir()
    if not simulator_dir.exists():
        raise PipelineError(f"Simulator app not found: {simulator_dir}")
    if shutil.which("npm") is None:
        raise PipelineError("Node.js/npm was not found on PATH. Install Node.js LTS before launching the simulator.")

    env = os.environ.copy()
    bundle = resolve_user_path(args.bundle) if args.bundle else None
    if bundle is not None:
        if not bundle.exists():
            raise PipelineError(f"Simulator bundle not found: {bundle}")
        env["TECAN_SIMULATOR_BUNDLE"] = str(bundle)

    node_modules = simulator_dir / "node_modules"
    if not node_modules.exists() and not args.skip_install:
        print("Installing simulator dependencies with npm install...")
        install = subprocess.run(["npm", "install"], cwd=simulator_dir)
        if install.returncode:
            return install.returncode
    elif not node_modules.exists():
        print("Warning: node_modules is missing and --skip-install was set.", file=sys.stderr)

    open_target = "/?sample=launch-bundle" if bundle is not None else "/"
    command = ["npm", "run", "dev", "--", "--host", args.host, "--port", str(args.port)]
    if args.strict_port:
        command.append("--strictPort")
    if not args.no_open:
        command.extend(["--open", open_target])

    print(f"Simulator app: {simulator_dir}")
    if bundle is not None:
        print(f"Preloaded bundle: {bundle}")
    print(f"Local URL: http://{args.host}:{args.port}{open_target}")
    print("Keep this process running while using the simulator.")
    return subprocess.call(command, cwd=simulator_dir, env=env)
