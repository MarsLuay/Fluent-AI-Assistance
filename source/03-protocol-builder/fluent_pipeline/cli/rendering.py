"""Terminal rendering helpers for thin CLI adapters."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from ..authoring_status import AuthoringState, AuthoringStatus
from ..application_services import (
    BundleVerificationResult,
    GenerationResult,
    LogAnalysisResult,
    ProjectImportResult,
    ProjectInspectionResult,
    RepairApplyResult,
    RepairPlanResult,
    RequestSpecCreateResult,
    RequestSpecValidationResult,
)
from ..delivery_bundle import validate_v2_delivery_bundle
from ..progress import ProgressCallback, ProgressEvent, render_plain_progress_event
from ..spec_lint import render_lint_report


ProgressMode = str


def normalize_progress_mode(value: Any) -> ProgressMode:
    if value is True:
        return "plain"
    if value is False:
        return "none"
    mode = str(value or "auto").strip().lower()
    if mode in {"auto", "plain", "json", "none"}:
        return mode
    return "auto"


def progress_callback_from_mode(mode: Any) -> ProgressCallback | None:
    normalized = normalize_progress_mode(mode)
    if normalized == "none":
        return None

    def callback(event: ProgressEvent) -> None:
        if normalized == "json":
            line = json.dumps(asdict(event), sort_keys=True)
        else:
            line = render_plain_progress_event(event)
        print(line, file=sys.stderr, flush=True)

    return callback


def print_authoring_status(status: AuthoringStatus, *, stream: TextIO = sys.stdout) -> None:
    """Render the shared application status without deriving adapter-local state."""
    print(f"Authoring status: {status.status.value}", file=stream)
    for finding in status.findings:
        location = f" at {finding.location}" if finding.location else ""
        print(
            f"Authoring finding [{finding.severity}] {finding.code}{location}: {finding.message}",
            file=stream,
        )
    print(f"Authoring artifacts: {json.dumps(list(status.artifacts))}", file=stream)
    print(f"Allowed action: {status.allowed_action}", file=stream)
    print(f"Next: {status.next_action}", file=stream)
    for action in status.handoff_actions:
        print(f"Next ({action.label}): {action.next_action}", file=stream)


def generation_exit_code(result: GenerationResult) -> int:
    manifest = result.manifest
    if result.authoring_status.status != AuthoringState.FINAL_READY_HANDOFF:
        return 1
    bundle_dir = _generation_protocol_folder(manifest)
    if bundle_dir is None:
        return 1
    return 0 if validate_v2_delivery_bundle(bundle_dir, require_final_reports=True).ok else 1


def generation_simulator_bundle(result: GenerationResult) -> Path:
    manifest = result.manifest
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


def _generation_protocol_folder(manifest: dict[str, Any]) -> Path | None:
    if manifest.get("published_protocol_folder"):
        return Path(str(manifest["published_protocol_folder"]))
    published = Path(str(manifest.get("published_zeia_path") or ""))
    if published.suffix.lower() == ".zeia":
        return published.parent
    artifacts = [Path(str(path)) for path in (manifest.get("ready_to_import_artifacts") or []) if str(path).strip()]
    zeia_artifacts = [path for path in artifacts if path.suffix.lower() == ".zeia"]
    if len(zeia_artifacts) == 1:
        return zeia_artifacts[0].parent
    return None


def print_generation_result(result: GenerationResult, *, stream: TextIO = sys.stdout) -> None:
    manifest = result.manifest
    authoring_status = result.authoring_status
    status = str(manifest.get("workflow_status") or "scaffold_not_validated")
    readiness_status = str(manifest.get("readiness_status") or status)
    if authoring_status.status == AuthoringState.FINAL_READY_HANDOFF:
        if readiness_status == "import_ready_needs_review":
            print(f"Status: {status} (READY TO IMPORT; NEEDS REVIEW)", file=stream)
        else:
            print(f"Status: {status} (READY TO IMPORT)", file=stream)
    elif authoring_status.allowed_action == "provide_full_zeia_export":
        print(f"Status: {status} (FULL ZEIA EXPORT REQUIRED - ask user before continuing)", file=stream)
    elif authoring_status.status == AuthoringState.SCAFFOLD_NEEDS_REVIEW:
        print(f"Status: {status} (SCAFFOLD ONLY - NOT validated, NOT ready to import)", file=stream)
    else:
        print(f"Status: {status} (NOT ready to import - see ready_validation.md)", file=stream)
    print(f"Readiness status: {readiness_status}", file=stream)
    print_authoring_status(authoring_status, stream=stream)
    print(f"Generation workflow: {manifest['workflow_report']}", file=stream)
    print(f"Generation manifest: {manifest['generation_manifest']}", file=stream)
    print(f"Request spec: {manifest['request_spec']}", file=stream)
    inference = manifest.get("inference") or {}
    if manifest.get("inference_report"):
        print(f"Inference review: {manifest['inference_report']}", file=stream)
        print(
            "Inferred details: "
            f"{int(inference.get('inferred_count') or 0)} "
            f"(unresolved: {int(inference.get('unresolved_count') or 0)})",
            file=stream,
        )
    if manifest.get("inference_json"):
        print(f"Inference JSON: {manifest['inference_json']}", file=stream)
    if manifest.get("full_zeia_export_report"):
        print(f"Full ZEIA export check: {manifest['full_zeia_export_report']}", file=stream)
    if manifest.get("protocol_ir"):
        print(f"Protocol IR: {manifest['protocol_ir']}", file=stream)
    if manifest.get("python_draft"):
        print(f"Python draft: {manifest['python_draft']}", file=stream)
    if manifest.get("recreate_script"):
        print(f"Recreate guide: {manifest['recreate_script']}", file=stream)
    if manifest.get("worktable_changes"):
        print(f"Worktable changes: {manifest['worktable_changes']}", file=stream)
    if manifest.get("worktable_patch"):
        print(f"Worktable patch JSON: {manifest['worktable_patch']}", file=stream)
    if manifest.get("validation_diff"):
        print(f"Validation diff: {manifest['validation_diff']}", file=stream)
    if manifest.get("published_protocol_folder"):
        print(f"Ready-to-import protocol folder: {manifest['published_protocol_folder']}", file=stream)
    if manifest.get("published_zeia_path"):
        print(f"Ready-to-import ZEIA: {manifest['published_zeia_path']}", file=stream)
    else:
        print("No ready-to-import ZEIA was published.", file=stream)
    print("Artifact policy: standalone XSCR files are internal only and never deliverables.", file=stream)


def print_project_import_result(result: ProjectImportResult, *, stream: TextIO = sys.stdout) -> None:
    ctx = result.context
    manifest = ctx.manifest
    print(f"Imported project: {ctx.name}", file=stream)
    print(f"  Root:       {ctx.root}", file=stream)
    print(f"  Manifest:   {ctx.root / 'manifest.json'}", file=stream)
    print(f"  Report:     {ctx.root / 'project_report.md'}", file=stream)
    print(f"  Scripts:    {len(manifest.get('scripts', []))}", file=stream)
    print(f"  Objects:    {len(manifest.get('objects', []))}", file=stream)
    print(f"  Workspaces: {len(manifest.get('workspaces', []))}", file=stream)
    print(f"  Snapshots:  {len(manifest.get('snapshot_evidence', []))}", file=stream)
    if result.request.activate:
        print(f"Active project: {ctx.name}", file=stream)


def print_project_inspection_result(
    result: ProjectInspectionResult,
    *,
    as_json: bool = False,
    stream: TextIO = sys.stdout,
) -> None:
    if as_json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True), file=stream)
        return
    if result.report_path and result.report_path.exists():
        print(result.report_path.read_text(encoding="utf-8").rstrip(), file=stream)
        return
    manifest = result.context.manifest
    print(f"Project: {result.context.name}", file=stream)
    print(f"  Root:       {result.context.root}", file=stream)
    print(f"  Scripts:    {len(manifest.get('scripts', []))}", file=stream)
    print(f"  Objects:    {len(manifest.get('objects', []))}", file=stream)
    print(f"  Workspaces: {len(manifest.get('workspaces', []))}", file=stream)
    print(f"  Snapshots:  {len(manifest.get('snapshot_evidence', []))}", file=stream)


def print_request_spec_result(result: RequestSpecCreateResult, *, stream: TextIO = sys.stdout) -> None:
    print(f"Request spec: {result.output_path}", file=stream)
    print("Next: review request.spec.yaml, then run generate --spec request.spec.yaml", file=stream)
    print_authoring_status(result.authoring_status, stream=stream)


def request_spec_validation_exit_code(result: RequestSpecValidationResult) -> int:
    return 1 if result.authoring_status.status == AuthoringState.REQUEST_SPEC_INVALID else 0


def print_request_spec_validation_result(
    result: RequestSpecValidationResult,
    *,
    stream: TextIO = sys.stdout,
) -> None:
    print(render_lint_report(result.result, source=str(result.request.spec_path)), file=stream)
    print_authoring_status(result.authoring_status, stream=stream)


def print_repair_plan_result(
    result: RepairPlanResult,
    *,
    as_json: bool = False,
    stream: TextIO = sys.stdout,
) -> None:
    if result.report_path is not None:
        print(f"Repair report: {result.report_path}", file=stream)
    if as_json:
        payload = result.plan.to_dict()
        payload["authoring_status"] = result.authoring_status.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True), file=stream)
        return
    _print_repair_plan(result.plan, stream=stream)
    print_authoring_status(result.authoring_status, stream=stream)


def print_repair_apply_result(result: RepairApplyResult, *, stream: TextIO = sys.stdout) -> None:
    if result.report_path is not None:
        print(f"Repair report: {result.report_path}", file=stream)
    print(f"Repaired draft: {result.request.output_path}", file=stream)
    if not result.applied_actions:
        print("No actions were applied. The output is a copy of the input draft.", file=stream)
    for action in result.applied_actions:
        suffix = f" line={action.line}" if action.line else ""
        print(f"Applied {action.kind}{suffix}: {action.summary}", file=stream)
    if result.request.context_name:
        print(f"Project context: {result.request.context_name}", file=stream)
    print_authoring_status(result.authoring_status, stream=stream)


def log_analysis_exit_code(result: LogAnalysisResult) -> int:
    return 0 if result.report.get("diagnostic_count", 0) else 1


def print_log_analysis_result(
    result: LogAnalysisResult,
    *,
    as_json: bool = False,
    stream: TextIO | None = None,
) -> None:
    if stream is None:
        stream = sys.stdout
    if result.json_path is not None:
        print(f"FluentControl log diagnostics JSON: {result.json_path}", file=stream)
    if result.report_path is not None:
        print(f"FluentControl log diagnostics report: {result.report_path}", file=stream)
    if as_json:
        print(json.dumps(result.report, indent=2, sort_keys=True), file=stream)
        return
    print(
        "FluentControl log diagnostics: "
        f"{result.report.get('diagnostic_count', 0)} diagnostic(s), "
        f"{result.report.get('record_count', 0)} parsed record(s)",
        file=stream,
    )
    for item in (result.report.get("diagnostics") or [])[:10]:
        print(f"  [{item.get('severity')}] {item.get('title')}", file=stream)
        if item.get("suggested_fix"):
            print(f"    fix: {item.get('suggested_fix')}", file=stream)


def bundle_verification_exit_code(result: BundleVerificationResult) -> int:
    return 0 if result.authoring_status.status == AuthoringState.VERIFICATION_READY else 1


def print_bundle_verification_result(
    result: BundleVerificationResult,
    *,
    as_json: bool = False,
    stream: TextIO = sys.stdout,
) -> None:
    if result.json_path is not None:
        print(f"Ready-validation JSON: {result.json_path}", file=stream)
    if result.report_path is not None:
        print(f"Ready-validation report: {result.report_path}", file=stream)
    if as_json:
        payload = dict(result.report)
        payload["authoring_status"] = result.authoring_status.to_dict()
        print(json.dumps(payload, indent=2, sort_keys=True), file=stream)
        return
    print(
        "Ready validation: "
        f"{'passed' if result.report.get('ready') else 'failed'}; "
        f"status={result.report.get('readiness_status') or result.report.get('offline_validation', {}).get('status')}",
        file=stream,
    )
    for gate in (result.report.get("gates") or [])[:10]:
        if gate.get("status") not in {"failed", "needs_review"}:
            continue
        print(f"  [{gate.get('status')}] {gate.get('id')}: {gate.get('summary')}", file=stream)
    print_authoring_status(result.authoring_status, stream=stream)


def _print_repair_plan(plan: Any, *, stream: TextIO = sys.stdout) -> None:
    actions = list(getattr(plan, "actions", []))
    print(
        f"Repair plan: {len(actions)} action(s) for {getattr(plan, 'draft_path', '<draft>')}",
        file=stream,
    )
    for action in actions:
        line = f" line={action.line}" if getattr(action, "line", None) else ""
        command = f" command={action.command_id}" if getattr(action, "command_id", None) else ""
        print(
            f"  [{action.status}] {action.kind}{line}{command}: {action.summary}",
            file=stream,
        )
