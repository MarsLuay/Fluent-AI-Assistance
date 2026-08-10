"""Shared application services for CLI and MCP adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

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
from .project_context import (
    ProjectContext,
    active_project_name,
    import_project as import_project_context,
    inspection_payload,
    load_project,
    set_active_project,
)
from .repair import RepairAction, RepairPlan, apply_repair_plan, build_repair_plan, render_repair_markdown
from .request_spec import build_request_spec, write_request_spec
from .runner import ensure_parent, write_json
from .spec_lint import LintResult, lint_request_spec_file
from .validation import render_validation_markdown, validate_ready_to_import


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
