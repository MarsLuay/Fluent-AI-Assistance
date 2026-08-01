"""CLI for the local Codex-friendly Fluent protocol builder."""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import asdict
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
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
)
from ...compatibility import (
    TargetSetup,
    build_compatibility_report,
    current_manual_target,
    render_compatibility_markdown,
)
from ...compiled_xscr_finalizer import (
    finalize_compiled_xscr,
    render_compiled_xscr_finalization_markdown,
)
from ...bundle_media import process_prompt_media_captures
from ...determinism import compare_run_dirs, render_determinism_report
from ...diagnostics import diagnose_input
from ...external_commands import (
    inspect_external_command,
    render_external_command_contract_markdown,
    write_external_command_contract,
)
from ...application_services import (
    ProjectImportRequest,
    RepairApplyRequest,
    RepairPlanRequest,
    apply_repair as apply_repair_service,
    import_project as import_project_service,
    plan_repair as plan_repair_service,
)
from ...fluent_library import resolve_local_fluent_script, stage_local_fluent_script
from ...mcp_gateway import resolve_process_media_ir_path
from ...fluent_log_parser import (
    build_fluent_log_report,
    build_latest_fluent_log_report,
    render_fluent_log_report_markdown,
    report_to_json,
)
from ...generation_workflow import ApprovalSet, GenerationRequest
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
    load_request_spec,
    request_spec_generation_defaults,
)
from ...generation_options import GenerationOptions, generation_options_from_cli_args
from ...project_context import (
    ProjectContext,
    ProjectLike,
    active_project_name,
    clear_active_project,
    create_project_collection,
    find_in_project,
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
    cli_module,
    _command_context,
    _default_generation_dir,
    _default_output,
    _resolve_artifact_output_path,
    _fluent_method_from_artifact,
    _fluent_method_from_ir,
    _print_process,
    _run_cli_fluent_context_check,
    _safe_output_label,
    _simulate_protocol,
    _write_roundtrip_report,
)
from ..requests import (
    generation_context_from_args as _shared_generation_context_from_args,
    generation_options_from_generate_args as _shared_generation_options_from_generate_args,
    generation_request_from_cli,
    merge_generate_spec_args as _shared_merge_generate_spec_args,
    repair_apply_request_from_cli,
    repair_plan_request_from_cli,
    resolve_generation_event_log as _shared_resolve_generation_event_log,
    resolve_ir_source as _shared_resolve_ir_source,
)
from ..rendering import (
    generation_exit_code,
    generation_simulator_bundle,
    print_generation_result,
    print_repair_apply_result,
    print_repair_plan_result,
)

def _cmd_decompile(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    source = resolve_context_script(ctx, args.input)
    output = (
        _resolve_artifact_output_path(args.output, ctx=ctx)
        if args.output
        else _default_output(ctx, "drafts", f"{source.stem}_decompiled.py")
    )
    ensure_parent(output)

    command: list[str | Path] = ["decompile", source, "-o", output]
    if args.strict:
        command.append("--strict")
    result = run_fluentcoder(command, catalog_db=ensure_project_catalog(ctx))
    _print_process(result)
    if result.ok:
        print(f"Decompiled Python: {output}")
        if ctx:
            print(f"Project context: {ctx.name}")
    return result.returncode

def _cmd_compile(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    protocol = resolve_context_path(ctx, args.input)
    output = (
        _resolve_artifact_output_path(args.output, ctx=ctx)
        if args.output
        else _default_output(ctx, "build", f"{protocol.stem}.xscr")
    )
    ensure_parent(output)
    result = run_fluentcoder(["compile", protocol, "-o", output], catalog_db=ensure_project_catalog(ctx))
    _print_process(result)
    compile_report = output.with_suffix(".compile.md")
    compile_report.write_text(render_compile_markdown(protocol, output, result), encoding="utf-8")
    return_code = result.returncode
    finalization_report = None
    if result.ok:
        finalization_report = finalize_compiled_xscr(
            output,
            protocol,
            ctx.manifest if ctx else None,
            [protocol] if protocol.suffix.lower() == ".xscr" else None,
            {"source_ir_origin": "compile_input"},
        )
        _append_compiled_xscr_finalization_report(compile_report, finalization_report)
        if not finalization_report.ok:
            return_code = 1
    print(f"Compile report: {compile_report}")
    if result.ok and finalization_report and finalization_report.ok:
        print(f"Compiled XSCR: {output}")
        _run_cli_fluent_context_check(
            args,
            xscr_path=output,
            fallback_method=_fluent_method_from_artifact(protocol, output.stem),
            report_base=output.with_suffix(""),
        )
        if ctx:
            print(f"Project context: {ctx.name}")
    else:
        if finalization_report and finalization_report.errors:
            print(f"Compiled XSCR finalization failed: {finalization_report.errors[0]}", file=sys.stderr)
        if ctx:
            print(f"Project context: {ctx.name}")
    return return_code

def _cmd_ir_export(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    source = _resolve_ir_source(ctx, args.input)
    output = (
        _resolve_artifact_output_path(args.output, ctx=ctx)
        if args.output
        else _default_output(ctx, "build", f"{source.stem}.protocol-ir.json")
    )
    payload = protocol_ir_from_path(source)
    write_ir_payload(payload, output)
    print(f"Canonical IR: {output}")
    if payload.get("ir_version") == CANONICAL_IR_BUNDLE_VERSION:
        print(f"Protocols: {payload.get('protocol_count', 0)}")
    elif payload.get("ir_version") == CANONICAL_IR_VERSION:
        print(f"Steps: {len(payload.get('steps') or [])}")
    if ctx:
        print(f"Project context: {ctx.name}")
    return 0

def _cmd_ir_build(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    source = resolve_context_path(ctx, args.input)
    payload = load_ir_payload(source)
    out_dir = (
        _resolve_artifact_output_path(args.out_dir, ctx=ctx)
        if args.out_dir
        else _default_output(ctx, "build", source.stem)
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    if is_ir_bundle(payload):
        payload = migrate_protocol_ir_bundle(payload)
        return_codes = []
        for protocol in payload.get("protocols") or []:
            protocol_dir = out_dir / Path(protocol_filename(protocol, "")).stem
            return_codes.append(
                _build_ir_artifacts(
                    protocol,
                    protocol_dir,
                    compile_xscr=not args.no_compile,
                    source_manifest=ctx.manifest if ctx else None,
                    catalog_db=ensure_project_catalog(ctx),
                    fluent_args=args,
                )
            )
        print(f"Built {len(return_codes)} protocol IR package(s) under: {out_dir}")
        if ctx:
            print(f"Project context: {ctx.name}")
        return max(return_codes, default=0)

    ir = load_protocol_ir(source)
    code = _build_ir_artifacts(
        ir,
        out_dir,
        compile_xscr=not args.no_compile,
        source_manifest=ctx.manifest if ctx else None,
        catalog_db=ensure_project_catalog(ctx),
        fluent_args=args,
    )
    if ctx:
        print(f"Project context: {ctx.name}")
    return code

def _cmd_ir_schema(args: argparse.Namespace) -> int:
    if args.versions:
        versions = [info.as_dict() for info in protocol_ir_schema_versions()]
        rendered = (
            "\n".join(
                f"- `{item['version']}` ({'current' if item['current'] else 'registered'}): {item['schema_id']}"
                for item in versions
            )
            + "\n"
            if args.format == "markdown"
            else json.dumps({"versions": versions}, indent=2, sort_keys=True)
        )
    elif args.bundle:
        schema = protocol_ir_bundle_json_schema()
        rendered = (
            _render_bundle_schema_markdown(schema)
            if args.format == "markdown"
            else json.dumps(schema, indent=2, sort_keys=True)
        )
    else:
        rendered = (
            protocol_ir_schema_markdown(args.version)
            if args.format == "markdown"
            else json.dumps(protocol_ir_json_schema(args.version), indent=2, sort_keys=True)
        )
    if args.output:
        output = resolve_user_path(args.output)
        ensure_parent(output)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Protocol IR schema: {output}")
    else:
        print(rendered)
    return 0

def _render_bundle_schema_markdown(schema: dict[str, Any]) -> str:
    lines = [
        f"# Protocol IR Bundle Schema: {CANONICAL_IR_BUNDLE_VERSION}",
        "",
        f"- Schema ID: `{schema.get('$id', '')}`",
        f"- JSON Schema draft: `{schema.get('$schema', '')}`",
        "",
        "## Required Root Fields",
        "",
    ]
    for key in schema.get("required", []):
        lines.append(f"- `{key}`")
    lines.extend(
        [
            "",
            "## Contents",
            "",
            f"- `protocols` is an array of `{CANONICAL_IR_VERSION}` protocol documents.",
            "- `protocol_count` must match the number of entries in `protocols`.",
            "",
        ]
    )
    return "\n".join(lines)

def _cmd_generate(args: argparse.Namespace) -> int:
    cli = cli_module()
    request = cli.generation_request_from_cli(args)
    display_callback = cli.progress_callback_from_mode(getattr(args, "progress", "auto"))
    event_log_path = _resolve_generation_event_log(args, request.output_directory)
    event_handle = None

    def emit_event(event) -> None:
        if display_callback is not None:
            display_callback(event)
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": round((event.elapsed_seconds or 0.0) * 1000),
            "stage": event.stage_id,
            "status": {
                "started": "start",
                "running": "heartbeat",
                "completed": "done",
                "failed": "error",
                "skipped": "info",
                "warning": "info",
            }.get(event.status, event.status),
            "message": event.message,
            "event": asdict(event),
        }
        line = json.dumps(payload, sort_keys=True)
        if event_handle is not None:
            event_handle.write(line + "\n")
            event_handle.flush()
        if getattr(args, "event_log_stderr", False):
            print(line, file=sys.stderr, flush=True)

    try:
        if event_log_path is not None:
            ensure_parent(event_log_path)
            event_handle = event_log_path.open("a", encoding="utf-8", buffering=1)
        callback = emit_event if (display_callback is not None or event_handle is not None or getattr(args, "event_log_stderr", False)) else None
        if callback is None:
            result = cli.generate_protocol(request)
        else:
            result = cli.generate_protocol(request, progress_callback=callback)
        if event_handle is not None:
            event_handle.write(
                json.dumps(
                    {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "elapsed_ms": None,
                        "stage": "workflow",
                        "status": "done" if result.manifest.get("ready_to_import") else "error",
                        "message": str(result.manifest.get("workflow_status") or "generation completed"),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            event_handle.flush()
    finally:
        if event_handle is not None:
            event_handle.close()
    cli.print_generation_result(result, stream=sys.stdout)
    return_code = cli.generation_exit_code(result)
    if args.launch_simulator and return_code == 0:
        bundle = cli.generation_simulator_bundle(result)
        print(f"Launching simulator with bundle: {bundle}")
        return cli._cmd_launch_simulator(
            argparse.Namespace(
                bundle=bundle,
                host=args.simulator_host,
                port=args.simulator_port,
                strict_port=args.simulator_strict_port,
                no_open=args.simulator_no_open,
                skip_install=args.simulator_skip_install,
            )
        )
    return return_code

def _cmd_bundle_lifecycle(args: argparse.Namespace) -> int:
    ready_root = resolve_user_path(args.root) if args.root else READY_TO_IMPORT_DIR
    probe_roots = [resolve_user_path(path) for path in (args.probe_root or [])]
    if not args.no_default_probe_root and DEFAULT_PROBE_ROOT.exists():
        probe_roots.append(DEFAULT_PROBE_ROOT)
    records = scan_bundle_lifecycle(
        ready_root=ready_root,
        probe_roots=probe_roots,
        keep_latest_ready=max(0, int(args.keep_latest_ready)),
    )
    archived: list[dict[str, str]] | None = None
    if args.archive:
        archive_root = resolve_user_path(args.archive_dir) if args.archive_dir else ready_root / "archive"
        archived = archive_recommended_bundles(records, archive_root=archive_root)
    if args.as_json:
        payload = {
            "ready_root": str(ready_root),
            "probe_roots": [str(path) for path in probe_roots],
            "dry_run": not args.archive,
            "bundles": [record.to_dict() for record in records],
            "archived": archived or [],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        report = render_bundle_index(records, archived=archived)
        print(report)
    if args.report:
        report_path = resolve_user_path(args.report)
        ensure_parent(report_path)
        report_path.write_text(render_bundle_index(records, archived=archived), encoding="utf-8")
        print(f"Bundle lifecycle report: {report_path}")
    if args.write_index:
        index_path = ready_root / "BUNDLE_INDEX.md"
        ensure_parent(index_path)
        index_path.write_text(render_bundle_index(records, archived=archived), encoding="utf-8")
        print(f"Bundle index: {index_path}")
    if not args.archive:
        archive_count = sum(1 for record in records if record.recommendation == "archive")
        print(f"Dry run: {archive_count} item(s) recommended for archive; rerun with --archive to move them.")
    return 0

def _merge_generate_spec_args(args: argparse.Namespace, request_spec: dict[str, Any]) -> argparse.Namespace:
    return _shared_merge_generate_spec_args(args, request_spec)


def _generation_options_from_generate_args(
    args: argparse.Namespace,
    request_spec: dict[str, Any] | None,
) -> GenerationOptions:
    return _shared_generation_options_from_generate_args(args, request_spec)

def _generation_context_from_args(args: argparse.Namespace) -> tuple[ProjectLike | None, Path | None]:
    return _shared_generation_context_from_args(args)

def _collection_name_from_parts(parts: list[str]) -> str:
    label = "-and-".join(parts[:4])
    if len(parts) > 4:
        label = f"{label}-plus-{len(parts) - 4}"
    return _safe_output_label(label)[:80] or "project-collection"

def _resolve_ir_source(ctx: ProjectLike | None, value: Path) -> Path:
    return _shared_resolve_ir_source(ctx, value)

def _resolve_generation_event_log(args: argparse.Namespace, out_dir: Path) -> Path | None:
    return _shared_resolve_generation_event_log(args, out_dir)


def _append_compiled_xscr_finalization_report(report_path: Path, report: Any) -> None:
    with report_path.open("a", encoding="utf-8") as handle:
        handle.write("\n")
        handle.write(render_compiled_xscr_finalization_markdown(report))

def _build_ir_artifacts(
    ir: dict[str, Any],
    out_dir: Path,
    *,
    compile_xscr: bool,
    source_manifest: dict[str, Any] | None = None,
    catalog_db: Path | None = None,
    fluent_args: argparse.Namespace | None = None,
) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    base = Path(protocol_filename(ir, "")).stem or "protocol"
    ir_path = out_dir / f"{base}.protocol-ir.json"
    python_path = out_dir / f"{base}.py"
    gwl_path = out_dir / f"{base}.gwl"
    xscr_path = out_dir / f"{base}.xscr"
    recreate_path = out_dir / "RECREATE_SCRIPT.md"
    worktable_changes_path = out_dir / "worktable_changes.md"
    worktable_patch_path = out_dir / "worktable.patch.json"

    write_protocol_ir(ir, ir_path)
    python_path.write_text(render_python_draft(ir), encoding="utf-8")

    generated_files = {
        "ir": ir_path.name,
        "python": python_path.name,
    }
    gwl_text = render_gwl(ir)
    if gwl_text.strip():
        gwl_path.write_text(gwl_text, encoding="utf-8")
        generated_files["gwl"] = gwl_path.name

    return_code = 0
    compile_report = out_dir / f"{base}.compile.md"
    compile_result = None
    finalization_report = None
    if compile_xscr:
        compile_result = run_fluentcoder(["compile", python_path, "-o", xscr_path], catalog_db=catalog_db)
        compile_report.write_text(render_compile_markdown(python_path, xscr_path, compile_result), encoding="utf-8")
        if compile_result.ok:
            finalization_report = finalize_compiled_xscr(
                xscr_path,
                ir,
                source_manifest,
                None,
                {"source_ir_origin": "ir_build"},
            )
            _append_compiled_xscr_finalization_report(compile_report, finalization_report)
            if finalization_report.ok:
                generated_files["xscr"] = xscr_path.name
            else:
                return_code = 1
        else:
            return_code = compile_result.returncode
            _print_process(compile_result)

    recreate_path.write_text(
        render_recreate_markdown(ir, generated_files=generated_files),
        encoding="utf-8",
    )
    worktable_diff = diff_worktable_requirements(ir, source_manifest=source_manifest)
    worktable_changes_path.write_text(render_worktable_changes_markdown(worktable_diff), encoding="utf-8")
    worktable_patch_path.write_text(render_worktable_patch_json(worktable_diff), encoding="utf-8")

    print(f"Canonical IR copy: {ir_path}")
    print(f"Python draft: {python_path}")
    if "gwl" in generated_files:
        print(f"GWL draft: {gwl_path}")
    if "xscr" in generated_files:
        print(f"Compiled XSCR: {xscr_path}")
        print(f"Compile report: {compile_report}")
    print(f"Recreate guide: {recreate_path}")
    print(f"Worktable changes: {worktable_changes_path}")
    print(f"Worktable patch JSON: {worktable_patch_path}")
    if compile_result is not None and compile_result.ok and finalization_report and finalization_report.ok:
        _run_cli_fluent_context_check(
            fluent_args,
            xscr_path=xscr_path,
            fallback_method=_fluent_method_from_ir(ir, base),
            report_base=out_dir / base,
        )
    elif compile_result is not None:
        print(f"Compile report: {compile_report}")
    return return_code

def _cmd_roundtrip(args: argparse.Namespace) -> int:
    ctx = _command_context(args.context)
    source = resolve_context_script(ctx, args.input)
    out_dir = (
        _resolve_artifact_output_path(args.out_dir, ctx=ctx)
        if args.out_dir
        else _default_output(ctx, "roundtrips", f"{source.stem}_roundtrip")
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    stages: list[dict[str, Any]] = []
    decompiled = out_dir / f"{source.stem}_decompiled.py"
    compiled = out_dir / f"{source.stem}_roundtrip.xscr"
    simulation_json = out_dir / f"{source.stem}_simulation.json"
    simulation_report = out_dir / f"{source.stem}_simulation.md"
    repair_json = out_dir / f"{source.stem}_repair-plan.json"
    repair_report = out_dir / f"{source.stem}_repair-plan.md"
    roundtrip_report = out_dir / "roundtrip_report.md"

    decompile_command: list[str | Path] = ["decompile", source, "-o", decompiled]
    if args.strict_decompile:
        decompile_command.append("--strict")
    catalog_db = ensure_project_catalog(ctx)
    decompile_result = run_fluentcoder(decompile_command, catalog_db=catalog_db)
    stages.append({"name": "Decompile", "result": decompile_result, "output": decompiled})
    if not decompile_result.ok:
        _write_roundtrip_report(roundtrip_report, source, stages)
        print(f"Roundtrip report: {roundtrip_report}")
        _print_process(decompile_result)
        return decompile_result.returncode

    simulate_result, simulation_data = _simulate_protocol(
        decompiled,
        catalog_db=catalog_db,
        strict=args.strict_simulate,
        fail_on_opaque=args.fail_on_opaque,
        min_coverage=args.min_coverage,
    )
    stages.append(
        {
            "name": "Simulate Decompiled Draft",
            "result": simulate_result,
            "output": simulation_report,
            "simulation": simulation_data,
        }
    )
    if simulation_data is not None:
        write_json(simulation_json, simulation_data)
    simulation_report.write_text(
        render_simulation_markdown(decompiled, simulation_data, simulate_result),
        encoding="utf-8",
    )
    if not simulate_result.ok:
        _write_roundtrip_report(roundtrip_report, source, stages)
        print(f"Decompiled Python: {decompiled}")
        print(f"Simulation report: {simulation_report}")
        print(f"Roundtrip report: {roundtrip_report}")
        if ctx:
            print(f"Project context: {ctx.name}")
        if simulate_result.stderr.strip():
            print(simulate_result.stderr.strip(), file=sys.stderr)
        return simulate_result.returncode

    repair_plan = build_repair_plan(
        decompiled,
        context=ctx,
        simulation_json_path=simulation_json if simulation_json.exists() else None,
    )
    write_json(repair_json, repair_plan.to_dict())
    repair_report.write_text(render_repair_markdown(repair_plan), encoding="utf-8")

    compile_result = run_fluentcoder(["compile", decompiled, "-o", compiled], catalog_db=catalog_db)
    compile_report = out_dir / f"{source.stem}_compile.md"
    compile_report.write_text(render_compile_markdown(decompiled, compiled, compile_result), encoding="utf-8")
    stages.append({"name": "Compile Roundtrip", "result": compile_result, "output": compiled})
    _write_roundtrip_report(roundtrip_report, source, stages)
    finalization_report = None
    return_code = compile_result.returncode

    print(f"Decompiled Python: {decompiled}")
    print(f"Simulation report: {simulation_report}")
    print(f"Repair report: {repair_report}")
    print(f"Roundtrip report: {roundtrip_report}")
    if compile_result.ok:
        finalization_report = finalize_compiled_xscr(
            compiled,
            source,
            ctx.manifest if ctx else None,
            [source],
            {"source_ir_origin": "roundtrip_source_xscr"},
        )
        _append_compiled_xscr_finalization_report(compile_report, finalization_report)
        if not finalization_report.ok:
            return_code = 1
            print(f"Compiled XSCR finalization failed: {finalization_report.errors[0]}", file=sys.stderr)
    print(f"Compile report: {compile_report}")
    if compile_result.ok and finalization_report and finalization_report.ok:
        print(f"Compiled XSCR: {compiled}")
        _run_cli_fluent_context_check(
            args,
            xscr_path=compiled,
            fallback_method=_fluent_method_from_artifact(decompiled, compiled.stem),
            report_base=out_dir / f"{source.stem}_roundtrip",
        )
    else:
        _print_process(compile_result)
    if ctx:
        print(f"Project context: {ctx.name}")
    return return_code

def _cmd_repair_plan(args: argparse.Namespace) -> int:
    cli = cli_module()
    request = cli.repair_plan_request_from_cli(args)
    result = cli.plan_repair(request)
    cli.print_repair_plan_result(result, as_json=args.as_json)
    return 0

def _cmd_determinism_check(args: argparse.Namespace) -> int:
    report = compare_run_dirs(
        args.first,
        args.second,
        extra_roots=[str(root) for root in args.root],
    )
    if args.as_json:
        print(json.dumps(report, indent=2))
    else:
        print(render_determinism_report(report))
    return 0 if report.get("deterministic") else 1

def _cmd_repair_draft(args: argparse.Namespace) -> int:
    cli = cli_module()
    request = cli.repair_apply_request_from_cli(args)
    result = cli.apply_repair(request)
    cli.print_repair_apply_result(result)
    return 0
