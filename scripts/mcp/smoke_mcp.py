"""Exercise the installed MCP server over stdio (status + bootstrap gate)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Minimum tool surface install must expose (includes hard-start gate tools).
REQUIRED_TOOLS: frozenset[str] = frozenset(
    {
        "fluent_status",
        "fluent_agent_brief",
        "fluent_resolve_brief_mode",
        "fluent_bootstrap_status",
        "fluent_list_projects",
        "fluent_import_project",
        "fluent_inspect_project",
        "fluent_project_query",
        "fluent_inspect_script",
        "fluent_find_external_command",
        "fluent_diagnose",
        "fluent_parse_fluent_log",
        "fluent_create_request_spec",
        "fluent_validate_request_spec",
        "fluent_generate_protocol",
        "fluent_plan_repair",
        "fluent_apply_repair",
        "fluent_verify_bundle",
        "fluent_process_media",
        "fluent_verify_archive",
        "fluent_list_capabilities",
        "fluent_run_safe_cli",
        "fluent_run_opt_in_cli",
        "fluent_worktable_diff",
        "fluent_summarize_simulation",
    }
)

REQUIRED_NEXT_STEP_KEYS: frozenset[str] = frozenset(
    {
        "action",
        "tool",
        "cli",
        "arguments",
        "brief_mode",
        "reason",
        "allowed_tools",
        "blocked_tools",
        "unlock_generate_after",
    }
)


def _tool_payload(result: Any) -> dict[str, Any]:
    if getattr(result, "isError", False):
        raise RuntimeError(f"MCP tool returned error: {result!r}")
    chunks: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            chunks.append(text)
    if not chunks:
        structured = getattr(result, "structuredContent", None) or getattr(result, "data", None)
        if isinstance(structured, dict):
            return structured
        raise RuntimeError(f"MCP tool returned no JSON payload: {result!r}")
    raw = "\n".join(chunks).strip()
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise RuntimeError(f"MCP tool payload must be an object, got {type(payload).__name__}")
    return payload


def validate_smoke_result(
    *,
    tool_names: list[str],
    status_ok: bool,
    bootstrap: dict[str, Any] | None,
) -> list[str]:
    """Return human-readable failure reasons (empty means pass)."""
    failures: list[str] = []
    present = set(tool_names)
    missing = sorted(REQUIRED_TOOLS - present)
    if missing:
        failures.append(f"missing required MCP tools: {', '.join(missing)}")
    if not status_ok:
        failures.append("fluent_status failed")
    if bootstrap is None:
        failures.append("fluent_bootstrap_status returned no payload")
        return failures
    next_step = bootstrap.get("next_step")
    if not isinstance(next_step, dict) or not next_step:
        failures.append("fluent_bootstrap_status missing next_step object")
        return failures
    absent = sorted(key for key in REQUIRED_NEXT_STEP_KEYS if key not in next_step)
    if absent:
        failures.append(f"next_step missing keys: {', '.join(absent)}")
    if not next_step.get("action"):
        failures.append("next_step.action is empty")
    if not next_step.get("tool"):
        failures.append("next_step.tool is empty")
    if "allowed_tools" in next_step and not isinstance(next_step.get("allowed_tools"), list):
        failures.append("next_step.allowed_tools must be a list")
    if "blocked_tools" in next_step and not isinstance(next_step.get("blocked_tools"), list):
        failures.append("next_step.blocked_tools must be a list")
    return failures


async def smoke() -> dict[str, Any]:
    repo_root = Path(__file__).resolve().parents[2]
    package_root = repo_root / "source" / "03-protocol-builder"
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "fluent_pipeline.mcp_server"],
        cwd=str(package_root),
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    async with stdio_client(parameters) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            tools = await session.list_tools()
            resources = await session.list_resources()
            resource_templates = await session.list_resource_templates()
            status_result = await session.call_tool("fluent_status", {})
            status_payload = _tool_payload(status_result)
            bootstrap_result = await session.call_tool(
                "fluent_bootstrap_status",
                {"write_report": False},
            )
            bootstrap_payload = _tool_payload(bootstrap_result)
            bootstrap_resource = await session.read_resource("fluent://bootstrap")
            brief_resource = await session.read_resource("fluent://brief/status")

    tool_names = sorted(tool.name for tool in tools.tools)
    resource_uris = sorted(
        str(getattr(item, "uri", "") or "") for item in resources.resources
    )
    template_uris = sorted(
        str(getattr(item, "uriTemplate", getattr(item, "uri", "")) or "")
        for item in resource_templates.resourceTemplates
    )
    failures = validate_smoke_result(
        tool_names=tool_names,
        status_ok=bool(status_payload),
        bootstrap=bootstrap_payload,
    )
    if "fluent://bootstrap" not in resource_uris:
        failures.append("missing resource fluent://bootstrap")
    if not any("fluent://brief/" in uri or "brief/{mode}" in uri for uri in template_uris + resource_uris):
        failures.append("missing resource template fluent://brief/{mode}")

    def _resource_text(result: Any) -> str:
        chunks: list[str] = []
        for content in getattr(result, "contents", None) or []:
            text = getattr(content, "text", None)
            if text:
                chunks.append(text)
        return "\n".join(chunks)

    try:
        bootstrap_mirror = json.loads(_resource_text(bootstrap_resource))
        if not isinstance(bootstrap_mirror.get("next_step"), dict):
            failures.append("fluent://bootstrap missing next_step")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"fluent://bootstrap unreadable: {exc}")
    try:
        brief_mirror = json.loads(_resource_text(brief_resource))
        if not brief_mirror.get("ok"):
            failures.append("fluent://brief/status not ok")
    except Exception as exc:  # noqa: BLE001
        failures.append(f"fluent://brief/status unreadable: {exc}")

    return {
        "ok": not failures,
        "failures": failures,
        "tool_count": len(tool_names),
        "tools": tool_names,
        "resource_count": len(resources.resources),
        "resource_uris": resource_uris,
        "resource_template_count": len(resource_templates.resourceTemplates),
        "resource_templates": template_uris,
        "status": status_payload,
        "bootstrap": {
            "ok": bootstrap_payload.get("ok"),
            "doctor_ok": bootstrap_payload.get("doctor_ok"),
            "project_count": bootstrap_payload.get("project_count"),
            "next_step": bootstrap_payload.get("next_step"),
        },
    }


def main() -> int:
    try:
        result = asyncio.run(smoke())
    except Exception as exc:  # noqa: BLE001 - install smoke must fail loud
        print(json.dumps({"ok": False, "failures": [str(exc)]}, indent=2))
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
