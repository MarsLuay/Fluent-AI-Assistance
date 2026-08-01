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
    READY_TO_IMPORT_DIR,
    TEMP_FILES_DIRNAME,
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
from ...application_services import LogAnalysisRequest, analyze_logs
from ...exports import export_ready_to_import
from ...fluent_library import resolve_local_fluent_script, stage_local_fluent_script
from ...mcp_gateway import resolve_process_media_ir_path
from ...fluent_log_parser import (
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



from ..runtime import cli_module, _safe_output_label


def _cmd_analyze(args: argparse.Namespace) -> int:
    cli = cli_module()
    if args.fluent_script and args.input is not None:
        raise PipelineError("pass either an input path or --fluent-script, not both")
    input_path = resolve_user_path(args.input) if args.input is not None else None
    input_is_archive = bool(input_path and (input_path.suffix.lower() == ".zeia" or is_context_archive(input_path)))
    ctx = None if input_is_archive or args.fluent_script else cli._command_context(args.context)
    if input_path is None and ctx is None and not args.fluent_script:
        raise PipelineError("analyze requires an input path, --context, --fluent-script, or an active project")

    out_dir = _analysis_output_dir(
        args.out_dir,
        ctx,
        input_path or (Path(str(args.fluent_script)) if args.fluent_script else None),
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    local_fluent_record = None
    if args.fluent_script:
        local_fluent_record = resolve_local_fluent_script(
            args.fluent_script,
            folder=args.fluent_folder,
            database=args.fluent_database,
        )
        input_path = stage_local_fluent_script(local_fluent_record, out_dir)
        ctx = _analysis_context_for_staged_fluent_script(out_dir, input_path, local_fluent_record)
        input_is_archive = False

    error_text = _analysis_error_text(ctx, args.error_text, args.error_file)
    diagnosis_bundle = None
    diagnosis_error = ""

    if input_path is not None:
        try:
            diagnosis_bundle = cli.diagnose_input(
                input_path,
                context=ctx,
                script=args.script,
                project_name=args.name,
                force_import=args.force_import,
                snapshot_archives=[resolve_user_path(path) for path in args.snapshot],
                error_text=error_text,
                out_dir=out_dir / "diagnostics",
            )
        except Exception as exc:
            diagnosis_error = str(exc)
        if input_is_archive and diagnosis_bundle is not None:
            ctx_name = str((diagnosis_bundle.report.get("context") or {}).get("name") or "").strip()
            if ctx_name:
                try:
                    ctx = load_project(ctx_name)
                except Exception:
                    ctx = None
    elif ctx is not None:
        try:
            selected = _analysis_context_script_path(ctx, args.script, args.script_index)
            diagnosis_bundle = cli.diagnose_input(
                selected,
                context=ctx,
                script=args.script,
                error_text=error_text,
                out_dir=out_dir / "diagnostics",
            )
        except Exception as exc:
            diagnosis_error = str(exc)

    script_report = None
    script_error = ""
    if ctx is not None:
        try:
            script_report = cli.analyze_script(
                ctx,
                script=args.script,
                script_index=args.script_index,
                out_dir=out_dir / "script_report",
                max_commands=args.max_commands,
            )
        except Exception as exc:
            script_error = str(exc)

    log_report = None
    log_error = ""
    xscr_paths: tuple[Path, ...] = ()
    if ctx is not None:
        try:
            xscr_paths = tuple(sorted(Path(ctx.root).joinpath("extracted").rglob("*.xscr"))[:40])
        except OSError:
            xscr_paths = ()
    try:
        if args.latest_log:
            log_report = analyze_logs(
                LogAnalysisRequest(
                    latest=True,
                    since_hours=args.since_hours,
                    max_files=args.max_files,
                    max_records=args.max_records,
                )
            ).report
        elif args.log:
            log_path = resolve_context_path(ctx, args.log) if ctx else resolve_user_path(args.log)
            log_report = analyze_logs(
                LogAnalysisRequest(
                    log_path=log_path,
                    audit_paths=tuple(resolve_user_path(path) for path in args.audit_log),
                    xscr_paths=xscr_paths,
                )
            ).report
    except Exception as exc:
        log_error = str(exc)

    artifacts: dict[str, str] = {}
    if diagnosis_bundle is not None:
        if diagnosis_bundle.report_path:
            artifacts["diagnosis_markdown"] = str(diagnosis_bundle.report_path)
        if diagnosis_bundle.json_path:
            artifacts["diagnosis_json"] = str(diagnosis_bundle.json_path)
    if script_report is not None:
        for key, value in (script_report.get("artifacts") or {}).items():
            artifacts[f"script_{key}"] = str(value)
    if log_report is not None:
        log_md = out_dir / "fluent-log.md"
        log_json = out_dir / "fluent-log.json"
        log_md.write_text(render_fluent_log_report_markdown(log_report), encoding="utf-8")
        log_json.write_text(report_to_json(log_report), encoding="utf-8")
        artifacts["fluent_log_markdown"] = str(log_md)
        artifacts["fluent_log_json"] = str(log_json)

    analysis = _build_analysis_report(
        input_path=input_path,
        context=ctx,
        diagnosis=diagnosis_bundle.report if diagnosis_bundle is not None else None,
        diagnosis_error=diagnosis_error,
        script_report=script_report,
        script_error=script_error,
        log_report=log_report,
        log_error=log_error,
        local_fluent_script=local_fluent_record,
        artifacts=artifacts,
    )
    analysis_md = out_dir / "analysis.md"
    analysis_json = out_dir / "analysis.json"
    analysis["artifacts"]["analysis_markdown"] = str(analysis_md)
    analysis["artifacts"]["analysis_json"] = str(analysis_json)
    analysis_md.write_text(_render_analysis_markdown(analysis), encoding="utf-8")
    write_json(analysis_json, analysis)

    if args.as_json:
        print(json.dumps(analysis, indent=2, sort_keys=True))
        return 0

    summary = analysis.get("summary") or {}
    print(f"Analysis: {summary.get('status', 'unknown')}")
    print(f"Analysis report: {analysis_md}")
    print(f"Analysis JSON: {analysis_json}")
    for action in summary.get("recommended_actions") or []:
        print(f"  [{action.get('severity')}] {action.get('title')}")
    return 0

def _analysis_output_dir(out_dir: Path | None, ctx: ProjectLike | None, input_path: Path | None) -> Path:
    if out_dir is not None:
        return resolve_context_path(ctx, out_dir) if ctx else resolve_user_path(out_dir)
    if ctx is not None:
        return (ctx.reports_dir / "analysis").resolve()
    label = _safe_output_label((input_path.stem if input_path else "script") + "-analysis")
    return (READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "reports" / label).resolve()

def _analysis_error_text(ctx: ProjectLike | None, error_text: str | None, error_file: Path | None) -> str:
    parts = []
    if error_text:
        parts.append(error_text)
    if error_file:
        path = resolve_context_path(ctx, error_file) if ctx else resolve_user_path(error_file)
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts).strip()

def _analysis_context_script_path(ctx: ProjectLike, script: str | None, script_index: int) -> Path:
    if script:
        return resolve_context_script(ctx, script)
    scripts = list(ctx.manifest.get("scripts") or [])
    if not scripts:
        raise PipelineError(f"context `{ctx.name}` has no scripts to analyze")
    index = max(1, script_index)
    if index > len(scripts):
        raise PipelineError(f"--script-index {index} is out of range; context has {len(scripts)} script(s)")
    raw = scripts[index - 1].get("extracted_path") or scripts[index - 1].get("path")
    if not raw:
        raise PipelineError(f"script index {index} has no extracted path in context `{ctx.name}`")
    path = Path(str(raw))
    return path if path.is_absolute() else (ctx.root / path).resolve()

def _analysis_context_for_staged_fluent_script(
    out_dir: Path,
    staged_script: Path,
    record: dict[str, Any],
) -> ProjectContext:
    try:
        extracted_path = str(staged_script.relative_to(out_dir))
    except ValueError:
        extracted_path = str(staged_script)
    script_record = {
        "kind": "script",
        "entry": f"local_fluent_database/{Path(staged_script).name}",
        "extracted_path": extracted_path,
        "object_name": record.get("object_name") or Path(staged_script).stem,
        "object_path": record.get("object_path") or "",
        "guid": record.get("guid") or "",
        "script_guid": record.get("guid") or "",
        "command_count": 0,
        "command_counts": {},
        "family_counts": {},
        "dependencies": {},
    }
    return ProjectContext(
        name="local-fluent-database",
        root=out_dir,
        manifest={
            "kind": "local_fluent_database",
            "scripts": [script_record],
            "objects": [],
            "workspaces": [],
            "errors": [],
            "local_fluent_script": record,
        },
    )

def _build_analysis_report(
    *,
    input_path: Path | None,
    context: ProjectLike | None,
    diagnosis: dict[str, Any] | None,
    diagnosis_error: str,
    script_report: dict[str, Any] | None,
    script_error: str,
    log_report: dict[str, Any] | None,
    log_error: str,
    local_fluent_script: dict[str, Any] | None,
    artifacts: dict[str, str],
) -> dict[str, Any]:
    diagnostic_summary = (diagnosis or {}).get("summary") or {}
    improvements = list((script_report or {}).get("potential_improvements") or [])
    log_diagnostics = list((log_report or {}).get("diagnostics") or [])
    errors = [item for item in (diagnosis_error, script_error, log_error) if item]
    recommended = _analysis_recommended_actions(diagnosis, improvements, log_diagnostics, errors)
    status = "blocked" if errors else str(diagnostic_summary.get("status") or "analysis_complete")
    if status in {"no_clear_static_fault", "analysis_complete"} and (improvements or log_diagnostics):
        status = "needs_review"
    return {
        "analysis_version": ANALYSIS_REPORT_VERSION,
        "input": {
            "path": str(input_path) if input_path else "",
            "kind": input_path.suffix.lower() if input_path else "context",
        },
        "context": {
            "name": context.name if context else "",
            "root": str(context.root) if context else "",
        },
        "summary": {
            "status": status,
            "diagnosis_status": diagnostic_summary.get("status"),
            "diagnostic_findings": diagnostic_summary.get("finding_count", 0),
            "improvement_count": len(improvements),
            "log_diagnostic_count": len(log_diagnostics),
            "recommended_actions": recommended[:10],
            "errors": errors,
        },
        "diagnosis": diagnosis,
        "script_report": script_report,
        "log_report": log_report,
        "local_fluent_script": local_fluent_script,
        "artifacts": dict(artifacts),
    }

def _analysis_recommended_actions(
    diagnosis: dict[str, Any] | None,
    improvements: list[dict[str, Any]],
    log_diagnostics: list[dict[str, Any]],
    errors: list[str],
) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for error in errors:
        actions.append({"severity": "blocking", "title": "Analysis step failed", "detail": error})
    for item in ((diagnosis or {}).get("summary") or {}).get("top_likely_causes") or []:
        actions.append(
            {
                "severity": str(item.get("severity") or "info"),
                "title": str(item.get("title") or item.get("id") or "Diagnostic finding"),
                "detail": str(item.get("id") or ""),
            }
        )
    for item in log_diagnostics[:5]:
        actions.append(
            {
                "severity": str(item.get("severity") or "info"),
                "title": str(item.get("title") or item.get("id") or "Log diagnostic"),
                "detail": str(item.get("suggested_fix") or item.get("likely_workflow_defect") or ""),
            }
        )
    for item in improvements[:5]:
        actions.append(
            {
                "severity": str(item.get("severity") or "info"),
                "title": str(item.get("title") or "Potential improvement"),
                "detail": str(item.get("description") or ""),
            }
        )
    return actions

def _render_analysis_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# Fluent AI-Assistance Analysis",
        "",
        f"- Status: `{summary.get('status', 'unknown')}`",
        f"- Input: `{(report.get('input') or {}).get('path') or 'active/imported context'}`",
        f"- Context: `{(report.get('context') or {}).get('name') or 'none'}`",
        f"- Diagnostic findings: `{summary.get('diagnostic_findings', 0)}`",
        f"- Improvement suggestions: `{summary.get('improvement_count', 0)}`",
        f"- Log diagnostics: `{summary.get('log_diagnostic_count', 0)}`",
        "",
    ]
    local_script = report.get("local_fluent_script") or {}
    if local_script:
        lines.extend(
            [
                "## Local FluentControl Source",
                "",
                f"- Script: `{local_script.get('object_name') or ''}`",
                f"- Folder: `{local_script.get('object_path') or '<root>'}`",
                f"- Database file: `{local_script.get('path') or ''}`",
                f"- Staged copy: `{(report.get('input') or {}).get('path') or ''}`",
                "",
            ]
        )
    lines.extend(["## Recommended Actions", ""])
    actions = summary.get("recommended_actions") or []
    if actions:
        for item in actions:
            lines.append(f"- `{item.get('severity')}` {item.get('title')}")
            if item.get("detail"):
                lines.append(f"  - {item.get('detail')}")
    else:
        lines.append("- No high-confidence static issue was found. Review the generated artifacts before editing the script.")

    diagnosis = report.get("diagnosis") or {}
    top_causes = (diagnosis.get("summary") or {}).get("top_likely_causes") or []
    lines.extend(["", "## Debug Findings", ""])
    if top_causes:
        for item in top_causes:
            lines.append(f"- `{item.get('severity')}` {item.get('title')}")
    elif diagnosis:
        lines.append("- Diagnosis completed without high-confidence blocking or high-severity causes.")
    else:
        lines.append("- Diagnosis did not run.")

    script_report = report.get("script_report") or {}
    lines.extend(["", "## Script Summary", ""])
    what_it_does = script_report.get("what_it_does") or []
    if what_it_does:
        for item in what_it_does:
            lines.append(f"- {item}")
    else:
        lines.append("- No script summary was available.")

    lines.extend(["", "## Potential Improvements", ""])
    improvements = script_report.get("potential_improvements") or []
    if improvements:
        for item in improvements:
            lines.append(f"- `{item.get('severity')}` {item.get('title')}")
            for command in item.get("manual_commands") or []:
                lines.append(f"  - Manual check: {command}")
    else:
        lines.append("- No specific static improvement candidates were found.")

    log_report = report.get("log_report") or {}
    log_diagnostics = log_report.get("diagnostics") or []
    if log_report:
        lines.extend(["", "## FluentControl Logs", ""])
        lines.append(f"- Source: `{log_report.get('source') or ''}`")
        lines.append(f"- Parsed records: `{log_report.get('record_count', 0)}`")
        if log_diagnostics:
            for item in log_diagnostics[:10]:
                lines.append(f"- `{item.get('severity')}` {item.get('title')}")
        else:
            lines.append("- No known log diagnostic pattern matched.")

    artifacts = report.get("artifacts") or {}
    if artifacts:
        lines.extend(["", "## Artifacts", ""])
        for key, value in artifacts.items():
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines).rstrip() + "\n"

def _resolve_media_source(source: Path) -> tuple[Path, str]:
    """Locate the IR or media_placeholders.json for a map-media input."""
    if source.is_dir():
        irs = sorted(source.glob("*.protocol-ir.json"))
        if irs:
            return irs[0], "ir"
        placeholders = source / "media_placeholders.json"
        if placeholders.exists():
            return placeholders, "placeholders"
        raise FileNotFoundError(f"No *.protocol-ir.json or media_placeholders.json found in {source}")
    if source.name == "media_placeholders.json":
        return source, "placeholders"
    return source, "ir"

def _cmd_map_media(args: argparse.Namespace) -> int:
    cli = cli_module()
    ctx = cli._command_context(args.context)
    source = resolve_context_path(ctx, args.input)
    media_source, kind = _resolve_media_source(source)
    out_dir = source if source.is_dir() else media_source.parent

    if kind == "placeholders":
        data = json.loads(media_source.read_text(encoding="utf-8"))
        rows = data.get("prompts") if isinstance(data, dict) else None
        protocol_name = data.get("protocol") if isinstance(data, dict) else None
        subfolder = args.subfolder
        if not subfolder:
            subfolder = touchtools_media_subfolder(str(protocol_name or "script"))
        path_map = build_media_path_map_from_placeholder_rows(
            rows or [],
            args.touchtools_dir,
            subfolder=subfolder,
            protocol_name=protocol_name,
        )
    else:
        ir = load_protocol_ir(media_source)
        subfolder = args.subfolder
        if not subfolder:
            subfolder = resolve_touchtools_media_subfolder(ir)
        path_map = build_media_path_map(ir, args.touchtools_dir, subfolder=subfolder)

    if args.as_json:
        print(json.dumps(path_map, indent=2, sort_keys=True))
        return 0

    apply_xscr = getattr(args, "apply_xscr", None)
    if apply_xscr:
        xscr_path = resolve_user_path(apply_xscr)
        fixups = apply_touchtools_media_path_map_to_xscr(xscr_path, path_map)
        print(f"Applied {len(fixups)} prompt media path update(s) in {xscr_path}")
        for item in fixups[:10]:
            print(f"  - {item.get('from')} -> {item.get('to')}")
        if len(fixups) > 10:
            print(f"  ... and {len(fixups) - 10} more")

    output = resolve_context_path(ctx, args.output) if args.output else out_dir / "media_path_map.md"
    json_output = output.parent / "media_path_map.json"
    ensure_parent(output)
    output.write_text(render_media_path_map_markdown(path_map), encoding="utf-8")
    json_output.write_text(json.dumps(path_map, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Media path map: {output}")
    print(f"Media path map JSON: {json_output}")
    print(
        f"Mapped {path_map.get('image_count', 0)} image and "
        f"{path_map.get('video_count', 0)} video slot(s) under {path_map.get('touchtools_dir')}"
    )
    if ctx:
        print(f"Project context: {ctx.name}")
    return 0

def _cmd_process_media(args: argparse.Namespace) -> int:
    target = resolve_user_path(args.target)
    media_dir = target / "media" if (target / "media").is_dir() else target
    if not media_dir.is_dir():
        raise SystemExit(f"Media directory not found: {media_dir}")

    ir_path = resolve_user_path(args.ir) if args.ir else None
    ir_path = resolve_process_media_ir_path(target, ir_path)

    ir = load_protocol_ir(ir_path)
    build_dir = target if (target / "source").is_dir() else media_dir.parent
    unprocessed_dirs = [resolve_user_path(path) for path in args.unprocessed_dir]
    default_unprocessed = media_dir / "unprocessed"
    if default_unprocessed.is_dir() and default_unprocessed not in unprocessed_dirs:
        unprocessed_dirs.append(default_unprocessed)

    policy: dict[str, Any] = {}
    extra_source_dirs = [str(resolve_user_path(path)) for path in args.source_dir]
    if extra_source_dirs:
        policy["extra_source_dirs"] = extra_source_dirs

    if args.as_json:
        with contextlib.redirect_stdout(sys.stderr):
            report = process_prompt_media_captures(
                ir,
                media_dir,
                build_dir=build_dir if (build_dir / "source").is_dir() else None,
                policy=policy or None,
                unprocessed_dirs=unprocessed_dirs,
                finalize=not bool(args.no_finalize),
            )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    report = process_prompt_media_captures(
        ir,
        media_dir,
        build_dir=build_dir if (build_dir / "source").is_dir() else None,
        policy=policy or None,
        unprocessed_dirs=unprocessed_dirs,
        finalize=not bool(args.no_finalize),
    )
    resolved = int(report.get("resolved_count") or 0)
    converted = int(report.get("converted_count") or 0)
    normalized = int(report.get("normalized_count") or 0)
    failed = int(report.get("failed_count") or 0)
    staged = int(report.get("staged_count") or 0)
    print(
        f"Processed media: resolved={resolved} converted={converted} "
        f"normalized={normalized} staged={staged} failed={failed}"
    )
    report_path = report.get("report_json")
    if report_path:
        print(f"Media report JSON: {report_path}")
    return 0 if failed == 0 else 1

def _cmd_diagnose(args: argparse.Namespace) -> int:
    cli = cli_module()
    input_path = resolve_user_path(args.input)
    if args.input.suffix.lower() == ".zeia" or is_context_archive(input_path):
        ctx = None
    else:
        ctx = cli._command_context(args.context)
    error_text_parts = []
    if args.error_text:
        error_text_parts.append(args.error_text)
    if args.error_file:
        error_file = resolve_context_path(ctx, args.error_file) if ctx else resolve_user_path(args.error_file)
        error_text_parts.append(error_file.read_text(encoding="utf-8"))
    out_dir = resolve_context_path(ctx, args.out_dir) if args.out_dir and ctx else (resolve_user_path(args.out_dir) if args.out_dir else None)
    bundle = cli.diagnose_input(
        args.input,
        context=ctx,
        script=args.script,
        project_name=args.name,
        force_import=args.force_import,
        snapshot_archives=[resolve_user_path(path) for path in args.snapshot],
        error_text="\n".join(error_text_parts).strip(),
        out_dir=out_dir,
    )
    if args.as_json:
        print(json.dumps(bundle.report, indent=2, sort_keys=True))
        return 0

    summary = bundle.report["summary"]
    print(f"Diagnosis: {summary['status']} ({summary['finding_count']} finding(s))")
    print(f"Diagnosis report: {bundle.report_path}")
    print(f"Diagnosis JSON: {bundle.json_path}")
    for item in summary.get("top_likely_causes") or []:
        print(f"  [{item['severity']}] {item['title']}")
    return 0

def _cmd_parse_fluent_log(args: argparse.Namespace) -> int:
    cli = cli_module()
    request = cli.log_analysis_request_from_cli(args)
    result = cli.analyze_logs(request)
    cli.print_log_analysis_result(result, as_json=args.as_json)
    return cli.log_analysis_exit_code(result)

def _cmd_template_list(args: argparse.Namespace) -> int:
    templates = list_templates()
    if args.as_json:
        print(json.dumps({"templates": templates}, indent=2, sort_keys=True))
        return 0
    if not templates:
        print("No protocol templates found.")
        return 0
    for item in templates:
        print(f"{item['name']}: steps={item['step_count']}  {item.get('description', '')}")
    return 0

def _cmd_template_info(args: argparse.Namespace) -> int:
    info = template_info(args.name)
    if args.as_json:
        print(json.dumps(info, indent=2, sort_keys=True))
        return 0
    print(f"Template: {info['name']}")
    print(f"  Protocol: {info.get('protocol_name')}")
    print(f"  Steps:    {info.get('step_count')}")
    print(f"  Labware:  {info.get('labware_count')}")
    print(f"  IR:       {info.get('template_ir')}")
    print(f"  Schema:   {info.get('request_schema')}")
    print(f"  Valid:    {info.get('valid')}")
    if info.get("examples"):
        print("  Examples:")
        for example in info["examples"]:
            print(f"    {example}")
    if info.get("issues"):
        print("  Issues:")
        for issue in info["issues"]:
            print(f"    {issue.get('path')}: {issue.get('message')}")
    return 0 if info.get("valid") else 1
