"""Thin, safe MCP adapter over the existing fluent_pipeline Python API."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import platform
import subprocess
import threading
from pathlib import Path
from typing import Any

from .application_services import (
    BundleVerificationRequest,
    RepairApplyRequest,
    RepairPlanRequest,
    analyze_logs,
    apply_repair as apply_repair_service,
    create_request_spec as create_request_spec_service,
    generate_protocol,
    inspect_project,
    import_project as import_project_service,
    plan_repair as plan_repair_service,
    validate_request_spec as validate_request_spec_service,
    verify_bundle as verify_bundle_service,
)
from .bundle_media import process_prompt_media_captures
from .checksums import checksum_backend_name
from .config import PROJECT_DIR, PROJECTS_DIR, READY_TO_IMPORT_DIR, REPO_ROOT, SHARED_TEMP_DIR, TEMP_FILES_DIRNAME
from .agent_brief import AGENT_BRIEF_MODES, render_agent_brief, resolve_agent_brief_mode
from .delivery_bundle import validate_v2_delivery_bundle
from .diagnostics import diagnose_input
from .exports import verify_generated_project_archive
from .external_commands import (
    inspect_external_command,
    write_external_command_contract,
)
from .generation_options import (
    GenerationOptions,
)
from .mcp_requests import (
    generation_request_from_mcp,
    log_analysis_request_from_mcp,
    project_import_request_from_mcp,
    project_inspection_request_from_mcp,
    request_spec_create_request_from_mcp,
    request_spec_validation_request_from_mcp,
)
from .project_context import (
    list_projects,
    load_project,
    query_project,
    resolve_context_script,
)
from .protocol_ir import load_protocol_ir, protocol_ir_from_path
from .reports import compact_simulation
from .request_spec import (
    load_request_spec,
)
from .runner import PipelineError
from .script_analysis import analyze_script
from .worktable_diff import (
    diff_worktable_requirements,
    render_worktable_changes_markdown,
    render_worktable_patch_json,
)


_MUTATION_LOCK = threading.Lock()


# Every CLI command is classified here so the MCP surface cannot silently fall
# behind it. A command is exposed by a purpose-built MCP tool, accepted by the
# constrained offline CLI bridge, or retained behind a server-side opt-in when
# it executes arbitrary Python or changes the local environment.
_CLI_COMMAND_COVERAGE: dict[str, dict[str, str]] = {
    "alias-list": {"mode": "bridge"},
    "alias-normalize-ir": {"mode": "bridge"},
    "alias-resolve": {"mode": "bridge"},
    "analyze": {"mode": "bridge"},
    "bundle-lifecycle": {"mode": "bridge"},
    "catalog-find": {"mode": "bridge"},
    "catalog-info": {"mode": "bridge"},
    "clear-project": {"mode": "bridge"},
    "collection-info": {"mode": "bridge"},
    "compatibility-matrix": {"mode": "bridge"},
    "compile": {
        "mode": "opt_in",
        "environment_flag": "TECAN_MCP_ENABLE_DRAFT_EXECUTION",
        "reason": "compiling a Python draft can execute code",
    },
    "create-collection": {"mode": "bridge"},
    "current-project": {"mode": "bridge"},
    "decompile": {"mode": "bridge"},
    "determinism-check": {"mode": "bridge"},
    "diagnose": {"mode": "native", "tool": "fluent_diagnose"},
    "doctor": {"mode": "bridge"},
    "bootstrap-status": {"mode": "native", "tool": "fluent_bootstrap_status"},
    "fluent-prepare-check": {
        "mode": "opt_in",
        "environment_flag": "TECAN_MCP_ENABLE_PREPARE_CHECK",
        "reason": "the current implementation is an offline prepare-check compatibility shim",
    },
    "generate": {"mode": "native", "tool": "fluent_generate_protocol"},
    "import-project": {"mode": "native", "tool": "fluent_import_project"},
    "inspect-external-command": {"mode": "native", "tool": "fluent_find_external_command"},
    "ir-build": {"mode": "bridge", "note": "requires --no-compile"},
    "ir-export": {"mode": "bridge", "note": "Python draft inputs are rejected"},
    "ir-schema": {"mode": "bridge"},
    "ir-validate": {"mode": "bridge"},
    "list-collections": {"mode": "bridge"},
    "list-projects": {"mode": "native", "tool": "fluent_list_projects"},
    "map-media": {"mode": "bridge", "note": "XSCR in-place rewriting is rejected"},
    "parse-fluent-log": {"mode": "native", "tool": "fluent_parse_fluent_log"},
    "process-media": {"mode": "native", "tool": "fluent_process_media"},
    "project-find": {"mode": "native", "tool": "fluent_project_query"},
    "project-info": {"mode": "native", "tool": "fluent_inspect_project"},
    "repair-draft": {"mode": "native", "tool": "fluent_apply_repair"},
    "repair-plan": {"mode": "native", "tool": "fluent_plan_repair"},
    "request-spec": {"mode": "native", "tool": "fluent_create_request_spec"},
    "resolve-spec": {"mode": "bridge"},
    "roundtrip": {
        "mode": "opt_in",
        "environment_flag": "TECAN_MCP_ENABLE_DRAFT_EXECUTION",
        "reason": "roundtrip runs Python through the simulator/compiler path",
    },
    "script-report": {"mode": "native", "tool": "fluent_inspect_script"},
    "setup": {
        "mode": "opt_in",
        "environment_flag": "TECAN_MCP_ENABLE_SETUP",
        "reason": "setup creates environments and installs local dependencies",
    },
    "simulate": {
        "mode": "opt_in",
        "environment_flag": "TECAN_MCP_ENABLE_DRAFT_EXECUTION",
        "reason": "simulation loads Python protocol drafts with exec_module",
    },
    "template-info": {"mode": "bridge"},
    "template-list": {"mode": "bridge"},
    "use-project": {"mode": "bridge"},
    "validate-delivery-bundle": {"mode": "bridge"},
    "validate-spec": {"mode": "native", "tool": "fluent_validate_request_spec"},
    "verify-bundle": {"mode": "native", "tool": "fluent_verify_bundle"},
    "worktable-diff": {"mode": "bridge"},
    "launch-simulator": {
        "mode": "opt_in",
        "environment_flag": "TECAN_MCP_ENABLE_SIMULATOR_LAUNCH",
        "reason": "opens the local protocol simulator UI against a generated bundle",
    },
}

_BRIDGED_CLI_COMMANDS = frozenset(
    command for command, coverage in _CLI_COMMAND_COVERAGE.items() if coverage["mode"] == "bridge"
)
_MUTATING_CLI_COMMANDS = frozenset(
    {
        "alias-normalize-ir",
        "analyze",
        "clear-project",
        "create-collection",
        "decompile",
        "ir-build",
        "ir-export",
        "map-media",
        "compile",
        "fluent-prepare-check",
        "use-project",
        "roundtrip",
        "setup",
        "simulate",
        "worktable-diff",
    }
)
_CLI_OUTPUT_ATTRIBUTES = (
    "archive_dir",
    "event_log",
    "json_out",
    "log_report",
    "markdown_out",
    "out_dir",
    "output",
    "report",
    "write_normalized",
)
_BLOCKED_CLI_ARGUMENTS = frozenset(
    {
        "--apply-modeling",
        "--apply-xscr",
        "--archive",
        "--fluent-command",
        "--fluent-context-check",
        "--fluent-host",
        "--fluent-insecure",
        "--fluent-port",
        "--fluent-provider",
        "--force-import",
        "--install-missing",
        "--no-fluent-close-method",
        "--write-index",
    }
)


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _extra_write_roots() -> list[Path]:
    raw = os.environ.get("TECAN_MCP_WRITE_ROOTS", "")
    return [
        Path(item).expanduser().resolve()
        for item in raw.split(os.pathsep)
        if item.strip()
    ]


def _files_under(root: Path) -> list[str]:
    if root.is_file():
        return [str(root)]
    if not root.exists():
        return []
    return [str(item) for item in sorted(root.rglob("*")) if item.is_file()][:250]


def _delivery_bundle_validation_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    folder = manifest.get("published_protocol_folder")
    if folder:
        result = validate_v2_delivery_bundle(Path(str(folder)), require_final_reports=True)
        return result.to_dict()
    published_zeia = manifest.get("published_zeia_path")
    if published_zeia:
        result = validate_v2_delivery_bundle(Path(str(published_zeia)).parent, require_final_reports=True)
        return result.to_dict()
    return {
        "ok": False,
        "bundle_dir": None,
        "protocol_name": None,
        "issues": [
            {
                "severity": "error",
                "code": "missing_published_protocol_folder",
                "message": "final generation did not report a published protocol folder",
                "path": None,
            }
        ],
    }


def resolve_process_media_ir_path(target: Path, ir_path: Path | None = None) -> Path:
    """Resolve the protocol IR used for prompt media processing.

    If the caller supplies ``ir_path``, use it directly. Otherwise infer the
    IR from the bundle layout and require an explicit choice when more than one
    candidate exists.
    """
    if ir_path is not None:
        if not ir_path.is_file():
            raise PipelineError(f"protocol IR path not found: {ir_path}")
        return ir_path

    search_roots = [target]
    if target.name.casefold() == "media":
        search_roots.append(target.parent)

    candidates: list[Path] = []
    seen: set[Path] = set()

    def _add(candidate: Path) -> None:
        resolved = candidate.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        candidates.append(resolved)

    for search_root in search_roots:
        for candidate in sorted(search_root.glob("*.protocol-ir.json")):
            _add(candidate)
        source_ir = search_root / "source" / "protocol.ir.json"
        if source_ir.is_file():
            _add(source_ir)

    if not candidates:
        raise PipelineError(f"no protocol IR found under {target}")
    if len(candidates) > 1:
        candidate_list = ", ".join(str(path) for path in candidates)
        raise PipelineError(
            f"multiple protocol IR files found under {target}: {candidate_list}. "
            "Pass an explicit ir_path / --ir."
        )
    return candidates[0]


class ProtocolBuilderGateway:
    """Selected fluent_pipeline services exposed to MCP clients."""

    def __init__(self, project_dir: Path = PROJECT_DIR) -> None:
        self.project_dir = project_dir.resolve()
        self.write_roots = [
            READY_TO_IMPORT_DIR.resolve(),
            *_extra_write_roots(),
        ]

    def status(self) -> dict[str, Any]:
        return {
            "name": "Fluent AI-Assistance",
            "interfaces": ["Python API", "CLI", "MCP"],
            "mcp_role": "thin adapter over fluent_pipeline",
            "mcp_transport": "stdio",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "project_dir": str(self.project_dir),
            "projects_dir": str(PROJECTS_DIR.resolve()),
            "ready_to_import_dir": str(READY_TO_IMPORT_DIR.resolve()),
            "checksum_backend": checksum_backend_name(),
            "hardware_operations_exposed": False,
        }

    def agent_brief(self, mode: str = "status", intent: str | None = None) -> dict[str, Any]:
        """Return a short mode-scoped checklist for the connected agent."""
        resolution: dict[str, Any] | None = None
        chosen = mode
        if intent and str(intent).strip():
            resolution = resolve_agent_brief_mode(str(intent))
            chosen = str(resolution["mode"])
        try:
            text = render_agent_brief(chosen)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc
        normalized = "new-script" if chosen.strip().casefold() == "script" else chosen.strip().casefold()
        payload: dict[str, Any] = {
            "ok": True,
            "mode": normalized,
            "brief": text,
            "modes": [item for item in AGENT_BRIEF_MODES if item != "script"],
            "contract": "source/03-protocol-builder/AGENTS.md",
        }
        if resolution is not None:
            payload["intent"] = str(intent).strip()
            payload["resolution"] = resolution
        return payload

    def resolve_brief_mode(self, intent: str, default: str = "status") -> dict[str, Any]:
        """Map free-text intent to a brief mode without rendering the checklist."""
        try:
            resolution = resolve_agent_brief_mode(intent, default=default)
        except ValueError as exc:
            raise PipelineError(str(exc)) from exc
        return {"ok": True, **resolution}

    def bootstrap_status(
        self,
        *,
        install_missing: bool = False,
        confirm_install: bool = False,
        write_report: bool = True,
        inspected: bool = False,
    ) -> dict[str, Any]:
        """Run doctor + list-projects and return the next required step."""
        from .bootstrap_status import build_bootstrap_status

        return build_bootstrap_status(
            install_missing=install_missing,
            confirm_install=confirm_install,
            write_report=write_report,
            inspected=inspected,
        )

    def projects(self) -> list[dict[str, Any]]:
        return list_projects()

    def project(self, name: str | None = None) -> dict[str, Any]:
        return inspect_project(project_inspection_request_from_mcp(name)).to_dict()

    def project_query(
        self,
        pattern: str,
        *,
        context: str | None = None,
        kind: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        project = load_project(context)
        return query_project(project, pattern, kind=kind, limit=limit)

    def import_archive(
        self,
        archive: str,
        *,
        name: str | None = None,
        activate: bool = True,
        snapshots: list[str] | None = None,
        force: bool = False,
        confirm_replace: bool = False,
    ) -> dict[str, Any]:
        if force and not confirm_replace:
            raise PipelineError("force import requires confirm_replace=true")
        archive_path = self._existing_input(archive)
        snapshot_paths = [self._existing_input(item) for item in (snapshots or [])]
        with _MUTATION_LOCK:
            result = import_project_service(
                project_import_request_from_mcp(
                    archive_path,
                    name=name,
                    activate=activate,
                    snapshots=snapshot_paths,
                    force=force,
                )
            )
        return result.to_dict()

    def inspect_script(
        self,
        *,
        context: str,
        script: str | None = None,
        output_directory: str | None = None,
    ) -> dict[str, Any]:
        project = load_project(context)
        output = self._output_path(
            output_directory,
            default=project.reports_dir / "mcp_script_report",
        )
        with _MUTATION_LOCK:
            report = analyze_script(project, script=script, out_dir=output)
        return {"ok": True, "report": report, "artifacts": _files_under(output)}

    def find_external_command(
        self,
        command_name: str,
        *,
        context: str,
        module: str | None = None,
        source_script: str | None = None,
        output_directory: str | None = None,
    ) -> dict[str, Any]:
        project = load_project(context)
        output = self._output_path(
            output_directory,
            default=project.reports_dir / "mcp_external_commands",
        )
        with _MUTATION_LOCK:
            output.mkdir(parents=True, exist_ok=True)
            stem = "".join(char if char.isalnum() else "_" for char in command_name).strip("_") or "command"
            report = inspect_external_command(
                project.manifest,
                context_root=project.root,
                command_name=command_name,
                module_name=module,
                source_script=source_script,
            )
            json_path = output / f"{stem}.json"
            markdown_path = output / f"{stem}.md"
            write_external_command_contract(
                report,
                json_path=json_path,
                markdown_path=markdown_path,
            )
        return {
            "ok": bool(report.get("match_count")),
            "report": report,
            "artifacts": [str(json_path), str(markdown_path)],
        }

    def diagnose(
        self,
        input_path: str,
        *,
        context: str | None = None,
        script: str | None = None,
        error_file: str | None = None,
        output_directory: str | None = None,
    ) -> dict[str, Any]:
        source = self._existing_input(input_path)
        project = load_project(context) if context else None
        output = self._output_path(
            output_directory,
            default=READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "mcp" / "diagnosis" / source.stem,
        )
        error_text = (
            self._existing_input(error_file).read_text(encoding="utf-8", errors="replace")
            if error_file
            else None
        )
        with _MUTATION_LOCK:
            bundle = diagnose_input(
                source,
                context=project,
                script=script,
                error_text=error_text,
                out_dir=output,
            )
        return {
            "ok": (bundle.report.get("summary") or {}).get("status") != "blocking",
            "report": bundle.report,
            "report_path": str(bundle.report_path) if bundle.report_path else None,
            "json_path": str(bundle.json_path) if bundle.json_path else None,
            "artifacts": _files_under(output),
        }

    def parse_log(
        self,
        log_path: str,
        *,
        output_directory: str | None = None,
    ) -> dict[str, Any]:
        source = self._existing_input(log_path)
        output = self._output_path(
            output_directory,
            default=READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "mcp" / "logs" / source.stem,
        )
        with _MUTATION_LOCK:
            output.mkdir(parents=True, exist_ok=True)
            result = analyze_logs(log_analysis_request_from_mcp(source, output_directory=output))
        payload = result.to_dict()
        payload["artifacts"] = _files_under(output)
        return payload

    def create_request_spec(
        self,
        intent: str,
        *,
        context: str | None = None,
        source_scripts: list[str] | None = None,
        protocol_name: str | None = None,
        generation_options: GenerationOptions | dict[str, Any] | None = None,
        output_path: str | None = None,
    ) -> dict[str, Any]:
        output = self._output_path(
            output_path,
            default=READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "mcp" / (protocol_name or "request") / "request.spec.yaml",
        )
        with _MUTATION_LOCK:
            result = create_request_spec_service(
                request_spec_create_request_from_mcp(
                    intent,
                    context=context,
                    source_scripts=source_scripts,
                    protocol_name=protocol_name,
                    generation_options=generation_options,
                    output_path=output,
                )
            )
        return result.to_dict()

    def validate_request_spec(self, spec_path: str) -> dict[str, Any]:
        source = self._existing_input(spec_path)
        return validate_request_spec_service(request_spec_validation_request_from_mcp(source)).to_dict()

    def generate(
        self,
        spec_path: str,
        *,
        context: str | None = None,
        ir_path: str | None = None,
        output_directory: str | None = None,
        mode: str = "scaffold",
        confirm_final: bool = False,
        generation_options: GenerationOptions | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode not in {"scaffold", "final"}:
            raise PipelineError("mode must be 'scaffold' or 'final'")
        if mode == "final" and not confirm_final:
            raise PipelineError("final generation requires confirm_final=true")
        spec_source = self._existing_input(spec_path)
        spec = load_request_spec(spec_source)
        output = self._output_path(
            output_directory,
            default=READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "mcp" / f"{spec_source.parent.name}_{mode}",
        )
        selected_ir = self._existing_input(ir_path) if ir_path else None
        request = generation_request_from_mcp(
            spec_source,
            spec=spec,
            context=context,
            selected_ir=selected_ir,
            output_directory=output,
            mode=mode,
            generation_options=generation_options,
        )
        with _MUTATION_LOCK:
            result = generate_protocol(request)
        payload = result.to_dict()
        if mode == "final":
            delivery_validation = _delivery_bundle_validation_from_manifest(result.manifest)
            payload["delivery_bundle_validation"] = delivery_validation
            payload["ok"] = bool(result.manifest.get("ready_to_import")) and bool(delivery_validation.get("ok"))
        else:
            payload["ok"] = True
        payload["mode"] = mode
        payload["artifacts"] = _files_under(output)
        return payload

    def plan_repair(
        self,
        draft_path: str,
        *,
        context: str | None = None,
        simulation_json_path: str | None = None,
        output_directory: str | None = None,
    ) -> dict[str, Any]:
        draft = self._existing_input(draft_path)
        simulation = self._existing_input(simulation_json_path) if simulation_json_path else None
        output = self._output_path(
            output_directory,
            default=READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "mcp" / "repairs" / draft.stem,
        )
        with _MUTATION_LOCK:
            result = plan_repair_service(
                RepairPlanRequest(
                    draft_path=draft,
                    context_name=context,
                    simulation_json_path=simulation,
                    report_path=output / "repair_plan.md",
                )
            )
        payload = result.to_dict()
        payload["artifacts"] = _files_under(output)
        return payload

    def apply_repair(
        self,
        draft_path: str,
        *,
        output_path: str | None = None,
        context: str | None = None,
        simulation_json_path: str | None = None,
        apply_modeling: bool = False,
        output_directory: str | None = None,
    ) -> dict[str, Any]:
        draft = self._existing_input(draft_path)
        simulation = self._existing_input(simulation_json_path) if simulation_json_path else None
        output_dir = self._output_path(
            output_directory,
            default=READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "mcp" / "repairs" / draft.stem,
        )
        repaired_output = (
            self._output_path(output_path, default=output_dir / f"{draft.stem}_repaired.py")
            if output_path
            else output_dir / f"{draft.stem}_repaired.py"
        )
        with _MUTATION_LOCK:
            result = apply_repair_service(
                RepairApplyRequest(
                    draft_path=draft,
                    output_path=repaired_output,
                    context_name=context,
                    simulation_json_path=simulation,
                    apply_modeling=apply_modeling,
                    report_path=output_dir / "repair_plan.md",
                )
            )
        payload = result.to_dict()
        payload["artifacts"] = _files_under(output_dir)
        return payload

    def verify_bundle(
        self,
        compiled_xscr: str,
        *,
        draft_path: str | None = None,
        protocol_ir: str | None = None,
        worklist: str | None = None,
        source_projects: list[str] | None = None,
        source_scripts: list[str] | None = None,
        source_xscr: str | None = None,
        recreate_guide: str | None = None,
        output_directory: str | None = None,
    ) -> dict[str, Any]:
        compiled = self._existing_input(compiled_xscr)
        output = self._output_path(
            output_directory,
            default=READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "mcp" / "verify" / compiled.stem,
        )
        with _MUTATION_LOCK:
            result = verify_bundle_service(
                BundleVerificationRequest(
                    compiled_xscr=compiled,
                    draft_path=self._existing_input(draft_path) if draft_path else None,
                    protocol_ir=self._existing_input(protocol_ir) if protocol_ir else None,
                    worklist=self._existing_input(worklist) if worklist else None,
                    source_projects=tuple(self._existing_input(path) for path in (source_projects or [])),
                    source_scripts=tuple(self._existing_input(path) for path in (source_scripts or [])),
                    source_xscr=self._existing_input(source_xscr) if source_xscr else None,
                    recreate_guide=self._existing_input(recreate_guide) if recreate_guide else None,
                    report_path=output / "ready_validation.md",
                    json_path=output / "ready_validation.json",
                )
            )
        payload = result.to_dict()
        payload["artifacts"] = _files_under(output)
        return payload

    def process_media(
        self,
        target: str,
        *,
        ir_path: str | None = None,
        confirm_in_place: bool = False,
    ) -> dict[str, Any]:
        if not confirm_in_place:
            raise PipelineError("media processing requires confirm_in_place=true")
        root = self._existing_input(target)
        media_dir = root / "media" if (root / "media").is_dir() else root
        if not media_dir.is_dir():
            raise PipelineError(f"media directory not found: {media_dir}")
        ir = load_protocol_ir(
            resolve_process_media_ir_path(root, self._existing_input(ir_path) if ir_path else None)
        )
        build_dir = root if (root / "source").is_dir() else media_dir.parent
        unprocessed = media_dir / "unprocessed"
        with _MUTATION_LOCK:
            report = process_prompt_media_captures(
                ir,
                media_dir,
                build_dir=build_dir if (build_dir / "source").is_dir() else None,
                unprocessed_dirs=[unprocessed] if unprocessed.is_dir() else [],
            )
        return {
            "ok": not bool(report.get("failed_count")),
            "report": report,
            "artifacts": _files_under(media_dir),
        }

    def verify_archive(self, archive: str) -> dict[str, Any]:
        path = self._existing_input(archive)
        return verify_generated_project_archive(path, bundle_root=path.parent)

    def cli_capabilities(self) -> dict[str, Any]:
        """Return the audited MCP coverage for every registered CLI command."""
        parser, command_choices = self._cli_parser_and_commands()
        del parser  # Keep parser construction local so normal CLI imports stay acyclic.
        registered = set(command_choices)
        classified = set(_CLI_COMMAND_COVERAGE)
        return {
            "registered_cli_command_count": len(registered),
            "classified_cli_command_count": len(classified),
            "unclassified_cli_commands": sorted(registered - classified),
            "stale_classifications": sorted(classified - registered),
            "commands": {
                command: _CLI_COMMAND_COVERAGE[command]
                for command in sorted(classified)
            },
            "safety_boundary": {
                "arbitrary_shell": False,
                "programdata_writes": False,
                "fluentcontrol_ui_automation": False,
                "hardware_operations": False,
                "untrusted_python_execution": "server_opt_in_only",
            },
        }

    def run_safe_cli(
        self,
        operation: str,
        arguments: list[str] | None = None,
        *,
        confirm_mutation: bool = False,
    ) -> dict[str, Any]:
        """Run one audited offline CLI command without invoking a shell.

        The parser and its command handler are called directly in-process.  This
        is deliberately not a generic command runner: only operations classified
        as ``bridge`` are accepted, live-provider flags are rejected, and every
        explicit output path must remain under the MCP artifact roots.
        """
        return self._run_cli_bridge(
            operation,
            arguments,
            expected_mode="bridge",
            confirmed=confirm_mutation,
            confirmation_name="confirm_mutation",
        )

    def run_opt_in_cli(
        self,
        operation: str,
        arguments: list[str] | None = None,
        *,
        confirm_execution: bool = False,
    ) -> dict[str, Any]:
        """Run an explicitly enabled local-execution CLI operation.

        The server process must have the operation's documented environment flag
        set to ``1``. This cannot be enabled by an MCP request, so a connected
        agent cannot silently opt itself into Python execution or environment
        setup. Live provider, desktop/UI, driver, ProgramData, and shell options
        remain rejected even when this path is enabled.
        """
        coverage = _CLI_COMMAND_COVERAGE.get(operation)
        if coverage is None:
            raise PipelineError(f"unknown Fluent CLI operation: {operation}")
        if coverage["mode"] != "opt_in":
            raise PipelineError(f"{operation} is not an opt-in MCP CLI operation")
        environment_flag = coverage.get("environment_flag")
        if not environment_flag or os.environ.get(environment_flag) != "1":
            raise PipelineError(
                f"{operation} requires {environment_flag}=1 in the MCP server environment"
            )
        return self._run_cli_bridge(
            operation,
            arguments,
            expected_mode="opt_in",
            confirmed=confirm_execution,
            confirmation_name="confirm_execution",
        )

    def _run_cli_bridge(
        self,
        operation: str,
        arguments: list[str] | None,
        *,
        expected_mode: str,
        confirmed: bool,
        confirmation_name: str,
    ) -> dict[str, Any]:
        coverage = _CLI_COMMAND_COVERAGE.get(operation)
        if coverage is None:
            raise PipelineError(f"unknown Fluent CLI operation: {operation}")
        if coverage["mode"] != expected_mode:
            if coverage["mode"] == "native":
                raise PipelineError(
                    f"{operation} is exposed by the dedicated MCP tool "
                    f"{coverage.get('tool', 'for this operation')}"
                )
            if coverage["mode"] == "opt_in":
                raise PipelineError(
                    f"{operation} is retained as an opt-in MCP operation; use fluent_run_opt_in_cli"
                )
            raise PipelineError(
                f"{operation} is intentionally unavailable through MCP: {coverage.get('reason', 'unsafe operation')}"
            )

        cli_arguments = list(arguments or [])
        if any(not isinstance(value, str) or not value for value in cli_arguments):
            raise PipelineError("CLI arguments must be non-empty strings")
        blocked = sorted(set(cli_arguments).intersection(_BLOCKED_CLI_ARGUMENTS))
        if blocked:
            raise PipelineError(
                "MCP does not allow these CLI arguments: " + ", ".join(blocked)
            )

        parser, command_choices = self._cli_parser_and_commands()
        if operation not in command_choices:
            raise PipelineError(f"CLI operation is not registered: {operation}")

        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                parsed = parser.parse_args([operation, *cli_arguments])
        except SystemExit as exc:
            details = stderr.getvalue().strip() or stdout.getvalue().strip()
            raise PipelineError(f"invalid arguments for {operation}: {details or exc}") from None

        self._validate_safe_cli_request(operation, parsed)
        mutates = operation in _MUTATING_CLI_COMMANDS or any(
            getattr(parsed, attribute, None) is not None
            for attribute in _CLI_OUTPUT_ATTRIBUTES
        )
        if mutates and not confirmed:
            raise PipelineError(f"{operation} requires {confirmation_name}=true")

        try:
            with _MUTATION_LOCK, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = parsed.func(parsed)
        except PipelineError:
            raise

        exit_code = int(result) if isinstance(result, int) else 0
        return {
            "ok": exit_code == 0,
            "operation": operation,
            "exit_code": exit_code,
            "stdout": stdout.getvalue().strip(),
            "stderr": stderr.getvalue().strip(),
            "mutates": mutates,
            "mode": coverage["mode"],
        }

    def diff_worktable(
        self,
        protocol_ir_path: str,
        *,
        context: str | None = None,
        source_scripts: list[str] | None = None,
        output_directory: str | None = None,
    ) -> dict[str, Any]:
        """Compare a protocol IR's worktable requirements without running code."""
        source = self._existing_input(protocol_ir_path)
        project = load_project(context) if context else None
        protocol = load_protocol_ir(source)
        source_irs: list[dict[str, Any]] = []
        for raw_script in source_scripts or []:
            script_path = (
                resolve_context_script(project, raw_script)
                if project is not None
                else self._existing_input(raw_script)
            )
            payload = protocol_ir_from_path(script_path)
            if isinstance(payload, dict) and payload.get("protocols"):
                source_irs.extend(
                    item for item in payload.get("protocols") or [] if isinstance(item, dict)
                )
            elif isinstance(payload, dict):
                source_irs.append(payload)

        diff = diff_worktable_requirements(
            protocol,
            source_manifest=project.manifest if project else None,
            source_irs=source_irs,
        )
        payload: dict[str, Any] = {
            "ok": True,
            "protocol_ir_path": str(source),
            "context": project.name if project else None,
            "diff": diff,
        }
        if output_directory:
            output = self._output_path(
                output_directory,
                default=READY_TO_IMPORT_DIR / "unscoped" / TEMP_FILES_DIRNAME / "mcp" / "worktable",
            )
            with _MUTATION_LOCK:
                output.mkdir(parents=True, exist_ok=True)
                markdown = output / "worktable_changes.md"
                patch = output / "worktable.patch.json"
                markdown.write_text(render_worktable_changes_markdown(diff), encoding="utf-8")
                patch.write_text(render_worktable_patch_json(diff), encoding="utf-8")
            payload["artifacts"] = [str(markdown), str(patch)]
        return payload

    def summarize_simulation(
        self,
        simulation_json_path: str,
        *,
        protocol_ir_path: str | None = None,
    ) -> dict[str, Any]:
        """Summarize existing simulation JSON without executing a protocol draft."""
        simulation_path = self._existing_input(simulation_json_path)
        try:
            simulation = json.loads(simulation_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PipelineError(f"invalid simulation JSON: {simulation_path}: {exc}") from exc
        if not isinstance(simulation, dict):
            raise PipelineError("simulation JSON must contain an object")
        protocol_path = self._existing_input(protocol_ir_path) if protocol_ir_path else None
        return {
            "ok": True,
            "simulation_json_path": str(simulation_path),
            "protocol_ir_path": str(protocol_path) if protocol_path else None,
            "summary": compact_simulation(simulation),
        }

    def _cli_parser_and_commands(self) -> tuple[argparse.ArgumentParser, dict[str, Any]]:
        # Import lazily: CLI command modules import this gateway for shared helpers.
        from .cli.parser import _build_parser

        parser = _build_parser()
        action = next(
            (
                item
                for item in parser._actions
                if isinstance(item, argparse._SubParsersAction)
            ),
            None,
        )
        if action is None:
            raise PipelineError("Fluent CLI parser has no command registry")
        return parser, action.choices

    def _validate_safe_cli_request(self, operation: str, parsed: argparse.Namespace) -> None:
        for attribute in _CLI_OUTPUT_ATTRIBUTES:
            value = getattr(parsed, attribute, None)
            if value is not None:
                self._output_path(str(value), default=Path(str(value)))

        if operation == "alias-normalize-ir" and not getattr(parsed, "output", None):
            raise PipelineError("alias-normalize-ir requires an explicit --output path under MCP artifact roots")
        if operation == "worktable-diff" and not (
            getattr(parsed, "as_json", False) or getattr(parsed, "output", None)
        ):
            raise PipelineError("worktable-diff requires --json or an explicit --output path")
        if operation == "map-media" and not (
            getattr(parsed, "as_json", False) or getattr(parsed, "output", None)
        ):
            raise PipelineError("map-media requires --json or an explicit --output path")
        if operation == "ir-build" and not getattr(parsed, "no_compile", False):
            raise PipelineError("ir-build requires --no-compile in MCP; use reviewed final generation for compilation")
        if operation == "ir-export":
            source = Path(str(parsed.input))
            if source.suffix.lower() == ".py":
                raise PipelineError("ir-export does not accept Python draft inputs through MCP")

    def _existing_input(self, value: str) -> Path:
        path = Path(value).expanduser().resolve()
        if not path.exists():
            raise PipelineError(f"input path does not exist: {path}")
        return path

    def _output_path(self, value: str | None, *, default: Path) -> Path:
        path = Path(value).expanduser().resolve() if value else default.resolve()
        if not any(_path_is_within(path, root) for root in self.write_roots):
            roots = ", ".join(str(root) for root in self.write_roots)
            raise PipelineError(
                f"MCP write path is outside configured roots: {path}. Allowed roots: {roots}"
            )
        if _path_is_within(path, READY_TO_IMPORT_DIR.resolve()):
            relative = path.relative_to(READY_TO_IMPORT_DIR.resolve())
            if len(relative.parts) < 2 or relative.parts[1] != TEMP_FILES_DIRNAME:
                raise PipelineError(
                    "MCP workflow artifacts must be written under "
                    f"ready-to-import/<project>/{TEMP_FILES_DIRNAME}/: {path}"
                )
        return path


def json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)
