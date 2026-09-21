"""Shared application services for CLI and MCP adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping
import zipfile

from .authoring_status import (
    AuthoringStatus,
    generation_status,
    repair_apply_status,
    repair_plan_status,
    request_spec_created_status,
    request_spec_validation_status,
    verification_status,
)
from .fluent_log_parser import (
    build_fluent_log_report,
    build_latest_fluent_log_report,
    render_fluent_log_report_markdown,
)
from .generation_options import GenerationOptions
from .generation_workflow import GenerationRequest, run_generation_workflow
from .progress import ProgressCallback
from .config import READY_TO_IMPORT_DIR, TEMP_FILES_DIRNAME
from .project_context import (
    ProjectContext,
    active_project_name,
    create_project_collection,
    import_project as import_project_context,
    inspection_payload,
    load_project,
    load_project_collection,
    set_active_project,
)
from .repair import RepairAction, RepairPlan, apply_repair_plan, build_repair_plan, render_repair_markdown
from .request_spec import build_request_spec, write_request_spec
from .runner import ensure_parent, write_json
from .readiness import full_export_readiness_to_offline_validation
from .spec_lint import LintResult, lint_request_spec_file
from .validation import render_validation_markdown, validate_ready_to_import
from tecan_reader.diagnostics import (
    DiagnosticCode,
    IngestionArchiveError,
    make_diagnostic,
    sort_diagnostics,
)
from tecan_reader.full_export_readiness import (
    resolve_full_export_readiness as resolve_reader_readiness,
)
from tecan_reader.project_index import discover_zeia_paths
from tecan_reader.zeia_adapters import probe_zeia


@dataclass(frozen=True)
class GenerationResult:
    request: GenerationRequest
    manifest: dict[str, Any]

    @property
    def authoring_status(self) -> AuthoringStatus:
        return generation_status(self.manifest)

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_status": self.manifest.get("workflow_status"),
            "ready_to_import": bool(self.manifest.get("ready_to_import")),
            "readiness_status": self.manifest.get("readiness_status"),
            "readiness": self.manifest.get("readiness"),
            "published_protocol_folder": self.manifest.get("published_protocol_folder"),
            "published_zeia_path": self.manifest.get("published_zeia_path"),
            "published_artifacts": self.manifest.get("published_artifacts") or [],
            "internal_artifacts": self.manifest.get("internal_artifacts") or [],
            "deliverable": self.manifest.get("deliverable"),
            "authoring_status": self.authoring_status.to_dict(),
            "manifest": self.manifest,
        }


@dataclass(frozen=True)
class ProjectImportRequest:
    archive: Path
    name: str | None = None
    force: bool = False
    snapshot_archives: tuple[Path, ...] = ()
    activate: bool = False


@dataclass(frozen=True)
class ProjectImportResult:
    request: ProjectImportRequest
    context: ProjectContext
    active_context_name: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = inspection_payload(self.context)
        payload["active"] = self.request.activate
        payload["active_context_name"] = self.active_context_name
        return payload


@dataclass(frozen=True)
class ProjectInspectionRequest:
    context_name: str | None = None


@dataclass(frozen=True)
class ProjectInspectionResult:
    request: ProjectInspectionRequest
    context: ProjectContext
    report_path: Path | None

    def to_dict(self) -> dict[str, Any]:
        return inspection_payload(self.context, report_path=self.report_path)


@dataclass(frozen=True)
class FullExportReadinessRequest:
    """Inputs for the shared full-export readiness service."""

    archives: tuple[Path, ...] = ()
    context_name: str | None = None
    approve_partial_zeia: bool = False


@dataclass(frozen=True)
class FullExportReadinessResult:
    request: FullExportReadinessRequest
    readiness: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.readiness)


@dataclass(frozen=True)
class InputInspectionRequest:
    """Inputs for one-shot, generation-free ZEIA inspection."""

    input_path: Path


@dataclass(frozen=True)
class InputInspectionResult:
    request: InputInspectionRequest
    archives: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()
    classification: str = "unsupported"

    @property
    def ok(self) -> bool:
        return self.classification == "supported"

    @property
    def exit_code(self) -> int:
        if self.ok:
            return 0
        return {
            "supported": 0,
            "missing": 2,
            "unsupported": 3,
            "ambiguous": 4,
            "corrupt": 5,
        }.get(self.classification, 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "tecan.input_inspection.v1",
            "input": str(self.request.input_path.expanduser().resolve()),
            "classification": self.classification,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "archives": [dict(item) for item in self.archives],
            "diagnostics": sort_diagnostics(self.diagnostics),
        }


@dataclass(frozen=True)
class FullExportValidationRequest:
    """Inputs for generation-free authoritative full-export validation."""

    input_path: Path | None = None
    archives: tuple[Path, ...] = ()
    context_name: str | None = None
    approve_partial_zeia: bool = False


@dataclass(frozen=True)
class FullExportValidationResult:
    request: FullExportValidationRequest
    readiness: dict[str, Any]

    @property
    def ok(self) -> bool:
        return bool(self.readiness.get("accepted"))

    @property
    def exit_code(self) -> int:
        if self.ok:
            return 0
        return {
            "complete": 0,
            "complete_with_warnings": 0,
            "partial": 5,
            "unsupported": 3,
            "ambiguous": 4,
            "corrupt": 5,
        }.get(str(self.readiness.get("readiness_status") or self.readiness.get("status") or ""), 1)

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.readiness)
        payload["schema_version"] = "tecan.full_export_validation.v1"
        payload["input"] = str(self.request.input_path.expanduser().resolve()) if self.request.input_path else None
        payload["ok"] = self.ok
        payload["exit_code"] = self.exit_code
        payload["offline_validation"] = full_export_readiness_to_offline_validation(self.readiness)
        return payload


@dataclass(frozen=True)
class OneShotRunRequest:
    """Inputs for the canonical full-export-to-handoff workflow."""

    input_path: Path
    request: str | None = None
    request_file: Path | None = None
    output_directory: Path | None = None
    protocol_name: str | None = None
    project_name: str | None = None
    approve_partial_zeia: bool = False
    force_import: bool = False
    generation_options: GenerationOptions = field(default_factory=GenerationOptions)
    progress_callback: ProgressCallback | None = None


@dataclass(frozen=True)
class OneShotRunResult:
    """Structured result for the one-shot service and CLI adapter."""

    request: OneShotRunRequest
    input_archives: tuple[Path, ...] = ()
    context_name: str | None = None
    readiness: dict[str, Any] | None = None
    request_spec_path: Path | None = None
    generation: GenerationResult | None = None
    failed_stage: str | None = None
    error: str | None = None

    _EXIT_CODES = {
        "discover_input": 2,
        "request": 3,
        "import_context": 4,
        "readiness": 5,
        "request_spec": 6,
        "request_validation": 7,
        "generation": 8,
    }

    @property
    def ok(self) -> bool:
        return bool(
            self.generation is not None
            and self.generation.manifest.get("ready_to_import")
            and not self.failed_stage
        )

    @property
    def exit_code(self) -> int:
        """Return a stable CLI status for the first failed workflow stage."""
        if self.ok:
            return 0
        return self._EXIT_CODES.get(self.failed_stage or "", 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "input_archives": [str(path) for path in self.input_archives],
            "context_name": self.context_name,
            "readiness": self.readiness,
            "request_spec_path": str(self.request_spec_path) if self.request_spec_path else None,
            "failed_stage": self.failed_stage,
            "error": self.error,
            "exit_code": self.exit_code,
            "generation": self.generation.to_dict() if self.generation else None,
        }


@dataclass(frozen=True)
class RequestSpecCreateRequest:
    intent: str
    output_path: Path
    protocol_name: str | None = None
    context: str | None = None
    context_kind: str | None = None
    contexts: tuple[dict[str, Any], ...] = ()
    project_archives: tuple[Path, ...] = ()
    collection: str | None = None
    source_scripts: tuple[str, ...] = ()
    pattern_refs: tuple[str, ...] = ()
    index_db: Path | None = None
    pattern_ids: tuple[int | str, ...] = ()
    pattern_queries: tuple[str, ...] = ()
    source_script_rank: int = 1
    generation_options: GenerationOptions = field(default_factory=GenerationOptions)
    fluent_method: str | None = None


@dataclass(frozen=True)
class RequestSpecCreateResult:
    request: RequestSpecCreateRequest
    spec: dict[str, Any]
    output_path: Path

    @property
    def authoring_status(self) -> AuthoringStatus:
        return request_spec_created_status(self.output_path)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "spec": self.spec,
            "artifacts": [str(self.output_path)],
            "authoring_status": self.authoring_status.to_dict(),
        }


@dataclass(frozen=True)
class RequestSpecValidationRequest:
    spec_path: Path


@dataclass(frozen=True)
class RequestSpecValidationResult:
    request: RequestSpecValidationRequest
    result: LintResult

    @property
    def authoring_status(self) -> AuthoringStatus:
        return request_spec_validation_status(
            ok=self.result.ok,
            findings=(asdict(item) for item in self.result.findings),
            spec_path=self.request.spec_path,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.result.ok,
            "estimated_ir_body_steps": self.result.estimated_ir_body_steps,
            "findings": [asdict(item) for item in self.result.findings],
            "authoring_status": self.authoring_status.to_dict(),
        }


@dataclass(frozen=True)
class RepairPlanRequest:
    draft_path: Path
    context_name: str | None = None
    simulation_json_path: Path | None = None
    report_path: Path | None = None


@dataclass(frozen=True)
class RepairPlanResult:
    request: RepairPlanRequest
    plan: RepairPlan
    report_path: Path | None = None

    @property
    def authoring_status(self) -> AuthoringStatus:
        return repair_plan_status(
            self.plan.to_dict(),
            artifacts=(self.report_path,) if self.report_path else (),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan.to_dict(),
            "report_path": str(self.report_path) if self.report_path else None,
            "authoring_status": self.authoring_status.to_dict(),
        }


@dataclass(frozen=True)
class RepairApplyRequest:
    draft_path: Path
    output_path: Path
    context_name: str | None = None
    simulation_json_path: Path | None = None
    apply_modeling: bool = False
    report_path: Path | None = None


@dataclass(frozen=True)
class RepairApplyResult:
    request: RepairApplyRequest
    plan: RepairPlan
    applied_actions: tuple[RepairAction, ...]
    report_path: Path | None = None

    @property
    def authoring_status(self) -> AuthoringStatus:
        artifacts = [self.request.output_path]
        if self.report_path:
            artifacts.append(self.report_path)
        return repair_apply_status(
            plan=self.plan.to_dict(),
            applied_actions=(action.to_dict() for action in self.applied_actions),
            artifacts=artifacts,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "output_path": str(self.request.output_path),
            "plan": self.plan.to_dict(),
            "applied_actions": [action.to_dict() for action in self.applied_actions],
            "report_path": str(self.report_path) if self.report_path else None,
            "authoring_status": self.authoring_status.to_dict(),
        }


@dataclass(frozen=True)
class BundleVerificationRequest:
    compiled_xscr: Path
    draft_path: Path | None = None
    protocol_ir: Path | None = None
    worklist: Path | None = None
    source_projects: tuple[Path, ...] = ()
    source_scripts: tuple[Path, ...] = ()
    source_xscr: Path | None = None
    source_manifest: dict[str, Any] | None = None
    recreate_guide: Path | None = None
    validation_context: Mapping[str, Any] | None = None
    report_path: Path | None = None
    json_path: Path | None = None


@dataclass(frozen=True)
class BundleVerificationResult:
    request: BundleVerificationRequest
    report: dict[str, Any]
    report_path: Path | None = None
    json_path: Path | None = None

    @property
    def authoring_status(self) -> AuthoringStatus:
        artifacts = [path for path in (self.report_path, self.json_path) if path is not None]
        return verification_status(self.report, artifacts=artifacts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.report.get("ready")),
            "ready": bool(self.report.get("ready")),
            "readiness_status": self.report.get("readiness_status"),
            "report": self.report,
            "report_path": str(self.report_path) if self.report_path else None,
            "json_path": str(self.json_path) if self.json_path else None,
            "authoring_status": self.authoring_status.to_dict(),
        }


@dataclass(frozen=True)
class LogAnalysisRequest:
    log_path: Path | None = None
    audit_paths: tuple[Path, ...] = ()
    xscr_paths: tuple[Path, ...] = ()
    latest: bool = False
    since_hours: float = 48.0
    max_files: int = 12
    max_records: int = 80
    report_path: Path | None = None
    json_path: Path | None = None


@dataclass(frozen=True)
class LogAnalysisResult:
    request: LogAnalysisRequest
    report: dict[str, Any]
    report_path: Path | None = None
    json_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "report": self.report,
            "report_path": str(self.report_path) if self.report_path else None,
            "json_path": str(self.json_path) if self.json_path else None,
        }


def generate_protocol(
    request: GenerationRequest,
    *,
    progress_callback: ProgressCallback | None = None,
) -> GenerationResult:
    """Run the complete generation workflow as one top-level operation."""
    if progress_callback is None:
        return GenerationResult(request=request, manifest=run_generation_workflow(request))
    return GenerationResult(
        request=request,
        manifest=run_generation_workflow(request, progress_callback=progress_callback),
    )


def import_project(request: ProjectImportRequest) -> ProjectImportResult:
    """Import a ZEIA project context and optionally activate it."""
    context = import_project_context(
        request.archive,
        name=request.name,
        force=request.force,
        snapshot_archives=list(request.snapshot_archives),
    )
    active_context_name = None
    if request.activate:
        active_context_name = set_active_project(context.name).name
    return ProjectImportResult(
        request=request,
        context=context,
        active_context_name=active_context_name,
    )


def inspect_project(request: ProjectInspectionRequest) -> ProjectInspectionResult:
    """Load one imported project and surface its stable inspection artifacts."""
    context = _load_project_context(request.context_name)
    if context is None:
        raise ValueError("inspect_project requires a context or an active project")
    report_path = context.root / "project_report.md"
    return ProjectInspectionResult(
        request=request,
        context=context,
        report_path=report_path if report_path.exists() else None,
    )


def resolve_full_export_readiness(
    request: FullExportReadinessRequest,
) -> FullExportReadinessResult:
    """Resolve readiness once for CLI, MCP, and direct Python callers."""
    if request.context_name:
        try:
            context = load_project(request.context_name)
        except Exception:
            context = load_project_collection(request.context_name)
        from tecan_reader.full_export_readiness import resolve_manifest_readiness

        readiness = resolve_manifest_readiness(
            context.manifest,
            approve_partial_zeia=request.approve_partial_zeia,
        )
    else:
        result = resolve_reader_readiness(
            request.archives,
            approve_partial_zeia=request.approve_partial_zeia,
        )
        readiness = result.to_dict()
    return FullExportReadinessResult(request=request, readiness=readiness)


def inspect_input(request: InputInspectionRequest) -> InputInspectionResult:
    """Inspect ZEIA input structure without importing or running generation."""
    source = request.input_path.expanduser()
    try:
        paths = tuple(discover_zeia_paths((source,)))
    except FileNotFoundError as exc:
        diagnostic = make_diagnostic(
            DiagnosticCode.INPUT_NOT_FOUND,
            str(exc),
            archive_path=str(source.resolve()),
            next_action="Provide an existing .zeia archive or a directory containing .zeia archives.",
            exception=exc,
        )
        return InputInspectionResult(request, diagnostics=(diagnostic,), classification="missing")
    except (OSError, ValueError) as exc:
        diagnostic = make_diagnostic(
            DiagnosticCode.INPUT_UNREADABLE,
            str(exc),
            archive_path=str(source.resolve()),
            next_action="Provide a readable .zeia archive or export directory.",
            exception=exc,
        )
        return InputInspectionResult(request, diagnostics=(diagnostic,), classification="corrupt")

    archives: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for path in paths:
        try:
            detection = probe_zeia(path)
            records = list(detection.diagnostic_records)
            archive_status = _inspection_classification(detection.status, records)
            archives.append(
                {
                    "path": str(path.resolve()),
                    "status": detection.status,
                    "classification": archive_status,
                    "detection": detection.to_dict(),
                }
            )
            diagnostics.extend(records)
        except IngestionArchiveError as exc:
            record = dict(exc.diagnostic)
            archives.append(
                {
                    "path": str(path.resolve()),
                    "status": "corrupt",
                    "classification": "corrupt",
                    "detection": {"status": "corrupt", "diagnostic_records": [record]},
                }
            )
            diagnostics.append(record)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            record = make_diagnostic(
                DiagnosticCode.ARCHIVE_INVALID,
                f"{type(exc).__name__}: {exc}",
                archive_path=str(path.resolve()),
                next_action="Provide an uncorrupted FluentControl ZEIA ZIP export.",
                exception=exc,
            )
            archives.append(
                {
                    "path": str(path.resolve()),
                    "status": "corrupt",
                    "classification": "corrupt",
                    "detection": {"status": "corrupt", "diagnostic_records": [record]},
                }
            )
            diagnostics.append(record)

    classification = _aggregate_inspection_classification(archives, diagnostics)
    return InputInspectionResult(
        request,
        archives=tuple(sorted(archives, key=lambda item: str(item.get("path") or ""))),
        diagnostics=tuple(sort_diagnostics(diagnostics)),
        classification=classification,
    )


def validate_full_export(request: FullExportValidationRequest) -> FullExportValidationResult:
    """Run the project-reader-owned full-export readiness evaluation."""
    archives = request.archives
    if request.input_path is not None:
        archives = (request.input_path,)
    readiness = resolve_full_export_readiness(
        FullExportReadinessRequest(
            archives=tuple(archives),
            context_name=request.context_name,
            approve_partial_zeia=request.approve_partial_zeia,
        )
    ).to_dict()
    return FullExportValidationResult(request=request, readiness=readiness)


# Descriptive aliases for adapters/tests that use the command terminology.
InspectRequest = InputInspectionRequest
InspectResult = InputInspectionResult
ValidateRequest = FullExportValidationRequest
ValidateResult = FullExportValidationResult


def _inspection_classification(status: str, records: list[Mapping[str, Any]]) -> str:
    codes = {str(item.get("code") or "").upper() for item in records}
    if "AMBIGUOUS" in status.casefold() or any("AMBIGUOUS" in code for code in codes):
        return "ambiguous"
    if any(
        code in {
            DiagnosticCode.ARCHIVE_INVALID,
            DiagnosticCode.INPUT_UNREADABLE,
            DiagnosticCode.ENCODING_DECODE_FAILED,
            DiagnosticCode.PARSER_FAILED,
            DiagnosticCode.ZEIA_SCHEMA_MALFORMED,
            DiagnosticCode.LIMIT_ENTRY_COUNT,
            DiagnosticCode.LIMIT_UNCOMPRESSED_BYTES,
        }
        for code in codes
    ):
        return "corrupt"
    if status.casefold() == "supported":
        return "supported"
    return "unsupported"


def _aggregate_inspection_classification(
    archives: list[Mapping[str, Any]], diagnostics: list[Mapping[str, Any]]
) -> str:
    if not archives:
        return "missing"
    classifications = {str(item.get("classification") or "unsupported") for item in archives}
    if "corrupt" in classifications:
        return "corrupt"
    if "ambiguous" in classifications:
        return "ambiguous"
    if classifications == {"supported"}:
        return "supported"
    return "unsupported"


def run_one_shot(request: OneShotRunRequest) -> OneShotRunResult:
    """Run import, readiness, request capture, generation, and packaging in order.

    The service deliberately returns a stage-labelled result for expected input
    and readiness failures so CLI, MCP, and direct callers share the same
    failure boundary. The source archives are only read; imported contexts and
    generated artifacts are written under the normal ready-to-import workspace.
    """
    try:
        archives = tuple(discover_zeia_paths((request.input_path,)))
    except Exception as exc:
        return OneShotRunResult(request=request, failed_stage="discover_input", error=str(exc))
    if not archives:
        return OneShotRunResult(
            request=request,
            failed_stage="discover_input",
            error=f"no .zeia archives found at {request.input_path}",
        )

    intent = str(request.request or "").strip()
    if request.request_file is not None:
        try:
            file_intent = request.request_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return OneShotRunResult(
                request=request,
                input_archives=archives,
                failed_stage="request",
                error=str(exc),
            )
        if intent and file_intent:
            return OneShotRunResult(
                request=request,
                input_archives=archives,
                failed_stage="request",
                error="provide exactly one of request or request_file",
            )
        intent = file_intent
    if not intent:
        return OneShotRunResult(
            request=request,
            input_archives=archives,
            failed_stage="request",
            error="a non-empty request or request_file is required",
        )

    def safe_name(value: str) -> str:
        cleaned = "".join(char if char.isalnum() or char in "-_." else "-" for char in value)
        return cleaned.strip("-_.")[:80] or "one-shot"

    base_name = safe_name(request.project_name or request.protocol_name or archives[0].stem)
    context_names: list[str] = []
    try:
        for index, archive in enumerate(archives, start=1):
            name = base_name if len(archives) == 1 else f"{base_name}-{index}"
            imported = import_project_context(
                archive,
                name=name,
                force=request.force_import,
                snapshot_archives=[],
            )
            context_names.append(imported.name)
        if len(context_names) == 1:
            context_name = context_names[0]
        else:
            context_name = create_project_collection(
                base_name,
                context_names,
                force=request.force_import,
            ).name
    except Exception as exc:
        return OneShotRunResult(
            request=request,
            input_archives=archives,
            failed_stage="import_context",
            error=str(exc),
        )

    try:
        readiness_result = resolve_full_export_readiness(
            FullExportReadinessRequest(
                context_name=context_name,
                approve_partial_zeia=request.approve_partial_zeia,
            )
        )
    except Exception as exc:
        return OneShotRunResult(
            request=request,
            input_archives=archives,
            context_name=context_name,
            failed_stage="readiness",
            error=str(exc),
        )
    readiness = readiness_result.to_dict()
    if not readiness.get("accepted"):
        return OneShotRunResult(
            request=request,
            input_archives=archives,
            context_name=context_name,
            readiness=readiness,
            failed_stage="readiness",
            error=str(readiness.get("summary") or "full-export readiness did not pass"),
        )

    output_directory = (
        request.output_directory
        or READY_TO_IMPORT_DIR / base_name / TEMP_FILES_DIRNAME / "one-shot"
    ).resolve()
    spec_path = output_directory / "request.spec.yaml"
    options = replace(
        request.generation_options,
        approve_partial_zeia=request.approve_partial_zeia
        or request.generation_options.approve_partial_zeia,
        protocol_name=request.protocol_name or request.generation_options.protocol_name or base_name,
        project_archive=None,
        project_name=None,
        force_import=False,
    )
    try:
        spec_result = create_request_spec(
            RequestSpecCreateRequest(
                intent=intent,
                output_path=spec_path,
                protocol_name=request.protocol_name or base_name,
                context=context_name if len(archives) == 1 else None,
                context_kind="project_collection" if len(archives) > 1 else None,
                contexts=tuple({"name": name} for name in context_names),
                project_archives=archives,
                collection=context_name if len(archives) > 1 else None,
                generation_options=options,
            )
        )
    except Exception as exc:
        return OneShotRunResult(
            request=request,
            input_archives=archives,
            context_name=context_name,
            readiness=readiness,
            request_spec_path=spec_path,
            failed_stage="request_spec",
            error=str(exc),
        )
    lint = validate_request_spec(RequestSpecValidationRequest(spec_path=spec_path))
    if not lint.result.ok:
        return OneShotRunResult(
            request=request,
            input_archives=archives,
            context_name=context_name,
            readiness=readiness,
            request_spec_path=spec_path,
            failed_stage="request_validation",
            error="request spec validation failed",
        )
    try:
        from .request_factory import build_generation_request_from_spec

        generation_request = build_generation_request_from_spec(
            spec_path,
            spec=spec_result.spec,
            context=context_name,
            selected_ir=None,
            output_directory=output_directory,
            mode="final",
            generation_options=options,
            use_active_context=False,
        )
        generated = generate_protocol(
            generation_request,
            progress_callback=request.progress_callback,
        )
    except Exception as exc:
        return OneShotRunResult(
            request=request,
            input_archives=archives,
            context_name=context_name,
            readiness=readiness,
            request_spec_path=spec_path,
            failed_stage="generation",
            error=str(exc),
        )
    return OneShotRunResult(
        request=request,
        input_archives=archives,
        context_name=context_name,
        readiness=readiness,
        request_spec_path=spec_path,
        generation=generated,
    )


def create_request_spec(request: RequestSpecCreateRequest) -> RequestSpecCreateResult:
    """Create and persist a request specification through the shared application layer."""
    spec = build_request_spec(
        intent=request.intent,
        protocol_name=request.protocol_name,
        context=request.context,
        context_kind=request.context_kind,
        contexts=list(request.contexts),
        project_archives=list(request.project_archives),
        collection=request.collection,
        source_scripts=list(request.source_scripts),
        pattern_refs=list(request.pattern_refs),
        index_db=request.index_db,
        pattern_ids=list(request.pattern_ids),
        pattern_queries=list(request.pattern_queries),
        source_script_rank=request.source_script_rank,
        generation_options=request.generation_options,
        fluent_method=request.fluent_method,
    )
    write_request_spec(spec, request.output_path)
    return RequestSpecCreateResult(
        request=request,
        spec=spec,
        output_path=request.output_path,
    )


def validate_request_spec(request: RequestSpecValidationRequest) -> RequestSpecValidationResult:
    """Lint a request specification through the shared application layer."""
    return RequestSpecValidationResult(
        request=request,
        result=lint_request_spec_file(request.spec_path),
    )


def plan_repair(request: RepairPlanRequest) -> RepairPlanResult:
    """Build a repair plan for a generated draft."""
    plan = build_repair_plan(
        request.draft_path,
        context=_load_project_context(request.context_name),
        simulation_json_path=request.simulation_json_path,
    )
    report_path = request.report_path.resolve() if request.report_path else None
    if report_path is not None:
        ensure_parent(report_path)
        report_path.write_text(render_repair_markdown(plan), encoding="utf-8")
    return RepairPlanResult(request=request, plan=plan, report_path=report_path)


def apply_repair(request: RepairApplyRequest) -> RepairApplyResult:
    """Plan and apply repairs through one shared service path."""
    plan_result = plan_repair(
        RepairPlanRequest(
            draft_path=request.draft_path,
            context_name=request.context_name,
            simulation_json_path=request.simulation_json_path,
            report_path=request.report_path,
        )
    )
    applied = apply_repair_plan(
        plan_result.plan,
        request.output_path,
        apply_modeling=request.apply_modeling,
    )
    return RepairApplyResult(
        request=request,
        plan=plan_result.plan,
        applied_actions=tuple(applied),
        report_path=plan_result.report_path,
    )


def _discover_simulation_json(draft_path: Path) -> Path | None:
    candidates = (
        draft_path.with_suffix(".simulation.json"),
        draft_path.parent / f"{draft_path.stem}.simulation.json",
        draft_path.parent / "reports" / "simulation.json",
        draft_path.parent.parent / "reports" / "simulation.json",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _simulation_passed(data: Mapping[str, Any] | None) -> bool:
    if not isinstance(data, Mapping):
        return False
    if "ok" in data:
        return bool(data.get("ok")) and not data.get("failure")
    status = str(data.get("status") or "").lower()
    return status in {"ok", "passed", "pass", "success"} and not data.get("failure")


def _enrich_bundle_verification_context(
    context: Mapping[str, Any] | None,
    *,
    draft_path: Path | None,
    project: ProjectContext | None = None,
) -> dict[str, Any]:
    """Fill simulation/repair gates when verify-bundle is given a draft.

    Generate fills these via the workflow. Standalone ``verify-bundle`` previously
    failed STRICT_READY solely because the context omitted them even when a draft
    (and often a nearby simulation JSON) was available.
    """
    import json

    from .runner import parse_json_stdout, run_fluentcoder

    enriched = dict(context or {})
    if draft_path is None or not draft_path.is_file():
        return enriched

    sim_path = _discover_simulation_json(draft_path)
    if "simulation_passed" not in enriched and "simulation" not in enriched:
        simulation_data: dict[str, Any] | None = None
        if sim_path is not None:
            try:
                loaded = json.loads(sim_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    simulation_data = loaded
            except (OSError, json.JSONDecodeError):
                simulation_data = None
        if simulation_data is None:
            result = run_fluentcoder(["simulate", draft_path, "--json"])
            try:
                simulation_data = parse_json_stdout(result)
            except Exception:
                simulation_data = None
            if simulation_data is None:
                simulation_data = {
                    "status": "failed",
                    "failure": (result.stderr or result.stdout or "simulate produced no JSON"),
                }
            elif not result.ok and "status" not in simulation_data:
                simulation_data = {
                    **simulation_data,
                    "status": "failed",
                    "failure": result.stderr or result.stdout or "simulate failed",
                }
        if isinstance(simulation_data, dict):
            enriched["simulation"] = simulation_data
            enriched["simulation_passed"] = _simulation_passed(simulation_data)

    if "repair_plan" not in enriched:
        plan = build_repair_plan(
            draft_path,
            context=project,
            simulation_json_path=sim_path,
        )
        enriched["repair_plan"] = plan.to_dict()
    return enriched


def verify_bundle(request: BundleVerificationRequest) -> BundleVerificationResult:
    """Validate a ready-to-import bundle through the shared application layer."""
    project = None
    if request.source_manifest and isinstance(request.source_manifest, Mapping):
        name = str(request.source_manifest.get("name") or request.source_manifest.get("context") or "").strip()
        if name:
            try:
                project = load_project(name)
            except Exception:
                project = None
    validation_context = _enrich_bundle_verification_context(
        request.validation_context,
        draft_path=request.draft_path,
        project=project,
    )
    report = validate_ready_to_import(
        compiled_xscr=request.compiled_xscr,
        draft_path=request.draft_path,
        protocol_ir=request.protocol_ir,
        worklist=request.worklist,
        source_projects=list(request.source_projects),
        source_scripts=list(request.source_scripts),
        source_xscr=request.source_xscr,
        source_manifest=request.source_manifest,
        recreate_guide=request.recreate_guide,
        validation_context=validation_context,
    )
    report_path = request.report_path.resolve() if request.report_path else None
    json_path = request.json_path.resolve() if request.json_path else None
    if report_path is not None:
        ensure_parent(report_path)
        report_path.write_text(render_validation_markdown(report), encoding="utf-8")
    if json_path is not None:
        write_json(json_path, report)
    return BundleVerificationResult(
        request=request,
        report=report,
        report_path=report_path,
        json_path=json_path,
    )


def analyze_logs(request: LogAnalysisRequest) -> LogAnalysisResult:
    """Parse one FluentControl log or scan the latest recent logs."""
    if request.latest:
        report = build_latest_fluent_log_report(
            since_hours=request.since_hours,
            max_files=request.max_files,
            max_records=request.max_records,
        )
    elif request.log_path is not None:
        report = build_fluent_log_report(
            request.log_path,
            audit_paths=request.audit_paths,
            xscr_paths=request.xscr_paths,
        )
    else:
        raise ValueError("analyze_logs requires either latest=True or log_path")
    report_path = request.report_path.resolve() if request.report_path else None
    json_path = request.json_path.resolve() if request.json_path else None
    if report_path is not None:
        ensure_parent(report_path)
        report_path.write_text(render_fluent_log_report_markdown(report), encoding="utf-8")
    if json_path is not None:
        write_json(json_path, report)
    return LogAnalysisResult(
        request=request,
        report=report,
        report_path=report_path,
        json_path=json_path,
    )


def _load_project_context(context_name: str | None) -> ProjectContext | None:
    resolved_name = context_name or active_project_name()
    if not resolved_name:
        return None
    return load_project(resolved_name)
