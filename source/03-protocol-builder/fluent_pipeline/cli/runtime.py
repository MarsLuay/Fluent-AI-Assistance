"""CLI for the local Codex-friendly Fluent protocol builder."""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..aliases import alias_records, load_alias_maps, normalize_protocol_ir_aliases, resolve_alias
from ..bundle_lifecycle import (
    DEFAULT_PROBE_ROOT,
    archive_recommended_bundles,
    render_bundle_index,
    scan_bundle_lifecycle,
)
from .. import bootstrap
from ..config import (
    DEFAULT_FLUENTCODER_PYTHON,
    PROJECT_DIR,
    READY_TO_IMPORT_DIR,
    TEMP_FILES_DIRNAME,
    TECAN_AI_DIR,
    fluentcoder_python,
    fluentcoder_root,
    resolve_user_path,
    workflow_event_log_path,
)
from ..compatibility import (
    TargetSetup,
    build_compatibility_report,
    current_manual_target,
    render_compatibility_markdown,
)
from ..bundle_media import process_prompt_media_captures
from ..determinism import compare_run_dirs, render_determinism_report
from ..diagnostics import diagnose_input
from ..external_commands import (
    inspect_external_command,
    render_external_command_contract_markdown,
    write_external_command_contract,
)
from ..fluent_library import resolve_local_fluent_script, stage_local_fluent_script
from ..mcp_gateway import resolve_process_media_ir_path
from ..fluent_log_parser import (
    build_fluent_log_report,
    build_latest_fluent_log_report,
    render_fluent_log_report_markdown,
    report_to_json,
)
from ..generation_workflow import run_generation_workflow
from ..project_catalog import ensure_project_catalog
from ..protocol_ir import (
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
from ..request_spec import (
    build_request_spec,
    load_request_spec,
    request_spec_generation_defaults,
    write_request_spec,
)
from ..project_context import (
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
from ..reports import (
    compact_simulation,
    render_compile_markdown,
    render_doctor_markdown,
    render_roundtrip_markdown,
    render_simulation_markdown,
)
from ..repair import apply_repair_plan, build_repair_plan, render_repair_markdown
from ..runner import (
    PipelineError,
    ensure_parent,
    parse_json_stdout,
    run_fluentcoder,
    run_fluentcoder_with_log_watch,
    run_python,
    write_json,
)
from ..runtime_bridge import (
    FluentContextCheckConfig,
    render_fluent_context_check_markdown,
    run_fluent_context_check,
)
from ..script_analysis import analyze_script, render_script_analysis_markdown
from ..spec_lint import lint_request_spec_file, render_lint_report
from ..template_library import list_templates, template_info
from ..worktable_diff import (
    diff_worktable_requirements,
    render_worktable_changes_markdown,
    render_worktable_patch_json,
)
ANALYSIS_REPORT_VERSION = "tecan.analysis_report.v1"
_VERSIONED_FOLDER_RE = re.compile(r"^(?P<base>.+)_v(?P<version>\d+)$", re.IGNORECASE)


def cli_module():
    """Return the CLI package so command handlers can follow package-level patches."""
    return importlib.import_module("fluent_pipeline.cli")


def main(argv: list[str] | None = None) -> int:
    from .parser import _build_parser

    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except PipelineError as exc:
        print(f"Pipeline error: {exc}", file=sys.stderr)
        return 2


def _command_context(name: str | None) -> ProjectContext | None:
    if name:
        return load_project(name)
    active = active_project_name()
    if active:
        return load_project(active)
    return None

def _default_output(ctx: ProjectLike | None, folder: str, filename: str) -> Path:
    if ctx is None:
        return (READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / folder / filename).resolve()
    folders = {
        "drafts": ctx.drafts_dir,
        "build": ctx.build_dir,
        "reports": ctx.reports_dir,
        "roundtrips": ctx.roundtrips_dir,
    }
    return (folders[folder] / filename).resolve()


def _resolve_artifact_output_path(value: str | Path, *, ctx: ProjectLike | None = None) -> Path:
    """Resolve a user-selected intermediate output inside a project temp_files folder."""
    output = resolve_context_path(ctx, value) if ctx is not None else resolve_user_path(value)
    try:
        ready_relative = output.relative_to(READY_TO_IMPORT_DIR.resolve())
    except ValueError as exc:
        raise PipelineError(
            f"workflow artifacts must be written under ready-to-import/<project>/{TEMP_FILES_DIRNAME}/: {output}."
        ) from exc
    if len(ready_relative.parts) < 2 or ready_relative.parts[1] != TEMP_FILES_DIRNAME:
        raise PipelineError(
            f"workflow artifacts must be written under ready-to-import/<project>/{TEMP_FILES_DIRNAME}/: {output}."
        )
    return output

def _default_generation_dir(ctx: ProjectLike | None, intent: str) -> Path:
    label = _safe_output_label(intent)
    parent = (
        (ctx.build_dir / "generations").resolve()
        if ctx is not None
        else (READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "build" / "generations").resolve()
    )
    return _next_versioned_output_dir(parent, label)


def _split_versioned_output_label(label: str) -> tuple[str, int | None]:
    match = _VERSIONED_FOLDER_RE.fullmatch(label)
    if match is None:
        return label, None
    return match.group("base"), int(match.group("version"))


def _next_versioned_output_dir(parent: Path, label: str) -> Path:
    family_base, requested_version = _split_versioned_output_label(label)
    pattern = re.compile(rf"^{re.escape(family_base)}(?:_v(\d+))?$", re.IGNORECASE)
    highest_version = 0
    if parent.exists():
        for child in parent.iterdir():
            if not child.is_dir():
                continue
            match = pattern.fullmatch(child.name)
            if match is None:
                continue
            highest_version = max(highest_version, int(match.group(1) or 1))
    next_version = max(highest_version + 1 if highest_version else 1, requested_version or 1)
    return (parent / f"{family_base}_v{next_version}").resolve()

def _simulator_app_dir() -> Path:
    candidates = [
        TECAN_AI_DIR / "source" / "04-protocol-simulator",
        PROJECT_DIR.parent / "04-protocol-simulator",
        PROJECT_DIR.parent / "source" / "04-protocol-simulator",
    ]
    for candidate in candidates:
        if (candidate / "package.json").exists():
            return candidate.resolve()
    return candidates[0].resolve()

def _safe_output_label(value: str) -> str:
    keep = []
    for char in value.lower().strip():
        if char.isalnum():
            keep.append(char)
        elif char in {" ", "_", "-"}:
            keep.append("-")
    label = "".join(keep).strip("-")
    while "--" in label:
        label = label.replace("--", "-")
    return label[:80] or "generated-protocol"

def _generation_return_code(manifest: dict[str, Any]) -> int:
    published = Path(str(manifest.get("published_zeia_path") or ""))
    if (
        manifest.get("ready_to_import")
        and published.suffix.lower() == ".zeia"
        and published.parent.name == published.stem
        and published.exists()
        and (published.parent / "RECREATE_SCRIPT.md").exists()
        and (published.parent / "generation_manifest.json").exists()
        and (published.parent / "GENERATION_WORKFLOW.md").exists()
        and (published.parent / "request.spec.yaml").exists()
        and (published.parent / "protocol.ir.json").exists()
        and (published.parent / "generated" / "protocol.py").exists()
        and (published.parent / "reports").exists()
    ):
        return 0
    return 1

def _manifest_simulator_bundle(manifest: dict[str, Any]) -> Path:
    published_zeia = manifest.get("published_zeia_path")
    if published_zeia:
        return Path(str(published_zeia)).resolve()
    artifacts = [Path(str(path)) for path in (manifest.get("ready_to_import_artifacts") or []) if str(path).strip()]
    artifact_parents = [path.parent for path in artifacts]
    if artifact_parents:
        first = artifact_parents[0]
        if all(parent == first for parent in artifact_parents):
            return first.resolve()
        try:
            return Path(os.path.commonpath([str(parent) for parent in artifact_parents])).resolve()
        except ValueError:
            return first.resolve()
    return Path(str(manifest.get("out_dir") or ".")).resolve()

def _related_reports(ctx: ProjectLike | None, protocol: Path) -> list[Path]:
    candidates: list[Path] = []
    search_dirs = [protocol.parent]
    if ctx is not None:
        search_dirs.append(ctx.reports_dir)
    else:
        search_dirs.append(Path.cwd() / "build")

    seen: set[Path] = set()
    for folder in search_dirs:
        if not folder.exists():
            continue
        for suffix in ("*.md", "*.json"):
            for path in folder.glob(f"{protocol.stem}*{suffix[1:]}"):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    candidates.append(resolved)
    return candidates

def _context_source_projects(ctx: ProjectLike | None) -> list[Path]:
    if ctx is None:
        return []
    raw_values = []
    raw_values.extend(ctx.manifest.get("copied_archives") or [])
    raw_values.extend(ctx.manifest.get("source_archives") or [])
    raw = ctx.manifest.get("copied_archive") or ctx.manifest.get("source_archive")
    if raw:
        raw_values.append(raw)

    paths = []
    seen = set()
    for raw_value in raw_values:
        path = Path(str(raw_value))
        if not path.exists():
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        paths.append(resolved)
    return paths

def _context_project_report(ctx: ProjectLike | None) -> dict[str, Path]:
    if ctx is None:
        return {}
    report = ctx.root / "project_report.md"
    return {"project_report": report} if report.exists() else {}

def _run_cli_fluent_context_check(
    args: argparse.Namespace | None,
    *,
    xscr_path: Path,
    fallback_method: str,
    report_base: Path,
) -> tuple[dict[str, Any] | None, Path | None, Path | None]:
    if args is None or not getattr(args, "fluent_context_check", False):
        return None, None, None
    if not xscr_path.exists():
        print("FluentControl import/load diagnostic skipped: compiled XSCR is missing.", file=sys.stderr)
        return None, None, None
    method = getattr(args, "fluent_method", None) or fallback_method
    result = run_fluent_context_check(
        FluentContextCheckConfig(
            method=method,
            xscr_path=xscr_path,
            provider=getattr(args, "fluent_provider", "auto"),
            host=getattr(args, "fluent_host", "127.0.0.1"),
            port=getattr(args, "fluent_port", 50052),
            insecure=getattr(args, "fluent_insecure", False),
            timeout_seconds=getattr(args, "fluent_timeout", 180.0),
            command=getattr(args, "fluent_command", None),
            close_method=not getattr(args, "no_fluent_close_method", False),
        )
    )
    json_path = report_base.with_name(f"{report_base.name}.fluent-context-check.json")
    report_path = report_base.with_name(f"{report_base.name}.fluent-context-check.md")
    write_json(json_path, result)
    ensure_parent(report_path)
    report_path.write_text(render_fluent_context_check_markdown(result), encoding="utf-8")
    print(f"FluentControl import/load diagnostic report: {report_path}")
    if not result.get("ok"):
        print(f"FluentControl import/load diagnostic failed: {result.get('summary')}", file=sys.stderr)
    return result, report_path, json_path

def _fluent_method_from_artifact(path: Path, fallback: str) -> str:
    try:
        ir = protocol_ir_from_path(path)
    except Exception:
        return fallback
    return _fluent_method_from_ir(ir, fallback)

def _fluent_method_from_ir(ir: dict[str, Any], fallback: str) -> str:
    protocol = ir.get("protocol") if isinstance(ir, dict) else {}
    method = str((protocol or {}).get("name") or "").strip()
    return method or fallback

def _simulate_protocol(
    protocol: Path,
    *,
    catalog_db: Path | None = None,
    strict: bool = False,
    fail_on_opaque: bool = False,
    min_coverage: float | None = None,
) -> tuple[Any, dict[str, Any] | None]:
    command: list[str | Path] = ["simulate", protocol, "--json"]
    if fail_on_opaque:
        command.append("--fail-on-opaque")
    if min_coverage is not None:
        command.extend(["--min-coverage", str(min_coverage)])
    if strict:
        command.append("--strict")
    result = run_fluentcoder(command, catalog_db=catalog_db)
    try:
        data = parse_json_stdout(result)
    except json.JSONDecodeError:
        data = None
    return result, data

def _simulate_protocol_with_log_watch(
    protocol: Path,
    *,
    catalog_db: Path | None = None,
    strict: bool = False,
    fail_on_opaque: bool = False,
    min_coverage: float | None = None,
    log_path: Path,
    script_filter: str | None = None,
) -> tuple[Any, dict[str, Any] | None, Any]:
    command: list[str | Path] = ["simulate", protocol, "--json"]
    if fail_on_opaque:
        command.append("--fail-on-opaque")
    if min_coverage is not None:
        command.extend(["--min-coverage", str(min_coverage)])
    if strict:
        command.append("--strict")
    watch = run_fluentcoder_with_log_watch(
        command,
        catalog_db=catalog_db,
        log_path=log_path,
        script_filter=script_filter,
    )
    try:
        data = parse_json_stdout(watch.result)
    except json.JSONDecodeError:
        data = None
    return watch.result, data, watch

def _render_log_watch_markdown(protocol: Path, watch: Any) -> str:
    lines = [
        "# Simulation Log Watch",
        "",
        f"- Protocol: `{protocol}`",
        f"- Log file: `{watch.log_path}`",
        f"- Script filter: `{watch.script_filter or 'none'}`",
        f"- Captured lines: `{len(watch.captured_lines)}`",
        f"- Command: `{watch.result.command_line()}`",
        f"- Exit code: `{watch.result.returncode}`",
        "",
    ]
    if watch.notes:
        lines.extend(["## Notes", ""])
        for note in watch.notes:
            lines.append(f"- {note}")
        lines.append("")
    lines.extend(["## Captured Lines", ""])
    if watch.captured_lines:
        lines.append("```text")
        lines.extend(watch.captured_lines)
        lines.append("```")
    else:
        lines.append("- No matching appended log lines were captured while the simulation process ran.")
    return "\n".join(lines).rstrip() + "\n"

def _write_roundtrip_report(path: Path, source: Path, stages: list[dict[str, Any]]) -> None:
    ensure_parent(path)
    path.write_text(render_roundtrip_markdown(source, stages), encoding="utf-8")

def _result_check(name: str, result: Any) -> dict[str, Any]:
    detail = (result.stdout or result.stderr).strip().splitlines()
    return {
        "name": name,
        "ok": result.ok,
        "detail": detail[0] if detail else "",
        "result": result,
    }

def _print_process(result: Any) -> None:
    if result.stdout.strip():
        print(result.stdout.rstrip())
    if result.stderr.strip():
        print(result.stderr.rstrip(), file=sys.stderr)

def _print_repair_plan(plan: Any) -> None:
    data = plan.to_dict()
    summary = data["summary"]
    print(
        "Repair plan: "
        f"actions={summary['action_count']}; "
        f"ready={summary['ready_count']}; "
        f"suggested={summary['suggested_count']}; "
        f"needs_review={summary['needs_review_count']}"
    )
    for action in data["actions"]:
        line = f" line={action['line']}" if action.get("line") else ""
        command = f" command={action['command_id']}" if action.get("command_id") else ""
        print(f"  [{action['status']}] {action['kind']}{line}{command}: {action['summary']}")
