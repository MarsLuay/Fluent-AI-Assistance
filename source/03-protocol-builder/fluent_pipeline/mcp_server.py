"""MCP server for the local Fluent AI-Assistance workflow."""

from __future__ import annotations

import argparse
from typing import Literal

from mcp.server.fastmcp import FastMCP

from .generation_options import GenerationOptions
from .mcp_gateway import ProtocolBuilderGateway, json_text


INSTRUCTIONS = """
Use this server to inspect Tecan FluentControl exports, mine existing script
patterns, diagnose logs, build reviewed request specifications, generate
protocol artifacts, and validate ready-to-import ZEIA archives.

Start every session with fluent_bootstrap_status (or read fluent://bootstrap),
or fluent_agent_brief / fluent_resolve_brief_mode / fluent://brief/{mode} when
picking a checklist. Pass intent text to auto-select mode. Follow
next_step.tool before exploring the repo.
Always inspect an imported project and existing command examples before
generation. Treat request.spec.yaml as the user contract and protocol.ir.json
as the generation source of truth. Final generated scripts still require manual
Script Editor and hardware validation on the target instrument.

This server intentionally exposes no arbitrary shell, driver installation,
ProgramData writes, FluentControl UI automation, or direct hardware operation.
It exposes every CLI command through either a dedicated MCP tool, a constrained
offline CLI bridge, or a server-side opt-in discoverable through
``fluent_list_capabilities``.
""".strip()

mcp = FastMCP("Fluent AI-Assistance", instructions=INSTRUCTIONS)
gateway = ProtocolBuilderGateway()


@mcp.tool()
def fluent_status() -> dict:
    """Check the local MCP, Python, workspace, and checksum configuration."""
    return gateway.status()


@mcp.tool()
def fluent_agent_brief(mode: str = "status", intent: str = "") -> dict:
    """Return a short mode-scoped checklist.

    Pass intent (user request text) to auto-pick mode via keyword mapper
    (repair/new-script/simulator/install/status). Intent wins over mode when set.
    """
    return gateway.agent_brief(mode, intent=intent or None)


@mcp.tool()
def fluent_resolve_brief_mode(intent: str, default: str = "status") -> dict:
    """One-liner: map user intent text → brief mode (no checklist body)."""
    return gateway.resolve_brief_mode(intent, default=default)


@mcp.tool()
def fluent_bootstrap_status(
    install_missing: bool = False,
    confirm_install: bool = False,
    write_report: bool = True,
    inspected: bool = False,
) -> dict:
    """Run doctor + list-projects and return the next required tool/step.

    Set inspected=true after fluent_inspect_project (or equivalent) so
    next_step allows fluent_generate_protocol. Until then generate stays in
    blocked_tools when projects already exist.
    """
    return gateway.bootstrap_status(
        install_missing=install_missing,
        confirm_install=confirm_install,
        write_report=write_report,
        inspected=inspected,
    )


@mcp.tool()
def fluent_list_projects() -> list[dict]:
    """List locally imported ZEIA project contexts."""
    return gateway.projects()


@mcp.tool()
def fluent_import_project(
    archive: str,
    name: str | None = None,
    activate: bool = True,
    snapshots: list[str] | None = None,
    force: bool = False,
    confirm_replace: bool = False,
) -> dict:
    """Import a ZEIA and optional Snapshot ZIPs into an isolated local context."""
    return gateway.import_archive(
        archive,
        name=name,
        activate=activate,
        snapshots=snapshots,
        force=force,
        confirm_replace=confirm_replace,
    )


@mcp.tool()
def fluent_inspect_project(name: str | None = None) -> dict:
    """Inspect one imported project: summary + path pointers only (no full manifest)."""
    return gateway.project(name)


@mcp.tool()
def fluent_project_query(
    pattern: str,
    context: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> dict:
    """Search an imported ZEIA context; returns capped compact matches (max 50)."""
    return gateway.project_query(
        pattern,
        context=context,
        kind=kind,
        limit=limit,
    )


@mcp.tool()
def fluent_inspect_script(
    context: str,
    script: str | None = None,
    output_directory: str | None = None,
) -> dict:
    """Create a structured report for an imported FluentControl script."""
    return gateway.inspect_script(
        context=context,
        script=script,
        output_directory=output_directory,
    )


@mcp.tool()
def fluent_find_external_command(
    command_name: str,
    context: str,
    module: str | None = None,
    source_script: str | None = None,
    output_directory: str | None = None,
) -> dict:
    """Mine a real source usage before generating a vendor/external command."""
    return gateway.find_external_command(
        command_name,
        context=context,
        module=module,
        source_script=source_script,
        output_directory=output_directory,
    )


@mcp.tool()
def fluent_diagnose(
    input_path: str,
    context: str | None = None,
    script: str | None = None,
    error_file: str | None = None,
    output_directory: str | None = None,
) -> dict:
    """Diagnose an XSCR, ZEIA, GWL, protocol IR, or Python protocol draft."""
    return gateway.diagnose(
        input_path,
        context=context,
        script=script,
        error_file=error_file,
        output_directory=output_directory,
    )


@mcp.tool()
def fluent_parse_fluent_log(
    log_path: str,
    output_directory: str | None = None,
) -> dict:
    """Parse a FluentControl/VisionX log into structured findings and Markdown."""
    return gateway.parse_log(log_path, output_directory=output_directory)


@mcp.tool()
def fluent_create_request_spec(
    intent: str,
    context: str | None = None,
    source_scripts: list[str] | None = None,
    protocol_name: str | None = None,
    generation_options: GenerationOptions | None = None,
    output_path: str | None = None,
) -> dict:
    """Create the reviewable request.spec.yaml contract for a new protocol."""
    return gateway.create_request_spec(
        intent,
        context=context,
        source_scripts=source_scripts,
        protocol_name=protocol_name,
        generation_options=generation_options,
        output_path=output_path,
    )


@mcp.tool()
def fluent_validate_request_spec(spec_path: str) -> dict:
    """Lint request.spec.yaml before any protocol generation."""
    return gateway.validate_request_spec(spec_path)


@mcp.tool()
def fluent_generate_protocol(
    spec_path: str,
    context: str | None = None,
    ir_path: str | None = None,
    output_directory: str | None = None,
    mode: Literal["scaffold", "final"] = "scaffold",
    confirm_final: bool = False,
    generation_options: GenerationOptions | None = None,
) -> dict:
    """Generate scaffold or final protocol artifacts from a reviewed spec and IR.

    Final mode forces simulation and compilation before the workflow can report
    readiness.
    """
    return gateway.generate(
        spec_path,
        context=context,
        ir_path=ir_path,
        output_directory=output_directory,
        mode=mode,
        confirm_final=confirm_final,
        generation_options=generation_options,
    )


@mcp.tool()
def fluent_plan_repair(
    draft_path: str,
    context: str | None = None,
    simulation_json_path: str | None = None,
    output_directory: str | None = None,
) -> dict:
    """Build a project-aware repair plan for a generated Python draft."""
    return gateway.plan_repair(
        draft_path,
        context=context,
        simulation_json_path=simulation_json_path,
        output_directory=output_directory,
    )


@mcp.tool()
def fluent_apply_repair(
    draft_path: str,
    output_path: str | None = None,
    context: str | None = None,
    simulation_json_path: str | None = None,
    apply_modeling: bool = False,
    output_directory: str | None = None,
) -> dict:
    """Apply a reviewed repair plan and write a repaired Python draft."""
    return gateway.apply_repair(
        draft_path,
        output_path=output_path,
        context=context,
        simulation_json_path=simulation_json_path,
        apply_modeling=apply_modeling,
        output_directory=output_directory,
    )


@mcp.tool()
def fluent_verify_bundle(
    compiled_xscr: str,
    draft_path: str | None = None,
    protocol_ir: str | None = None,
    worklist: str | None = None,
    source_projects: list[str] | None = None,
    source_scripts: list[str] | None = None,
    source_xscr: str | None = None,
    recreate_guide: str | None = None,
    output_directory: str | None = None,
) -> dict:
    """Run shared ready-validation over a generated compiled bundle."""
    return gateway.verify_bundle(
        compiled_xscr,
        draft_path=draft_path,
        protocol_ir=protocol_ir,
        worklist=worklist,
        source_projects=source_projects,
        source_scripts=source_scripts,
        source_xscr=source_xscr,
        recreate_guide=recreate_guide,
        output_directory=output_directory,
    )


@mcp.tool()
def fluent_process_media(
    target: str,
    ir_path: str | None = None,
    confirm_in_place: bool = False,
) -> dict:
    """Process replacement media in a bundle; requires explicit in-place confirmation.

    When the bundle contains more than one protocol IR candidate, pass
    ``ir_path`` explicitly so the wrong protocol is never selected by default.
    """
    return gateway.process_media(target, ir_path=ir_path, confirm_in_place=confirm_in_place)


@mcp.tool()
def fluent_verify_archive(archive: str) -> dict:
    """Audit a generated ZEIA archive, datastore metadata, and checksums."""
    return gateway.verify_archive(archive)


@mcp.tool()
def fluent_list_capabilities() -> dict:
    """List audited MCP coverage for every Fluent CLI command and safety exclusion."""
    return gateway.cli_capabilities()


@mcp.tool()
def fluent_run_safe_cli(
    operation: str,
    arguments: list[str] | None = None,
    confirm_mutation: bool = False,
) -> dict:
    """Run one audited, offline-only CLI operation without using a shell.

    Use ``fluent_list_capabilities`` first.  Commands that execute arbitrary
    Python, alter the environment, or use a live FluentControl/UI provider are
    rejected rather than being passed through.
    """
    return gateway.run_safe_cli(
        operation,
        arguments,
        confirm_mutation=confirm_mutation,
    )


@mcp.tool()
def fluent_run_opt_in_cli(
    operation: str,
    arguments: list[str] | None = None,
    confirm_execution: bool = False,
) -> dict:
    """Run a server-enabled Python-execution/setup CLI operation.

    The MCP server process must have the environment flag reported by
    ``fluent_list_capabilities`` set to ``1`` before launch. Live providers,
    browser/UI control, ProgramData writes, drivers, and shell command templates
    remain unavailable.
    """
    return gateway.run_opt_in_cli(
        operation,
        arguments,
        confirm_execution=confirm_execution,
    )


@mcp.tool()
def fluent_worktable_diff(
    protocol_ir_path: str,
    context: str | None = None,
    source_scripts: list[str] | None = None,
    output_directory: str | None = None,
) -> dict:
    """Compare protocol worktable requirements without executing a draft or UI."""
    return gateway.diff_worktable(
        protocol_ir_path,
        context=context,
        source_scripts=source_scripts,
        output_directory=output_directory,
    )


@mcp.tool()
def fluent_summarize_simulation(
    simulation_json_path: str,
    protocol_ir_path: str | None = None,
) -> dict:
    """Summarize an existing simulation result without executing Python."""
    return gateway.summarize_simulation(
        simulation_json_path,
        protocol_ir_path=protocol_ir_path,
    )


@mcp.resource("fluent://status")
def status_resource() -> str:
    """Current local server and protocol-builder status."""
    return json_text(gateway.status())


@mcp.resource("fluent://bootstrap")
def bootstrap_resource() -> str:
    """Doctor + list-projects + next_step (read-only mirror of fluent_bootstrap_status)."""
    return json_text(gateway.bootstrap_status(write_report=False))


@mcp.resource("fluent://brief/{mode}")
def brief_resource(mode: str) -> str:
    """Mode-scoped agent checklist (mirror of fluent_agent_brief)."""
    from .runner import PipelineError

    try:
        return json_text(gateway.agent_brief(mode))
    except (PipelineError, ValueError) as exc:
        return json_text(
            {
                "ok": False,
                "mode": mode,
                "error": str(exc),
                "modes": [
                    "install",
                    "status",
                    "new-script",
                    "repair",
                    "simulator",
                ],
            }
        )


@mcp.resource("fluent://projects")
def projects_resource() -> str:
    """Imported project context inventory."""
    return json_text(gateway.projects())


@mcp.resource("fluent://projects/{name}")
def project_resource(name: str) -> str:
    """Manifest for an imported project context."""
    return json_text(gateway.project(name))


@mcp.resource("fluent://capabilities")
def capabilities_resource() -> str:
    """Audited CLI-to-MCP coverage and intentional safety exclusions."""
    return json_text(gateway.cli_capabilities())


@mcp.prompt()
def create_fluent_protocol(
    request: str,
    context: str | None = None,
    source_script: str | None = None,
) -> str:
    """Reusable safe workflow prompt for creating a FluentControl protocol."""
    source = f"\nPreferred source script: {source_script}" if source_script else ""
    selected_context = context or "<ask the user or list projects>"
    return f"""Create a Tecan FluentControl protocol for this request:

{request}

Project context: {selected_context}{source}

Use the Tecan MCP tools in this order:
0. fluent_bootstrap_status (follow next_step; use fluent_resolve_brief_mode /
   fluent_agent_brief(intent=...) for mode checklists).
1. Inspect the project and relevant source scripts.
2. Mine existing usages for every external/vendor command.
3. Create and validate request.spec.yaml.
4. Generate a scaffold and review protocol.ir.json with the user.
5. Generate final artifacts only after explicit user approval.
6. Report validation findings and the ready-to-import bundle path.

Do not install drivers, write to FluentControl ProgramData, or claim hardware
readiness. The operator must open the script in FluentControl and validate the
deck and movements on the target instrument."""


def _self_test() -> int:
    from .bootstrap_status import build_bootstrap_status

    status = gateway.status()
    tools = mcp._tool_manager.list_tools()
    tool_names = sorted(tool.name for tool in tools)
    required = {
        "fluent_status",
        "fluent_agent_brief",
        "fluent_resolve_brief_mode",
        "fluent_bootstrap_status",
        "fluent_list_projects",
        "fluent_generate_protocol",
    }
    missing = sorted(required - set(tool_names))
    bootstrap = build_bootstrap_status(write_report=False)
    next_step = bootstrap.get("next_step") if isinstance(bootstrap, dict) else None
    ok = not missing and isinstance(next_step, dict) and bool(next_step.get("action")) and bool(
        next_step.get("tool")
    )
    print(
        json_text(
            {
                "ok": ok,
                "server": status["name"],
                "transport": status["mcp_transport"],
                "tool_count": len(tool_names),
                "tools": tool_names,
                "missing_required_tools": missing,
                "bootstrap": {
                    "ok": bootstrap.get("ok") if isinstance(bootstrap, dict) else False,
                    "next_step": next_step,
                },
            }
        )
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Fluent AI-Assistance MCP server.")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
