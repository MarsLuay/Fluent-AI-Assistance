"""Request builders for thin MCP adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .application_services import (
    LogAnalysisRequest,
    ProjectImportRequest,
    ProjectInspectionRequest,
    RequestSpecCreateRequest,
    RequestSpecValidationRequest,
)
from .generation_options import GenerationOptions
from .generation_workflow import GenerationRequest
from .request_factory import (
    build_generation_request_from_spec,
    build_request_spec_create_request,
)


def generation_request_from_mcp(
    spec_source: Path,
    *,
    spec: dict[str, Any],
    context: str | None,
    selected_ir: Path | None,
    output_directory: Path,
    mode: str,
    generation_options: GenerationOptions | dict[str, Any] | None,
) -> GenerationRequest:
    return build_generation_request_from_spec(
        spec_source,
        spec=spec,
        context=context,
        selected_ir=selected_ir,
        output_directory=output_directory,
        mode=mode,
        generation_options=generation_options,
        use_active_context=False,
    )


def project_import_request_from_mcp(
    archive: Path,
    *,
    name: str | None,
    activate: bool,
    snapshots: list[Path] | None,
    force: bool,
) -> ProjectImportRequest:
    return ProjectImportRequest(
        archive=archive,
        name=name,
        force=force,
        snapshot_archives=tuple(snapshots or []),
        activate=activate,
    )


def project_inspection_request_from_mcp(name: str | None) -> ProjectInspectionRequest:
    return ProjectInspectionRequest(context_name=name)


def log_analysis_request_from_mcp(log_path: Path, *, output_directory: Path) -> LogAnalysisRequest:
    return LogAnalysisRequest(
        log_path=log_path,
        json_path=output_directory / "fluent_log_report.json",
        report_path=output_directory / "fluent_log_report.md",
    )


def request_spec_create_request_from_mcp(
    intent: str,
    *,
    context: str | None,
    source_scripts: list[str] | None,
    protocol_name: str | None,
    generation_options: GenerationOptions | dict[str, Any] | None,
    output_path: Path,
) -> RequestSpecCreateRequest:
    return build_request_spec_create_request(
        intent=intent,
        output_path=output_path,
        protocol_name=protocol_name,
        context=context,
        contexts=tuple([{"name": context}] if context else []),
        source_scripts=tuple(source_scripts or []),
        generation_options=generation_options,
    )


def request_spec_validation_request_from_mcp(spec_path: Path) -> RequestSpecValidationRequest:
    return RequestSpecValidationRequest(spec_path=spec_path)
