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
from ...project_catalog import ensure_project_catalog, project_datastore_dir
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


def _result_check(name: str, result: Any) -> dict[str, Any]:
    detail = (result.stdout or result.stderr).strip().splitlines()
    return {
        "name": name,
        "ok": result.ok,
        "detail": detail[0] if detail else "",
        "result": result,
    }



def _global_catalog_db_path() -> Path:
    return fluentcoder_root() / "fluentcoder" / "catalog" / "install_index.db"


def _catalog_info_ok() -> bool:
    return bool(run_fluentcoder(["catalog", "info"]).ok)


def _fc_install_with_components() -> Path | None:
    """Return FluentControl install root when Components/ exists (env or default)."""
    try:
        from fluentcoder.catalog.indexer import install_path_default
    except Exception:
        return None
    install = Path(install_path_default())
    components = install / "SystemSpecific" / "Worktable" / "Components"
    if components.is_dir():
        return install
    return None


def _project_catalog_refresh_sources() -> list[tuple[str, Path]]:
    """Prefer active project DataStore, then other imported contexts."""
    sources: list[tuple[str, Path]] = []
    seen: set[str] = set()

    def add(name: str, datastore: Path | None) -> None:
        if datastore is None:
            return
        key = str(datastore.resolve())
        if key in seen:
            return
        seen.add(key)
        sources.append((name, datastore))

    active = active_project_name()
    if active:
        try:
            add(active, project_datastore_dir(load_project(active)))
        except Exception:
            pass
    for project in list_projects():
        name = str(project.get("name") or "").strip()
        if not name or name == active:
            continue
        try:
            add(name, project_datastore_dir(load_project(name)))
        except Exception:
            continue
    return sources


def _copy_catalog_db(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        src = Path(f"{source}{suffix}") if suffix else source
        dst = Path(f"{destination}{suffix}") if suffix else destination
        if src.exists():
            shutil.copy2(src, dst)


def ensure_global_catalog_index(*, force_refresh: bool = False) -> dict[str, Any]:
    """Heal an empty global fluentcoder catalog for doctor/bootstrap.

    Order:
    1. Reuse/copy an active (or any) project-local catalog DB (fast).
    2. Refresh from a FluentControl install with Components/.
    3. Refresh from a ZEIA-extracted DataStore when no FC install exists.
    """
    if not force_refresh and _catalog_info_ok():
        return {"ok": True, "action": "already_populated", "detail": "global catalog index already populated"}

    global_db = _global_catalog_db_path()

    # Fast path: project-local catalog already built for generation.
    for name, _datastore in _project_catalog_refresh_sources():
        try:
            ctx = load_project(name)
            project_db = ensure_project_catalog(ctx)
        except Exception:
            continue
        if project_db is None or not project_db.exists():
            continue
        _copy_catalog_db(project_db, global_db)
        if _catalog_info_ok():
            return {
                "ok": True,
                "action": "copied_project_catalog",
                "detail": f"copied project catalog from `{name}` into `{global_db}`",
                "source": name,
                "db": str(global_db),
            }

    install = _fc_install_with_components()
    if install is not None:
        result = run_fluentcoder(
            ["catalog", "refresh", "--install", str(install), "--db", str(global_db)],
            timeout=600,
        )
        if result.ok and _catalog_info_ok():
            return {
                "ok": True,
                "action": "refreshed_fc_install",
                "detail": f"refreshed global catalog from FluentControl install `{install}`",
                "source": str(install),
                "db": str(global_db),
            }

    for name, datastore in _project_catalog_refresh_sources():
        result = run_fluentcoder(
            ["catalog", "refresh", "--install", str(datastore), "--db", str(global_db)],
            timeout=600,
        )
        if result.ok and _catalog_info_ok():
            return {
                "ok": True,
                "action": "refreshed_zeia_datastore",
                "detail": f"refreshed global catalog from ZEIA DataStore `{name}`",
                "source": str(datastore),
                "db": str(global_db),
            }

    return {
        "ok": False,
        "action": "unavailable",
        "detail": (
            "Catalog index is empty and no FluentControl install or imported ZEIA "
            "DataStore was available to refresh. Import a full ZEIA, set "
            "FLUENTCODER_FC_INSTALL, or run `python -m fluentcoder.cli catalog refresh`."
        ),
    }


def collect_doctor_checks(*, install_missing: bool = False) -> list[dict[str, Any]]:
    """Run doctor checks and return structured results (no printing)."""
    if install_missing:
        _install_local_dependencies()

    catalog_heal = ensure_global_catalog_index()

    checks: list[dict[str, Any]] = []

    root = fluentcoder_root()
    python = fluentcoder_python(root)
    checks.append(
        {
            "name": "fluentcoder root",
            "ok": root.exists(),
            "detail": f"`{root}`",
        }
    )
    checks.append(
        {
            "name": "shared repo venv python",
            "ok": python.exists(),
            "detail": f"`{python}`",
        }
    )

    if root.exists() and python.exists():
        checks.append(_result_check("Python version", run_python(["--version"])))
        checks.append(
            _result_check(
                "fluentcoder import",
                run_python(["-c", "import fluentcoder; print('fluentcoder import ok')"]),
            )
        )
        lxml = run_python(
            [
                "-c",
                "import importlib.util; "
                "print('present' if importlib.util.find_spec('lxml') else 'missing')",
            ]
        )
        lxml_detail = (lxml.stdout or lxml.stderr).strip()
        if lxml_detail == "missing":
            lxml_detail = (
                "missing; acceptable for this wrapper's tested decompile/simulate/compile "
                "path on Python 3.14"
            )
        checks.append(
            {
                "name": "lxml availability",
                "ok": True,
                "detail": lxml_detail,
                "result": lxml,
            }
        )
        catalog_check = _result_check("catalog info", run_fluentcoder(["catalog", "info"]))
        if catalog_heal.get("action") not in {None, "already_populated"}:
            heal_detail = str(catalog_heal.get("detail") or "").strip()
            if heal_detail:
                existing = catalog_check.get("detail") or ""
                catalog_check["detail"] = (
                    f"{existing} ({heal_detail})" if existing else heal_detail
                )
            catalog_check["heal"] = catalog_heal
        if not catalog_check["ok"] and not catalog_heal.get("ok"):
            catalog_check["detail"] = str(catalog_heal.get("detail") or catalog_check.get("detail") or "")
        checks.append(catalog_check)
        checks.append(_catalog_workspace_files_check())

    api_keys = [name for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY") if os.environ.get(name)]
    checks.append(
        {
            "name": "API key usage",
            "ok": True,
            "detail": (
                "none detected; this wrapper does not call author/chat"
                if not api_keys
                else f"present but unused by this wrapper: {', '.join(api_keys)}"
            ),
        }
    )
    return checks


def _cmd_doctor(args: argparse.Namespace) -> int:
    checks = collect_doctor_checks(install_missing=bool(args.install_missing))

    if args.report:
        report = resolve_user_path(args.report)
        ensure_parent(report)
        report.write_text(render_doctor_markdown(checks), encoding="utf-8")

    for check in checks:
        prefix = "OK" if check["ok"] else "FAIL"
        detail = check.get("detail") or ""
        print(f"[{prefix}] {check['name']}: {detail}")
    if args.report:
        print(f"Doctor report: {resolve_user_path(args.report)}")
    return 0 if all(check["ok"] for check in checks) else 1


def _cmd_bootstrap_status(args: argparse.Namespace) -> int:
    from ...bootstrap_status import build_bootstrap_status
    from ...runner import PipelineError

    try:
        payload = build_bootstrap_status(
            install_missing=bool(args.install_missing),
            confirm_install=bool(args.confirm_install),
            write_report=not bool(args.no_report),
            inspected=bool(getattr(args, "inspected", False)),
        )
    except PipelineError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(payload, indent=2, default=str))
    if payload.get("doctor_report"):
        print(f"Doctor report: {payload['doctor_report']}", file=sys.stderr)
    next_step = payload.get("next_step") or {}
    if next_step.get("cli"):
        print(f"Next: {next_step.get('action')} -> {next_step['cli']}", file=sys.stderr)
    return 0 if payload.get("ok") else 1


def _catalog_workspace_files_check() -> dict[str, Any]:
    active_catalog = _active_project_catalog_check()
    if active_catalog is not None:
        return active_catalog

    db_path = fluentcoder_root() / "fluentcoder" / "catalog" / "install_index.db"
    if not db_path.exists():
        return {
            "name": "catalog workspace files",
            "ok": False,
            "detail": f"catalog index DB not found: `{db_path}`",
        }
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT guid, name, file_path FROM workspaces ORDER BY name").fetchall()
    except sqlite3.DatabaseError as exc:
        return {
            "name": "catalog workspace files",
            "ok": False,
            "detail": f"could not inspect workspace paths in `{db_path}`: {exc}",
        }

    if not rows:
        return {
            "name": "catalog workspace files",
            "ok": False,
            "detail": "no workspace rows found; workspace-bound drafts cannot simulate or compile",
        }

    missing = [
        {"guid": str(row[0]), "name": str(row[1]), "file_path": Path(str(row[2]))}
        for row in rows
        if not Path(str(row[2])).exists()
    ]
    if missing:
        examples = "; ".join(
            f"{item['name']} ({item['guid']}): `{item['file_path']}`"
            for item in missing[:3]
        )
        more = "" if len(missing) <= 3 else f"; plus {len(missing) - 3} more"
        return {
            "name": "catalog workspace files",
            "ok": False,
            "detail": f"missing {len(missing)} of {len(rows)} workspace file(s): {examples}{more}",
        }

    return {
        "name": "catalog workspace files",
        "ok": True,
        "detail": f"all {len(rows)} indexed workspace file(s) exist",
    }

def _active_project_catalog_check() -> dict[str, Any] | None:
    active = active_project_name()
    if not active:
        return None
    try:
        ctx = load_project(active)
        db_path = ensure_project_catalog(ctx)
    except Exception as exc:
        return {
            "name": "active project catalog files",
            "ok": False,
            "detail": f"active project `{active}` catalog could not be prepared: {exc}",
        }
    if db_path is None:
        return None
    return _workspace_files_check_from_db(
        db_path,
        name="active project catalog files",
        prefix=f"active project `{active}` catalog `{db_path}`",
    )

def _workspace_files_check_from_db(db_path: Path, *, name: str, prefix: str) -> dict[str, Any]:
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT guid, name, file_path FROM workspaces ORDER BY name").fetchall()
    except sqlite3.DatabaseError as exc:
        return {
            "name": name,
            "ok": False,
            "detail": f"could not inspect workspace paths in `{db_path}`: {exc}",
        }

    if not rows:
        return {
            "name": name,
            "ok": False,
            "detail": f"{prefix} has no workspace rows; workspace-bound drafts cannot simulate or compile",
        }

    missing = [
        {"guid": str(row[0]), "name": str(row[1]), "file_path": Path(str(row[2]))}
        for row in rows
        if not Path(str(row[2])).exists()
    ]
    if missing:
        examples = "; ".join(
            f"{item['name']} ({item['guid']}): `{item['file_path']}`"
            for item in missing[:3]
        )
        more = "" if len(missing) <= 3 else f"; plus {len(missing) - 3} more"
        return {
            "name": name,
            "ok": False,
            "detail": f"{prefix} is missing {len(missing)} of {len(rows)} workspace file(s): {examples}{more}",
        }

    return {
        "name": name,
        "ok": True,
        "detail": f"{prefix}: all {len(rows)} indexed workspace file(s) exist",
    }

def _cmd_setup(args: argparse.Namespace) -> int:
    _install_local_dependencies()
    return _cmd_doctor(argparse.Namespace(report=None, install_missing=False))

def _install_local_dependencies() -> None:
    root = fluentcoder_root()
    if not root.exists():
        raise PipelineError(f"fluentcoder root not found: {root}")

    python = fluentcoder_python(root)
    if os.environ.get("FLUENTCODER_PYTHON") and not python.exists():
        raise PipelineError(
            f"FLUENTCODER_PYTHON points to a missing executable: {python}. "
            "Unset FLUENTCODER_PYTHON or create that environment manually."
        )

    venv_dir = DEFAULT_FLUENTCODER_PYTHON.parent.parent
    if not python.exists():
        print(f"Creating shared repository virtual environment: {venv_dir}")
        bootstrap._run_setup_command([sys.executable, "-m", "venv", str(venv_dir)], cwd=PROJECT_DIR)
        python = fluentcoder_python(root)
    bootstrap.bootstrap_workspace(python)


def _cmd_compatibility_matrix(args: argparse.Namespace) -> int:
    target = None
    if args.fluentcontrol_version:
        current_manual = current_manual_target()
        is_current_manual = args.fluentcontrol_version == current_manual.fluentcontrol_version
        target = TargetSetup(
            fluentcontrol_version=args.fluentcontrol_version,
            fluentcontrol_build=args.fluentcontrol_build,
            manual_version=args.manual_version or (current_manual.manual_version if is_current_manual else "unknown"),
            windows_environment=args.windows_environment
            or (current_manual.windows_environment if is_current_manual else "unknown"),
        )
    report = build_compatibility_report(connector=args.connector, target=target)
    rendered = render_compatibility_markdown(report)

    if args.json_out:
        json_out = resolve_user_path(args.json_out)
        write_json(json_out, report)
        print(f"Compatibility matrix JSON: {json_out}")

    if args.report:
        report_path = resolve_user_path(args.report)
        ensure_parent(report_path)
        report_path.write_text(rendered, encoding="utf-8")
        print(f"Compatibility matrix report: {report_path}")

    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(rendered.rstrip())
    return 0
