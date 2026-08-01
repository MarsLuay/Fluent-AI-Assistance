"""Shared bootstrap status payload for MCP and CLI."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .agent_brief import render_agent_brief
from .config import SHARED_TEMP_DIR
from .project_context import list_projects
from .runner import PipelineError

# All MCP tools that participate in the bootstrap gate (keep sorted).
_ALL_GATED_TOOLS: tuple[str, ...] = (
        "fluent_agent_brief",
        "fluent_apply_repair",
        "fluent_bootstrap_status",
        "fluent_create_request_spec",
        "fluent_diagnose",
        "fluent_find_external_command",
        "fluent_generate_protocol",
        "fluent_import_project",
        "fluent_inspect_project",
        "fluent_inspect_script",
        "fluent_list_capabilities",
        "fluent_list_projects",
        "fluent_parse_fluent_log",
        "fluent_plan_repair",
        "fluent_process_media",
        "fluent_project_query",
        "fluent_resolve_brief_mode",
        "fluent_run_opt_in_cli",
        "fluent_run_safe_cli",
        "fluent_status",
        "fluent_summarize_simulation",
        "fluent_validate_request_spec",
        "fluent_verify_archive",
        "fluent_verify_bundle",
        "fluent_worktable_diff",
    )

_META_TOOLS: frozenset[str] = frozenset(
    {
        "fluent_status",
        "fluent_agent_brief",
        "fluent_resolve_brief_mode",
        "fluent_bootstrap_status",
        "fluent_list_capabilities",
        "fluent_list_projects",
    }
)

_INSPECT_TOOLS: frozenset[str] = frozenset(
    {
        "fluent_inspect_project",
        "fluent_project_query",
        "fluent_inspect_script",
        "fluent_find_external_command",
        "fluent_diagnose",
        "fluent_parse_fluent_log",
        "fluent_verify_archive",
        "fluent_import_project",
    }
)


def _tool_gate(allowed: frozenset[str]) -> tuple[list[str], list[str]]:
    allowed_sorted = sorted(allowed)
    blocked_sorted = sorted(set(_ALL_GATED_TOOLS) - allowed)
    return allowed_sorted, blocked_sorted


def next_bootstrap_step(
    *,
    doctor_ok: bool,
    projects: list[dict[str, Any]],
    inspected: bool = False,
) -> dict[str, Any]:
    """Choose the next required tool/CLI step after doctor + project inventory."""
    if not doctor_ok:
        allowed, blocked = _tool_gate(_META_TOOLS)
        return {
            "action": "fix_doctor",
            "tool": "fluent_bootstrap_status",
            "cli": "bootstrap-status --install-missing --confirm-install",
            "arguments": {"install_missing": True, "confirm_install": True},
            "brief_mode": "status",
            "reason": "one or more doctor checks failed; repair the local env before protocol work",
            "allowed_tools": allowed,
            "blocked_tools": blocked,
            "unlock_generate_after": ["fluent_bootstrap_status"],
        }
    if not projects:
        allowed, blocked = _tool_gate(_META_TOOLS | {"fluent_import_project", "fluent_verify_archive"})
        return {
            "action": "import_project",
            "tool": "fluent_import_project",
            "cli": "import-project <path-to-user-zeia>",
            "arguments": {"archive": "<path-to-user-zeia>"},
            "brief_mode": "new-script",
            "reason": "toolchain is healthy but no imported ZEIA contexts exist yet",
            "allowed_tools": allowed,
            "blocked_tools": blocked,
            "unlock_generate_after": ["fluent_import_project", "fluent_inspect_project"],
        }
    if not inspected:
        allowed, blocked = _tool_gate(_META_TOOLS | _INSPECT_TOOLS)
        return {
            "action": "inspect_project",
            "tool": "fluent_inspect_project",
            "cli": "project-info",
            "arguments": {"name": projects[0].get("name")},
            "brief_mode": "new-script",
            "reason": (
                f"{len(projects)} imported context(s) ready; inspect the ZEIA/"
                "scripts before request-spec or generate "
                "(re-call bootstrap with inspected=true after inspect)"
            ),
            "allowed_tools": allowed,
            "blocked_tools": blocked,
            "unlock_generate_after": ["fluent_inspect_project"],
        }
    allowed, blocked = _tool_gate(frozenset(_ALL_GATED_TOOLS))
    return {
        "action": "choose_workflow",
        "tool": "fluent_agent_brief",
        "cli": "python3 scripts/agent/agent-brief.py --mode new-script",
        "arguments": {"mode": "new-script"},
        "brief_mode": "new-script",
        "reason": (
            f"toolchain healthy with {len(projects)} imported context(s) and "
            "inspect attested; ask the user for the ZEIA/script goal, then follow new-script brief"
        ),
        "allowed_tools": allowed,
        "blocked_tools": blocked,
        "unlock_generate_after": [],
    }


def build_bootstrap_status(
    *,
    install_missing: bool = False,
    confirm_install: bool = False,
    write_report: bool = True,
    inspected: bool = False,
) -> dict[str, Any]:
    """Run doctor + list-projects and return the shared bootstrap payload."""
    if install_missing and not confirm_install:
        raise PipelineError("install_missing requires confirm_install=true")

    # Lazy import: fluent_pipeline.cli package init imports mcp_gateway.
    from .cli.commands.doctor import collect_doctor_checks

    checks = collect_doctor_checks(install_missing=install_missing)
    serializable_checks = [
        {
            "name": check.get("name"),
            "ok": bool(check.get("ok")),
            "detail": str(check.get("detail") or ""),
        }
        for check in checks
    ]
    doctor_ok = all(item["ok"] for item in serializable_checks)
    report_path: Path | None = None
    if write_report:
        from .reports import render_doctor_markdown

        report_path = SHARED_TEMP_DIR / "logs" / "doctor.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_doctor_markdown(checks), encoding="utf-8")

    projects = list_projects()
    next_step = next_bootstrap_step(
        doctor_ok=doctor_ok,
        projects=projects,
        inspected=inspected,
    )
    return {
        "ok": doctor_ok,
        "doctor_ok": doctor_ok,
        "doctor_checks": serializable_checks,
        "doctor_report": str(report_path) if report_path is not None else None,
        "project_count": len(projects),
        "projects": projects,
        "inspected": bool(inspected),
        "next_step": next_step,
        "brief": render_agent_brief(next_step["brief_mode"]),
    }
